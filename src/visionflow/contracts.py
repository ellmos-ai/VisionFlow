"""Shared data contracts for VisionFlow workflows and run adapters.

This module deliberately has no NumPy or OpenCV dependency. ``Frame.image`` is
opaque here so the same contracts work for API, CLI, fixtures, and vision tools.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


Region = tuple[float, float, float, float] | None


@dataclass(slots=True)
class InputConfig:
    kind: str
    source: str


@dataclass(slots=True)
class Limits:
    max_observation_seconds: int = 300
    max_frames: int = 300
    max_llm_calls: int = 4


@dataclass(slots=True)
class Node:
    id: str
    type: str
    params: dict[str, Any]
    next: list[str] | None = None


@dataclass(slots=True)
class OutputConfig:
    type: str
    requires_approval: bool = False


@dataclass(slots=True)
class WorkflowConfig:
    schema_version: int
    name: str
    input: InputConfig
    limits: Limits
    nodes: list[Node]
    outputs: list[OutputConfig]


@dataclass(slots=True)
class Finding:
    tool: str
    frame_ids: list[str]
    region: Region
    metrics: dict[str, Any]
    evidence_ref: str


@dataclass(slots=True)
class ObservationRequest:
    source_id: str
    region: Region
    duration_seconds: float
    sample_fps: float
    question: str


@dataclass(slots=True)
class ModelDecision:
    action: str
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class Frame:
    frame_id: str
    image: Any
    timestamp_seconds: float = 0.0


@dataclass(slots=True)
class ActionProposal:
    type: str
    run_id: str
    action_id: str
    content: str
    content_hash: str
    expires_at: datetime
    sent: bool = False
    destination: str | None = None


@dataclass(slots=True)
class ApprovalReceipt:
    receipt_id: str
    run_id: str
    action_id: str
    content_hash: str
    confirmed_at: datetime


@dataclass(slots=True)
class RunResult:
    run_id: str
    status: str
    findings: list[Finding] = field(default_factory=list)
    trace: list[dict[str, Any]] = field(default_factory=list)
    outputs: list[dict[str, Any]] = field(default_factory=list)
    pending_requests: list[dict[str, Any]] = field(default_factory=list)


class VisionFlowError(Exception):
    """Base class for errors callers may safely handle as workflow failures."""


class ConfigError(VisionFlowError, ValueError):
    """Raised when a workflow document is invalid or unsupported."""


class UnknownRun(VisionFlowError, LookupError):
    """Raised when a requested run ID is not known to the controller."""


class InvalidTransition(VisionFlowError, RuntimeError):
    """Raised when a run cannot move to the requested state."""


class BudgetExceeded(VisionFlowError, RuntimeError):
    """Raised when a run would exceed one of its configured budgets."""


class MediaError(VisionFlowError, ValueError):
    """Raised when media is missing, corrupt, or unsupported."""


class ObservationRejected(VisionFlowError, ValueError):
    """Raised when a requested follow-up observation violates policy."""


class ApprovalRejected(VisionFlowError, ValueError):
    """Raised when an action approval is missing, stale, or mismatched."""


__all__ = [
    "ActionProposal",
    "ApprovalReceipt",
    "ApprovalRejected",
    "BudgetExceeded",
    "ConfigError",
    "Finding",
    "Frame",
    "InputConfig",
    "InvalidTransition",
    "Limits",
    "MediaError",
    "ModelDecision",
    "Node",
    "ObservationRejected",
    "ObservationRequest",
    "OutputConfig",
    "RunResult",
    "Region",
    "UnknownRun",
    "VisionFlowError",
    "WorkflowConfig",
]
