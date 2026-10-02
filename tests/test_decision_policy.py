from __future__ import annotations

import hashlib
import importlib
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from visionflow.contracts import ModelDecision
from visionflow.recording import ClipRef


_DEFAULT = object()


@dataclass(frozen=True)
class _AssessmentFixture:
    object_class: str
    hazard: bool | None
    confidence: float | None
    summary: str
    evidence_frame_ids: tuple[str, ...]
    decision: ModelDecision

    def to_dict(self) -> dict[str, Any]:
        return {
            "object_class": self.object_class,
            "hazard": self.hazard,
            "confidence": self.confidence,
            "summary": self.summary,
            "evidence_frame_ids": list(self.evidence_frame_ids),
            "decision": {
                "action": self.decision.action,
                "payload": dict(self.decision.payload),
            },
        }


class FakeClock:
    def __init__(self) -> None:
        self._monotonic = 40.0
        self._utc = datetime(2026, 10, 2, 12, 0, tzinfo=timezone(timedelta(hours=2)))

    def monotonic(self) -> float:
        return self._monotonic

    def utcnow(self) -> datetime:
        return self._utc

    def advance(self, seconds: float) -> None:
        self._monotonic += seconds
        self._utc += timedelta(seconds=seconds)


def _require_decision_module():
    try:
        return importlib.import_module("visionflow.decision_policy")
    except ModuleNotFoundError as error:
        if error.name == "visionflow.decision_policy":
            pytest.fail(
                "DecisionPolicy feature module is missing; this is an implementation RED, not a collection error.",
                pytrace=False,
            )
        raise


def _assessment(
    *,
    object_class: str = "vehicle",
    hazard: bool | None = True,
    confidence: float | None = 0.70,
    evidence_frame_ids: tuple[str, ...] = ("frame-1",),
) -> Any:
    values = {
        "object_class": object_class,
        "hazard": hazard,
        "confidence": confidence,
        "summary": "Fahrzeug entgegen der erlaubten Richtung.",
        "evidence_frame_ids": evidence_frame_ids,
        "decision": ModelDecision(action="report", payload={}),
    }
    try:
        module = importlib.import_module("visionflow.video_model")
    except ModuleNotFoundError as error:
        if error.name != "visionflow.video_model":
            raise
        return _AssessmentFixture(**values)
    assessment_type = getattr(module, "VideoAssessment", None)
    if assessment_type is None:
        return _AssessmentFixture(**values)
    return assessment_type(**values)


def _real_clip(tmp_path: Path, *, frame_ids: tuple[str, ...] = ("frame-1",)) -> ClipRef:
    payload = b"immutable local clip bytes for decision policy tests"
    path = tmp_path / "clip.mp4"
    path.write_bytes(payload)
    return ClipRef(
        clip_id="clip-1",
        source_id="camera-1",
        version=3,
        path=path,
        sha256=hashlib.sha256(payload).hexdigest(),
        start_seconds=8.0,
        end_seconds=18.0,
        frame_ids=frame_ids,
        source_fps=20.0,
        available_preroll=5.0,
    )


def _fixture(tmp_path: Path, **policy_options: Any):
    module = _require_decision_module()
    clock = FakeClock()
    inbox = module.SimulatedAuthorityInbox()
    policy = module.DecisionPolicy(**policy_options)
    store = module.DecisionStore(policy=policy, inbox=inbox, clock=clock)
    return clock, store, inbox


def _open(
    tmp_path: Path,
    *,
    assessment: Any = _DEFAULT,
    clip: Any = _DEFAULT,
    cv_confirmed: bool = True,
    model_error: str | None = None,
    case_id: str = "case-1",
    run_id: str = "run-1",
    source_id: str = "camera-1",
    policy_options: dict[str, Any] | None = None,
):
    clock, store, inbox = _fixture(tmp_path, **(policy_options or {}))
    actual_clip = _real_clip(tmp_path) if clip is _DEFAULT else clip
    actual_assessment = _assessment() if assessment is _DEFAULT else assessment
    case = store.open_case(
        "alice",
        case_id,
        run_id,
        actual_clip,
        actual_assessment,
        cv_confirmed,
        model_error,
        source_id=source_id,
    )
    return clock, store, inbox, case


