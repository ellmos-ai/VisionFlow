"""Coordinate bounded road-review cases over recorded camera sources."""

from __future__ import annotations

import copy
import json
import math
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .config import load_workflow
from .contracts import Finding, ModelDecision, RunResult, WorkflowConfig
from .decision_policy import DecisionConflict, DecisionError, DecisionNotFound
from .gemini_video import GEMINI_MODEL_ID
from .model import ModelAdapter, ModelRunContext
from .model_exchange import ModelExchangeStore
from .recording import ClipNotReady, ClipRef, RecordingError, RecordingStore
from .runtime import create_default_fixture_adapter, create_registry
from .video_model import (
    VideoAssessment,
    VideoModelError,
    VideoModelFactory,
    VideoRequest,
    VideoTransportResult,
)


_FIXTURE_PROFILES = frozenset({"pending", "high", "bird", "leaf", "followup", "unclear", "error"})
_TERMINAL_PHASES = frozenset(
    {"normal", "source_ended", "dismissed", "reported", "no_response", "rejected", "cancelled"}
)
# A run can spend 75 seconds observing, 30 seconds waiting for each of two clip
# versions, and up to 30 seconds in each native call. 300 seconds also leaves
# bounded scheduling margin without confusing wall time with observation limits.
_CASE_WALL_DEADLINE_SECONDS = 300.0
_WORKFLOW_PATH = Path(__file__).resolve().parents[2] / "examples" / "workflows" / "road-review.json"


class RoadCaseError(RuntimeError):
    """A road-case failure with a stable public code and HTTP-like status."""

    def __init__(self, code: str, message: str | None = None, status: int = 409) -> None:
        self.code = code
        self.status = status
        super().__init__(message or code.replace("_", " "))


@dataclass(slots=True)
class _Case:
    owner_id: str
    case_id: str
    source_id: str
    mode: str
    fixture_profile: str | None
    marker: str
    config: WorkflowConfig
    created_at: float
    run_id: str | None = None
    bound: bool = False
    binding_lock: threading.Lock = field(default_factory=threading.Lock)
    finalize_lock: Any = field(default_factory=threading.RLock)
    cancel_event: threading.Event = field(default_factory=threading.Event)
    phase: str = "starting"
    model_error: str | None = None
    event_time: float | None = None
    latest_clip: ClipRef | None = None
    exchange_ids: list[str] = field(default_factory=list)
    request_count: int = 0
    decision_open: bool = False
    decision_snapshot: dict[str, Any] | None = None
    controller_result: RunResult | None = None
    monitor: threading.Thread | None = None
    finalized: bool = False
    analysis_complete: bool = False
    stopped_reason: str | None = None


class RoadModelRouter:
    """Route only privately marked road-case runs to whole-video assessment."""

    def __init__(self, *, native_transport: Any = None) -> None:
        if native_transport is not None and not callable(getattr(native_transport, "generate", None)):
            raise TypeError("native_transport must implement generate")
        self._native_transport = native_transport
        self._lock = threading.RLock()
        self._service: RoadCaseService | None = None

    @property
    def capabilities(self) -> dict[str, Any]:
        modes = ["fixture"]
        if self._native_transport is not None:
            modes.append("gemini_video")
        return {
            "modes": modes,
            "fixture_profiles": sorted(_FIXTURE_PROFILES),
            "native_enabled": self._native_transport is not None,
        }

    def bind(self, service: RoadCaseService) -> None:
        if not isinstance(service, RoadCaseService):
            raise TypeError("service must be a RoadCaseService")
        with self._lock:
            if self._service is not None and self._service is not service:
                raise RoadCaseError("router_already_bound")
            self._service = service

    def create_adapter(self, config: WorkflowConfig, context: ModelRunContext) -> ModelAdapter:
        if not isinstance(config, WorkflowConfig):
            raise TypeError("config must be a WorkflowConfig")
        if not isinstance(context, ModelRunContext):
            raise TypeError("context must be a ModelRunContext")
        with self._lock:
            service = self._service
        case = service._case_for_factory(config, context) if service is not None else None
        if case is None:
            return create_default_fixture_adapter(config)

        service._ensure_bound(case, context.run_id)
        if context.cancelled() or case.cancel_event.is_set():
            raise VideoModelError("cancelled")
        if case.mode == "fixture":
            transport: Any = _FixtureProfileTransport(case)
        else:
            if self._native_transport is None:
                raise VideoModelError("native_disabled")
            transport = self._native_transport
        transport = _RoadAuthorityTransport(transport, case.source_id)
        factory = VideoModelFactory(
            request_factory=service._request_factory(case),
            transport=transport,
            exchanges=service.exchanges,
            owner_for_run=service._owner_for_run,
        )
        return factory.create_adapter(config, context)


