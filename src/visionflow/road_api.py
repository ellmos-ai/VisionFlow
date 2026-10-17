"""Owner-scoped sessions, recordings, and clips for the road camera demo.

The controller and model receive only camera frames and source identifiers.
Simulator event labels never enter analysis results. The source override opens
only a registered road session; HTTP callers must first pass the owner check.
"""
from __future__ import annotations
from contextlib import nullcontext
from functools import wraps

import base64
import hashlib
from copy import deepcopy
import math
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import cv2

from visionflow.contracts import Frame, UnknownRun
from visionflow.recording import ClipNotReady, ClipRef, RecordingError, RecordingStore
from visionflow.simulation import (
    ProducerLimitReached,
    SimulationCapacityError,
    SimulationSession,
    SimulationStore,
)


ROAD_SOURCE_PREFIX = "road-sim-"
# Analysis overlay: only during dense sampling and while the analysed frame is recent.
_OVERLAY_MIN_RATE = 1.2
_OVERLAY_MAX_LAG_SECONDS = 4.0
# One scenario event per analysis (W4). Without a candidate, an event is over once
# the analysis has looked at this many seconds of footage after it (the slowest
# object crosses the scene in about 8.5 s); this is measured on the analysed
# frames, so a lagging analysis extends the wait. Once the run moved past the node
# that watched the event, a candidate keeps the source busy through its 10 s tail,
# clip and model review until the run ends (the estimate only feeds the countdown).
# The cap bounds every wait.
_EVENT_SCENE_SECONDS = 10.0
_EVENT_REVIEW_SECONDS = 20.0
_EVENT_MAX_COOLDOWN_SECONDS = 60.0
_TERMINAL_RUN_STATES = frozenset({"completed", "failed", "cancelled"})
# RecordingStore.finish() reports a session without frames as ClipNotReady.
_EMPTY_RECORDING_CODES = frozenset({"empty_recording", "clip_not_ready"})

_ROAD_MONITOR_WORKFLOW: dict[str, Any] = {
    "schema_version": 1,
    "name": "road-monitor",
    "input": {"kind": "camera", "source": "USER_PROVIDED"},
    "limits": {
        "max_observation_seconds": 15,
        "max_frames": 80,
        "max_llm_calls": 1,
    },
    "nodes": [
        {
            "id": "initial-direction",
            "type": "analyze",
            "params": {
                "tool": "direction",
                "tool_params": {
                    "lanes": [
                        {"id": "northbound", "region": [0, 90, 480, 450], "expected": [0, -1]},
                        {"id": "southbound", "region": [480, 90, 480, 450], "expected": [0, 1]},
                    ],
                    "min_frames": 3,
                    "min_displacement_px": 4.0,
                    "opposition_cosine": -0.75,
                    "initial_frames": 3,
                },
            },
            "next": ["watch"],
        },
        {
            "id": "watch",
            "type": "observe",
            "params": {
                "duration_seconds": 5,
                "sample_fps": 5,
                "region": None,
                "stop_when": {
                    "tool": "direction",
                    "metric": "opposition_count",
                    "operator": "gte",
                    "value": 1,
                    "min_consecutive": 3,
                },
            },
            "next": ["candidate"],
        },
        {
            "id": "candidate",
            "type": "branch",
            "params": {
                "finding_tool": "direction",
                "metric": "opposition_count",
                "operator": "gte",
                "value": 1,
                "then_node": "candidate-tail",
                "else_node": "report",
            },
            "next": ["candidate-tail", "report"],
        },
        {
            "id": "candidate-tail",
            "type": "observe",
            "params": {
                "duration_seconds": 10,
                "sample_fps": 1,
                "region": None,
            },
            "next": ["report"],
        },
        {
            "id": "report",
            "type": "report",
            "params": {"format": "markdown"},
            "next": [],
        },
    ],
    "outputs": [{"type": "display", "requires_approval": False}],
}


def road_monitor_workflow() -> dict[str, Any]:
    """Return a fresh offline camera workflow template for one authorized source."""
    return deepcopy(_ROAD_MONITOR_WORKFLOW)