@pytest.mark.parametrize("decision", ["confirm", "reject"])
def test_human_decision_at_19_999_seconds_precedes_deadline(tmp_path, decision):
    clock, store, inbox, case = _open(
        tmp_path,
        assessment=_assessment(confidence=0.70),
    )
    clock.advance(19.999)

    result = store.resolve(
        "alice", case["case_id"], case["version"], case["content_hash"], decision, f"key-{decision}"
    )

    if decision == "confirm":
        assert result["phase"] == "reported"
        assert inbox.list("alice")[0]["reason"] == "human_confirmed"
        assert inbox.list("alice")[0]["human_confirmed"] is True
    else:
        assert result["phase"] == "rejected"
        assert inbox.list("alice") == []


def test_deadline_wins_and_late_rejection_is_one_correction(tmp_path):
    clock, store, inbox, case = _open(
        tmp_path,
        assessment=_assessment(confidence=0.70),
    )
    clock.advance(20.0)

    store.resolve("alice", "case-1", case["version"], case["content_hash"], "reject", "reject-1")
    store.resolve("alice", "case-1", case["version"], case["content_hash"], "reject", "reject-1")

    messages = inbox.list("alice")
    assert [message["kind"] for message in messages] == ["first_report", "correction"]
    assert messages[0]["reason"] == "no_response"
    assert messages[0]["human_confirmed"] is False
    assert messages[1]["original_receipt_id"] == messages[0]["receipt_id"]
    assert messages[1]["content_hash"] == case["content_hash"]


def test_exact_deadline_confirm_is_annotation_not_human_confirmation(tmp_path):
    clock, store, inbox, case = _open(
        tmp_path,
        assessment=_assessment(confidence=0.70),
    )
    clock.advance(20.0)

    result = store.resolve("alice", "case-1", case["version"], case["content_hash"], "confirm", "late-confirm")

    assert [message["reason"] for message in inbox.list("alice")] == ["no_response"]
    assert inbox.list("alice")[0]["human_confirmed"] is False
    assert result["phase"] == "no_response"
    assert any(entry["event"] == "late_confirm_annotation" for entry in result["journal"])


def test_deadline_tick_sends_no_response_without_a_snapshot_read(tmp_path):
    clock, store, inbox, _case = _open(
        tmp_path,
        assessment=_assessment(confidence=0.70),
    )
    clock.advance(20.0)

    store.tick()

    assert len(inbox.list("alice")) == 1
    assert inbox.list("alice")[0]["reason"] == "no_response"
    assert inbox.list("alice")[0]["human_confirmed"] is False


def test_high_confidence_requires_vehicle_hazard_cv_valid_clip_and_matching_evidence(tmp_path):
    _clock, store, inbox, case = _open(
        tmp_path,
        assessment=_assessment(confidence=0.90),
        policy_options={"confidence_threshold": 0.90},
    )

    assert case["phase"] == "reported"
    assert case["deadline_at"] is None
    assert [message["reason"] for message in inbox.list("alice")] == ["high_confidence"]
    assert inbox.list("alice")[0]["human_confirmed"] is False


