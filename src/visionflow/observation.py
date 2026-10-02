"""Budgeted follow-up sampling over an already-open frame source."""

from __future__ import annotations

import math
from collections.abc import Callable
from typing import Protocol

import numpy as np

from .budget import Budget
from .contracts import (
    BudgetExceeded,
    Finding,
    Frame,
    ObservationRejected,
    ObservationRequest,
    WorkflowConfig,
)
from .media import CameraSource, FrameSource
from .model import _normalize_observation_payload


MAX_SLEEP_CHUNK_SECONDS = 0.25


class Clock(Protocol):
    """Minimal monotonic clock interface so observation timing is testable."""

    def monotonic(self) -> float:
        """Return a monotonic time in seconds."""

    def sleep(self, seconds: float) -> None:
        """Wait for at most the requested interval."""


Analyzer = Callable[[Frame, ObservationRequest], Finding | list[Finding]]


def _normalize_request(request: ObservationRequest) -> ObservationRequest:
    if type(request) is not ObservationRequest:
        raise ObservationRejected("request must be an ObservationRequest")
    payload = {
        "source_id": request.source_id,
        "region": request.region,
        "duration_seconds": request.duration_seconds,
        "sample_fps": request.sample_fps,
        "question": request.question,
    }
    try:
        normalized = _normalize_observation_payload(payload)
    except (TypeError, ValueError) as exc:
        raise ObservationRejected(f"invalid observation request: {exc}") from exc
    return ObservationRequest(**normalized)


def _frame_count(request: ObservationRequest) -> int:
    product = request.duration_seconds * request.sample_fps
    if not math.isfinite(product):
        raise ObservationRejected("requested frame count must be finite")
    try:
        return math.ceil(product)
    except (OverflowError, ValueError) as exc:
        raise ObservationRejected("requested frame count is invalid") from exc


def _configured_region(value: object) -> tuple[float, float, float, float] | None:
    if value is None:
        return None
    if type(value) not in (tuple, list) or len(value) != 4:
        raise ObservationRejected("configured observe region is invalid")
    coordinates: list[float] = []
    for coordinate in value:
        if isinstance(coordinate, bool) or type(coordinate) not in (int, float):
            raise ObservationRejected("configured observe region is invalid")
        numeric = float(coordinate)
        if not math.isfinite(numeric):
            raise ObservationRejected("configured observe region is invalid")
        coordinates.append(numeric)
    x, y, width, height = coordinates
    if x < 0 or y < 0 or width <= 0 or height <= 0:
        raise ObservationRejected("configured observe region is invalid")
    if not math.isfinite(x + width) or not math.isfinite(y + height):
        raise ObservationRejected("configured observe region is invalid")
    return x, y, width, height


def _contained(
    requested: tuple[float, float, float, float] | None,
    configured: tuple[float, float, float, float] | None,
) -> bool:
    if configured is None:
        return True
    if requested is None:
        return False
    x, y, width, height = requested
    bound_x, bound_y, bound_width, bound_height = configured
    return (
        x >= bound_x
        and y >= bound_y
        and x + width <= bound_x + bound_width
        and y + height <= bound_y + bound_height
    )


def validate_observation(
    request: ObservationRequest,
    config: WorkflowConfig,
    budget: Budget,
) -> ObservationRequest:
    """Check source, configured observation bounds, rate, and remaining run budget."""
    normalized = _normalize_request(request)
    if type(config) is not WorkflowConfig:
        raise ObservationRejected("config must be a WorkflowConfig")
    if not isinstance(budget, Budget):
        raise ObservationRejected("budget must be a Budget")
    if type(config.input.source) is not str or normalized.source_id != config.input.source:
        raise ObservationRejected("observation cannot switch the configured source")
    if config.input.kind not in {"image", "video", "camera"}:
        raise ObservationRejected("configured input kind is unsupported")
    if config.input.kind == "camera" and normalized.sample_fps > 1:
        raise ObservationRejected("camera observation is limited to one frame per second")

    authorities: list[tuple[float, float, tuple[float, float, float, float] | None]] = []
    for node in config.nodes:
        if node.type != "observe":
            continue
        params = node.params
        try:
            duration = float(params["duration_seconds"])
            sample_fps = float(params["sample_fps"])
        except (KeyError, TypeError, ValueError, OverflowError) as exc:
            raise ObservationRejected("configured observe limits are invalid") from exc
        if (
            isinstance(params.get("duration_seconds"), bool)
            or isinstance(params.get("sample_fps"), bool)
            or not math.isfinite(duration)
            or duration <= 0
            or not math.isfinite(sample_fps)
            or sample_fps <= 0
        ):
            raise ObservationRejected("configured observe limits are invalid")
        region = _configured_region(params.get("region"))
        authorities.append((duration, sample_fps, region))
    if not authorities:
        raise ObservationRejected("workflow does not configure follow-up observation")

    permitted = any(
        normalized.duration_seconds <= duration
        and normalized.sample_fps <= sample_fps
        and _contained(normalized.region, region)
        for duration, sample_fps, region in authorities
    )
    if not permitted:
        raise ObservationRejected("observation exceeds the configured observe bounds")

    requested_frames = _frame_count(normalized)
    remaining = budget.remaining
    if (
        normalized.duration_seconds > remaining["observation_seconds"]
        or requested_frames > remaining["frames"]
    ):
        raise BudgetExceeded("observation request exceeds the remaining run budget")
    return normalized