class RoadSessionError(RuntimeError):
    """A safe road-service error with a stable code and HTTP status."""

    def __init__(self, code: str, message: str, status: int = 409, *, retry_after: int | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.status = status
        self.retry_after = retry_after


@dataclass(frozen=True, slots=True)
class RoadHttpResponse:
    """A route result; HTTP session, CSRF, and serialization remain API-owned."""

    status: int
    payload: Any
    content_type: str = "application/json"
    headers: dict[str, str] | None = None


@dataclass(slots=True)
class _RunBinding:
    owner_id: str
    source_id: str
    run_id: str
    monitor_clip: bool
    on_stopped: Callable[[str, str], None] | None
    stop_event: threading.Event
    watcher: threading.Thread | None = None
    notified: bool = False
    event_time: float | None = None
    clip_ref: ClipRef | None = None


def _protected_source(operation):
    @wraps(operation)
    def call(self, owner_id, source_id, *args, **kwargs):
        self.get(owner_id, source_id)  # ownership is checked before leasing
        lease = self.retention.protect(source_id) if self.retention else nullcontext()
        with lease:
            return operation(self, owner_id, source_id, *args, **kwargs)
    return call


class RoadSessionService:
    """Owner-scoped façade over simulation, recording, and controller state.

    Public lookup helpers are intended for server-side integration. In
    particular, callers must authorize an HTTP session with get(owner, source)
    before a camera workflow is passed to Controller.start or resume.
    """

    def __init__(
        self,
        sessions: SimulationStore,
        recordings: RecordingStore,
        controller: Any,
        *,
        watcher_poll_seconds: float = 0.15,
        retention: Any = None,
    ) -> None:
        if watcher_poll_seconds <= 0 or not math.isfinite(watcher_poll_seconds):
            raise ValueError("watcher_poll_seconds must be a finite positive value")
        self.sessions = sessions
        self.recordings = recordings
        self.controller = controller
        self.retention = retention
        self._poll_seconds = float(watcher_poll_seconds)
        # The event excerpt needs ten real seconds of post-roll. Allow a small
        # margin for frame pacing jitter, but bound a completed run's watcher.
        self._clip_wait_seconds = 12.0
        self._lock = threading.RLock()
        self._closed = False
        self._source_owners: dict[str, str] = {}
        self._sessions_by_source: dict[str, SimulationSession] = {}
        self._run_sources: dict[str, tuple[str, str]] = {}
        self._bindings: dict[str, _RunBinding] = {}
        self._bindings_by_source: dict[tuple[str, str], list[str]] = {}
        self._active_by_source: dict[tuple[str, str], str] = {}
        # (owner, source) -> [run_id, analysing node, injected at, candidate seen at]
        self._last_events: dict[tuple[str, str], list[Any]] = {}

    def create(self, owner_id: str, seed: int = 42) -> dict[str, Any]:
        owner = self._identifier(owner_id, "owner_id")
        if type(seed) is not int:
            raise RoadSessionError("invalid_seed", "Der Startwert muss eine Ganzzahl sein.", 422)
        with self._lock:
            if self._closed:
                raise RoadSessionError("service_closed", "Die Kamerademo ist beendet.", 503)
            try:
                session = self._create_session(owner, seed)
            except SimulationCapacityError:
                raise RoadSessionError(
                    "demo_busy",
                    "Alle Demo-Kameras sind belegt. Jede Sitzung endet nach zwei Minuten; bitte gleich erneut versuchen.",
                    503,
                ) from None
        return {"source_id": session.source_id, "accepted": True}

    def _create_session(self, owner_id: str, seed: int) -> SimulationSession:
        source_ref: dict[str, str] = {}
        producer_lease = None

        def producer_closed() -> None:
            nonlocal producer_lease
            if producer_lease is None:
                return
            try:
                preview = session.final_preview_jpeg() or b""
                with self.retention.change_usage(source_ref["source_id"], "volatile", len(preview)):
                    pass
                self.recordings.producer_finished(owner_id, source_ref["source_id"])
            finally:
                producer_lease.__exit__(None, None, None)
                producer_lease = None

        def append_frame(frame: Any) -> None:
            source_id = source_ref.get("source_id")
            if source_id is None:
                raise RecordingError("source_not_open", "Die Aufnahmequelle wurde nicht geöffnet.")
            try:
                self.recordings.append(source_id, frame)
            except RecordingError as exc:
                # A full recording ends the camera like its time cap, not as a fault.
                if exc.code in {"byte_limit", "duration_limit"}:
                    raise ProducerLimitReached("recording_limit") from None
                raise

        session = self.sessions.create(owner_id, seed, on_frame=append_frame,
                                       **({"on_closed": producer_closed} if self.retention else {}))
        source_id = session.source_id
        source_ref["source_id"] = source_id
        if not source_id.startswith(ROAD_SOURCE_PREFIX):
            session.close()
            raise RoadSessionError("invalid_source", "Die Quelle besitzt keine gültige Demo-Kennung.", 500)
        try:
            if self.retention:
                # Bounded 960x540 producer images, world and consumer scratch,
                # separately charged from the full compressed recording reserve.
                volatile_bytes = 32 * 1024 * 1024
                self.retention.reserve_source(owner_id, source_id, self.recordings.max_bytes + volatile_bytes)
                producer_lease = self.retention.protect(source_id, activity=True)
                producer_lease.__enter__()
                self.retention.ensure_usage(source_id, "volatile", volatile_bytes)
            # The recorder is ready before the first producer callback can run.
            self.recordings.open(owner_id, source_id)
            self._source_owners[source_id] = owner_id
            self._sessions_by_source[source_id] = session
            session.start()
            session.latest_frame()
        except Exception:
            session.close()
            self._source_owners.pop(source_id, None)
            self._sessions_by_source.pop(source_id, None)
            if self.retention:
                try:
                    self.retention.discard(source_id)
                except KeyError:
                    self.sessions.forget(owner_id, source_id)
            raise
        return session

    def get(self, owner_id: str, source_id: str) -> SimulationSession:
        owner = self._identifier(owner_id, "owner_id")
        source = self._identifier(source_id, "source_id")
        with self._lock:
            if self._source_owners.get(source) != owner:
                raise KeyError(source)
            session = self._sessions_by_source.get(source)
        if session is None:
            raise KeyError(source)
        return session

    def source_owner(self, source_id: str) -> str | None:
        with self._lock:
            return self._source_owners.get(source_id)

    def open_reader(self, source_id: str):
        """Open a cursor for a registered source after its caller authorizes it."""
        if not isinstance(source_id, str) or not source_id.startswith(ROAD_SOURCE_PREFIX):
            raise KeyError(source_id)
        owner = self.source_owner(source_id)
        if owner is None:
            raise KeyError(source_id)
        return self.get(owner, source_id).open_reader()

    def _live_progress(self, run_id: str | None) -> tuple[str | None, float | None]:
        """Node and source time of the run's newest analysed frame."""
        if run_id is None:
            return None, None
        try:
            live = self.controller.live_analysis(run_id)
        except (UnknownRun, AttributeError):
            return None, None
        if not live:
            return None, None
        return live.get("node_id"), live.get("timestamp_seconds")

    def event_cooldown(self, owner_id: str, source_id: str) -> float | None:
        """Seconds until the previous event of this source has been analysed, else None."""
        key = (owner_id, source_id)
        with self._lock:
            record = self._last_events.get(key)
            active = self._active_by_source.get(key)
        if record is None:
            return None
        run_id, node_id, injected_at, candidate_at, event_source_time = record
        now = time.monotonic()
        if run_id is None or active != run_id or now - injected_at >= _EVENT_MAX_COOLDOWN_SECONDS:
            return None
        live_node, analysed_time = self._live_progress(run_id)
        if candidate_at is not None or (node_id is not None and live_node not in (None, node_id)):
            # The watching node stopped: a candidate is in its tail, clip and review.
            if candidate_at is None:
                with self._lock:
                    candidate_at = record[3] = now
            return max(1.0, candidate_at + _EVENT_REVIEW_SECONDS - now)
        if isinstance(analysed_time, (int, float)) and isinstance(event_source_time, (int, float)):
            remaining = event_source_time + _EVENT_SCENE_SECONDS - analysed_time
        else:
            remaining = _EVENT_SCENE_SECONDS - (now - injected_at)
        return remaining if remaining > 0 else None

    @_protected_source
    def inject(self, owner_id: str, source_id: str, event: str) -> dict[str, Any]:
        session = self.get(owner_id, source_id)
        if session.closed:
            raise RoadSessionError("source_closed", "Die Kameraquelle ist beendet.")
        if event not in {"wrong_way", "bird", "leaf"}:
            raise RoadSessionError("event_rejected", "Dieses Verkehrsszenario ist nicht verfügbar.", 422)
        remaining = self.event_cooldown(owner_id, source_id)
        if remaining is not None:
            raise RoadSessionError(
                "event_cooldown",
                "Das vorherige Ereignis wird noch analysiert. Bitte warte, bis es abgeschlossen ist.",
                409,
                retry_after=max(1, math.ceil(remaining)),
            )
        with self._lock:
            run_id = self._active_by_source.get((owner_id, source_id))
        node_id, _analysed = self._live_progress(run_id)
        try:
            event_source_time = float(session.latest_frame().timestamp_seconds)
            session.inject(event)
        except ValueError as exc:
            raise RoadSessionError("event_rejected", "Dieses Verkehrsszenario ist nicht verfügbar.", 422) from exc
        except RuntimeError as exc:
            raise RoadSessionError("source_closed", "Die Kameraquelle ist beendet.") from exc
        with self._lock:
            self._last_events[(owner_id, source_id)] = [run_id, node_id, time.monotonic(), None, event_source_time]
        # Acknowledgement confirms only scene injection, never a detector result.
        return {"source_id": source_id, "accepted": True}

    def _live_overlay(self, owner_id: str, source_id: str, session: SimulationSession):
        """Pair the bound run's newest direction result with that very analysed frame.

        Only while the run samples densely (watch phase, >= 1.2 analyses/s) and the
        analysed frame is at most 4 s behind the camera; otherwise there is no
        overlay. The measured lag behind the newest camera frame is reported.
        """
        with self._lock:
            run_id = self._active_by_source.get((owner_id, source_id))
        if run_id is None or session.closed:
            return None, None
        try:
            live = self.controller.live_analysis(run_id)
        except (UnknownRun, AttributeError):
            return None, None
        if not live or live.get("tool") != "direction" or live.get("region") is not None:
            return None, None
        metrics = live.get("metrics") or {}
        tracks = metrics.get("tracks")
        frame = live.get("frame")
        rate = live.get("analyses_per_second")
        try:
            newest = session.latest_frame()
        except RuntimeError:
            return None, None
        if (
            not isinstance(frame, Frame)
            or frame.frame_id != live.get("frame_id")
            or not isinstance(tracks, list)
            or not isinstance(rate, (int, float))
            or rate < _OVERLAY_MIN_RATE
            or newest.timestamp_seconds - frame.timestamp_seconds > _OVERLAY_MAX_LAG_SECONDS
        ):
            return None, None
        height, width = frame.image.shape[:2]
        return frame, {
            "frame_id": frame.frame_id,
            "run_id": run_id,
            "tool": "direction",
            "coordinate_space": [int(width), int(height)],
            "opposition_count": metrics.get("opposition_count"),
            "analyses_per_second": live.get("analyses_per_second"),
            "lag_seconds": round(max(0.0, newest.timestamp_seconds - frame.timestamp_seconds), 2),
            "tracks": [
                {
                    "region": list(track["region"]),
                    "dx_px": track["dx_px"],
                    "dy_px": track["dy_px"],
                    "opposition": track["opposition"],
                    "frames": len(track.get("frame_ids", [])),
                }
                for track in tracks
                if isinstance(track, dict)
            ],
        }

    @_protected_source
    def frame(self, owner_id: str, source_id: str, *, analysed: bool = False) -> dict[str, Any]:
        """Newest camera frame; with *analysed* the frame the bound run analysed last.

        The analysed variant carries that frame's own direction tracks, so an
        overlay can only ever show results of the image it is drawn on. Without
        a densely sampling run it falls back to the newest frame and analysis null.
        """
        session = self.get(owner_id, source_id)
        analysis = None
        analysis_state = "none"
        try:
            frame = None
            if analysed:
                frame, analysis = self._live_overlay(owner_id, source_id, session)
                with self._lock:
                    bound = (owner_id, source_id) in self._active_by_source
                analysis_state = "paired" if analysis else "active" if bound and not session.closed else "none"
            if frame is None:
                frame = session.latest_frame()
            ok, encoded = cv2.imencode(".jpg", frame.image, [cv2.IMWRITE_JPEG_QUALITY, 88])
            if not ok or encoded.size == 0:
                raise RoadSessionError("frame_unavailable", "Das aktuelle Kamerabild ist nicht verfügbar.")
            frame_id, timestamp = frame.frame_id, frame.timestamp_seconds
            jpeg = encoded.tobytes()
        except RuntimeError as exc:
            # Closed producers retain only one compressed preview and its identity.
            analysis = None
            jpeg = session.final_preview_jpeg()
            metadata = session.final_preview_metadata()
            if jpeg is None or metadata is None:
                raise RoadSessionError("frame_unavailable", "Das aktuelle Kamerabild ist nicht verfügbar.") from exc
            frame_id, timestamp = metadata
        except cv2.error as exc:
            raise RoadSessionError("frame_unavailable", "Das aktuelle Kamerabild ist nicht verfügbar.") from exc
        payload = {
            "source_id": source_id,
            "frame_id": frame_id,
            "timestamp_seconds": float(timestamp),
            "image_base64": base64.b64encode(jpeg).decode("ascii"),
            "analysis": analysis,
        }
        if analysed:
            # paired: image and tracks of one analysed frame; active: a run analyses
            # this source but no recent analysed frame fits; none: no analysis run.
            payload["analysis_state"] = analysis_state if analysis or analysis_state != "paired" else "none"
        if session.closed:
            payload["closed"] = True
        cooldown = None if session.closed else self.event_cooldown(owner_id, source_id)
        payload["next_event_in_seconds"] = None if cooldown is None else round(cooldown, 1)
        payload["end_reason"] = session.end_reason
        if session.error_code:
            payload["error_code"] = session.error_code
        return payload

    @_protected_source
    def recording(self, owner_id: str, source_id: str) -> dict[str, Any]:
        session = self.get(owner_id, source_id)
        try:
            manifest = self.recordings.manifest(owner_id, source_id)
            clips = [
                self._clip_metadata(self.recordings.clip(owner_id, str(item["clip_id"])))
                for item in manifest.get("clips", [])
                if isinstance(item, dict) and isinstance(item.get("clip_id"), str)
            ]
        except RecordingError as exc:
            raise self._recording_error(exc) from exc
        fields = (
            "status", "frame_count", "source_fps", "start_seconds", "end_seconds",
            "max_seconds", "max_bytes", "compressed_bytes", "export_bytes",
        )
        result = {key: manifest.get(key) for key in fields}
        result.update(
            source_id=source_id,
            closed=session.closed,
            end_reason=session.end_reason,
            error_code=session.error_code,
            clips=clips,
        )
        return result

    def clip(self, owner_id: str, clip_id: str) -> tuple[dict[str, Any], bytes]:
        owner = self._identifier(owner_id, "owner_id")
        try:
            ref = self.recordings.clip(owner, clip_id)
        except RecordingError as exc:
            raise self._recording_error(exc) from exc
        lease = self.retention.protect(ref.source_id) if self.retention else nullcontext()
        with lease:
            return self._clip_payload(ref)

    def _clip_payload(self, ref: ClipRef) -> tuple[dict[str, Any], bytes]:
        try:
            size = ref.path.stat().st_size
            if size <= 0 or size > self.recordings.max_bytes:
                raise RoadSessionError("clip_too_large", "Der Clip überschreitet die erlaubte Größe.", 413)
            payload = ref.path.read_bytes()
        except RecordingError as exc:
            raise self._recording_error(exc) from exc
        except OSError as exc:
            raise RoadSessionError("clip_unavailable", "Der Clip ist nicht verfügbar.", 404) from exc
        if hashlib.sha256(payload).hexdigest() != ref.sha256:
            raise RoadSessionError("clip_integrity", "Die Clip-Prüfsumme stimmt nicht.", 409)
        return self._clip_metadata(ref), payload

    @_protected_source
    def reset(self, owner_id: str, source_id: str) -> dict[str, Any]:
        owner = self._identifier(owner_id, "owner_id")
        old = self.get(owner, source_id)
        self._stop_bound_source(owner, source_id, "source_reset")
        old.close()
        try:
            self.recordings.finish(owner, source_id)
        except RecordingError as exc:
            if exc.code not in _EMPTY_RECORDING_CODES:
                raise self._recording_error(exc) from exc
        with self._lock:
            if self._closed:
                raise RoadSessionError("service_closed", "Die Kamerademo ist beendet.", 503)
            replacement = self._create_session(owner, 42)
        return {
            "source_id": replacement.source_id,
            "previous_source_id": source_id,
            "accepted": True,
        }

    @_protected_source
    def stop(self, owner_id: str, source_id: str) -> dict[str, Any]:
        owner = self._identifier(owner_id, "owner_id")
        session = self.get(owner, source_id)
        self._stop_bound_source(owner, source_id, "source_stopped")
        session.close()
        try:
            ref = self.recordings.finish(owner, source_id)
        except RecordingError as exc:
            if exc.code in _EMPTY_RECORDING_CODES:
                return {
                    "source_id": source_id,
                    "closed": True,
                    "error_code": "recording_empty",
                }
            raise self._recording_error(exc) from exc
        return {
            "source_id": source_id,
            "closed": True,
            "clip": self._clip_metadata(ref),
        }

    def bind_run(
        self,
        owner_id: str,
        source_id: str,
        run_id: str,
        *,
        monitor_clip: bool = True,
        on_stopped: Callable[[str, str], None] | None = None,
    ) -> None:
        owner = self._identifier(owner_id, "owner_id")
        source = self._identifier(source_id, "source_id")
        run = self._identifier(run_id, "run_id")
        if type(monitor_clip) is not bool:
            raise ValueError("monitor_clip must be a boolean")
        if on_stopped is not None and not callable(on_stopped):
            raise ValueError("on_stopped must be callable or None")
        self.get(owner, source)
        self.controller.status(run)
        key = (owner, source)
        with self._lock:
            if self._closed:
                raise RoadSessionError("service_closed", "Die Kamerademo ist beendet.", 503)
            if run in self._run_sources:
                if self._run_sources[run] == key:
                    return
                raise RoadSessionError("run_bound", "Dieser Lauf ist bereits einer anderen Quelle zugeordnet.")
            previous_run = self._active_by_source.get(key)
            if previous_run is not None:
                previous = self._bindings.get(previous_run)
                if previous is not None and not previous.stop_event.is_set():
                    raise RoadSessionError("source_busy", "Für diese Quelle läuft noch eine Analyse oder Clipprüfung.")
            binding = _RunBinding(
                owner_id=owner,
                source_id=source,
                run_id=run,
                monitor_clip=monitor_clip,
                on_stopped=on_stopped,
                stop_event=threading.Event(),
            )
            self._run_sources[run] = key
            self._bindings[run] = binding
            self._bindings_by_source.setdefault(key, []).append(run)
            self._active_by_source[key] = run
            watcher = threading.Thread(
                target=self._watch_run,
                args=(binding,),
                name=f"road-watch-{run[:8]}",
                daemon=True,
            )
            binding.watcher = watcher
            watcher.start()

    def rebind_run(self, owner_id: str, source_id: str, run_id: str) -> None:
        """Move a paused/resumed run to an explicitly authorized road source."""
        owner = self._identifier(owner_id, "owner_id")
        source = self._identifier(source_id, "source_id")
        run = self._identifier(run_id, "run_id")
        self.get(owner, source)
        self.controller.status(run)
        key = (owner, source)
        with self._lock:
            if self._closed:
                raise RoadSessionError("service_closed", "Die Kamerademo ist beendet.", 503)
            old = self._bindings.get(run)
            if old is None or old.owner_id != owner:
                raise KeyError(run)
            if old.source_id == source:
                return
            active_run = self._active_by_source.get(key)
            if active_run is not None and active_run != run:
                active = self._bindings.get(active_run)
                if active is not None and not active.stop_event.is_set():
                    raise RoadSessionError("source_busy", "Für diese Quelle läuft noch eine Analyse oder Clipprüfung.")
            old.stop_event.set()
            old.notified = True  # a source replacement is not a stopped run
            old_key = (old.owner_id, old.source_id)
            if self._active_by_source.get(old_key) == run:
                self._active_by_source.pop(old_key, None)
            binding = _RunBinding(
                owner_id=owner,
                source_id=source,
                run_id=run,
                monitor_clip=old.monitor_clip,
                on_stopped=old.on_stopped,
                stop_event=threading.Event(),
            )
            self._bindings[run] = binding
            self._run_sources[run] = key
            self._bindings_by_source.setdefault(key, []).append(run)
            self._active_by_source[key] = run
            watcher = threading.Thread(
                target=self._watch_run,
                args=(binding,),
                name=f"road-watch-{run[:8]}",
                daemon=True,
            )
            binding.watcher = watcher
            watcher.start()
        if old.watcher is not None and old.watcher is not threading.current_thread():
            old.watcher.join(timeout=1.0)

    def owner_source_for_run(self, run_id: str) -> tuple[str, str] | None:
        with self._lock:
            return self._run_sources.get(run_id)

    def run_for_source(self, owner_id: str, source_id: str) -> str | None:
        key = (owner_id, source_id)
        with self._lock:
            return self._active_by_source.get(key)

    def cancel_run(self, owner_id: str, run_id: str, *, reason: str = "run_cancelled") -> None:
        owner = self._identifier(owner_id, "owner_id")
        with self._lock:
            binding = self._bindings.get(run_id)
            known = self._run_sources.get(run_id)
            if binding is None or known is None:
                raise KeyError(run_id)
            if known[0] != owner:
                raise KeyError(run_id)
            binding.stop_event.set()
        try:
            status = self.controller.status(run_id).status
            if status not in _TERMINAL_RUN_STATES:
                self.controller.cancel(run_id)
        except UnknownRun:
            pass
        self._notify_stopped(binding, reason)
        self._retire_binding(binding)

    def _stop_bound_source(self, owner: str, source: str, reason: str) -> None:
        # Keep historical bindings after the watcher retires: review/countdown
        # work may outlive the Controller run and still needs a stop signal.
        with self._lock:
            run_ids = tuple(self._bindings_by_source.get((owner, source), ()))
        for run_id in run_ids:
            with self._lock:
                binding = self._bindings.get(run_id)
                still_bound = binding is not None and (binding.owner_id, binding.source_id) == (owner, source)
            if not still_bound:
                continue
            try:
                self.cancel_run(owner, run_id, reason=reason)
            except KeyError:
                continue

    def _watch_run(self, binding: _RunBinding) -> None:
        terminal_at: float | None = None
        while not binding.stop_event.is_set():
            try:
                result = self.controller.status(binding.run_id)
            except UnknownRun:
                self._notify_stopped(binding, "run_missing")
                self._retire_binding(binding)
                return

            if binding.monitor_clip and binding.event_time is None:
                candidate = self._candidate_time(binding, result)
                if candidate is not None:
                    binding.event_time = candidate

            clip_pending = False
            if binding.monitor_clip and binding.event_time is not None and binding.clip_ref is None:
                try:
                    binding.clip_ref = self.recordings.event_clip(
                        binding.owner_id, binding.source_id, binding.event_time
                    )
                except ClipNotReady:
                    clip_pending = True
                except RecordingError as exc:
                    if exc.code == "export_busy":
                        # Bounded export slots are transient; retry until the clip deadline.
                        clip_pending = True
                    else:
                        self._notify_stopped(binding, "recording_error")
                        self._retire_binding(binding)
                        return

            if result.status in _TERMINAL_RUN_STATES:
                if result.status == "cancelled":
                    self._notify_stopped(binding, "run_cancelled")
                    self._retire_binding(binding)
                    return
                if not binding.monitor_clip or binding.event_time is None or binding.clip_ref is not None:
                    self._retire_binding(binding)
                    return
                if terminal_at is None:
                    terminal_at = time.monotonic()
                if clip_pending:
                    source_end = self._source_end_reason(binding)
                    if source_end is not None:
                        self._notify_stopped(binding, source_end)
                        self._retire_binding(binding)
                        return
                    if time.monotonic() >= terminal_at + self._clip_wait_seconds:
                        self._notify_stopped(binding, "clip_timeout")
                        self._retire_binding(binding)
                        return
            binding.stop_event.wait(self._poll_seconds)
        self._retire_binding(binding)

    def _source_end_reason(self, binding: _RunBinding) -> str | None:
        try:
            session = self.get(binding.owner_id, binding.source_id)
        except KeyError:
            return "source_end"
        if session.error_code:
            return "source_error"
        if session.closed:
            return "source_end"
        return None

    def _candidate_time(self, binding: _RunBinding, result: Any) -> float | None:
        candidates: list[str] = []
        for finding in getattr(result, "findings", []):
            if getattr(finding, "tool", None) != "direction":
                continue
            metrics = getattr(finding, "metrics", None)
            if not isinstance(metrics, dict):
                continue
            count = metrics.get("opposition_count")
            if isinstance(count, bool) or not isinstance(count, (int, float)) or count <= 0:
                continue
            frame_ids = getattr(finding, "frame_ids", [])
            if isinstance(frame_ids, list):
                candidates.extend(item for item in frame_ids if isinstance(item, str))
        if not candidates:
            return None
        try:
            manifest = self.recordings.manifest(binding.owner_id, binding.source_id)
        except RecordingError:
            return None
        times = {
            item.get("frame_id"): item.get("timestamp_seconds")
            for item in manifest.get("frames", [])
            if isinstance(item, dict)
        }
        valid = [
            float(times[frame_id])
            for frame_id in candidates
            if frame_id in times
            and isinstance(times[frame_id], (int, float))
            and not isinstance(times[frame_id], bool)
            and math.isfinite(float(times[frame_id]))
        ]
        return min(valid) if valid else None

    def _notify_stopped(self, binding: _RunBinding, reason: str) -> None:
        callback: Callable[[str, str], None] | None = None
        with self._lock:
            if binding.notified:
                return
            binding.notified = True
            callback = binding.on_stopped
        if callback is not None:
            try:
                callback(binding.run_id, reason)
            except Exception:
                # Callers receive only status; callback failures cannot damage the run.
                return

    def _retire_binding(self, binding: _RunBinding) -> None:
        with self._lock:
            key = (binding.owner_id, binding.source_id)
            if self._active_by_source.get(key) == binding.run_id:
                self._active_by_source.pop(key, None)

    def forget_source(self, owner_id: str, source_id: str) -> None:
        with self._lock:
            session = self._sessions_by_source.get(source_id)
            if session is not None and (session.owner_id != owner_id or not session.closed):
                raise RuntimeError("source still active or foreign")
            bindings = [row for row in self._bindings.values()
                        if row.owner_id == owner_id and row.source_id == source_id]
        for binding in bindings:
            binding.stop_event.set()
            if binding.watcher is not None and binding.watcher is not threading.current_thread():
                binding.watcher.join(timeout=1)
                if binding.watcher.is_alive():
                    raise RuntimeError("source watcher still active")
        with self._lock:
            for binding in bindings:
                self._bindings.pop(binding.run_id, None)
                self._run_sources.pop(binding.run_id, None)
            self._active_by_source.pop((owner_id, source_id), None)
            self._bindings_by_source.pop((owner_id, source_id), None)
            self._last_events.pop((owner_id, source_id), None)
            self._sessions_by_source.pop(source_id, None)
            self._source_owners.pop(source_id, None)
            self.sessions.forget(owner_id, source_id)

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            bindings = list(self._bindings.values())
            sessions = list(self._sessions_by_source.values())
        # Notify archived as well as active bindings. A completed Controller
        # run may still own a pending review/countdown callback.
        for binding in bindings:
            try:
                self.cancel_run(binding.owner_id, binding.run_id, reason="service_closed")
            except KeyError:
                binding.stop_event.set()
                self._notify_stopped(binding, "service_closed")
        for session in sessions:
            session.close()
        self.sessions.close()
        if self.retention:
            self.retention.close()
        for binding in bindings:
            if binding.watcher is not None and binding.watcher is not threading.current_thread():
                binding.watcher.join(timeout=1.0)

    @staticmethod
    def _clip_metadata(ref: ClipRef) -> dict[str, Any]:
        return {
            "clip_id": ref.clip_id,
            "source_id": ref.source_id,
            "version": ref.version,
            "sha256": ref.sha256,
            "start_seconds": ref.start_seconds,
            "end_seconds": ref.end_seconds,
            "frame_ids": list(ref.frame_ids),
            "source_fps": ref.source_fps,
            "available_preroll": ref.available_preroll,
        }

    @staticmethod
    def _recording_error(exc: RecordingError) -> RoadSessionError:
        status = 403 if exc.code == "owner_mismatch" else 404 if exc.code in {"source_not_found", "clip_not_found"} else 409
        return RoadSessionError(exc.code, str(exc), status)

    @staticmethod
    def _identifier(value: str, label: str) -> str:
        if not isinstance(value, str) or not value or value != value.strip():
            raise RoadSessionError("invalid_identifier", f"{label} ist ungültig.", 422)
        return value


def make_source_override(service_lookup: Callable[[], RoadSessionService | None]):
    """Build a create_controller source override for registered road readers only."""

    def source_override(input_config: Any, _max_frames: int):
        if getattr(input_config, "kind", None) != "camera":
            return None
        source_id = getattr(input_config, "source", None)
        if not isinstance(source_id, str) or not source_id.startswith(ROAD_SOURCE_PREFIX):
            return None
        service = service_lookup()
        if service is None:
            return None
        return service.open_reader(source_id)

    return source_override



def _owned_source(service: RoadSessionService, owner_id: str, source_id: str) -> SimulationSession:
    try:
        return service.get(owner_id, source_id)
    except KeyError as exc:
        if service.source_owner(source_id) is None:
            raise RoadSessionError("road_source_not_found", "Die Kameraquelle wurde nicht gefunden.", 404) from exc
        raise RoadSessionError("owner_mismatch", "Diese Kameraquelle gehört zu einer anderen lokalen Sitzung.", 403) from exc


def dispatch_road_get(
    service: RoadSessionService,
    owner_id: str,
    path: str,
    *,
    cases: Any = None,
) -> RoadHttpResponse | None:
    """Dispatch a road GET route after the API has enforced its session cookie."""
    route = path.split("?", 1)[0]
    if route == "/road/workflow":
        return RoadHttpResponse(200, road_monitor_workflow())
    if route == "/road/capabilities":
        result = {
            "available": True,
            "encoder_available": True,
            "reason_code": None,
            "message": "Die lokale Straßenkamera-Aufzeichnung ist verfügbar.",
            "native_enabled": False,
            "modes": [],
            "fixture_profiles": [],
        }
        if cases is not None:
            result.update(cases.capabilities)
        return RoadHttpResponse(200, result)
    parts = route.strip("/").split("/")
    if route == "/road/cases":
        if cases is None:
            raise RoadSessionError("road_cases_unavailable", "Die Fallprüfung ist nicht verfügbar.", 404)
        return RoadHttpResponse(200, {"cases": cases.list_cases(owner_id)})
    if route == "/road/inbox":
        if cases is None:
            raise RoadSessionError("road_cases_unavailable", "Die Fallprüfung ist nicht verfügbar.", 404)
        inbox = getattr(getattr(cases, "decisions", None), "inbox", None)
        list_records = getattr(inbox, "list", None)
        if not callable(list_records):
            raise RoadSessionError("road_cases_unavailable", "Der Postfachempfänger ist nicht verfügbar.", 404)
        return RoadHttpResponse(200, list_records(owner_id))
    if len(parts) == 3 and parts[:2] == ["road", "cases"]:
        if cases is None:
            raise RoadSessionError("road_cases_unavailable", "Die Fallprüfung ist nicht verfügbar.", 404)
        return RoadHttpResponse(200, cases.snapshot(owner_id, parts[2]))
    if len(parts) == 4 and parts[:2] == ["road", "cases"] and parts[3] == "model":
        if cases is None:
            raise RoadSessionError("road_cases_unavailable", "Die Fallprüfung ist nicht verfügbar.", 404)
        snapshot = cases.snapshot(owner_id, parts[2])
        exchanges = cases.exchanges_for_case(owner_id, parts[2])
        for exchange in exchanges:
            exchange["mode"] = snapshot["mode"]
            exchange["fixture_profile"] = snapshot["fixture_profile"]
        return RoadHttpResponse(200, {"case_id": parts[2], "exchanges": exchanges})
    if len(parts) == 3 and parts[:2] == ["road", "clips"]:
        metadata, payload = service.clip(owner_id, parts[2])
        return RoadHttpResponse(
            200,
            payload,
            "video/mp4",
            {
                "Content-Disposition": 'inline; filename="road-clip.mp4"',
                "X-Clip-SHA256": metadata["sha256"],
            },
        )
    if len(parts) == 4 and parts[:2] == ["road", "sessions"]:
        source_id = parts[2]
        _owned_source(service, owner_id, source_id)
        if parts[3] == "frame":
            return RoadHttpResponse(200, service.frame(owner_id, source_id))
        if parts[3] == "analysis-frame":
            return RoadHttpResponse(200, service.frame(owner_id, source_id, analysed=True))
        if parts[3] == "recording":
            return RoadHttpResponse(200, service.recording(owner_id, source_id))
    return None


def dispatch_road_post(
    service: RoadSessionService,
    owner_id: str,
    path: str,
    body: Any,
    *,
    cases: Any = None,
) -> RoadHttpResponse | None:
    """Validate and dispatch a road POST after API origin/session/CSRF checks."""
    route = path.split("?", 1)[0]
    if route == "/road/sessions":
        if not isinstance(body, dict) or set(body) - {"seed"}:
            raise RoadSessionError("invalid_request", "Die Anfrage enthält ungültige Felder.", 400)
        seed = body.get("seed", 42)
        if type(seed) is not int:
            raise RoadSessionError("invalid_seed", "Der Startwert muss eine Ganzzahl sein.", 422)
        return RoadHttpResponse(201, service.create(owner_id, seed))
    parts = route.strip("/").split("/")
    if route == "/road/cases":
        if cases is None:
            raise RoadSessionError("road_cases_unavailable", "Die Fallprüfung ist nicht verfügbar.", 404)
        if not isinstance(body, dict) or set(body) - {"source_id", "mode", "fixture_profile"}:
            raise RoadSessionError("invalid_request", "Die Anfrage enthält ungültige Felder.", 400)
        source_id = body.get("source_id")
        mode = body.get("mode")
        if type(source_id) is not str or not source_id.strip() or type(mode) is not str:
            raise RoadSessionError("invalid_request", "Quelle und Modus müssen angegeben werden.", 422)
        profile_present = "fixture_profile" in body
        fixture_profile = body.get("fixture_profile")
        if mode == "gemini_video" and profile_present:
            raise RoadSessionError("fixture_profile_not_allowed", "Native Fälle verwenden kein Fixture-Profil.", 422)
        if mode == "fixture" and profile_present and type(fixture_profile) is not str:
            raise RoadSessionError("fixture_profile_not_allowed", "Das Fixture-Profil ist ungültig.", 422)
        _owned_source(service, owner_id, source_id)
        started = cases.start(owner_id, source_id, mode, fixture_profile=fixture_profile)
        return RoadHttpResponse(202, {"case": started})
    if len(parts) == 4 and parts[:2] == ["road", "cases"]:
        if cases is None:
            raise RoadSessionError("road_cases_unavailable", "Die Fallprüfung ist nicht verfügbar.", 404)
        case_id, action = parts[2], parts[3]
        # Authenticate the case before inspecting decision keys or cancellation
        # payloads so foreign sessions cannot probe the decision contract.
        cases.snapshot(owner_id, case_id)
        if action == "cancel":
            if not isinstance(body, dict) or body:
                raise RoadSessionError("invalid_request", "Für diese Aktion sind keine Zusatzfelder erlaubt.", 400)
            return RoadHttpResponse(200, cases.cancel(owner_id, case_id))
        if action == "decision":
            if not isinstance(body, dict) or set(body) != {
                "version", "content_hash", "decision", "idempotency_key"
            }:
                raise RoadSessionError("invalid_request", "Die Entscheidung enthält ungültige Felder.", 400)
            if type(body["version"]) is not int:
                raise RoadSessionError("invalid_version", "Die Fallversion muss eine Ganzzahl sein.", 422)
            if type(body["content_hash"]) is not str:
                raise RoadSessionError("invalid_content_hash", "Der Fallinhalt ist ungültig.", 422)
            if type(body["decision"]) is not str or body["decision"] not in {"confirm", "reject"}:
                raise RoadSessionError("invalid_decision", "Die Entscheidung ist ungültig.", 422)
            key = body["idempotency_key"]
            if (
                type(key) is not str
                or not key.strip()
                or len(key) > 512
                # Same rule as DecisionStore._identifier: no control characters.
                or any(ord(character) < 32 for character in key)
            ):
                raise RoadSessionError("invalid_idempotency_key", "Der Wiederholungsschlüssel ist ungültig.", 422)
            return RoadHttpResponse(200, cases.decide(
                owner_id,
                case_id,
                body["version"],
                body["content_hash"],
                body["decision"],
                body["idempotency_key"],
            ))
        return None
    if len(parts) != 4 or parts[:2] != ["road", "sessions"]:
        return None
    source_id, action = parts[2], parts[3]
    _owned_source(service, owner_id, source_id)
    if action == "events":
        if not isinstance(body, dict) or set(body) != {"event"} or not isinstance(body.get("event"), str):
            raise RoadSessionError("invalid_request", "Erwartet wird ein Szenarioereignis.", 400)
        return RoadHttpResponse(200, service.inject(owner_id, source_id, body["event"]))
    if action in {"reset", "stop"}:
        if not isinstance(body, dict) or body:
            raise RoadSessionError("invalid_request", "Für diese Aktion sind keine Zusatzfelder erlaubt.", 400)
        result = service.reset(owner_id, source_id) if action == "reset" else service.stop(owner_id, source_id)
        return RoadHttpResponse(200, result)
    return None


__all__ = [
    "ROAD_SOURCE_PREFIX",
    "RoadHttpResponse",
    "RoadSessionError",
    "RoadSessionService",
    "dispatch_road_get",
    "dispatch_road_post",
    "make_source_override",
    "road_monitor_workflow",
]
