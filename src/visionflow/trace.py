from __future__ import annotations

import copy
import json
import math
import re
from typing import Any

_SENSITIVE_KEY_MARKERS = (
    "prompt",
    "question",
    "message",
    "content",
    "credential",
    "password",
    "passwd",
    "secret",
    "authorization",
    "cookie",
    "token",
    "apikey",
    "privatekey",
)


def _validate_json_value(value: Any, seen: set[int]) -> None:
    if value is None or type(value) in (bool, int, str):
        return
    if type(value) is float:
        if not math.isfinite(value):
            raise ValueError("trace events cannot contain non-finite numbers")
        return

    if type(value) not in (dict, list):
        raise ValueError("trace events must contain only JSON values")

    identity = id(value)
    if identity in seen:
        raise ValueError("trace events cannot contain circular references")
    seen.add(identity)
    try:
        if type(value) is list:
            for item in value:
                _validate_json_value(item, seen)
            return

        for key, item in value.items():
            if type(key) is not str:
                raise ValueError("trace event keys must be strings")
            normalized_key = re.sub(r"[^a-z0-9]", "", key.casefold())
            if any(marker in normalized_key for marker in _SENSITIVE_KEY_MARKERS):
                raise ValueError("trace events cannot contain prompt or credential fields")
            _validate_json_value(item, seen)
    finally:
        seen.remove(identity)


class TraceRecorder:
    """Collect JSON-safe event snapshots without retaining caller-owned objects."""

    def __init__(self) -> None:
        self._events: list[dict[str, Any]] = []

    def append(self, event: dict) -> None:
        if type(event) is not dict:
            raise ValueError("a trace event must be a JSON object")
        event_type = event.get("type")
        if type(event_type) is not str or not event_type.strip():
            raise ValueError("a trace event must have a non-empty type")

        _validate_json_value(event, set())
        try:
            snapshot = json.loads(json.dumps(event, ensure_ascii=False, allow_nan=False))
        except (TypeError, ValueError) as exc:
            raise ValueError("trace event must be valid JSON") from exc
        self._events.append(snapshot)

    def export(self) -> list[dict]:
        return copy.deepcopy(self._events)