"""Owner-scoped, in-memory recipient for road-review demo messages.

This module deliberately has no transport, network client, or persistence
adapter. Its only authority is the local demonstration inbox.
"""

from __future__ import annotations

import copy
import math
import re
import secrets
import threading
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from visionflow.recording import ClipRef
    from visionflow.video_model import VideoAssessment


_CONTENT_HASH = re.compile(r"sha256:[0-9a-f]{64}\Z", re.ASCII)
_CLIP_FIELDS = (
    "clip_id",
    "source_id",
    "version",
    "sha256",
    "start_seconds",
    "end_seconds",
    "frame_ids",
    "source_fps",
    "available_preroll",
)
_ASSESSMENT_FIELDS = (
    "object_class",
    "hazard",
    "confidence",
    "summary",
    "evidence_frame_ids",
    "decision",
)


class InboxConflict(RuntimeError):
    """A local case already has a different report or correction."""

    code = "inbox_conflict"


def _clean_json(value: Any, *, private_path: str | None = None) -> Any:
    """Copy JSON values while dropping path-bearing keys and local path text."""
    if isinstance(value, Mapping):
        cleaned: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str) or "path" in key.casefold():
                continue
            cleaned[key] = _clean_json(item, private_path=private_path)
        return cleaned
    if isinstance(value, (list, tuple)):
        return [_clean_json(item, private_path=private_path) for item in value]
    if isinstance(value, str):
        if private_path and private_path in value:
            return value.replace(private_path, "[lokaler Pfad entfernt]")
        return value
    if isinstance(value, bool) or value is None or isinstance(value, int):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    return None


def _read_field(value: Any, name: str, source: Mapping[str, Any] | None) -> Any:
    if source is not None and name in source:
        return source[name]
    if isinstance(value, Mapping):
        return value.get(name)
    return getattr(value, name, None)


def _clip_public_metadata(clip: ClipRef | None) -> dict[str, Any] | None:
    """Serialize the public ClipRef fields and never expose its local path."""
    if clip is None:
        return None
    private_path = str(getattr(clip, "path", "")) or None
    source: Mapping[str, Any] | None = None
    try:
        from .video_model import clip_public_metadata
    except ModuleNotFoundError as error:
        if error.name != "visionflow.video_model":
            raise
    else:
        try:
            candidate = clip_public_metadata(clip)
        except TypeError:
            candidate = None
        if isinstance(candidate, Mapping):
            source = candidate

    if source is not None:
        fields = {
            name: _clean_json(source[name], private_path=private_path)
            for name in _CLIP_FIELDS
            if name in source
        }
    else:
        fields = {
            name: _clean_json(
                getattr(clip, name, None),
                private_path=private_path,
            )
            for name in _CLIP_FIELDS
            if hasattr(clip, name)
        }
    if "frame_ids" in fields and fields["frame_ids"] is None:
        fields["frame_ids"] = []
    return fields or None


def _assessment_public_metadata(
    assessment: VideoAssessment | Mapping[str, Any] | None,
    *,
    private_path: str | None = None,
) -> dict[str, Any] | None:
    """Return only the stable VideoAssessment contract as detached JSON."""
    if assessment is None:
        return None

    source: Mapping[str, Any] | None = None
    to_dict = getattr(assessment, "to_dict", None)
    if callable(to_dict):
        candidate = to_dict()
        if isinstance(candidate, Mapping):
            source = candidate
    elif isinstance(assessment, Mapping):
        source = assessment

    result: dict[str, Any] = {}
    for name in _ASSESSMENT_FIELDS:
        value = _read_field(assessment, name, source)
        if name == "evidence_frame_ids":
            if isinstance(value, (list, tuple)):
                result[name] = [
                    item for item in value if isinstance(item, str) and item
                ]
            else:
                result[name] = []
        elif name == "decision":
            if value is None:
                result[name] = None
                continue
            action = _read_field(value, "action", None)
            payload = _read_field(value, "payload", None)
            result[name] = {
                "action": action if isinstance(action, str) else "",
                "payload": _clean_json(
                    payload if isinstance(payload, Mapping) else {},
                    private_path=private_path,
                ),
            }
        elif name == "object_class":
            result[name] = (
                value
                if value in {"vehicle", "bird", "leaf", "unknown"}
                else "unknown"
            )
        elif name == "hazard":
            result[name] = value if isinstance(value, bool) else None
        elif name == "confidence":
            if isinstance(value, bool):
                result[name] = value
            elif isinstance(value, (int, float)) and math.isfinite(float(value)):
                result[name] = value
            else:
                result[name] = None
        elif name == "summary":
            result[name] = (
                _clean_json(value, private_path=private_path)
                if isinstance(value, str)
                else ""
            )

    return result