@pytest.mark.parametrize(
    ("assessment_options", "clip_mode", "cv_confirmed"),
    [
        ({"object_class": "bird", "confidence": 0.99}, "valid", True),
        ({"object_class": "leaf", "confidence": 0.99}, "valid", True),
        ({"object_class": "vehicle", "confidence": 0.99}, "valid", False),
        ({"object_class": "vehicle", "confidence": 0.99}, "missing", True),
        ({"object_class": "vehicle", "confidence": 0.99}, "missing_file", True),
        ({"object_class": "vehicle", "confidence": 0.99, "evidence_frame_ids": ()}, "valid", True),
        (
            {"object_class": "vehicle", "confidence": 0.99, "evidence_frame_ids": ("foreign-frame",)},
            "valid",
            True,
        ),
        ({"object_class": "vehicle", "confidence": 0.899}, "valid", True),
        ({"object_class": "unknown", "confidence": 0.99}, "valid", True),
    ],
)
def test_nonqualifying_high_score_stays_in_human_review(
    tmp_path, assessment_options, clip_mode, cv_confirmed
):
    clip = _real_clip(tmp_path) if clip_mode == "valid" else None
    if clip_mode == "missing_file":
        clip = _real_clip(tmp_path)
        clip.path.unlink()
    if assessment_options.get("evidence_frame_ids", ("frame-1",)):
        assessment = _assessment(**assessment_options)
    else:
        assessment = _AssessmentFixture(
            object_class=assessment_options.get("object_class", "vehicle"),
            hazard=assessment_options.get("hazard", True),
            confidence=assessment_options.get("confidence", 0.70),
            summary="Fahrzeug ohne Framebeleg.",
            evidence_frame_ids=(),
            decision=ModelDecision(action="report", payload={}),
        )

    _clock, _store, inbox, case = _open(
        tmp_path,
        assessment=assessment,
        clip=clip,
        cv_confirmed=cv_confirmed,
    )

    assert case["phase"] == "human_review"
    assert case["deadline_at"].endswith("Z")
    assert case["remaining_seconds"] == pytest.approx(20.0)
    assert inbox.list("alice") == []


def test_boolean_confidence_is_not_high_confidence(tmp_path):
    _clock, _store, inbox, case = _open(
        tmp_path,
        assessment=_assessment_fixture_with_confidence(True),
    )

    assert case["phase"] == "human_review"
    assert inbox.list("alice") == []


def test_out_of_range_confidence_is_not_high_confidence(tmp_path):
    _clock, _store, inbox, case = _open(
        tmp_path,
        assessment=_assessment_fixture_with_confidence(1.2),
    )

    assert case["phase"] == "human_review"
    assert case["remaining_seconds"] == pytest.approx(20.0)
    assert inbox.list("alice") == []


def _assessment_fixture_with_confidence(confidence: Any) -> _AssessmentFixture:
    return _AssessmentFixture(
        object_class="vehicle",
        hazard=True,
        confidence=confidence,
        summary="Bewertung mit ungültigem Scoretyp.",
        evidence_frame_ids=("frame-1",),
        decision=ModelDecision(action="report", payload={}),
    )


def test_hazard_false_dismisses_without_a_countdown_or_report(tmp_path):
    _clock, _store, inbox, case = _open(
        tmp_path,
        assessment=_assessment(object_class="vehicle", hazard=False, confidence=0.99),
    )

    assert case["phase"] == "dismissed"
    assert case["deadline_at"] is None
    assert case["remaining_seconds"] == 0
    assert inbox.list("alice") == []


def test_unknown_assessment_with_hazard_false_dismisses_without_countdown(tmp_path):
    _clock, _store, inbox, case = _open(
        tmp_path,
        assessment=_assessment(object_class="unknown", hazard=False, confidence=0.99),
    )

    assert case["phase"] == "dismissed"
    assert case["deadline_at"] is None
    assert case["remaining_seconds"] == 0
    assert inbox.list("alice") == []


def test_unknown_assessment_with_unclear_hazard_still_requires_human_review(tmp_path):
    _clock, _store, inbox, case = _open(
        tmp_path,
        assessment=_assessment(object_class="unknown", hazard=None, confidence=None),
    )

    assert case["phase"] == "human_review"
    assert case["deadline_at"] is not None
    assert inbox.list("alice") == []


