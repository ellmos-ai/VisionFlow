"""Generic, owner-scoped decision and countdown policy for review cases."""

from __future__ import annotations

import copy
import hashlib
import hmac
import json
import math
import re
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, Protocol

from .simulated_authority import (
    InboxConflict,
    SimulatedAuthorityInbox,
    _assessment_public_metadata,
    _clip_public_metadata,
)

if TYPE_CHECKING:
    from visionflow.recording import ClipRef
    from visionflow.video_model import VideoAssessment


_SHA256 = re.compile(r"(?:sha256:)?[0-9a-f]{64}\Z", re.ASCII)
_SAFE_MODEL_ERROR = re.compile(r"[a-z][a-z0-9_]{0,63}\Z", re.ASCII)
DecisionName = Literal["confirm", "reject"]


class ReviewClock(Protocol):
    """Clock seam used for deterministic deadlines and UTC display."""

    def monotonic(self) -> float: ...

    def utcnow(self) -> datetime: ...


@dataclass(frozen=True, slots=True)
class DecisionPolicy:
    confidence_threshold: float = 0.90
    countdown_seconds: float = 20.0

    def __post_init__(self) -> None:
        if (
            isinstance(self.confidence_threshold, bool)
            or not isinstance(self.confidence_threshold, (int, float))
            or not math.isfinite(float(self.confidence_threshold))
            or not 0.0 <= float(self.confidence_threshold) <= 1.0
        ):
            raise ValueError("confidence_threshold must be finite and between 0 and 1")
        if (
            isinstance(self.countdown_seconds, bool)
            or not isinstance(self.countdown_seconds, (int, float))
            or not math.isfinite(float(self.countdown_seconds))
            or float(self.countdown_seconds) <= 0.0
        ):
            raise ValueError("countdown_seconds must be finite and greater than 0")