def _required_text(value: Any, name: str) -> str:
    if (
        not isinstance(value, str)
        or not value.strip()
        or len(value) > 512
        or "\x00" in value
        or any(ord(character) < 32 for character in value)
    ):
        raise ValueError(f"{name} must be a non-empty safe identifier")
    return value


class SimulatedAuthorityInbox:
    """Thread-safe local inbox for one first report and one linked correction."""

    def __init__(self) -> None:
        self._records: dict[tuple[str, str], list[dict[str, Any]]] = {}
        self._lock = threading.RLock()

    def first_report(
        self,
        owner_id: str,
        case_id: str,
        content_hash: str,
        reason: Literal["high_confidence", "human_confirmed", "no_response"],
        clip: ClipRef | None,
        assessment: VideoAssessment | None,
    ) -> dict[str, Any]:
        owner_id = _required_text(owner_id, "owner_id")
        case_id = _required_text(case_id, "case_id")
        if not isinstance(content_hash, str) or not _CONTENT_HASH.fullmatch(content_hash):
            raise ValueError("content_hash must be a sha256 digest")
        if reason not in {"high_confidence", "human_confirmed", "no_response"}:
            raise ValueError("reason is not supported")

        clip_data = _clip_public_metadata(clip)
        private_path = str(getattr(clip, "path", "")) or None
        assessment_data = _assessment_public_metadata(
            assessment,
            private_path=private_path,
        )
        key = (owner_id, case_id)
        with self._lock:
            existing = self._records.get(key, [])
            if existing:
                first = existing[0]
                same_payload = (
                    first["content_hash"] == content_hash
                    and first["reason"] == reason
                    and first["clip"] == clip_data
                    and first["assessment"] == assessment_data
                )
                if not same_payload:
                    raise InboxConflict("case already has a different first report")
                return copy.deepcopy(first)

            record = {
                "kind": "first_report",
                "receipt_id": "report_" + secrets.token_urlsafe(18),
                "case_id": case_id,
                "content_hash": content_hash,
                "reason": reason,
                "human_confirmed": reason == "human_confirmed",
                "clip": clip_data,
                "assessment": assessment_data,
            }
            self._records[key] = [record]
            return copy.deepcopy(record)

    def correction(
        self,
        owner_id: str,
        case_id: str,
        original_receipt_id: str,
        content_hash: str,
    ) -> dict[str, Any]:
        owner_id = _required_text(owner_id, "owner_id")
        case_id = _required_text(case_id, "case_id")
        original_receipt_id = _required_text(original_receipt_id, "original_receipt_id")
        if not isinstance(content_hash, str) or not _CONTENT_HASH.fullmatch(content_hash):
            raise ValueError("content_hash must be a sha256 digest")

        key = (owner_id, case_id)
        with self._lock:
            records = self._records.get(key)
            if not records:
                raise InboxConflict("a correction requires an existing first report")
            first = records[0]
            if (
                first["receipt_id"] != original_receipt_id
                or first["content_hash"] != content_hash
            ):
                raise InboxConflict("correction does not match the first report")
            if len(records) > 1:
                correction = records[1]
                if (
                    correction["original_receipt_id"] != original_receipt_id
                    or correction["content_hash"] != content_hash
                ):
                    raise InboxConflict("case already has a different correction")
                return copy.deepcopy(correction)

            correction = {
                "kind": "correction",
                "receipt_id": "correction_" + secrets.token_urlsafe(18),
                "case_id": case_id,
                "content_hash": content_hash,
                "original_receipt_id": original_receipt_id,
                "human_confirmed": True,
            }
            records.append(correction)
            return copy.deepcopy(correction)

    def forget_case(self, owner_id: str, case_id: str) -> None:
        with self._lock:
            self._records.pop((owner_id, case_id), None)

    def list(self, owner_id: str) -> list[dict[str, Any]]:
        """Return detached records belonging only to the requested owner."""
        owner_id = _required_text(owner_id, "owner_id")
        with self._lock:
            return copy.deepcopy(
                [
                    record
                    for (record_owner, _case_id), records in self._records.items()
                    if record_owner == owner_id
                    for record in records
                ]
            )


__all__ = ["InboxConflict", "SimulatedAuthorityInbox"]
