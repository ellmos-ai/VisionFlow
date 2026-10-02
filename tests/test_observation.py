from __future__ import annotations

import math

import numpy as np
import pytest

from visionflow.budget import Budget
from visionflow.contracts import (
    BudgetExceeded,
    Finding,
    Frame,
    InputConfig,
    Limits,
    Node,
    ObservationRejected,
    ObservationRequest,
    WorkflowConfig,
)
from visionflow.observation import observe, validate_observation


def make_config(
    *,
    kind: str = "video",
    source: str = "clip.avi",
    region: tuple[float, float, float, float] | None = (1, 1, 6, 4),
    duration_seconds: float = 3,
    sample_fps: float = 2,
) -> WorkflowConfig:
    return WorkflowConfig(
        schema_version=1,
        name="observe-test",
        input=InputConfig(kind=kind, source=source),
        limits=Limits(),
        nodes=[
            Node(
                "observe",
                "observe",
                {
                    "duration_seconds": duration_seconds,
                    "sample_fps": sample_fps,
                    "region": region,
                },
                [],
            )
        ],
        outputs=[],
    )


def make_request(
    *,
    source_id: str = "clip.avi",
    region: tuple[float, float, float, float] | None = (2, 1, 3, 2),
    duration_seconds: float = 2,
    sample_fps: float = 1,
    question: str = "Did the object move?",
) -> ObservationRequest:
    return ObservationRequest(
        source_id=source_id,
        region=region,
        duration_seconds=duration_seconds,
        sample_fps=sample_fps,
        question=question,
    )


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []
        self.on_sleep = None

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds
        if self.on_sleep is not None:
            self.on_sleep()


class FakeSource:
    def __init__(self, source_id: str, frames: list[Frame]) -> None:
        self.source_id = source_id
        self._frames = list(frames)
        self._cursor = 0
        self.frames_read = 0
        self.read_calls = 0
        self.close_calls = 0
        self.on_read = None

    def read(self) -> Frame | None:
        self.read_calls += 1
        if self._cursor >= len(self._frames):
            return None
        frame = self._frames[self._cursor]
        self._cursor += 1
        self.frames_read += 1
        if self.on_read is not None:
            self.on_read()
        return frame

    def close(self) -> None:
        self.close_calls += 1


def make_frames(count: int, *, height: int = 6, width: int = 8) -> list[Frame]:
    return [
        Frame(
            frame_id=f"clip.avi:{index}",
            image=np.full((height, width, 3), index, dtype=np.uint8),
            timestamp_seconds=index / 2,
        )
        for index in range(count)
    ]


def test_model_cannot_switch_camera(config=None):
    config = config or make_config(kind="camera", source="camera-1")
    budget = Budget(max_observation_seconds=10, max_frames=10)
    request = make_request(source_id="unauthorized", duration_seconds=1, sample_fps=1)

    with pytest.raises(ObservationRejected):
        validate_observation(request, config, budget)


def test_validate_observation_requires_configured_observe_node():
    config = make_config()
    config.nodes = [Node("report", "report", {"format": "markdown"}, [])]

    with pytest.raises(ObservationRejected):
        validate_observation(make_request(), config, Budget())


def test_validate_observation_limits_roi_to_configured_bounding_box():
    config = make_config(region=(2, 2, 4, 3))
    allowed = make_request(region=(3, 2, 2, 3), duration_seconds=2, sample_fps=1)
    forbidden = make_request(region=(5, 2, 2, 3), duration_seconds=2, sample_fps=1)
    full_frame = make_request(region=None, duration_seconds=2, sample_fps=1)

    assert validate_observation(allowed, config, Budget()) == allowed
    with pytest.raises(ObservationRejected):
        validate_observation(forbidden, config, Budget())
    with pytest.raises(ObservationRejected):
        validate_observation(full_frame, config, Budget())


def test_unbounded_configured_roi_allows_full_frame_observation():
    config = make_config(region=None)
    request = make_request(region=None, duration_seconds=2, sample_fps=1)

    assert validate_observation(request, config, Budget()) == request


@pytest.mark.parametrize(
    "field,value",
    [
        ("duration_seconds", True),
        ("duration_seconds", 0),
        ("duration_seconds", -1),
        ("duration_seconds", math.nan),
        ("sample_fps", False),
        ("sample_fps", 0),
        ("sample_fps", math.inf),
    ],
)
def test_validate_observation_rejects_invalid_numeric_limits(field, value):
    values = {
        "source_id": "clip.avi",
        "region": (2, 1, 3, 2),
        "duration_seconds": 2,
        "sample_fps": 1,
        "question": "inspect",
    }
    values[field] = value
    budget = Budget(max_observation_seconds=10, max_frames=20)
    before = budget.snapshot()

    with pytest.raises(ObservationRejected):
        validate_observation(ObservationRequest(**values), make_config(), budget)

    assert budget.snapshot() == before


def test_camera_observation_is_limited_to_one_frame_per_second():
    config = make_config(kind="camera", source="camera-1", sample_fps=10)
    request = make_request(
        source_id="camera-1", duration_seconds=2, sample_fps=1
    )
    too_fast = make_request(source_id="camera-1", duration_seconds=2, sample_fps=1.01)

    assert validate_observation(request, config, Budget()) == request
    with pytest.raises(ObservationRejected):
        validate_observation(too_fast, config, Budget())