class DecisionError(RuntimeError):
    """Base for safe decision errors with a stable code."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


class DecisionConflict(DecisionError):
    """The decision is stale, replayed with another body, or already closed."""


class DecisionNotFound(DecisionError):
    """No case is visible to the requesting owner."""

    def __init__(self) -> None:
        super().__init__("case_not_found", "Der Fall wurde nicht gefunden.")


@dataclass(slots=True)
class _StoredCase:
    owner_id: str
    case_id: str
    run_id: str
    source_id: str
    clip: ClipRef | None
    clip_data: dict[str, Any] | None
    assessment_data: dict[str, Any] | None
    cv_confirmed: bool
    model_error: str | None
    content_hash: str
    version: int = 0
    phase: str = "initializing"
    deadline_monotonic: float | None = None
    deadline_at: str | None = None
    journal: list[dict[str, Any]] = field(default_factory=list)
    first_receipt: dict[str, Any] | None = None
    correction_receipt: dict[str, Any] | None = None
    idempotency: dict[str, tuple[tuple[Any, ...], dict[str, Any]]] = field(
        default_factory=dict
    )


class DecisionStore:
    """Thread-safe policy state with immutable content bindings and deadlines."""

    def __init__(
        self,
        policy: DecisionPolicy,
        inbox: SimulatedAuthorityInbox,
        clock: ReviewClock,
    ) -> None:
        if not isinstance(policy, DecisionPolicy):
            raise TypeError("policy must be a DecisionPolicy")
        if not isinstance(inbox, SimulatedAuthorityInbox):
            raise TypeError("inbox must be a SimulatedAuthorityInbox")
        if not callable(getattr(clock, "monotonic", None)) or not callable(
            getattr(clock, "utcnow", None)
        ):
            raise TypeError("clock must provide monotonic() and utcnow()")
        self.policy = policy
        self.inbox = inbox
        self.clock = clock
        self._cases: dict[tuple[str, str], _StoredCase] = {}
        self._lock = threading.RLock()

    def open_case(
        self,
        owner_id: str,
        case_id: str,
        run_id: str,
        clip: ClipRef | None,
        assessment: VideoAssessment | None,
        cv_confirmed: bool,
        model_error: str | None = None,
        *,
        source_id: str,
    ) -> dict[str, Any]:
        owner_id = self._identifier(owner_id, "owner_id")
        case_id = self._identifier(case_id, "case_id")
        run_id = self._identifier(run_id, "run_id")
        source_id = self._identifier(source_id, "source_id")
        if not isinstance(cv_confirmed, bool):
            raise ValueError("cv_confirmed must be a boolean")
        if model_error is not None and not isinstance(model_error, str):
            raise ValueError("model_error must be a string or None")

        clip_data = _clip_public_metadata(clip)
        private_path = str(getattr(clip, "path", "")) or None
        assessment_data = _assessment_public_metadata(
            assessment,
            private_path=private_path,
        )
        safe_error = (
            model_error
            if model_error is None or _SAFE_MODEL_ERROR.fullmatch(model_error)
            else "model_error"
        )
        content_hash = self._content_hash(
            owner_id=owner_id,
            case_id=case_id,
            run_id=run_id,
            source_id=source_id,
            clip_data=clip_data,
            assessment_data=assessment_data,
            cv_confirmed=cv_confirmed,
            model_error=safe_error,
        )

        with self._lock:
            key = (owner_id, case_id)
            if key in self._cases:
                raise DecisionConflict("case_exists", "Der Fall ist bereits angelegt.")

            now_monotonic = self._monotonic_now()
            now_utc = self._utc_now()
            state = _StoredCase(
                owner_id=owner_id,
                case_id=case_id,
                run_id=run_id,
                source_id=source_id,
                clip=clip,
                clip_data=clip_data,
                assessment_data=assessment_data,
                cv_confirmed=cv_confirmed,
                model_error=safe_error,
                content_hash=content_hash,
            )
            self._cases[key] = state
            self._record(state, "case_opened")

            object_class = (
                assessment_data.get("object_class")
                if assessment_data is not None
                else None
            )
            hazard = assessment_data.get("hazard") if assessment_data is not None else None

            if safe_error is not None or assessment_data is None or hazard is None:
                state.phase = "human_review"
                state.deadline_monotonic = (
                    now_monotonic + float(self.policy.countdown_seconds)
                )
                state.deadline_at = self._format_utc(
                    now_utc + timedelta(seconds=float(self.policy.countdown_seconds))
                )
                self._record(state, "human_review_started")
            elif hazard is False:
                state.phase = "dismissed"
                self._record(state, "hazard_false_dismissed")
            elif self._is_high_confidence(state):
                self._send_first_report(state, "high_confidence")
            else:
                state.phase = "human_review"
                state.deadline_monotonic = (
                    now_monotonic + float(self.policy.countdown_seconds)
                )
                state.deadline_at = self._format_utc(
                    now_utc + timedelta(seconds=float(self.policy.countdown_seconds))
                )
                self._record(state, "human_review_started")

            return self._public_case(state)

    def snapshot(self, owner_id: str, case_id: str) -> dict[str, Any]:
        with self._lock:
            return self._public_case(self._owned_case(owner_id, case_id))

    def tick(self) -> None:
        """Advance all expired review deadlines without requiring a UI read."""
        with self._lock:
            now = self._monotonic_now()
            for state in self._cases.values():
                self._expire_if_due(state, now)

    def cancel(self, owner_id: str, case_id: str) -> dict[str, Any]:
        with self._lock:
            state = self._owned_case(owner_id, case_id)
            if state.phase == "human_review" and state.deadline_monotonic is not None:
                state.phase = "cancelled"
                state.deadline_monotonic = None
                state.deadline_at = None
                self._record(state, "review_cancelled")
            return self._public_case(state)

    def resolve(
        self,
        owner_id: str,
        case_id: str,
        version: int,
        content_hash: str,
        decision: DecisionName,
        idempotency_key: str,
    ) -> dict[str, Any]:
        with self._lock:
            # Owner lookup intentionally precedes key and binding checks.
            state = self._owned_case(owner_id, case_id)
            idempotency_key = self._identifier(idempotency_key, "idempotency_key")
            body = (version, content_hash, decision)
            previous = state.idempotency.get(idempotency_key)
            if previous is not None:
                previous_body, previous_result = previous
                if previous_body != body:
                    raise DecisionConflict(
                        "idempotency_conflict",
                        "Der Idempotenzschlüssel ist an eine andere Entscheidung gebunden.",
                    )
                return copy.deepcopy(previous_result)

            if isinstance(version, bool) or not isinstance(version, int):
                raise DecisionConflict("invalid_version", "Die Fallversion ist ungültig.")
            if decision not in {"confirm", "reject"}:
                raise DecisionConflict("invalid_decision", "Die Entscheidung ist ungültig.")
            if version != state.version:
                raise DecisionConflict("stale_version", "Der Fall wurde inzwischen geändert.")
            if not isinstance(content_hash, str) or not hmac.compare_digest(
                content_hash, state.content_hash
            ):
                raise DecisionConflict("stale_content_hash", "Der Fallinhalt stimmt nicht mehr.")

            now = self._monotonic_now()
            self._expire_if_due(state, now)

            if decision == "confirm":
                if state.phase == "human_review":
                    self._send_first_report(state, "human_confirmed")
                elif state.first_receipt is not None:
                    self._record(
                        state,
                        "late_confirm_annotation",
                        human_confirmed=False,
                        original_receipt_id=state.first_receipt["receipt_id"],
                    )
                else:
                    raise DecisionConflict(
                        "case_closed",
                        "Für diesen Fall ist keine menschliche Entscheidung mehr offen.",
                    )
            elif state.phase == "human_review":
                state.phase = "rejected"
                state.deadline_monotonic = None
                self._record(state, "human_rejected")
            elif state.first_receipt is not None:
                if state.correction_receipt is None:
                    try:
                        correction = self.inbox.correction(
                            state.owner_id,
                            state.case_id,
                            state.first_receipt["receipt_id"],
                            state.content_hash,
                        )
                    except InboxConflict as error:
                        raise DecisionConflict(
                            "correction_conflict",
                            "Die verknüpfte Entwarnung konnte nicht angelegt werden.",
                        ) from error
                    state.correction_receipt = correction
                    state.phase = "corrected"
                    self._record(
                        state,
                        "late_reject_correction",
                        original_receipt_id=state.first_receipt["receipt_id"],
                        correction_receipt_id=correction["receipt_id"],
                    )
            else:
                raise DecisionConflict(
                    "case_closed",
                    "Für diesen Fall ist keine menschliche Entscheidung mehr offen.",
                )

            result = self._public_case(state)
            state.idempotency[idempotency_key] = (body, copy.deepcopy(result))
            return result

    def _owned_case(self, owner_id: str, case_id: str) -> _StoredCase:
        if not isinstance(owner_id, str) or not isinstance(case_id, str):
            raise DecisionNotFound()
        state = self._cases.get((owner_id, case_id))
        if state is None:
            raise DecisionNotFound()
        return state

    def _record(self, state: _StoredCase, event: str, **details: Any) -> None:
        state.version += 1
        entry: dict[str, Any] = {
            "version": state.version,
            "event": event,
            "at": self._format_utc(self._utc_now()),
        }
        if details:
            entry.update(details)
        state.journal.append(entry)

    def _send_first_report(
        self,
        state: _StoredCase,
        reason: Literal["high_confidence", "human_confirmed", "no_response"],
    ) -> None:
        if state.first_receipt is not None:
            return
        receipt = self.inbox.first_report(
            state.owner_id,
            state.case_id,
            state.content_hash,
            reason,
            state.clip,
            state.assessment_data,
        )
        state.first_receipt = receipt
        if reason == "no_response":
            state.phase = "no_response"
            state.deadline_monotonic = None
        else:
            state.phase = "reported"
            state.deadline_monotonic = None
        self._record(
            state,
            "first_report_sent",
            reason=reason,
            receipt_id=receipt["receipt_id"],
        )

    def _expire_if_due(self, state: _StoredCase, now: float) -> None:
        deadline = state.deadline_monotonic
        if (
            state.phase == "human_review"
            and deadline is not None
            and now >= deadline
        ):
            self._send_first_report(state, "no_response")

    def _is_high_confidence(self, state: _StoredCase) -> bool:
        assessment = state.assessment_data
        clip_data = state.clip_data
        if (
            assessment is None
            or clip_data is None
            or state.model_error is not None
            or not state.cv_confirmed
        ):
            return False
        if (
            assessment.get("object_class") != "vehicle"
            or assessment.get("hazard") is not True
        ):
            return False
        confidence = assessment.get("confidence")
        if (
            isinstance(confidence, bool)
            or not isinstance(confidence, (int, float))
            or not math.isfinite(float(confidence))
            or not 0.0 <= float(confidence) <= 1.0
            or float(confidence) < float(self.policy.confidence_threshold)
        ):
            return False
        if clip_data.get("source_id") != state.source_id:
            return False
        if not self._valid_clip_and_evidence(state.clip, clip_data, assessment):
            return False
        return True

    def _valid_clip_and_evidence(
        self,
        clip: ClipRef | None,
        clip_data: dict[str, Any],
        assessment: dict[str, Any],
    ) -> bool:
        if clip is None:
            return False
        clip_id = clip_data.get("clip_id")
        expected_hash = clip_data.get("sha256")
        if not isinstance(clip_id, str) or not clip_id.strip():
            return False
        if not isinstance(expected_hash, str) or not _SHA256.fullmatch(expected_hash):
            return False
        frame_ids = clip_data.get("frame_ids")
        evidence_ids = assessment.get("evidence_frame_ids")
        if (
            not isinstance(frame_ids, list)
            or not frame_ids
            or any(not isinstance(frame_id, str) or not frame_id for frame_id in frame_ids)
            or not isinstance(evidence_ids, list)
            or not evidence_ids
            or any(not isinstance(frame_id, str) or not frame_id for frame_id in evidence_ids)
            or not set(evidence_ids).issubset(set(frame_ids))
        ):
            return False
        version = clip_data.get("version")
        if isinstance(version, bool) or not isinstance(version, int) or version < 1:
            return False
        start = clip_data.get("start_seconds")
        end = clip_data.get("end_seconds")
        if (
            isinstance(start, bool)
            or isinstance(end, bool)
            or not isinstance(start, (int, float))
            or not isinstance(end, (int, float))
            or not math.isfinite(float(start))
            or not math.isfinite(float(end))
            or float(end) <= float(start)
        ):
            return False

        try:
            path = Path(getattr(clip, "path"))
            if path.is_symlink() or not path.is_file():
                return False
            digest = hashlib.sha256()
            with path.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)
            actual_hash = digest.hexdigest()
        except (AttributeError, OSError, TypeError, ValueError):
            return False

        return hmac.compare_digest(actual_hash, expected_hash.removeprefix("sha256:"))

    def _content_hash(
        self,
        *,
        owner_id: str,
        case_id: str,
        run_id: str,
        source_id: str,
        clip_data: dict[str, Any] | None,
        assessment_data: dict[str, Any] | None,
        cv_confirmed: bool,
        model_error: str | None,
    ) -> str:
        payload = {
            "schema": 1,
            "owner_id": owner_id,
            "case_id": case_id,
            "run_id": run_id,
            "source_id": source_id,
            "clip": clip_data,
            "assessment": assessment_data,
            "cv_confirmed": cv_confirmed,
            "model_error": model_error,
        }
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return "sha256:" + hashlib.sha256(encoded).hexdigest()

    def _public_case(self, state: _StoredCase) -> dict[str, Any]:
        remaining = 0.0
        if state.phase == "human_review" and state.deadline_monotonic is not None:
            remaining = max(0.0, state.deadline_monotonic - self._monotonic_now())
        return {
            "case_id": state.case_id,
            "run_id": state.run_id,
            "source_id": state.source_id,
            "version": state.version,
            "content_hash": state.content_hash,
            "phase": state.phase,
            "deadline_at": state.deadline_at,
            "remaining_seconds": remaining,
            "assessment": copy.deepcopy(state.assessment_data),
            "model_error": state.model_error,
            "clip": copy.deepcopy(state.clip_data),
            "journal": copy.deepcopy(state.journal),
            "first_receipt": copy.deepcopy(state.first_receipt),
            "correction_receipt": copy.deepcopy(state.correction_receipt),
        }

    def _monotonic_now(self) -> float:
        value = self.clock.monotonic()
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(float(value))
        ):
            raise DecisionError("invalid_clock", "Die monotone Uhr lieferte keinen gültigen Wert.")
        return float(value)

    def _utc_now(self) -> datetime:
        value = self.clock.utcnow()
        if not isinstance(value, datetime) or value.tzinfo is None:
            raise DecisionError("invalid_clock", "Die Anzeigeuhr muss eine Zeitzone liefern.")
        try:
            if value.utcoffset() is None:
                raise DecisionError(
                    "invalid_clock", "Die Anzeigeuhr muss eine Zeitzone liefern."
                )
            return value.astimezone(timezone.utc)
        except (OverflowError, ValueError) as error:
            raise DecisionError(
                "invalid_clock", "Die Anzeigeuhr lieferte keinen gültigen Zeitpunkt."
            ) from error

    @staticmethod
    def _format_utc(value: datetime) -> str:
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

    @staticmethod
    def _identifier(value: Any, name: str) -> str:
        if (
            not isinstance(value, str)
            or not value.strip()
            or len(value) > 512
            or "\x00" in value
            or any(ord(character) < 32 for character in value)
        ):
            raise ValueError(f"{name} must be a non-empty safe identifier")
        return value


__all__ = [
    "DecisionConflict",
    "DecisionError",
    "DecisionNotFound",
    "DecisionPolicy",
    "DecisionStore",
    "ReviewClock",
]
