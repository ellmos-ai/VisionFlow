"""Bounded owner-scoped queue for real road-camera scenario cases."""

from __future__ import annotations

import copy
import math
import re
import threading
import time
import uuid
from typing import Any


_EVENTS = frozenset({"wrong_way", "bird", "leaf"})
_FIXTURE_PROFILES = frozenset(
    {"pending", "high", "bird", "leaf", "followup", "unclear", "error"}
)
_TERMINAL_RUN_STATES = frozenset({"completed", "failed", "cancelled"})
_MAX_PENDING_PER_SOURCE = 4
_MAX_JOBS_PER_SOURCE = 20
_MIN_PREROLL_SECONDS = 5.0
_DEFAULT_WARMUP_TIMEOUT_SECONDS = 10.0
_DEFAULT_POLL_INTERVAL_SECONDS = 0.15
_SAFE_ERROR = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


class RoadScenarioError(RuntimeError):
    """Stable public error code and status for scenario queue requests."""

    def __init__(self, code: str, message: str | None = None, status: int = 409) -> None:
        self.code = code
        self.status = status
        super().__init__(message or code.replace("_", " "))


class RoadScenarioService:
    """Serialize actual case runs per source while keeping human review open."""

    def __init__(
        self,
        cases: Any,
        sessions: Any,
        controller: Any,
        *,
        warmup_timeout_seconds: float = _DEFAULT_WARMUP_TIMEOUT_SECONDS,
        poll_interval_seconds: float = _DEFAULT_POLL_INTERVAL_SECONDS,
    ) -> None:
        if not callable(getattr(cases, "start", None)) or not callable(
            getattr(cases, "snapshot", None)
        ):
            raise TypeError("cases must provide start() and snapshot()")
        if not callable(getattr(sessions, "get", None)) or not callable(
            getattr(sessions, "inject", None)
        ):
            raise TypeError("sessions must provide get() and inject()")
        if not callable(getattr(controller, "status", None)):
            raise TypeError("controller must provide status()")
        for value, label in (
            (warmup_timeout_seconds, "warmup_timeout_seconds"),
            (poll_interval_seconds, "poll_interval_seconds"),
        ):
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(float(value))
                or value <= 0
            ):
                raise ValueError(f"{label} must be finite and positive")
        if warmup_timeout_seconds > 10.0:
            raise ValueError("warmup_timeout_seconds cannot exceed ten seconds")

        self.cases = cases
        self.sessions = sessions
        self.controller = controller
        self._warmup_timeout = float(warmup_timeout_seconds)
        self._poll_interval = float(poll_interval_seconds)
        self._lock = threading.RLock()
        self._closed = False
        self._jobs: dict[tuple[str, str], list[dict[str, Any]]] = {}
        self._workers: dict[tuple[str, str], threading.Thread] = {}
        self._stop_events: dict[tuple[str, str], threading.Event] = {}
        self._active_cases: dict[tuple[str, str], str] = {}

    def enqueue(
        self,
        owner_id: str,
        source_id: str,
        event: str,
        mode: str,
        fixture_profile: str | None,
    ) -> dict[str, Any]:
        owner = _identifier(owner_id, "owner_id")
        source = _identifier(source_id, "source_id")
        if type(event) is not str or event not in _EVENTS:
            raise RoadScenarioError("event_rejected", status=422)
        if type(mode) is not str or mode not in {"fixture", "gemini_video"}:
            raise RoadScenarioError("mode_not_supported", status=422)

        capabilities = getattr(self.cases, "capabilities", {})
        if isinstance(capabilities, dict):
            modes = capabilities.get("modes")
            if isinstance(modes, list) and mode not in modes:
                raise RoadScenarioError("native_disabled", status=503)
            allowed_profiles = capabilities.get("fixture_profiles")
        else:
            allowed_profiles = None
        if mode == "fixture":
            profile = "pending" if fixture_profile is None else fixture_profile
            accepted_profiles = (
                set(allowed_profiles)
                if isinstance(allowed_profiles, (tuple, list, set, frozenset))
                else _FIXTURE_PROFILES
            )
            if type(profile) is not str or profile not in accepted_profiles:
                raise RoadScenarioError("fixture_profile_not_allowed", status=422)
        else:
            if fixture_profile is not None:
                raise RoadScenarioError("fixture_profile_not_allowed", status=422)
            profile = None

        try:
            session = self.sessions.get(owner, source)
        except (KeyError, ValueError) as exc:
            raise RoadScenarioError("source_not_found", status=404) from exc
        if _source_ended(session):
            raise RoadScenarioError("source_ended", status=409)

        key = (owner, source)
        with self._lock:
            if self._closed:
                raise RoadScenarioError("service_closed", status=503)
            jobs = self._jobs.setdefault(key, [])
            if len(jobs) >= _MAX_JOBS_PER_SOURCE:
                raise RoadScenarioError("job_history_limit", status=429)
            if sum(job["state"] == "queued" for job in jobs) >= _MAX_PENDING_PER_SOURCE:
                raise RoadScenarioError("queue_full", status=429)
            job = {
                "job_id": uuid.uuid4().hex,
                "source_id": source,
                "event": event,
                "mode": mode,
                "fixture_profile": profile,
                "state": "queued",
                "case_id": None,
                "run_id": None,
                "error": None,
            }
            jobs.append(job)
            self._ensure_worker_locked(key)
            return copy.deepcopy(job)

    def jobs(self, owner_id: str, source_id: str) -> list[dict[str, Any]]:
        owner = _identifier(owner_id, "owner_id")
        source = _identifier(source_id, "source_id")
        with self._lock:
            return copy.deepcopy(self._jobs.get((owner, source), []))

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            workers = tuple(self._workers.items())
            active = tuple(self._active_cases.items())
            for key, jobs in self._jobs.items():
                for job in jobs:
                    if job["state"] == "queued":
                        job.update(state="cancelled", error="service_closed")
            for stop_event in self._stop_events.values():
                stop_event.set()

        for (owner, source), case_id in active:
            try:
                status = self._case_analysis_status(owner, case_id)
                if not status["complete"]:
                    self.cases.cancel(owner, case_id)
            except Exception:
                continue
        for _, worker in workers:
            if worker is not threading.current_thread():
                worker.join(timeout=5.0)

    def _ensure_worker_locked(self, key: tuple[str, str]) -> None:
        current = self._workers.get(key)
        if current is not None and current.is_alive():
            return
        stop_event = self._stop_events.setdefault(key, threading.Event())
        worker = threading.Thread(
            target=self._run_source,
            args=(key, stop_event),
            name=f"road-scenarios-{key[1][-8:]}",
            daemon=True,
        )
        self._workers[key] = worker
        worker.start()

    def _run_source(self, key: tuple[str, str], stop_event: threading.Event) -> None:
        owner, source = key
        while True:
            with self._lock:
                if self._closed or stop_event.is_set():
                    self._cancel_pending_locked(key, "service_closed")
                    self._workers.pop(key, None)
                    self._active_cases.pop(key, None)
                    return
                job = next(
                    (item for item in self._jobs.get(key, []) if item["state"] == "queued"),
                    None,
                )
                if job is None:
                    self._workers.pop(key, None)
                    self._active_cases.pop(key, None)
                    return
                job["state"] = "starting"

            if self._source_is_ended(owner, source):
                self._finish(job, "cancelled", "source_ended")
                with self._lock:
                    self._cancel_pending_locked(key, "source_ended")
                continue

            case_id: str | None = None
            run_id: str | None = None
            try:
                started = self.cases.start(
                    owner,
                    source,
                    job["mode"],
                    fixture_profile=job["fixture_profile"],
                )
                case_id = started.get("case_id")
                run_id = started.get("run_id")
                if type(case_id) is not str or not case_id or type(run_id) is not str or not run_id:
                    raise RoadScenarioError("case_start_failed", status=500)
                with self._lock:
                    job.update(case_id=case_id, run_id=run_id)
                    self._active_cases[key] = case_id

                if not self._await_warmup(owner, source, run_id, stop_event):
                    if self._is_stopping(owner, source, stop_event):
                        self._cancel_case(owner, case_id)
                        self._finish(job, "cancelled", "source_ended")
                        with self._lock:
                            self._cancel_pending_locked(key, "source_ended")
                        continue
                    try:
                        run = self.controller.status(run_id)
                    except Exception:
                        run = None
                    if self._monitor_has_finished(run) or getattr(run, "status", None) in _TERMINAL_RUN_STATES:
                        self._cancel_case(owner, case_id)
                        self._finish(job, "failed", "run_ended_before_injection")
                        continue
                    self._cancel_case(owner, case_id)
                    self._finish(job, "failed", "warmup_timeout")
                    continue

                # Recheck immediately before the one allowed injection. A stopped
                # source or terminal controller run must never get a late event.
                run = self.controller.status(run_id)
                if not self._monitor_is_live(run):
                    self._cancel_case(owner, case_id)
                    self._finish(job, "failed", "run_ended_before_injection")
                    continue
                if self._is_stopping(owner, source, stop_event):
                    self._cancel_case(owner, case_id)
                    self._finish(job, "cancelled", "source_ended")
                    with self._lock:
                        self._cancel_pending_locked(key, "source_ended")
                    continue
                self.sessions.inject(owner, source, job["event"])
                self._set_state(job, "running")

                run_status = self._await_controller_end(run_id, owner, source, stop_event)
                if run_status == "cancelled" or stop_event.is_set():
                    self._cancel_case(owner, case_id)
                    self._finish(job, "cancelled", "source_ended" if not self._closed else "service_closed")
                    with self._lock:
                        self._cancel_pending_locked(key, "source_ended" if not self._closed else "service_closed")
                    continue

                analysis = self._await_case_finalization(owner, case_id, source, stop_event)
                if analysis is None:
                    self._cancel_case(owner, case_id)
                    self._finish(job, "cancelled", "source_ended" if not self._closed else "service_closed")
                    with self._lock:
                        self._cancel_pending_locked(key, "source_ended" if not self._closed else "service_closed")
                    continue
                if run_status == "completed":
                    self._finish(job, "completed", None)
                else:
                    self._finish(job, "failed", _error_code(analysis.get("error"), "controller_failed"))
            except Exception as exc:
                if case_id is not None:
                    self._cancel_case(owner, case_id)
                code = _error_code(getattr(exc, "code", None), "scenario_failed")
                self._finish(job, "failed", code)
            finally:
                with self._lock:
                    self._active_cases.pop(key, None)

    def _await_warmup(
        self,
        owner: str,
        source: str,
        run_id: str,
        stop_event: threading.Event,
    ) -> bool:
        deadline = time.monotonic() + self._warmup_timeout
        while time.monotonic() < deadline:
            if self._is_stopping(owner, source, stop_event):
                return False
            try:
                result = self.controller.status(run_id)
            except Exception:
                return False
            state = getattr(result, "status", None)
            if state in _TERMINAL_RUN_STATES or self._monitor_has_finished(result):
                return False
            if state != "observing":
                stop_event.wait(self._poll_interval)
                continue
            trace = getattr(result, "trace", [])
            observed = {
                item.get("evidence_id")
                for item in trace
                if isinstance(item, dict)
                and item.get("type") == "frame_observed"
                and item.get("node_id") == "monitor"
                and type(item.get("evidence_id")) is str
            }
            if len(observed) >= 3 and self._source_age(owner, source) >= _MIN_PREROLL_SECONDS:
                return True
            stop_event.wait(self._poll_interval)
        return False

    @staticmethod
    def _monitor_is_live(result: Any) -> bool:
        if getattr(result, "status", None) != "observing":
            return False
        trace = getattr(result, "trace", [])
        return not any(
            isinstance(item, dict)
            and item.get("type") == "monitor_stopped"
            and item.get("node_id") == "monitor"
            for item in trace
        )

    @staticmethod
    def _monitor_has_finished(result: Any) -> bool:
        trace = getattr(result, "trace", [])
        return any(
            isinstance(item, dict)
            and item.get("node_id") == "monitor"
            and item.get("type") in {"monitor_stopped", "observation_completed"}
            for item in trace
        )

    def _await_controller_end(
        self,
        run_id: str,
        owner: str,
        source: str,
        stop_event: threading.Event,
    ) -> str:
        while not stop_event.is_set():
            if self._source_is_ended(owner, source):
                return "cancelled"
            try:
                result = self.controller.status(run_id)
            except Exception:
                return "failed"
            status = getattr(result, "status", None)
            if status in _TERMINAL_RUN_STATES:
                return status
            stop_event.wait(self._poll_interval)
        return "cancelled"

    def _await_case_finalization(
        self,
        owner: str,
        case_id: str,
        source: str,
        stop_event: threading.Event,
    ) -> dict[str, Any] | None:
        while not stop_event.is_set():
            if self._source_is_ended(owner, source):
                return None
            try:
                status = self._case_analysis_status(owner, case_id)
            except Exception:
                return None
            if status["complete"]:
                return status
            stop_event.wait(self._poll_interval)
        return None

    def _case_analysis_status(self, owner: str, case_id: str) -> dict[str, Any]:
        method = getattr(self.cases, "analysis_status", None)
        if callable(method):
            result = method(owner, case_id)
            if isinstance(result, dict):
                return {
                    "complete": result.get("analysis_complete") is True,
                    "phase": result.get("phase"),
                    "error": result.get("model_error"),
                }
        snapshot = self.cases.snapshot(owner, case_id)
        phase = snapshot.get("phase")
        complete = phase in {
            "human_review", "reported", "dismissed", "no_response", "rejected",
            "normal", "source_ended", "cancelled",
        }
        return {"complete": complete, "phase": phase, "error": snapshot.get("model_error")}

    def _source_age(self, owner: str, source: str) -> float:
        method = getattr(self.sessions, "source_age_seconds", None)
        if callable(method):
            value = method(owner, source)
        else:
            recording = self.sessions.recording(owner, source)
            value = recording.get("end_seconds") if isinstance(recording, dict) else None
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return 0.0
        return max(0.0, float(value))

    def _source_is_ended(self, owner: str, source: str) -> bool:
        try:
            return _source_ended(self.sessions.get(owner, source))
        except (KeyError, ValueError):
            return True

    def _is_stopping(self, owner: str, source: str, stop_event: threading.Event) -> bool:
        return self._closed or stop_event.is_set() or self._source_is_ended(owner, source)

    def _cancel_case(self, owner: str, case_id: str) -> None:
        try:
            self.cases.cancel(owner, case_id)
        except Exception:
            return

    def _set_state(self, job: dict[str, Any], state: str) -> None:
        with self._lock:
            if job["state"] not in {"completed", "failed", "cancelled"}:
                job["state"] = state

    def _finish(self, job: dict[str, Any], state: str, error: str | None) -> None:
        with self._lock:
            if job["state"] not in {"completed", "failed", "cancelled"}:
                job.update(state=state, error=error)

    def _cancel_pending_locked(self, key: tuple[str, str], reason: str) -> None:
        for job in self._jobs.get(key, []):
            if job["state"] == "queued":
                job.update(state="cancelled", error=reason)


def _source_ended(session: Any) -> bool:
    return bool(getattr(session, "closed", False) or getattr(session, "error_code", None))


def _identifier(value: str, label: str) -> str:
    if type(value) is not str or not value or value != value.strip():
        raise RoadScenarioError("invalid_identifier", f"{label} is invalid.", 422)
    return value


def _error_code(value: Any, fallback: str) -> str:
    return value if type(value) is str and _SAFE_ERROR.fullmatch(value) else fallback


__all__ = ["RoadScenarioError", "RoadScenarioService"]