class _FixtureProfileTransport:
    """Deterministic offline responses selected explicitly before a run."""

    def __init__(self, case: _Case) -> None:
        self._case = case

    def generate(self, request: VideoRequest, on_delta: Any, cancelled: Any) -> VideoTransportResult:
        if cancelled() or self._case.cancel_event.is_set():
            raise VideoModelError("cancelled")
        profile = self._case.fixture_profile or "pending"
        if profile == "error":
            raise VideoModelError("fixture_error")

        second_followup = profile == "followup" and self._case.request_count >= 2
        if profile in {"bird", "leaf"}:
            object_class, hazard, confidence = profile, False, 0.99
            summary = (
                f"Offline fixture profile '{profile}' explicitly selected; "
                "this response is independent of scenario metadata."
            )
            action = {"action": "report", "payload": {"summary": summary}}
        elif profile == "unclear":
            object_class, hazard, confidence = "unknown", None, None
            summary = "The offline fixture profile explicitly selects an unclear result."
            action = {"action": "report", "payload": {"summary": summary}}
        elif profile == "followup" and not second_followup:
            object_class, hazard, confidence = "vehicle", True, 0.8
            summary = "The offline fixture requests one same-source follow-up observation."
            action = {
                "action": "observe",
                "payload": {
                    "source_id": request.clip.source_id,
                    "region": None,
                    "duration_seconds": 5.0,
                    "sample_fps": 5.0,
                    "question": "Observe the same road region for five more seconds.",
                },
            }
        else:
            object_class, hazard, confidence = "vehicle", True, (0.99 if profile == "high" else 0.8)
            summary = (
                "The offline fixture explicitly selects a high-confidence result."
                if profile == "high"
                else "The offline fixture explicitly selects a pending human-review result."
            )
            action = {"action": "report", "payload": {"summary": summary}}

        raw = json.dumps(
            {
                "object_class": object_class,
                "hazard": hazard,
                "confidence": confidence,
                "summary": summary,
                "evidence_frame_ids": [request.clip.frame_ids[-1]],
                "decision": action,
            },
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        )
        if raw:
            on_delta(raw)
        if cancelled() or self._case.cancel_event.is_set():
            raise VideoModelError("cancelled")
        return VideoTransportResult(raw_text=raw, usage=None, request_id="offline-fixture")


class _RoadAuthorityTransport:
    """Constrain follow-up observations to the case's exact full-frame ROI."""

    def __init__(self, transport: Any, source_id: str) -> None:
        self._transport = transport
        self._source_id = source_id

    def generate(self, request: VideoRequest, on_delta: Any, cancelled: Any) -> VideoTransportResult:
        result = self._transport.generate(request, on_delta, cancelled)
        if not isinstance(result, VideoTransportResult):
            return result
        try:
            raw = json.loads(result.raw_text)
            decision = raw["decision"]
            if decision["action"] == "observe":
                payload = decision["payload"]
                if (
                    payload.get("source_id") != self._source_id
                    or payload.get("region") is not None
                    or type(payload.get("duration_seconds")) not in (int, float)
                    or isinstance(payload.get("duration_seconds"), bool)
                    or float(payload["duration_seconds"]) != 5.0
                    or type(payload.get("sample_fps")) not in (int, float)
                    or isinstance(payload.get("sample_fps"), bool)
                    or float(payload["sample_fps"]) != 5.0
                ):
                    raise VideoModelError("observation_not_authorized")
        except VideoModelError:
            raise
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            # The whole-video factory will report the stable invalid-response code.
            return result
        return result


