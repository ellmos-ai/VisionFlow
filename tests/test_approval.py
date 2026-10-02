import importlib
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from visionflow.contracts import ApprovalRejected, RunResult


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


def _registered_action():
    result = RunResult(run_id="run-77", status="completed")
    proposal = _production_symbol("visionflow.outputs", "prepare_notification")(result)
    store = _production_symbol("visionflow.approval", "ApprovalStore")()
    store.register(proposal)
    return store, proposal


def test_changed_content_or_expired_approval_is_rejected():
    store, proposal = _registered_action()
    now = datetime.now(timezone.utc)

    with pytest.raises(ApprovalRejected):
        store.confirm(proposal.run_id, proposal.action_id, "changed-content", now)
    with pytest.raises(ApprovalRejected):
        store.confirm(
            proposal.run_id,
            proposal.action_id,
            proposal.content_hash,
            proposal.expires_at + timedelta(seconds=1),
        )


def test_approval_is_bound_to_the_run_id_and_action_id():
    store, proposal = _registered_action()

    with pytest.raises(ApprovalRejected):
        store.confirm("another-run", proposal.action_id, proposal.content_hash, datetime.now(timezone.utc))
    with pytest.raises(ApprovalRejected):
        store.confirm(proposal.run_id, "another-action", proposal.content_hash, datetime.now(timezone.utc))


def test_confirmation_requires_timezone_aware_time():
    store, proposal = _registered_action()

    with pytest.raises(ApprovalRejected):
        store.confirm(proposal.run_id, proposal.action_id, proposal.content_hash, datetime.now())


def test_duplicate_confirmation_returns_one_immutable_receipt():
    store, proposal = _registered_action()
    now = datetime.now(timezone.utc)

    first = store.confirm(proposal.run_id, proposal.action_id, proposal.content_hash, now)
    first.receipt_id = "tampered"
    second = store.confirm(proposal.run_id, proposal.action_id, proposal.content_hash, now)

    assert second.receipt_id != "tampered"
    assert second.run_id == proposal.run_id
    assert second.action_id == proposal.action_id
    assert second.content_hash == proposal.content_hash
    assert second.confirmed_at == now


def test_concurrent_duplicate_confirmations_share_one_receipt():
    store, proposal = _registered_action()
    now = datetime.now(timezone.utc)

    def confirm(_):
        return store.confirm(proposal.run_id, proposal.action_id, proposal.content_hash, now)

    with ThreadPoolExecutor(max_workers=8) as pool:
        receipts = list(pool.map(confirm, range(16)))

    assert len({receipt.receipt_id for receipt in receipts}) == 1


def test_rejected_proposal_cannot_be_confirmed():
    store, proposal = _registered_action()

    store.reject(proposal.run_id, proposal.action_id, datetime.now(timezone.utc))

    with pytest.raises(ApprovalRejected):
        store.confirm(
            proposal.run_id,
            proposal.action_id,
            proposal.content_hash,
            datetime.now(timezone.utc),
        )


@pytest.mark.parametrize(
    "changes",
    [
        {"content": "changed after signing"},
        {"run_id": "different-run"},
        {"action_id": "different-action"},
        {"type": "external_action"},
        {"sent": True},
        {"expires_at": datetime(2026, 10, 2, 12, 0)},
        {"destination": "unconfigured-target"},
        {"content_hash": "sha256:" + "é" * 64},
    ],
)
def test_registration_rejects_mutated_or_invalid_proposals(changes):
    proposal = _production_symbol("visionflow.outputs", "prepare_notification")(
        RunResult(run_id="run-88", status="completed")
    )
    invalid_proposal = replace(proposal, **changes)
    store = _production_symbol("visionflow.approval", "ApprovalStore")()

    with pytest.raises(ApprovalRejected):
        store.register(invalid_proposal)


def test_register_takes_a_snapshot_of_mutable_proposal():
    store, proposal = _registered_action()
    original_hash = proposal.content_hash
    proposal.content = "changed after registration"

    receipt = store.confirm(
        "run-77",
        proposal.action_id,
        original_hash,
        datetime.now(timezone.utc),
    )

    assert receipt.content_hash == original_hash
