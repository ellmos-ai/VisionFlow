from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pytest

from visionflow.budget import Budget
from visionflow.contracts import BudgetExceeded


def test_observation_budget_is_cumulative():
    budget = Budget(max_observation_seconds=300, max_frames=300, max_llm_calls=4)

    budget.consume(observation_seconds=200)
    budget.consume(observation_seconds=100)

    with pytest.raises(BudgetExceeded):
        budget.consume(observation_seconds=1)

    assert budget.remaining["observation_seconds"] == 0


def test_frame_and_model_call_budgets_are_cumulative():
    budget = Budget(max_observation_seconds=10, max_frames=2, max_llm_calls=1)

    budget.consume(frames=1, llm_calls=1)
    budget.consume(frames=1)

    with pytest.raises(BudgetExceeded):
        budget.consume(llm_calls=1)
    with pytest.raises(BudgetExceeded):
        budget.consume(frames=1)


def test_failed_multi_field_consume_is_atomic():
    budget = Budget(max_observation_seconds=10, max_frames=3, max_llm_calls=1)
    budget.consume(frames=2)
    before = budget.snapshot()

    with pytest.raises(BudgetExceeded):
        budget.consume(frames=1, observation_seconds=1, llm_calls=2)

    assert budget.snapshot() == before


@pytest.mark.parametrize("value", [-1, True, 1.5, "1", None])
def test_frame_count_requires_a_non_negative_integer(value):
    budget = Budget(max_observation_seconds=10, max_frames=5, max_llm_calls=5)

    with pytest.raises((TypeError, ValueError)):
        budget.consume(frames=value)

    assert budget.snapshot()["used"]["frames"] == 0


@pytest.mark.parametrize("value", [-1, True, 1.5, "1", None])
def test_model_call_count_requires_a_non_negative_integer(value):
    budget = Budget(max_observation_seconds=10, max_frames=5, max_llm_calls=5)

    with pytest.raises((TypeError, ValueError)):
        budget.consume(llm_calls=value)

    assert budget.snapshot()["used"]["llm_calls"] == 0


@pytest.mark.parametrize("value", [-1, float("nan"), float("inf"), True, "1", None])
def test_observation_seconds_must_be_finite_and_non_negative(value):
    budget = Budget(max_observation_seconds=10, max_frames=5, max_llm_calls=5)

    with pytest.raises((TypeError, ValueError)):
        budget.consume(observation_seconds=value)

    assert budget.snapshot()["used"]["observation_seconds"] == 0


def test_remaining_and_snapshot_report_independent_budget_state():
    budget = Budget(max_observation_seconds=8, max_frames=5, max_llm_calls=3)
    budget.consume(frames=2, observation_seconds=1.5, llm_calls=1)

    assert budget.remaining == {
        "frames": 3,
        "observation_seconds": 6.5,
        "llm_calls": 2,
    }
    snapshot = budget.snapshot()
    assert snapshot == {
        "used": {"frames": 2, "observation_seconds": 1.5, "llm_calls": 1},
        "limits": {"frames": 5, "observation_seconds": 8, "llm_calls": 3},
        "remaining": {"frames": 3, "observation_seconds": 6.5, "llm_calls": 2},
    }

    snapshot["used"]["frames"] = 99
    assert budget.snapshot()["used"]["frames"] == 2


@pytest.mark.parametrize("field", ["max_frames", "max_llm_calls"])
@pytest.mark.parametrize("value", [0, -1, True, 1.5, "5"])
def test_frame_and_model_call_limits_must_be_positive_integers(field, value):
    limits = {"max_observation_seconds": 10, "max_frames": 10, "max_llm_calls": 10}
    limits[field] = value

    with pytest.raises((TypeError, ValueError)):
        Budget(**limits)


@pytest.mark.parametrize("value", [0, -1, True, float("nan"), float("inf"), "10"])
def test_observation_limit_must_be_positive_and_finite(value):
    with pytest.raises((TypeError, ValueError)):
        Budget(max_observation_seconds=value)


def test_fractional_observation_limit_is_allowed():
    budget = Budget(max_observation_seconds=1.5, max_frames=2, max_llm_calls=1)

    budget.consume(observation_seconds=1.5)

    assert budget.remaining["observation_seconds"] == 0


def test_concurrent_consumption_never_exceeds_frame_limit():
    budget = Budget(max_observation_seconds=10, max_frames=10, max_llm_calls=10)

    def consume_one() -> bool:
        try:
            budget.consume(frames=1)
            return True
        except BudgetExceeded:
            return False

    with ThreadPoolExecutor(max_workers=16) as workers:
        successes = sum(workers.map(lambda _: consume_one(), range(100)))

    assert successes == 10
    assert budget.snapshot()["used"]["frames"] == 10
