import importlib
from datetime import datetime, timedelta, timezone

import pytest

from visionflow.contracts import Finding, RunResult


def _production_symbol(module_name, symbol_name):
    try:
        module = importlib.import_module(module_name)
    except ModuleNotFoundError as exc:
        if exc.name == module_name:
            pytest.fail(f"{module_name}.{symbol_name} is not implemented", pytrace=False)
        raise

    symbol = getattr(module, symbol_name, None)
    if symbol is None:
        pytest.fail(f"{module_name}.{symbol_name} is not implemented", pytrace=False)
    return symbol


def _result(*, trace=None, outputs=None, findings=None):
    return RunResult(
        run_id="run-42",
        status="completed",
        findings=findings or [],
        trace=trace or [],
        outputs=outputs or [],
    )


def test_report_uses_real_findings_trace_evidence_and_fixture_mode():
    finding = Finding(
        tool="quality",
        frame_ids=["frame-7"],
        region=(0.1, 0.2, 0.7, 0.8),
        metrics={"sharpness_variance": 18.25, "brightness_mean": 104.0},
        evidence_ref="evidence/run-42/frame-7.bin",
    )
    result = _result(
        trace=[
            {"type": "analysis_completed", "evidence_ref": finding.evidence_ref},
            {"type": "model_mode", "mode": "fixture"},
        ],
        outputs=[{"type": "display", "payload": {"findings": 1}}],
        findings=[finding],
    )

    report = _production_symbol("visionflow.outputs", "render_report")(result)

    for expected in (
        "run-42",
        "completed",
        "quality",
        "frame-7",
        "18.25",
        "104.0",
        finding.evidence_ref,
        "analysis_completed",
        "Fixture",
        "Grenzen",
        '"findings": 1',
    ):
        assert expected in report


def test_report_does_not_invent_findings_or_model_mode():
    report = _production_symbol("visionflow.outputs", "render_report")(_result())

    assert "Keine Befunde gespeichert" in report
    assert "nicht protokolliert" in report
    assert "Fixturemodus" not in report


def test_report_rejects_trace_prompt_data_instead_of_exposing_it():
    result = _result(trace=[{"type": "model_request", "prompt": "private question"}])

    render_report = _production_symbol("visionflow.outputs", "render_report")
    with pytest.raises(ValueError):
        render_report(result)


def test_notification_is_a_draft_bound_to_the_run_and_without_unconfigured_target():
    result = _result(outputs=[{"type": "notification_draft", "destination": "untrusted-target"}])
    before = datetime.now(timezone.utc)

    proposal = _production_symbol("visionflow.outputs", "prepare_notification")(result)

    after = datetime.now(timezone.utc)
    from visionflow.contracts import ActionProposal

    assert isinstance(proposal, ActionProposal)
    assert proposal.type == "notification_draft"
    assert proposal.run_id == result.run_id
    assert proposal.sent is False
    assert proposal.destination is None
    assert proposal.action_id
    assert proposal.content_hash.startswith("sha256:")
    assert proposal.expires_at.tzinfo is not None
    assert before + timedelta(minutes=14) <= proposal.expires_at <= after + timedelta(minutes=16)
    assert "run-42" in proposal.content


def test_notification_drafts_use_distinct_action_ids_and_hashes():
    prepare_notification = _production_symbol("visionflow.outputs", "prepare_notification")

    first = prepare_notification(_result())
    second = prepare_notification(_result())

    assert first.action_id != second.action_id
    assert first.content_hash != second.content_hash


def test_notification_rejects_a_naive_creation_time():
    prepare_notification = _production_symbol("visionflow.outputs", "prepare_notification")

    with pytest.raises(ValueError):
        prepare_notification(_result(), now=datetime(2026, 10, 2, 12, 0))