def test_model_error_without_assessment_starts_human_review(tmp_path):
    _clock, _store, inbox, case = _open(
        tmp_path,
        assessment=None,
        model_error="model_timeout",
    )

    assert case["phase"] == "human_review"
    assert case["model_error"] == "model_timeout"
    assert case["remaining_seconds"] == pytest.approx(20.0)
    assert inbox.list("alice") == []


def test_model_error_takes_priority_over_hazard_false(tmp_path):
    _clock, _store, inbox, case = _open(
        tmp_path,
        assessment=_assessment(object_class="vehicle", hazard=False, confidence=0.99),
        model_error="model_timeout",
    )

    assert case["phase"] == "human_review"
    assert case["model_error"] == "model_timeout"
    assert inbox.list("alice") == []


def test_untrusted_model_error_is_replaced_without_publicly_echoing_it(tmp_path):
    private_value = "sk_test_do_not_publish_123"
    private_path = tmp_path / "private" / "provider-credential.json"
    raw_error = f"provider failed with {private_value} at {private_path}"
    _clock, _store, inbox, case = _open(
        tmp_path,
        assessment=_assessment(object_class="vehicle", hazard=False, confidence=0.99),
        model_error=raw_error,
    )

    if case["model_error"] != "model_error":
        pytest.fail("unsafe model_error was exposed", pytrace=False)
    assert case["phase"] == "human_review"
    assert inbox.list("alice") == []


def test_clip_assessment_run_and_source_are_bound_into_the_content_hash(tmp_path):
    _clock, store, _inbox, first = _open(tmp_path, assessment=_assessment(confidence=0.70))
    second_clip = _real_clip(tmp_path)
    second = store.open_case(
        "alice",
        "case-2",
        "run-1",
        second_clip,
        _assessment(confidence=0.71),
        True,
        source_id="camera-2",
    )

    assert first["content_hash"].startswith("sha256:")
    assert first["content_hash"] != second["content_hash"]


@pytest.mark.parametrize("stale_field", ["version", "content_hash"])
def test_stale_version_or_hash_is_rejected_without_side_effects(tmp_path, stale_field):
    _clock, store, inbox, case = _open(tmp_path, assessment=_assessment(confidence=0.70))
    version = case["version"] - 1 if stale_field == "version" else case["version"]
    content_hash = "sha256:" + "0" * 64 if stale_field == "content_hash" else case["content_hash"]
    conflict_type = _require_decision_module().DecisionConflict

    with pytest.raises(conflict_type):
        store.resolve("alice", "case-1", version, content_hash, "confirm", "stale")

    assert inbox.list("alice") == []


def test_owner_is_checked_before_an_idempotency_key_can_be_replayed(tmp_path):
    _clock, store, _inbox, case = _open(tmp_path, assessment=_assessment(confidence=0.70))
    store.resolve("alice", "case-1", case["version"], case["content_hash"], "confirm", "owned-key")
    not_found_type = _require_decision_module().DecisionNotFound

    with pytest.raises(not_found_type):
        store.resolve(
            "bob", "case-1", case["version"], case["content_hash"], "confirm", "owned-key"
        )


def test_identical_idempotency_retry_precedes_stale_binding_and_returns_same_result(tmp_path):
    _clock, store, inbox, case = _open(tmp_path, assessment=_assessment(confidence=0.70))
    args = ("alice", "case-1", case["version"], case["content_hash"], "confirm", "confirm-1")

    first = store.resolve(*args)
    retry = store.resolve(*args)

    assert retry == first
    assert len(inbox.list("alice")) == 1
    assert inbox.list("alice")[0]["reason"] == "human_confirmed"


