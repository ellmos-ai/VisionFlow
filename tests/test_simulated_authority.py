from __future__ import annotations

import hashlib
import importlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from visionflow.contracts import ModelDecision
from visionflow.recording import ClipRef


@dataclass(frozen=True)
class _AssessmentFixture:
    object_class: str = "vehicle"
    hazard: bool | None = True
    confidence: float | None = 0.70
    summary: str = "Fahrzeug entgegen der erlaubten Richtung."
    evidence_frame_ids: tuple[str, ...] = ("frame-1",)
    decision: ModelDecision = field(default_factory=lambda: ModelDecision(action="report", payload={}))

    def to_dict(self) -> dict[str, Any]:
        return {
            "object_class": self.object_class,
            "hazard": self.hazard,
            "confidence": self.confidence,
            "summary": self.summary,
            "evidence_frame_ids": list(self.evidence_frame_ids),
            "decision": {"action": self.decision.action, "payload": dict(self.decision.payload)},
        }


def _require_inbox_module():
    try:
        return importlib.import_module("visionflow.simulated_authority")
    except ModuleNotFoundError as error:
        if error.name == "visionflow.simulated_authority":
            pytest.fail(
                "SimulatedAuthorityInbox feature module is missing; this is an implementation RED, not a collection error.",
                pytrace=False,
            )
        raise


def _clip(tmp_path: Path) -> ClipRef:
    payload = b"private local clip path must never reach the simulated inbox"
    path = tmp_path / "local-only.mp4"
    path.write_bytes(payload)
    return ClipRef(
        clip_id="clip-local",
        source_id="camera-local",
        version=1,
        path=path,
        sha256=hashlib.sha256(payload).hexdigest(),
        start_seconds=0.0,
        end_seconds=10.0,
        frame_ids=("frame-1",),
        source_fps=20.0,
        available_preroll=5.0,
    )


def _assessment() -> Any:
    try:
        module = importlib.import_module("visionflow.video_model")
    except ModuleNotFoundError as error:
        if error.name != "visionflow.video_model":
            raise
        return _AssessmentFixture()
    assessment_type = getattr(module, "VideoAssessment", None)
    if assessment_type is None:
        return _AssessmentFixture()
    return assessment_type(
        object_class="vehicle",
        hazard=True,
        confidence=0.70,
        summary="Fahrzeug entgegen der erlaubten Richtung.",
        evidence_frame_ids=("frame-1",),
        decision=ModelDecision(action="report", payload={}),
    )


def test_first_report_is_local_owner_scoped_and_redacts_private_clip_path(tmp_path):
    module = _require_inbox_module()
    inbox = module.SimulatedAuthorityInbox()
    clip = _clip(tmp_path)
    assessment = _assessment()
    report = inbox.first_report(
        "alice", "case-1", "sha256:" + "a" * 64, "human_confirmed", clip, assessment
    )

    assert report["kind"] == "first_report"
    assert report["human_confirmed"] is True
    assert inbox.list("bob") == []
    assert len(inbox.list("alice")) == 1
    assert str(clip.path) not in repr(inbox.list("alice"))
    assert "path" not in report["clip"]
    try:
        video_model = importlib.import_module("visionflow.video_model")
    except ModuleNotFoundError as error:
        if error.name != "visionflow.video_model":
            raise
        expected_clip = {
            name: getattr(clip, name)
            for name in (
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
        }
        expected_clip["frame_ids"] = list(expected_clip["frame_ids"])
    else:
        expected_clip = video_model.clip_public_metadata(clip)
    assert report["clip"] == expected_clip


def test_first_report_is_idempotent_and_conflicting_duplicate_is_rejected(tmp_path):
    module = _require_inbox_module()
    inbox = module.SimulatedAuthorityInbox()
    clip = _clip(tmp_path)
    assessment = _assessment()
    args = ("alice", "case-1", "sha256:" + "b" * 64, "no_response", clip, assessment)

    first = inbox.first_report(*args)
    duplicate = inbox.first_report(*args)

    assert duplicate == first
    assert len(inbox.list("alice")) == 1
    with pytest.raises(module.InboxConflict):
        inbox.first_report("alice", "case-1", args[2], "human_confirmed", clip, assessment)


def test_correction_is_linked_to_one_first_report_and_can_only_exist_once(tmp_path):
    module = _require_inbox_module()
    inbox = module.SimulatedAuthorityInbox()
    first = inbox.first_report(
        "alice", "case-1", "sha256:" + "c" * 64, "no_response", _clip(tmp_path), _assessment()
    )

    correction = inbox.correction(
        "alice", "case-1", first["receipt_id"], first["content_hash"]
    )
    duplicate = inbox.correction(
        "alice", "case-1", first["receipt_id"], first["content_hash"]
    )

    assert correction["kind"] == "correction"
    assert correction["original_receipt_id"] == first["receipt_id"]
    assert duplicate == correction
    assert [message["kind"] for message in inbox.list("alice")] == ["first_report", "correction"]


def test_inbox_returns_detached_records_and_does_not_share_other_owners(tmp_path):
    module = _require_inbox_module()
    inbox = module.SimulatedAuthorityInbox()
    first = inbox.first_report(
        "alice", "case-1", "sha256:" + "d" * 64, "human_confirmed", _clip(tmp_path), _assessment()
    )

    first["assessment"]["summary"] = "tampered"
    first["clip"]["frame_ids"].clear()

    fresh = inbox.list("alice")
    assert fresh[0]["assessment"]["summary"] != "tampered"
    assert fresh[0]["clip"]["frame_ids"] == ["frame-1"]
    assert inbox.list("bob") == []
