from __future__ import annotations

import hmac
import re
import secrets
import threading
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Literal

from visionflow.contracts import (
    ActionProposal,
    ApprovalReceipt,
    ApprovalRejected,
)
from visionflow.outputs import _proposal_hash

_SHA256_RE = re.compile(r"sha256:[0-9a-f]{64}\Z", re.ASCII)


@dataclass(slots=True)
class _StoredAction:
    proposal: ActionProposal
    state: Literal["pending", "confirmed", "rejected"] = "pending"
    receipt: ApprovalReceipt | None = None
    rejected_at: datetime | None = None


def _aware_utc(value: datetime, field: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ApprovalRejected(f"{field} must be timezone-aware")
    try:
        offset = value.utcoffset()
    except Exception as exc:
        raise ApprovalRejected(f"{field} must be timezone-aware") from exc
    if offset is None:
        raise ApprovalRejected(f"{field} must be timezone-aware")
    try:
        return value.astimezone(timezone.utc)
    except (OverflowError, ValueError) as exc:
        raise ApprovalRejected(f"{field} is not a valid datetime") from exc


class ApprovalStore:
    """Thread-safe in-memory approval records; confirmation never sends."""

    def __init__(self) -> None:
        self._actions: dict[str, _StoredAction] = {}
        self._lock = threading.RLock()

    def register(self, proposal: ActionProposal) -> None:
        if not isinstance(proposal, ActionProposal):
            raise ApprovalRejected("proposal must be an ActionProposal")
        if proposal.type != "notification_draft" or proposal.sent:
            raise ApprovalRejected("only unsent notification drafts can be registered")
        if (
            not isinstance(proposal.run_id, str)
            or not proposal.run_id
            or not isinstance(proposal.action_id, str)
            or not proposal.action_id
            or not isinstance(proposal.content, str)
            or not isinstance(proposal.content_hash, str)
            or not proposal.content_hash
        ):
            raise ApprovalRejected("proposal identity, content, and hash are required")
        if not _SHA256_RE.fullmatch(proposal.content_hash):
            raise ApprovalRejected("proposal content hash must be a SHA-256 digest")
        if proposal.destination is not None:
            raise ApprovalRejected("no output destination is configured")
        expires_at = _aware_utc(proposal.expires_at, "proposal.expires_at")

        expected_hash = _proposal_hash(
            proposal_type=proposal.type,
            run_id=proposal.run_id,
            action_id=proposal.action_id,
            content=proposal.content,
            destination=proposal.destination,
        )
        if not hmac.compare_digest(proposal.content_hash, expected_hash):
            raise ApprovalRejected("proposal content hash does not match its contents")

        snapshot = replace(proposal, expires_at=expires_at)
        with self._lock:
            existing = self._actions.get(snapshot.action_id)
            if existing is not None:
                if existing.proposal == snapshot:
                    return
                raise ApprovalRejected("action_id is already registered")
            self._actions[snapshot.action_id] = _StoredAction(snapshot)

    def confirm(
        self,
        run_id: str,
        action_id: str,
        content_hash: str,
        now: datetime,
    ) -> ApprovalReceipt:
        confirmed_at = _aware_utc(now, "now")
        if not isinstance(run_id, str) or not isinstance(action_id, str):
            raise ApprovalRejected("run_id and action_id must be strings")
        if not isinstance(content_hash, str):
            raise ApprovalRejected("content_hash must be a string")
        if not _SHA256_RE.fullmatch(content_hash):
            raise ApprovalRejected("content_hash must be a SHA-256 digest")

        with self._lock:
            stored = self._actions.get(action_id)
            if stored is None or stored.proposal.run_id != run_id:
                raise ApprovalRejected("approval does not match a registered run and action")
            if not hmac.compare_digest(content_hash, stored.proposal.content_hash):
                raise ApprovalRejected("approval content hash does not match")
            if stored.state == "rejected":
                raise ApprovalRejected("action was rejected")
            if stored.state == "confirmed":
                if stored.receipt is None:
                    raise ApprovalRejected("confirmed approval has no receipt")
                return replace(stored.receipt)
            if confirmed_at >= stored.proposal.expires_at:
                raise ApprovalRejected("approval has expired")

            receipt = ApprovalReceipt(
                receipt_id="receipt_" + secrets.token_urlsafe(24),
                run_id=stored.proposal.run_id,
                action_id=stored.proposal.action_id,
                content_hash=stored.proposal.content_hash,
                confirmed_at=confirmed_at,
            )
            stored.state = "confirmed"
            stored.receipt = receipt
            return replace(receipt)

    def reject(self, run_id: str, action_id: str, now: datetime) -> None:
        rejected_at = _aware_utc(now, "now")
        if not isinstance(run_id, str) or not isinstance(action_id, str):
            raise ApprovalRejected("run_id and action_id must be strings")

        with self._lock:
            stored = self._actions.get(action_id)
            if stored is None or stored.proposal.run_id != run_id:
                raise ApprovalRejected("rejection does not match a registered run and action")
            if stored.state == "confirmed":
                raise ApprovalRejected("a confirmed action cannot be rejected")
            if stored.state == "rejected":
                return
            stored.state = "rejected"
            stored.rejected_at = rejected_at


__all__ = ["ApprovalStore"]
