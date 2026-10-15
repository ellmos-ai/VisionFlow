"""Owner-scoped public snapshots for complete video model exchanges."""

from __future__ import annotations

import copy
import json
import re
from threading import RLock
from typing import Any

from .video_model import VideoAssessment, VideoRequest, clip_public_metadata


_PUBLIC_USAGE_FIELDS = frozenset(
    {
        "total_input_tokens",
        "total_output_tokens",
        "total_tokens",
        "total_thought_tokens",
        "total_cached_tokens",
        "input_tokens",
        "output_tokens",
        "cached_tokens",
    }
)
_SAFE_ERROR_CODE = re.compile(r"[a-z][a-z0-9_]{0,63}\Z", re.ASCII)
_MAX_DELTA_BYTES = 1024 * 1024
_MAX_OUTPUT_BYTES = 1024 * 1024


class ModelExchangeError(RuntimeError):
    """A safe store failure with a stable public error code."""

    def __init__(self, code: str) -> None:
        if type(code) is not str or not _SAFE_ERROR_CODE.fullmatch(code):
            code = "exchange_error"
        self.code = code
        super().__init__(code)


class ModelExchangeStore:
    """Keep request/response records private by owner and expose pathless snapshots."""

    def __init__(self, *, max_record_bytes: int | None = None) -> None:
        if max_record_bytes is not None and (type(max_record_bytes) is not int or max_record_bytes < 256):
            raise ValueError("max_record_bytes must be at least 256")
        self.max_record_bytes = max_record_bytes
        self._lock = RLock()
        self._records: dict[str, dict[str, Any]] = {}
        self._owners: dict[str, str] = {}

    def record_request(self, owner_id: str, request: VideoRequest) -> None:
        owner = _identifier(owner_id, "owner_id")
        if not isinstance(request, VideoRequest):
            raise TypeError("request must be a VideoRequest")
        with self._lock:
            if request.exchange_id in self._records:
                raise ModelExchangeError("exchange_exists")
            record = {
                "exchange_id": request.exchange_id,
                "case_id": request.case_id,
                "run_id": request.run_id,
                "status": "pending",
                "clip": clip_public_metadata(request.clip),
                "prompt": request.prompt,
                "evidence_refs": list(request.evidence_refs),
                "model_id": request.model_id,
                "processing_fps": float(request.processing_fps),
                "deltas": [],
                "raw_output": None,
                "assessment": None,
                "usage": None,
                "error_code": None,
            }
            self._check_size(record)
            self._owners[request.exchange_id] = owner
            self._records[request.exchange_id] = record

    def forget_case(self, owner_id: str, case_id: str) -> None:
        with self._lock:
            identifiers = [key for key, row in self._records.items()
                           if self._owners.get(key) == owner_id and row["case_id"] == case_id]
            for key in identifiers:
                if self._records[key]["status"] == "pending":
                    raise RuntimeError("model exchange still active")
            for key in identifiers:
                self._records.pop(key, None)
                self._owners.pop(key, None)

    def append_delta(self, exchange_id: str, text: str) -> None:
        exchange = _identifier(exchange_id, "exchange_id")
        if type(text) is not str:
            raise TypeError("delta must be text")
        if not text:
            return
        if len(text.encode("utf-8")) > _MAX_DELTA_BYTES:
            raise ModelExchangeError("delta_too_large")
        with self._lock:
            record = self._records.get(exchange)
            if record is None:
                raise KeyError(exchange)
            if record["status"] != "pending":
                raise ModelExchangeError("exchange_closed")
            current_size = sum(len(item.encode("utf-8")) for item in record["deltas"])
            if current_size + len(text.encode("utf-8")) > _MAX_OUTPUT_BYTES:
                raise ModelExchangeError("output_too_large")
            self._check_size({**record, "deltas": [*record["deltas"], text]})
            record["deltas"].append(text)

    def complete(
        self,
        exchange_id: str,
        assessment: VideoAssessment,
        raw_output: str,
        usage: dict[str, Any] | None,
    ) -> None:
        exchange = _identifier(exchange_id, "exchange_id")
        if not isinstance(assessment, VideoAssessment):
            raise TypeError("assessment must be a VideoAssessment")
        if type(raw_output) is not str or len(raw_output.encode("utf-8")) > _MAX_OUTPUT_BYTES:
            raise ModelExchangeError("output_too_large")
        safe_usage = _safe_usage(usage)
        with self._lock:
            record = self._records.get(exchange)
            if record is None:
                raise KeyError(exchange)
            if record["status"] != "pending":
                raise ModelExchangeError("exchange_closed")
            changes = {"status": "complete", "raw_output": raw_output,
                       "assessment": assessment.to_dict(), "usage": safe_usage}
            self._check_size({**record, **changes})
            record.update(changes)

    def _check_size(self, record: dict[str, Any]) -> None:
        if self.max_record_bytes is not None:
            size = len(json.dumps(record, ensure_ascii=False, allow_nan=False).encode("utf-8"))
            if size > self.max_record_bytes - 128:  # retain space for a terminal safe error
                from .retention import RetentionCapacityError
                raise RetentionCapacityError("The hosted demo model metadata is too large.")

    def fail(self, exchange_id: str, safe_code: str) -> None:
        exchange = _identifier(exchange_id, "exchange_id")
        if type(safe_code) is not str or not _SAFE_ERROR_CODE.fullmatch(safe_code):
            raise ValueError("safe_code must be a stable machine-readable code")
        with self._lock:
            record = self._records.get(exchange)
            if record is None:
                raise KeyError(exchange)
            if record["status"] != "pending":
                raise ModelExchangeError("exchange_closed")
            record["status"] = "failed"
            record["error_code"] = safe_code

    def snapshot(self, owner_id: str, exchange_id: str) -> dict[str, Any]:
        owner = _identifier(owner_id, "owner_id")
        exchange = _identifier(exchange_id, "exchange_id")
        with self._lock:
            if self._owners.get(exchange) != owner:
                # Do not reveal whether an exchange belongs to another owner.
                raise KeyError(exchange)
            return copy.deepcopy(self._records[exchange])


def _identifier(value: Any, label: str) -> str:
    if type(value) is not str or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")
    return value


def _safe_usage(usage: dict[str, Any] | None) -> dict[str, int | float] | None:
    if usage is None:
        return None
    if type(usage) is not dict:
        raise TypeError("usage must be a dictionary or null")
    public: dict[str, int | float] = {}
    for key in _PUBLIC_USAGE_FIELDS:
        value = usage.get(key)
        if (
            isinstance(key, str)
            and isinstance(value, (int, float))
            and not isinstance(value, bool)
            and value >= 0
        ):
            public[key] = value
    return public


__all__ = ["ModelExchangeError", "ModelExchangeStore"]
