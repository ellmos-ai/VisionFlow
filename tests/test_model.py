from __future__ import annotations

import pytest

from visionflow.budget import Budget
from visionflow.contracts import BudgetExceeded, ModelDecision
from visionflow.model import FixtureModelAdapter, validate_model_decision


def test_validate_model_decision_accepts_only_known_action_payloads():
    values = [
        {"action": "report"},
        {"action": "report", "payload": {"summary": "Two frames were observed."}},
        {"action": "request_input", "payload": {"reason": "Choose a clearer angle."}},
        {
            "action": "request_approval",
            "payload": {"action_type": "notification_draft"},
        },
        {
            "action": "observe",
            "payload": {
                "source_id": "camera-7",
                "region": [2, 3, 10, 12],
                "duration_seconds": 2,
                "sample_fps": 1,
                "question": "Did the object move?",
            },
        },
    ]

    decisions = [validate_model_decision(value) for value in values]

    assert [decision.action for decision in decisions] == [
        "report",
        "report",
        "request_input",
        "request_approval",
        "observe",
    ]
    assert decisions[-1].payload["region"] == (2.0, 3.0, 10.0, 12.0)
    assert decisions[-1].payload["duration_seconds"] == 2.0


@pytest.mark.parametrize(
    "value",
    [
        None,
        "report",
        {"action": "execute"},
        {"action": "report", "target": "https://example.invalid"},
        {"action": "report", "payload": {"tool": "motion"}},
        {"action": "request_input", "payload": {}},
        {"action": "request_input", "payload": {"reason": " ", "source": "camera-8"}},
        {"action": "request_approval", "payload": {"action_type": "email_send"}},
        {
            "action": "observe",
            "payload": {
                "source_id": "camera-7",
                "region": None,
                "duration_seconds": 2,
                "sample_fps": 1,
                "question": "inspect",
                "tool": "motion",
            },
        },
        {
            "action": "observe",
            "payload": {
                "source_id": "camera-7",
                "region": None,
                "duration_seconds": True,
                "sample_fps": 1,
                "question": "inspect",
            },
        },
    ],
)
def test_validate_model_decision_rejects_unknown_or_malformed_output(value):
    with pytest.raises(ValueError):
        validate_model_decision(value)


@pytest.mark.parametrize("region", [[-1, 0, 1, 1], [0, 0, 0, 1], [0, 0, 1, float("nan")]])
def test_validate_model_decision_rejects_invalid_observation_regions(region):
    with pytest.raises(ValueError):
        validate_model_decision(
            {
                "action": "observe",
                "payload": {
                    "source_id": "camera-7",
                    "region": region,
                    "duration_seconds": 1,
                    "sample_fps": 1,
                    "question": "inspect",
                },
            }
        )


def test_fixture_model_adapter_is_offline_controlled_copied_and_defaults_to_report():
    supplied = [{"action": "report", "payload": {"summary": "fixture result"}}]
    adapter = FixtureModelAdapter(supplied)
    supplied[0]["payload"]["summary"] = "mutated after construction"

    first = adapter.ask("question", [])
    first.payload["summary"] = "mutated after return"
    second = adapter.ask("question", [])

    assert adapter.MODE == "fixture"
    assert adapter.mode == "fixture"
    assert first.action == "report"
    assert second == ModelDecision("report", {})
    assert adapter.ask("question", []) == ModelDecision("report", {})


def test_fixture_model_adapter_rejects_invalid_decisions_before_use():
    with pytest.raises(ValueError):
        FixtureModelAdapter([{"action": "launch"}])


def test_fifth_model_call_is_blocked_by_the_cumulative_run_budget():
    budget = Budget(max_llm_calls=4)
    for _ in range(4):
        budget.consume(llm_calls=1)

    with pytest.raises(BudgetExceeded):
        budget.consume(llm_calls=1)

    assert budget.remaining["llm_calls"] == 0