def test_validate_observation_protects_remaining_time_and_ceil_frame_budget():
    request = make_request(duration_seconds=2.1, sample_fps=2)
    short_frames = Budget(max_observation_seconds=10, max_frames=4)
    short_frames.consume(frames=1)
    short_time = Budget(max_observation_seconds=2, max_frames=20)
    short_time.consume(observation_seconds=0.1)

    with pytest.raises(BudgetExceeded):
        validate_observation(request, make_config(), short_frames)
    with pytest.raises(BudgetExceeded):
        validate_observation(request, make_config(), short_time)
    assert short_frames.remaining["frames"] == 3
    assert short_time.remaining["observation_seconds"] == pytest.approx(1.9)


def test_validate_observation_caps_duration_and_rate_to_workflow_policy():
    config = make_config(duration_seconds=2, sample_fps=2)

    with pytest.raises(ObservationRejected):
        validate_observation(
            make_request(duration_seconds=2.1, sample_fps=1), config, Budget()
        )
    with pytest.raises(ObservationRejected):
        validate_observation(
            make_request(duration_seconds=1, sample_fps=2.1), config, Budget()
        )


def test_observe_returns_actual_frame_evidence_refunds_eof_and_keeps_cursor_open():
    source = FakeSource("clip.avi", make_frames(2))
    budget = Budget(max_observation_seconds=10, max_frames=10)
    clock = FakeClock()

    findings = observe(
        source,
        make_request(duration_seconds=2, sample_fps=2),
        budget,
        cancelled=lambda: False,
        clock=clock,
    )

    assert [finding.frame_ids for finding in findings] == [["clip.avi:0"], ["clip.avi:1"]]
    assert [finding.metrics["timestamp_seconds"] for finding in findings] == [0.0, 0.5]
    assert all(finding.evidence_ref == "" for finding in findings)
    assert source.frames_read == 2
    assert source.read_calls == 3
    assert source.close_calls == 0
    assert budget.snapshot()["used"] == {
        "frames": 2,
        "observation_seconds": 2.0,
        "llm_calls": 0,
    }
    assert clock.sleeps and max(clock.sleeps) <= 0.25


def test_cancel_before_read_consumes_no_frames():
    source = FakeSource("clip.avi", make_frames(2))
    budget = Budget(max_observation_seconds=10, max_frames=10)

    findings = observe(
        source,
        make_request(duration_seconds=1, sample_fps=1),
        budget,
        cancelled=lambda: True,
        clock=FakeClock(),
    )

    assert findings == []
    assert source.frames_read == 0
    assert source.read_calls == 0
    assert budget.snapshot()["used"]["frames"] == 0
    assert source.close_calls == 0


def test_cancel_after_read_stops_before_analyzer_and_next_read():
    source = FakeSource("clip.avi", make_frames(3))
    source.on_read = lambda: setattr(source, "cancelled", True)
    source.cancelled = False
    budget = Budget(max_observation_seconds=10, max_frames=10)
    analyzer_calls = []

    findings = observe(
        source,
        make_request(duration_seconds=3, sample_fps=1),
        budget,
        cancelled=lambda: source.cancelled,
        clock=FakeClock(),
        analyzer=lambda frame, request: analyzer_calls.append(frame.frame_id),
    )

    assert findings == []
    assert source.frames_read == 1
    assert source.read_calls == 1
    assert analyzer_calls == []
    assert budget.snapshot()["used"]["frames"] == 1


def test_observe_rejects_source_mismatch_before_consuming_budget():
    source = FakeSource("other-source", make_frames(1))
    budget = Budget(max_observation_seconds=10, max_frames=10)
    before = budget.snapshot()

    with pytest.raises(ObservationRejected):
        observe(
            source,
            make_request(duration_seconds=1, sample_fps=1),
            budget,
            cancelled=lambda: False,
            clock=FakeClock(),
        )

    assert source.read_calls == 0
    assert budget.snapshot() == before


def test_observe_checks_requested_roi_against_actual_frame_bounds():
    source = FakeSource("clip.avi", make_frames(1, height=4, width=5))
    budget = Budget(max_observation_seconds=10, max_frames=10)
    analyzer_calls = []

    with pytest.raises(ObservationRejected):
        observe(
            source,
            make_request(region=(4, 0, 2, 1), duration_seconds=1, sample_fps=1),
            budget,
            cancelled=lambda: False,
            clock=FakeClock(),
            analyzer=lambda frame, request: analyzer_calls.append(frame),
        )

    assert source.frames_read == 1
    assert analyzer_calls == []
    assert budget.snapshot()["used"]["frames"] == 1


def test_analyzer_receives_a_bounded_crop_and_actual_frame_metadata():
    source = FakeSource("clip.avi", make_frames(1, height=6, width=8))
    budget = Budget(max_observation_seconds=10, max_frames=10)
    observed = []

    def analyzer(frame: Frame, request: ObservationRequest) -> Finding:
        observed.append((frame, request))
        return Finding(
            tool="motion",
            frame_ids=[frame.frame_id],
            region=(2, 1, 3, 2),
            metrics={"motion_pixels": 4},
            evidence_ref="sha256:fixture",
        )

    findings = observe(
        source,
        make_request(region=(2, 1, 3, 2), duration_seconds=1, sample_fps=1),
        budget,
        cancelled=lambda: False,
        clock=FakeClock(),
        analyzer=analyzer,
    )

    assert len(observed) == 1
    cropped_frame, passed_request = observed[0]
    assert cropped_frame.image.shape == (2, 3, 3)
    assert cropped_frame.frame_id == "clip.avi:0"
    assert cropped_frame.timestamp_seconds == 0.0
    assert passed_request.region == (2.0, 1.0, 3.0, 2.0)
    assert findings[0].evidence_ref == "sha256:fixture"
    assert findings[0].frame_ids == ["clip.avi:0"]
