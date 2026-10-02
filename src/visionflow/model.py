"""Offline model adapter and strict validation for model-proposed actions."""

from __future__ import annotations

import math
from typing import Any, Protocol

from .contracts import Finding, ModelDecision


_ACTIONS = frozenset({"report", "observe", "request_input", "request_approval"})
_OBSERVATION_FIELDS = frozenset(
    {"source_id", "region", "duration_seconds", "sample_fps", "question"}
)


class ModelAdapter(Protocol):
    """A per-run adapter boundary; provider implementations live separately."""

    mode: str

    def ask(self, question: str, findings: list[Finding]) -> ModelDecision:
        """Return one decision without changing workflow policy or capabilities."""


def _non_empty_string(value: Any, field: str) -> str:
    if type(value) is not str or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    return value


def _positive_finite_number(value: Any, field: str) -> float:
    if isinstance(value, bool) or type(value) not in (int, float):
        raise ValueError(f"{field} must be a positive finite number")
    try:
        number = float(value)
    except OverflowError as exc:
        raise ValueError(f"{field} must be a positive finite number") from exc
    if not math.isfinite(number) or number <= 0:
        raise ValueError(f"{field} must be a positive finite number")
    return number


def _normalize_region(value: Any) -> tuple[float, float, float, float] | None:
    if value is None:
        return None
    if type(value) not in (tuple, list) or len(value) != 4:
        raise ValueError("region must be null or a four-number box")
    coordinates: list[float] = []
    for coordinate in value:
        if isinstance(coordinate, bool) or type(coordinate) not in (int, float):
            raise ValueError("region coordinates must be finite numbers")
        try:
            numeric = float(coordinate)
        except OverflowError as exc:
            raise ValueError("region coordinates must be finite numbers") from exc
        if not math.isfinite(numeric):
            raise ValueError("region coordinates must be finite numbers")
        coordinates.append(numeric)
    x, y, width, height = coordinates
    if x < 0 or y < 0 or width <= 0 or height <= 0:
        raise ValueError("region must have non-negative origin and positive size")
    if not math.isfinite(x + width) or not math.isfinite(y + height):
        raise ValueError("region bounds must be finite")
    return x, y, width, height


def _normalize_observation_payload(payload: Any) -> dict[str, Any]:
    if type(payload) is not dict or set(payload) != _OBSERVATION_FIELDS:
        raise ValueError("observe payload must contain exactly the observation fields")
    source_id = _non_empty_string(payload["source_id"], "source_id")
    question = _non_empty_string(payload["question"], "question")
    return {
        "source_id": source_id,
        "region": _normalize_region(payload["region"]),
        "duration_seconds": _positive_finite_number(
            payload["duration_seconds"], "duration_seconds"
        ),
        "sample_fps": _positive_finite_number(payload["sample_fps"], "sample_fps"),
        "question": question,
    }


def validate_model_decision(value: dict[str, Any] | ModelDecision) -> ModelDecision:
    """Validate and detach one untrusted decision without executing its text."""
    if type(value) is ModelDecision:
        action = value.action
        payload = value.payload
    elif type(value) is dict:
        unknown_top_level = set(value) - {"action", "payload"}
        if unknown_top_level:
            raise ValueError("model decision contains unknown top-level fields")
        action = value.get("action")
        payload = value.get("payload", {})
    else:
        raise ValueError("model decision must be a ModelDecision or object")

    if type(action) is not str or action not in _ACTIONS:
        raise ValueError("model decision action is unknown")
    if type(payload) is not dict:
        raise ValueError("model decision payload must be an object")

    if action == "report":
        if set(payload) - {"summary"}:
            raise ValueError("report payload may contain only summary")
        normalized = {}
        if "summary" in payload:
            normalized["summary"] = _non_empty_string(payload["summary"], "summary")
    elif action == "request_input":
        if set(payload) != {"reason"}:
            raise ValueError("request_input payload must contain only reason")
        normalized = {"reason": _non_empty_string(payload["reason"], "reason")}
    elif action == "request_approval":
        if set(payload) != {"action_type"} or payload["action_type"] != "notification_draft":
            raise ValueError("request_approval supports only notification_draft")
        normalized = {"action_type": "notification_draft"}
    else:
        normalized = _normalize_observation_payload(payload)

    return ModelDecision(action=action, payload=normalized)


class FixtureModelAdapter:
    """Deterministic offline adapter backed only by a validated decision list."""

    MODE = "fixture"

    def __init__(self, decisions: list[dict[str, Any] | ModelDecision] | None = None) -> None:
        if decisions is None:
            decisions = []
        if type(decisions) is not list:
            raise TypeError("fixture decisions must be a list")
        self._decisions = [validate_model_decision(item) for item in decisions]
        self._cursor = 0

    @property
    def mode(self) -> str:
        return self.MODE

    def ask(self, question: str, findings: list[Finding]) -> ModelDecision:
        """Return the next isolated fixture decision, then a safe report default."""
        if self._cursor >= len(self._decisions):
            return ModelDecision("report", {})
        decision = self._decisions[self._cursor]
        self._cursor += 1
        return validate_model_decision(decision)


__all__ = ["FixtureModelAdapter", "ModelAdapter", "validate_model_decision"]