class RoadCaseService:
    """Coordinate source-owned runs, actual recording clips, exchanges, and review."""

    def __init__(
        self,
        sessions: Any,
        controller: Any,
        recordings: RecordingStore,
        exchanges: ModelExchangeStore,
        decisions: Any,
    ) -> None:
        if not callable(getattr(sessions, "get", None)) or not callable(
            getattr(sessions, "bind_run", None)
        ):
            raise TypeError("sessions must implement the public RoadSessionService contract")
        if not callable(getattr(controller, "start", None)) or not callable(
            getattr(controller, "wait", None)
        ):
            raise TypeError("controller must implement start() and wait()")
        if not callable(getattr(recordings, "manifest", None)) or not callable(
            getattr(recordings, "event_clip", None)
        ):
            raise TypeError("recordings must implement manifest() and event_clip()")
        if not callable(getattr(exchanges, "record_request", None)) or not callable(
            getattr(exchanges, "snapshot", None)
        ):
            raise TypeError("exchanges must implement ModelExchangeStore")
        router = getattr(controller, "model_adapter", None)
        if not isinstance(router, RoadModelRouter):
            raise RoadCaseError("road_router_required", "Road cases need an explicit RoadModelRouter.")
        self.sessions = sessions
        self.controller = controller
        self.recordings = recordings
        self.exchanges = exchanges
        self.decisions = decisions
        self.router = router
        self._lock = threading.RLock()
        self._closed = False
        self._cases: dict[tuple[str, str], _Case] = {}
        self._by_marker: dict[str, _Case] = {}
        self._active_by_source: dict[tuple[str, str], str] = {}
        self._ticker_stop = threading.Event()
        self._ticker = threading.Thread(
            target=self._ticker_loop,
            name="road-case-decision-ticker",
            daemon=True,
        )
        self.router.bind(self)
        self._ticker.start()

    @property
    def capabilities(self) -> dict[str, Any]:
        return self.router.capabilities

    def start(
        self,
        owner_id: str,
        source_id: str,
        mode: str,
        *,
        fixture_profile: str | None = None,
    ) -> dict[str, Any]:
        owner = _identifier(owner_id, "owner_id")
        source = _identifier(source_id, "source_id")
        if mode not in {"fixture", "gemini_video"}:
            raise RoadCaseError("mode_not_supported", status=422)
        if mode == "fixture":
            profile = "pending" if fixture_profile is None else fixture_profile
            if type(profile) is not str or profile not in _FIXTURE_PROFILES:
                raise RoadCaseError("fixture_profile_not_allowed", status=422)
        else:
            if fixture_profile is not None:
                raise RoadCaseError("fixture_profile_not_allowed", status=422)
            if self.router._native_transport is None:
                raise RoadCaseError("native_disabled", status=503)
            profile = None

        try:
            session = self.sessions.get(owner, source)
        except (KeyError, ValueError) as exc:
            raise RoadCaseError("source_not_found", status=404) from exc
        if bool(getattr(session, "closed", False)) or getattr(session, "error_code", None):
            raise RoadCaseError("source_ended", status=409)
        try:
            manifest = self.recordings.manifest(owner, source)
        except Exception as exc:
            raise RoadCaseError("recording_unavailable", status=409) from exc
        if manifest.get("source_id") != source:
            raise RoadCaseError("source_mismatch", status=409)

        config = load_workflow(_WORKFLOW_PATH, create_registry())
        config.input.source = source
        marker = "road-review-" + uuid.uuid4().hex
        config.name = marker
        case = _Case(
            owner_id=owner,
            case_id=uuid.uuid4().hex,
            source_id=source,
            mode=mode,
            fixture_profile=profile,
            marker=marker,
            config=config,
            created_at=time.monotonic(),
        )
        key = (owner, case.case_id)
        source_key = (owner, source)
        with self._lock:
            if self._closed:
                raise RoadCaseError("service_closed", status=503)
            if source_key in self._active_by_source:
                raise RoadCaseError("source_in_use", "Für diese Quelle läuft bereits ein Fall.")
            # Reservation precedes controller.start so the RunModelFactory can bind
            # safely even if the background run reaches ask_model before start returns.
            self._cases[key] = case
            self._by_marker[marker] = case
            self._active_by_source[source_key] = case.case_id

        started = False
        try:
            result = self.controller.start(config)
            run_id = getattr(result, "run_id", None)
            if type(run_id) is not str or not run_id:
                raise RoadCaseError("run_start_failed", status=500)
            started = True
            self._ensure_bound(case, run_id)
            case.phase = "running"
            case.monitor = threading.Thread(
                target=self._watch_case,
                args=(case,),
                name=f"road-case-{case.case_id[:8]}",
                daemon=True,
            )
            case.monitor.start()
            return self.snapshot(owner, case.case_id)
        except Exception as exc:
            with self._lock:
                self._cases.pop(key, None)
                self._by_marker.pop(marker, None)
                if self._active_by_source.get(source_key) == case.case_id:
                    self._active_by_source.pop(source_key, None)
            if started and case.run_id:
                try:
                    self.controller.cancel(case.run_id)
                except Exception:
                    pass
            if isinstance(exc, RoadCaseError):
                raise
            raise RoadCaseError("run_start_failed", status=500) from None

    def snapshot(self, owner_id: str, case_id: str) -> dict[str, Any]:
        case = self._owned_case(owner_id, case_id)
        result: RunResult | None = None
        if case.run_id is not None:
            try:
                result = self.controller.status(case.run_id)
            except Exception:
                result = None
        if case.decision_open:
            try:
                decision = self.decisions.snapshot(case.owner_id, case.case_id)
            except Exception:
                decision = case.decision_snapshot
            if decision is not None:
                with case.finalize_lock:
                    if not case.cancel_event.is_set():
                        case.decision_snapshot = copy.deepcopy(decision)
                        case.phase = decision["phase"]
        with case.finalize_lock:
            clip = case.latest_clip
            public_clip = _clip_data(clip) if clip is not None else None
            decision_data = copy.deepcopy(case.decision_snapshot) if case.decision_open else None
            phase = case.phase
            model_error = case.model_error
            stopped_reason = case.stopped_reason
            exchange_ids = list(case.exchange_ids)
        return {
            "case_id": case.case_id,
            "run_id": case.run_id,
            "source_id": case.source_id,
            "mode": case.mode,
            "fixture_profile": case.fixture_profile,
            "phase": phase,
            "controller_status": getattr(result, "status", "unknown"),
            "perception_trace": copy.deepcopy(getattr(result, "trace", [])) if result else [],
            "exchange_ids": exchange_ids,
            "clip": public_clip,
            "decision": decision_data,
            "version": decision_data.get("version") if decision_data else None,
            "content_hash": decision_data.get("content_hash") if decision_data else None,
            "assessment": copy.deepcopy(decision_data.get("assessment")) if decision_data else None,
            "deadline_at": decision_data.get("deadline_at") if decision_data else None,
            "remaining_seconds": decision_data.get("remaining_seconds", 0.0) if decision_data else 0.0,
            "journal": copy.deepcopy(decision_data.get("journal", [])) if decision_data else [],
            "first_receipt": copy.deepcopy(decision_data.get("first_receipt")) if decision_data else None,
            "correction_receipt": copy.deepcopy(decision_data.get("correction_receipt")) if decision_data else None,
            "model_error": decision_data.get("model_error") if decision_data else model_error,
            "stopped_reason": stopped_reason,
        }

    def list_cases(self, owner_id: str, source_id: str) -> list[dict[str, Any]]:
        """Return this owner's cases for one source in creation order."""
        if type(owner_id) is not str or type(source_id) is not str:
            raise RoadCaseError("case_not_found", status=404)
        with self._lock:
            cases = sorted(
                (
                    case
                    for (owner, _), case in self._cases.items()
                    if owner == owner_id and case.source_id == source_id
                ),
                key=lambda case: (case.created_at, case.case_id),
            )
            case_ids = [case.case_id for case in cases]
        return [self.snapshot(owner_id, case_id) for case_id in case_ids]

    def analysis_status(self, owner_id: str, case_id: str) -> dict[str, Any]:
        """Expose a lightweight finalization gate without copying run traces."""
        case = self._owned_case(owner_id, case_id)
        with case.finalize_lock:
            result = case.controller_result
            return {
                "analysis_complete": case.analysis_complete,
                "phase": case.phase,
                "controller_status": getattr(result, "status", None),
                "model_error": case.model_error,
            }

    def exchanges_for_case(self, owner_id: str, case_id: str) -> list[dict[str, Any]]:
        case = self._owned_case(owner_id, case_id)
        rows: list[dict[str, Any]] = []
        for exchange_id in tuple(case.exchange_ids):
            try:
                rows.append(self.exchanges.snapshot(case.owner_id, exchange_id))
            except KeyError:
                # The request factory publishes its ID immediately before the
                # exchange store records the request; retrying the snapshot is safe.
                continue
        return rows

    def decide(
        self,
        owner_id: str,
        case_id: str,
        version: int,
        content_hash: str,
        decision: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        case = self._owned_case(owner_id, case_id)
        if not case.decision_open:
            raise RoadCaseError("case_not_found", status=404)
        try:
            result = self.decisions.resolve(
                owner_id,
                case_id,
                version,
                content_hash,
                decision,
                idempotency_key,
            )
        except (DecisionConflict, DecisionNotFound, DecisionError) as exc:
            raise RoadCaseError(exc.code, str(exc)) from exc
        with case.finalize_lock:
            case.decision_snapshot = copy.deepcopy(result)
            case.phase = result["phase"]
            self._release_if_terminal(case)
        return result

    def cancel(self, owner_id: str, case_id: str) -> dict[str, Any]:
        case = self._owned_case(owner_id, case_id)
        with case.finalize_lock:
            case.cancel_event.set()
        if case.run_id is not None:
            try:
                self.controller.cancel(case.run_id)
            except Exception:
                pass
            if case.bound:
                try:
                    self.sessions.cancel_run(owner_id, case.run_id, reason="case_cancelled")
                except Exception:
                    pass
        self._on_source_stopped(case, case.run_id or "", "case_cancelled")
        return self.snapshot(owner_id, case_id)

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            cases = list(self._cases.values())
        self._ticker_stop.set()
        for case in cases:
            if case.phase not in _TERMINAL_PHASES:
                self.cancel(case.owner_id, case.case_id)
            else:
                case.cancel_event.set()
        if self._ticker is not threading.current_thread():
            self._ticker.join(timeout=1.0)
        for case in cases:
            if case.monitor is not None and case.monitor is not threading.current_thread():
                case.monitor.join(timeout=1.0)

    def _case_for_factory(
        self,
        config: WorkflowConfig,
        context: ModelRunContext,
    ) -> _Case | None:
        with self._lock:
            case = self._by_marker.get(config.name)
            if case is None:
                return None
            if (
                config.input.kind != "camera"
                or config.input.source != case.source_id
                or context.source_id != case.source_id
                or case.config.name != config.name
                or case.config.input.source != config.input.source
                or (case.run_id is not None and case.run_id != context.run_id)
            ):
                return None
            return case

    def _ensure_bound(self, case: _Case, run_id: str) -> None:
        with case.binding_lock:
            if case.run_id is not None and case.run_id != run_id:
                raise RoadCaseError("run_mismatch")
            case.run_id = run_id
            if case.bound:
                return
            try:
                self.sessions.bind_run(
                    case.owner_id,
                    case.source_id,
                    run_id,
                    monitor_clip=False,
                    on_stopped=lambda stopped_run, reason: self._on_source_stopped(
                        case, stopped_run, reason
                    ),
                )
            except Exception as exc:
                raise RoadCaseError("source_binding_failed") from exc
            case.bound = True

    def _owner_for_run(self, run_id: str) -> str | None:
        known = self.sessions.owner_source_for_run(run_id)
        with self._lock:
            case = next(
                (
                    item
                    for item in self._cases.values()
                    if item.run_id == run_id
                    and known == (item.owner_id, item.source_id)
                ),
                None,
            )
        return case.owner_id if case is not None else None

    def _request_factory(self, case: _Case):
        def build(context: ModelRunContext, question: str, findings: list[Finding]) -> VideoRequest:
            if context.run_id != case.run_id or context.source_id != case.source_id:
                raise VideoModelError("run_mismatch")
            if context.cancelled() or case.cancel_event.is_set():
                raise VideoModelError("cancelled")
            try:
                return self._build_request(case, context, question, findings)
            except VideoModelError as exc:
                case.model_error = exc.code
                raise
            except RoadCaseError as exc:
                case.model_error = exc.code
                raise VideoModelError(exc.code) from None
            except Exception:
                case.model_error = "recording_failed"
                raise VideoModelError("recording_failed") from None

        return build

    def _build_request(
        self,
        case: _Case,
        context: ModelRunContext,
        question: str,
        findings: list[Finding],
    ) -> VideoRequest:
        if not findings:
            raise VideoModelError("candidate_not_found")
        manifest = self.recordings.manifest(case.owner_id, case.source_id)
        if manifest.get("source_id") != case.source_id:
            raise VideoModelError("source_mismatch")
        frames = manifest.get("frames")
        if not isinstance(frames, list):
            raise VideoModelError("recording_unavailable")
        times: dict[str, float] = {}
        for frame in frames:
            if not isinstance(frame, dict):
                continue
            frame_id = frame.get("frame_id")
            timestamp = frame.get("timestamp_seconds")
            if (
                type(frame_id) is str
                and not isinstance(timestamp, bool)
                and type(timestamp) in (int, float)
                and math.isfinite(float(timestamp))
            ):
                times[frame_id] = float(timestamp)
        candidate = _first_stable_candidate(findings, times)
        if candidate is None:
            raise VideoModelError("candidate_not_found")
        finding, candidate_frame_ids = candidate
        if case.event_time is None:
            current_times = [times[item] for item in finding.frame_ids if item in times]
            if not current_times:
                raise VideoModelError("candidate_time_missing")
            case.event_time = min(current_times)
        if case.request_count >= 2:
            raise VideoModelError("request_budget_exceeded")

        clip = self._obtain_clip(case, context)
        clip_frames = set(clip.frame_ids)
        evidence_ids = [item for item in candidate_frame_ids if item in clip_frames]
        if not evidence_ids:
            # The track may extend beyond the evaluated-frame list; those frame
            # IDs still came from this actual direction finding and manifest.
            evidence_ids = [item for item in finding.frame_ids if item in clip_frames]
        if not evidence_ids:
            raise VideoModelError("candidate_not_in_clip")
        reference = finding.evidence_ref
        if type(reference) is not str or not reference.startswith(context.run_id + "/"):
            raise VideoModelError("evidence_unavailable")

        exchange_id = uuid.uuid4().hex
        case.request_count += 1
        case.latest_clip = clip
        case.exchange_ids.append(exchange_id)
        application_prompt = self._application_prompt(case, clip, finding, evidence_ids, times)
        return VideoRequest(
            exchange_id=exchange_id,
            case_id=case.case_id,
            run_id=context.run_id,
            clip=clip,
            prompt=application_prompt,
            evidence_refs=(reference,),
            model_id=(
                "offline-fixture/" + (case.fixture_profile or "pending")
                if case.mode == "fixture"
                else GEMINI_MODEL_ID
            ),
            processing_fps=5.0,
        )

    def _obtain_clip(self, case: _Case, context: ModelRunContext) -> ClipRef:
        event_time = case.event_time
        if event_time is None:
            raise VideoModelError("candidate_time_missing")
        deadline = time.monotonic() + 30.0
        while True:
            if context.cancelled() or case.cancel_event.is_set():
                raise VideoModelError("cancelled")
            try:
                if case.latest_clip is None:
                    return self.recordings.event_clip(
                        case.owner_id,
                        case.source_id,
                        event_time,
                        before=5.0,
                        after=10.0,
                    )
                manifest = self.recordings.manifest(case.owner_id, case.source_id)
                end = manifest.get("end_seconds")
                if (
                    isinstance(end, bool)
                    or type(end) not in (int, float)
                    or not math.isfinite(float(end))
                    or float(end) < case.latest_clip.end_seconds + 5.0
                ):
                    if self._source_has_ended(case):
                        raise VideoModelError("source_ended")
                    if time.monotonic() >= deadline:
                        raise VideoModelError("clip_timeout")
                    time.sleep(0.05)
                    continue
                return self.recordings.event_clip(
                    case.owner_id,
                    case.source_id,
                    event_time,
                    before=5.0,
                    after=0.0,
                    end_override=float(end),
                )
            except ClipNotReady:
                if self._source_has_ended(case):
                    raise VideoModelError("source_ended") from None
                if time.monotonic() >= deadline:
                    raise VideoModelError("clip_timeout") from None
                time.sleep(0.05)
            except RecordingError as exc:
                raise VideoModelError(exc.code if _safe_code(exc.code) else "recording_failed") from None

    def _source_has_ended(self, case: _Case) -> bool:
        try:
            session = self.sessions.get(case.owner_id, case.source_id)
        except Exception:
            return True
        return bool(getattr(session, "closed", False) or getattr(session, "error_code", None))

    def _application_prompt(
        self,
        case: _Case,
        clip: ClipRef,
        finding: Finding,
        evidence_ids: list[str],
        times: dict[str, float],
    ) -> str:
        lanes: list[str] = []
        for node in case.config.nodes:
            if node.type != "analyze" or node.params.get("tool") != "direction":
                continue
            for lane in node.params.get("tool_params", {}).get("lanes", []):
                if isinstance(lane, dict):
                    lanes.append(
                        f"lane {lane.get('id')}: ROI {lane.get('region')}, "
                        f"expected vector {lane.get('expected')}"
                    )
        manifest_lines = [
            f"- {frame_id}: manifest source time {times[frame_id]:.3f} seconds"
            for frame_id in evidence_ids
            if frame_id in times
        ]
        available_preroll = min(5.0, max(0.0, clip.available_preroll))
        lines = [
            f"Road case {case.case_id} uses source {case.source_id}.",
            "Assess only the supplied recorded MP4 and its actual trigger images; "
            "do not use simulator flags, seeds, injected event names, or hidden metadata.",
            "The source-pixel observation ROI is null (the full source frame). "
            "The follow-up must keep that exact source and ROI and must be exactly "
            "5 seconds at 5 samples per second.",
            "Authorized lane configuration in source pixels:",
            *(lanes or ["No configured lane vectors were available."]),
            f"Available preroll: {available_preroll:.3f} seconds.",
            "Trigger frame timestamps from the recording manifest:",
            *(manifest_lines or ["No trigger-frame manifest timestamps were available."]),
            f"Current immutable clip: {clip.clip_id} version {clip.version}, "
            f"source time {clip.start_seconds:.3f}–{clip.end_seconds:.3f} seconds.",
            "The full observe payload must contain source_id, region, duration_seconds, "
            "sample_fps, and question. It may only observe this same source with "
            "region null, duration_seconds 5, and sample_fps 5.",
            "Direction-finding frame IDs: " + ", ".join(finding.frame_ids),
        ]
        return "\n".join(lines)

    def _watch_case(self, case: _Case) -> None:
        run_id = case.run_id
        if run_id is None:
            return
        deadline = case.created_at + _CASE_WALL_DEADLINE_SECONDS
        while not case.cancel_event.is_set():
            if time.monotonic() >= deadline:
                case.model_error = "case_timeout"
                try:
                    self.controller.cancel(run_id)
                except Exception:
                    pass
                self._on_source_stopped(case, run_id, "case_timeout")
                return
            try:
                result = self.controller.wait(run_id, timeout=0.15)
            except Exception:
                case.model_error = "run_unavailable"
                self._on_source_stopped(case, run_id, "run_unavailable")
                return
            status = getattr(result, "status", None)
            if status == "cancelled":
                self._on_source_stopped(case, run_id, "run_cancelled")
                return
            if status in {"completed", "failed"}:
                case.controller_result = result
                self._finalize(case, result)
                return
        if case.cancel_event.is_set() and not case.finalized:
            self._on_source_stopped(case, run_id, case.stopped_reason or "case_cancelled")

    def _finalize(self, case: _Case, result: RunResult) -> None:
        with case.finalize_lock:
            if case.finalized or case.cancel_event.is_set():
                return
            case.finalized = True
        findings = getattr(result, "findings", [])
        manifest = None
        try:
            manifest = self.recordings.manifest(case.owner_id, case.source_id)
        except Exception:
            pass
        times = _manifest_times(manifest)
        candidate = _first_stable_candidate(findings, times)
        if candidate is None:
            trace = getattr(result, "trace", [])
            stop = next(
                (
                    item
                    for item in reversed(trace)
                    if isinstance(item, dict) and item.get("type") == "monitor_stopped"
                ),
                None,
            )
            full_duration = (
                isinstance(stop, dict)
                and stop.get("reason") == "duration"
                and type(stop.get("duration_seconds")) in (int, float)
                and float(stop["duration_seconds"]) >= 60.0
            )
            with case.finalize_lock:
                if case.cancel_event.is_set():
                    return
                case.phase = "normal" if full_duration and result.status == "completed" else "source_ended"
                case.stopped_reason = str(stop.get("reason")) if isinstance(stop, dict) else result.status
                case.analysis_complete = True
                self._release_if_terminal(case)
            return

        with case.finalize_lock:
            if case.cancel_event.is_set():
                return
            case.phase = "model_review"
        exchange_rows = self.exchanges_for_case(case.owner_id, case.case_id)
        latest = exchange_rows[-1] if exchange_rows else None
        assessment = _assessment_from_exchange(latest) if latest is not None else None
        error_code = case.model_error
        if latest is not None and latest.get("status") == "failed":
            error_code = latest.get("error_code") or "model_error"
        if latest is None:
            error_code = error_code or "model_unavailable"
        elif latest.get("status") != "complete":
            error_code = error_code or "model_incomplete"
        elif assessment is not None and assessment.decision.action != "report":
            error_code = "followup_limit"
            assessment = None
        with case.finalize_lock:
            if case.cancel_event.is_set():
                return
            try:
                opened = self.decisions.open_case(
                    case.owner_id,
                    case.case_id,
                    case.run_id or "unknown",
                    case.latest_clip,
                    assessment if error_code is None else None,
                    True,
                    error_code,
                    source_id=case.source_id,
                )
            except DecisionError:
                case.phase = "human_review"
                case.model_error = error_code or "decision_unavailable"
                case.analysis_complete = True
                self._release_if_terminal(case)
                return
            case.decision_open = True
            case.decision_snapshot = copy.deepcopy(opened)
            case.phase = opened["phase"]
            case.model_error = error_code
            case.analysis_complete = True
            self._release_if_terminal(case)

    def _on_source_stopped(self, case: _Case, run_id: str, reason: str) -> None:
        if run_id and case.run_id is not None and run_id != case.run_id:
            return
        with case.finalize_lock:
            case.stopped_reason = reason
            case.cancel_event.set()
            case.analysis_complete = True
            if case.decision_open:
                try:
                    case.decision_snapshot = self.decisions.cancel(case.owner_id, case.case_id)
                    case.phase = case.decision_snapshot["phase"]
                except Exception:
                    pass
            elif case.phase not in _TERMINAL_PHASES:
                case.phase = "cancelled"
            self._release_if_terminal(case)

    def _ticker_loop(self) -> None:
        while not self._ticker_stop.wait(0.05):
            try:
                self.decisions.tick()
            except Exception:
                continue
            with self._lock:
                cases = tuple(self._cases.values())
            for case in cases:
                if not case.decision_open:
                    continue
                try:
                    decision = self.decisions.snapshot(case.owner_id, case.case_id)
                except Exception:
                    continue
                with case.finalize_lock:
                    if case.cancel_event.is_set():
                        continue
                    case.decision_snapshot = copy.deepcopy(decision)
                    case.phase = decision["phase"]
                    self._release_if_terminal(case)

    def _owned_case(self, owner_id: str, case_id: str) -> _Case:
        if type(owner_id) is not str or type(case_id) is not str:
            raise RoadCaseError("case_not_found", status=404)
        with self._lock:
            case = self._cases.get((owner_id, case_id))
        if case is None:
            raise RoadCaseError("case_not_found", status=404)
        return case

    def _release_if_terminal(self, case: _Case) -> None:
        if case.phase not in _TERMINAL_PHASES and not case.analysis_complete:
            return
        with self._lock:
            key = (case.owner_id, case.source_id)
            if self._active_by_source.get(key) == case.case_id:
                self._active_by_source.pop(key, None)


def _first_stable_candidate(
    findings: Any,
    times: dict[str, float],
) -> tuple[Finding, list[str]] | None:
    candidates: list[tuple[float, Finding, list[str]]] = []
    if not isinstance(findings, (list, tuple)):
        return None
    for finding in findings:
        if not isinstance(finding, Finding) or finding.tool != "direction":
            continue
        metrics = finding.metrics
        count = metrics.get("opposition_count")
        if (
            metrics.get("warmup") is True
            or isinstance(count, bool)
            or type(count) not in (int, float)
            or count <= 0
        ):
            continue
        tracks = metrics.get("tracks")
        if not isinstance(tracks, list):
            continue
        stable_track_ids: set[str] = set()
        for track in tracks:
            if not isinstance(track, dict) or track.get("opposition") is not True:
                continue
            frame_ids = track.get("frame_ids")
            if not isinstance(frame_ids, list):
                continue
            actual = [item for item in frame_ids if type(item) is str and item in times]
            if len(set(actual)) >= 3:
                stable_track_ids.update(actual)
        if len(stable_track_ids) < 3:
            continue
        current_ids = [item for item in finding.frame_ids if item in times]
        if not current_ids:
            continue
        event_time = min(times[item] for item in current_ids)
        candidates.append((event_time, finding, list(dict.fromkeys(current_ids + sorted(stable_track_ids)))))
    if not candidates:
        return None
    _, finding, ids = min(candidates, key=lambda item: item[0])
    return finding, ids


def _manifest_times(manifest: Any) -> dict[str, float]:
    if not isinstance(manifest, dict) or not isinstance(manifest.get("frames"), list):
        return {}
    result: dict[str, float] = {}
    for frame in manifest["frames"]:
        if not isinstance(frame, dict):
            continue
        frame_id = frame.get("frame_id")
        timestamp = frame.get("timestamp_seconds")
        if (
            type(frame_id) is str
            and not isinstance(timestamp, bool)
            and type(timestamp) in (int, float)
            and math.isfinite(float(timestamp))
        ):
            result[frame_id] = float(timestamp)
    return result


def _assessment_from_exchange(exchange: dict[str, Any] | None) -> VideoAssessment | None:
    if not isinstance(exchange, dict) or exchange.get("status") != "complete":
        return None
    value = exchange.get("assessment")
    if not isinstance(value, dict):
        return None
    decision_data = value.get("decision")
    if not isinstance(decision_data, dict):
        return None
    try:
        return VideoAssessment(
            object_class=value["object_class"],
            hazard=value["hazard"],
            confidence=value["confidence"],
            summary=value["summary"],
            evidence_frame_ids=tuple(value["evidence_frame_ids"]),
            decision=ModelDecision(
                decision_data["action"],
                copy.deepcopy(decision_data["payload"]),
            ),
        )
    except (KeyError, TypeError, ValueError):
        return None


def _clip_data(clip: ClipRef) -> dict[str, Any]:
    return {
        "clip_id": clip.clip_id,
        "source_id": clip.source_id,
        "version": clip.version,
        "sha256": clip.sha256,
        "start_seconds": clip.start_seconds,
        "end_seconds": clip.end_seconds,
        "frame_ids": list(clip.frame_ids),
        "source_fps": clip.source_fps,
        "available_preroll": clip.available_preroll,
    }


def _identifier(value: Any, label: str) -> str:
    if type(value) is not str or not value.strip() or len(value) > 512 or "\x00" in value:
        raise RoadCaseError("invalid_identifier", f"{label} must be non-empty text", 422)
    return value


def _safe_code(value: Any) -> bool:
    return (
        type(value) is str
        and bool(value)
        and len(value) <= 64
        and value[0].islower()
        and all(character.islower() or character.isdigit() or character == "_" for character in value)
    )


__all__ = ["RoadCaseError", "RoadCaseService", "RoadModelRouter"]