def test_same_idempotency_key_with_a_different_body_is_a_conflict_before_stale_check(tmp_path):
    _clock, store, _inbox, case = _open(tmp_path, assessment=_assessment(confidence=0.70))
    store.resolve("alice", "case-1", case["version"], case["content_hash"], "reject", "key-1")
    conflict_type = _require_decision_module().DecisionConflict

    with pytest.raises(conflict_type) as caught:
        store.resolve("alice", "case-1", case["version"], case["content_hash"], "confirm", "key-1")

    assert caught.value.code == "idempotency_conflict"


def test_cancel_stops_only_an_open_deadline_and_preserves_case_state(tmp_path):
    _clock, store, inbox, case = _open(tmp_path, assessment=_assessment(confidence=0.70))

    cancelled = store.cancel("alice", "case-1")

    assert cancelled["phase"] == "cancelled"
    assert cancelled["deadline_at"] is None
    assert cancelled["remaining_seconds"] == 0
    assert inbox.list("alice") == []
    with pytest.raises(_require_decision_module().DecisionConflict):
        store.resolve("alice", "case-1", cancelled["version"], case["content_hash"], "confirm", "after-cancel")


def test_cancel_does_not_hide_a_forwarded_case_from_late_entwarnung(tmp_path):
    _clock, store, inbox, case = _open(
        tmp_path,
        assessment=_assessment(confidence=0.99),
    )
    forwarded = store.cancel("alice", "case-1")
    assert forwarded["phase"] == "reported"
    assert forwarded["first_receipt"]["reason"] == "high_confidence"

    corrected = store.resolve(
        "alice",
        "case-1",
        forwarded["version"],
        forwarded["content_hash"],
        "reject",
        "late-correction",
    )

    assert corrected["phase"] == "corrected"
    assert len(inbox.list("alice")) == 2
    assert inbox.list("alice")[1]["kind"] == "correction"


def test_public_case_is_detached_and_never_contains_the_clip_path(tmp_path):
    _clock, store, _inbox, case = _open(tmp_path, assessment=_assessment(confidence=0.70))
    private_path = str(tmp_path / "clip.mp4")
    serialized = json.dumps(case, ensure_ascii=False)

    assert private_path not in serialized
    assert "path" not in case["clip"]
    case["assessment"]["summary"] = "verändert"
    case["journal"].clear()
    snapshot = store.snapshot("alice", "case-1")
    assert snapshot["assessment"]["summary"] != "verändert"
    assert snapshot["journal"]


def test_public_case_contains_the_required_fields_and_utc_deadline(tmp_path):
    _clock, store, _inbox, case = _open(tmp_path, assessment=_assessment(confidence=0.70))

    assert {
        "case_id",
        "run_id",
        "source_id",
        "version",
        "content_hash",
        "phase",
        "deadline_at",
        "remaining_seconds",
        "assessment",
        "model_error",
        "clip",
        "journal",
        "first_receipt",
        "correction_receipt",
    } <= set(case)
    assert case["deadline_at"] == "2026-10-02T10:00:20Z"


def test_two_resolvers_and_tick_at_the_deadline_create_at_most_one_report_and_correction(tmp_path):
    clock, store, inbox, case = _open(tmp_path, assessment=_assessment(confidence=0.70))
    clock.advance(20.0)
    barrier = threading.Barrier(4)

    def resolve(decision: str, key: str):
        barrier.wait()
        try:
            return store.resolve(
                "alice", "case-1", case["version"], case["content_hash"], decision, key
            )
        except _require_decision_module().DecisionConflict:
            return None

    def tick():
        barrier.wait()
        store.tick()

    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = [
            pool.submit(resolve, "confirm", "parallel-confirm"),
            pool.submit(resolve, "reject", "parallel-reject"),
            pool.submit(tick),
        ]
        barrier.wait()
        [future.result() for future in futures]

    messages = inbox.list("alice")
    assert sum(message["kind"] == "first_report" for message in messages) == 1
    assert sum(message["kind"] == "correction" for message in messages) <= 1
    assert messages[0]["reason"] == "no_response"
    assert messages[0]["human_confirmed"] is False