def _clock_now(clock: Clock) -> float:
    value = clock.monotonic()
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ObservationRejected("clock.monotonic() must return a finite number")
    now = float(value)
    if not math.isfinite(now):
        raise ObservationRejected("clock.monotonic() must return a finite number")
    return now


def _wait_for_slot(
    clock: Clock,
    target: float,
    deadline: float,
    cancelled: Callable[[], bool],
) -> bool:
    while True:
        if cancelled():
            return False
        now = _clock_now(clock)
        if now >= deadline:
            return False
        remaining = target - now
        if remaining <= 0:
            return True
        clock.sleep(min(remaining, deadline - now, MAX_SLEEP_CHUNK_SECONDS))


def _frame_dimensions(frame: Frame) -> tuple[int, int]:
    if type(frame) is not Frame or not isinstance(frame.image, np.ndarray):
        raise ObservationRejected("source returned an invalid frame")
    image = frame.image
    if image.dtype != np.uint8:
        raise ObservationRejected("source frame must use uint8 pixels")
    if image.ndim == 2:
        height, width = image.shape
    elif image.ndim == 3 and image.shape[2] in (3, 4):
        height, width = image.shape[:2]
    else:
        raise ObservationRejected("source frame must be grayscale, BGR, or BGRA")
    if width <= 0 or height <= 0:
        raise ObservationRejected("source frame must not be empty")
    return width, height


def _check_frame_region(
    region: tuple[float, float, float, float] | None,
    width: int,
    height: int,
) -> tuple[slice, slice] | None:
    if region is None:
        return None
    x, y, box_width, box_height = region
    if x + box_width > width or y + box_height > height:
        raise ObservationRejected("requested region lies outside the actual frame")
    left = math.floor(x)
    top = math.floor(y)
    right = math.ceil(x + box_width)
    bottom = math.ceil(y + box_height)
    return slice(top, bottom), slice(left, right)


def _as_findings(value: Finding | list[Finding]) -> list[Finding]:
    if isinstance(value, Finding):
        return [value]
    if type(value) is list and all(isinstance(item, Finding) for item in value):
        return value
    raise ObservationRejected("analyzer must return a Finding or list of Findings")


def observe(
    source: FrameSource,
    request: ObservationRequest,
    budget: Budget,
    cancelled: Callable[[], bool],
    clock: Clock,
    *,
    analyzer: Analyzer | None = None,
) -> list[Finding]:
    """Read a bounded sample from a persistent source without closing its cursor."""
    normalized = _normalize_request(request)
    if not isinstance(budget, Budget):
        raise ObservationRejected("budget must be a Budget")
    if not callable(cancelled):
        raise ObservationRejected("cancelled must be callable")
    if not callable(getattr(clock, "monotonic", None)) or not callable(
        getattr(clock, "sleep", None)
    ):
        raise ObservationRejected("clock must provide monotonic() and sleep()")
    if analyzer is not None and not callable(analyzer):
        raise ObservationRejected("analyzer must be callable")
    if not isinstance(getattr(source, "source_id", None), str):
        raise ObservationRejected("source must have a string source_id")
    if normalized.source_id != source.source_id:
        raise ObservationRejected("observation cannot switch the open source")
    if not callable(getattr(source, "read", None)):
        raise ObservationRejected("source must provide read()")
    if isinstance(source, CameraSource) and normalized.sample_fps > 1:
        raise ObservationRejected("camera observation is limited to one frame per second")

    requested_frames = _frame_count(normalized)
    remaining = budget.remaining
    if (
        normalized.duration_seconds > remaining["observation_seconds"]
        or requested_frames > remaining["frames"]
    ):
        raise BudgetExceeded("observation request exceeds the remaining run budget")

    started_at = _clock_now(clock)
    deadline = started_at + normalized.duration_seconds
    if not math.isfinite(deadline):
        raise ObservationRejected("observation deadline is not finite")
    budget.consume(observation_seconds=normalized.duration_seconds)

    findings: list[Finding] = []
    interval = 1.0 / normalized.sample_fps
    for index in range(requested_frames):
        target = started_at + index * interval
        if not _wait_for_slot(clock, target, deadline, cancelled):
            break
        if cancelled():
            break

        budget.consume(frames=1)
        frame = source.read()
        if frame is None:
            budget._refund(frames=1)
            break
        if cancelled():
            break

        width, height = _frame_dimensions(frame)
        crop = _check_frame_region(normalized.region, width, height)
        if analyzer is None:
            findings.append(
                Finding(
                    tool="observation",
                    frame_ids=[frame.frame_id],
                    region=normalized.region,
                    metrics={"timestamp_seconds": frame.timestamp_seconds},
                    evidence_ref="",
                )
            )
        else:
            if crop is None:
                analysis_frame = frame
            else:
                y_slice, x_slice = crop
                analysis_frame = Frame(
                    frame_id=frame.frame_id,
                    image=frame.image[y_slice, x_slice],
                    timestamp_seconds=frame.timestamp_seconds,
                )
            findings.extend(_as_findings(analyzer(analysis_frame, normalized)))

    return findings


__all__ = ["Analyzer", "Clock", "observe", "validate_observation"]
