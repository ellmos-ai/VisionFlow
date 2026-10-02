from __future__ import annotations

from pathlib import Path

import cv2
import pytest

from visionflow.config import validate_workflow
from visionflow.controller import Controller
from visionflow.media import open_source
from visionflow.runtime import create_registry
from road_helpers import default_lane_params, road_frames


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


def write_road_video(
    path: Path,
    *,
    event: str | None,
    count: int,
    codec: str = "MJPG",
) -> None:
    frames = road_frames(event, count=count, seed=17)
    writer = cv2.VideoWriter(
        str(path),
        cv2.VideoWriter_fourcc(*codec),
        5.0,
        (960, 540),
    )
    assert writer.isOpened(), f"{codec} writer must be available for the pixel integration test"
    try:
        for frame in frames:
            writer.write(frame.image)
    finally:
        writer.release()


def watch_workflow(video_path: Path, *, duration: int, tail: bool, stop_when=None):
    nodes = [
        {
            "id": "initial-direction",
            "type": "analyze",
            "params": {
                "tool": "direction",
                "tool_params": {**default_lane_params(), "initial_frames": 2},
            },
            "next": ["watch"],
        },
        {
            "id": "watch",
            "type": "observe",
            "params": {
                "duration_seconds": duration,
                "sample_fps": 5,
                "region": [480, 90, 480, 450],
                **({"stop_when": stop_when} if stop_when is not None else {}),
            },
            "next": ["tail"] if tail else ["report"],
        },
    ]
    if tail:
        nodes.append(
            {
                "id": "tail",
                "type": "observe",
                "params": {
                    "duration_seconds": 10,
                    "sample_fps": 1,
                    "region": [480, 90, 480, 450],
                },
                "next": ["report"],
            }
        )
    nodes.append(
        {
            "id": "report",
            "type": "report",
            "params": {"format": "json"},
            "next": [],
        }
    )
    return validate_workflow(
        {
            "schema_version": 1,
            "name": "road-monitoring-test",
            "input": {"kind": "video", "source": str(video_path)},
            "limits": {
                "max_observation_seconds": duration + (10 if tail else 0),
                "max_frames": 80,
                "max_llm_calls": 2,
            },
            "nodes": nodes,
            "outputs": [{"type": "display", "requires_approval": False}],
        },
        create_registry(),
    )


def monitor_events(result):
    return [event for event in result.trace if event["type"] == "monitor_stopped"]


def test_normal_watch_records_duration_and_actual_source_timeline(tmp_path):
    path = tmp_path / "Straße.avi"
    write_road_video(path, event=None, count=20)
    config = watch_workflow(path, duration=2, tail=False)
    opened = []

    def source_factory(input_config, max_frames):
        source = open_source(input_config, max_frames)
        opened.append(source)
        return source

    controller = Controller(
        create_registry(),
        source_factory=source_factory,
        evidence_root=tmp_path / "evidence",
        clock=FakeClock(),
    )
    queued = controller.start(config)
    result = controller.wait(queued.run_id, timeout=10)

    events = monitor_events(result)
    assert result.status == "completed"
    assert len(events) == 1
    stopped = events[0]
    assert stopped["reason"] == "duration"
    assert stopped["node_id"] == "watch"
    assert stopped["source_id"] == str(path)
    assert stopped["frame_ids"] == [f"{path}:{index}" for index in range(2, 12)]
    assert stopped["source_times"] == pytest.approx([0.4 + index / 5 for index in range(10)])
    assert stopped["duration_seconds"] == 2
    assert len(opened) == 1


def test_real_direction_condition_stops_early_then_reads_ten_second_tail_from_same_cursor(tmp_path):
    path = tmp_path / "Straße.avi"
    write_road_video(path, event="wrong_way", count=80, codec="FFV1")
    stop_when = {
        "tool": "direction",
        "metric": "opposition_count",
        "operator": "gte",
        "value": 1,
        "min_consecutive": 1,
    }
    config = watch_workflow(path, duration=5, tail=True, stop_when=stop_when)
    assert config.nodes[0].params["tool_params"]["min_frames"] == 3
    opened = []

    def source_factory(input_config, max_frames):
        source = open_source(input_config, max_frames)
        opened.append(source)
        return source

    controller = Controller(
        create_registry(),
        source_factory=source_factory,
        evidence_root=tmp_path / "evidence",
        clock=FakeClock(),
    )
    queued = controller.start(config)
    result = controller.wait(queued.run_id, timeout=15)

    events = monitor_events(result)
    assert result.status == "completed"
    assert len(events) == 2
    watch, tail = events
    assert watch["reason"] == "condition"
    assert watch["node_id"] == "watch"
    assert watch["source_id"] == tail["source_id"] == str(path)
    assert 0 < len(watch["frame_ids"]) < 25
    assert len(watch["source_times"]) == len(watch["frame_ids"])
    assert tail["frame_ids"] == [
        f"{path}:{int(watch['frame_ids'][-1].rsplit(':', 1)[-1]) + 1 + 5 * index}"
        for index in range(10)
    ]
    assert tail["reason"] == "duration"
    assert tail["duration_seconds"] == 10
    assert tail["source_times"][-1] - tail["source_times"][0] == pytest.approx(9.0)
    assert len(opened) == 1
    final_budget = tail["budget"]["used"]
    assert final_budget["frames"] == 2 + len(watch["frame_ids"]) + len(tail["frame_ids"])
    assert final_budget["observation_seconds"] == 15


def test_legacy_custom_observer_with_stop_condition_is_rejected_without_default_fallback(tmp_path):
    from visionflow.config import validate_workflow

    source_id = "simulation-legacy-observer"
    reads = []
    observer_calls = []

    class Source:
        def __init__(self):
            self.source_id = source_id

        def read(self):
            reads.append(True)
            return None

        def close(self):
            pass

    def legacy_observer(source, request, budget, cancelled, clock):
        observer_calls.append(True)
        return []

    workflow = validate_workflow(
        {
            "schema_version": 1,
            "name": "legacy-observer-stop-condition",
            "input": {"kind": "video", "source": source_id},
            "limits": {
                "max_observation_seconds": 1,
                "max_frames": 1,
                "max_llm_calls": 1,
            },
            "nodes": [
                {
                    "id": "watch",
                    "type": "observe",
                    "params": {
                        "duration_seconds": 1,
                        "sample_fps": 1,
                        "region": None,
                        "stop_when": {
                            "tool": "motion",
                            "metric": "motion_pixels",
                            "operator": "gte",
                            "value": 0,
                            "min_consecutive": 1,
                        },
                    },
                    "next": ["report"],
                },
                {
                    "id": "report",
                    "type": "report",
                    "params": {"format": "json"},
                    "next": [],
                },
            ],
            "outputs": [],
        },
        create_registry(),
    )
    controller = Controller(
        create_registry(),
        source_factory=lambda input_config, max_frames: Source(),
        evidence_root=tmp_path / "evidence",
        clock=FakeClock(),
        observer=legacy_observer,
    )

    queued = controller.start(workflow)
    result = controller.wait(queued.run_id, timeout=5)

    assert result.status == "failed"
    assert reads == []
    assert observer_calls == []


def _scripted_monitor(
    tmp_path,
    metric_samples,
    *,
    duration_seconds,
    min_consecutive,
    source_frame_count=None,
    read_started=None,
    read_release=None,
):
    import numpy as np

    from visionflow.config import validate_workflow
    from visionflow.contracts import Frame
    from visionflow.registry import ToolDescriptor, ToolRegistry

    source_id = f"scripted:{tmp_path.name}"
    produced = []
    handler_calls = []
    registry = ToolRegistry()

    class Source:
        def __init__(self):
            self.source_id = source_id
            self.index = 0
            self.closed = False

        def read(self):
            if read_started is not None:
                read_started.set()
                if not read_release.wait(timeout=5):
                    return None
            if source_frame_count is not None and self.index >= source_frame_count:
                return None
            index = self.index
            self.index += 1
            return Frame(
                f"{source_id}:{index}",
                np.zeros((16, 16), dtype=np.uint8),
                index / 5.0,
            )

        def close(self):
            self.closed = True

    source = Source()

    def scripted_handler(frame, params, context):
        state = context["state"]
        index = state.get("scripted_index", 0)
        state["scripted_index"] = index + 1
        handler_calls.append(frame.frame_id)
        sample = metric_samples[index] if index < len(metric_samples) else {}
        produced.append(sample)
        return sample

    registry.register(
        ToolDescriptor("scripted", "Deterministic test-only frame metric.", ("video",)),
        scripted_handler,
    )
    stop_when = {
        "tool": "scripted",
        "metric": "signal",
        "operator": "gt",
        "value": 0,
        "min_consecutive": min_consecutive,
    }
    workflow = validate_workflow(
        {
            "schema_version": 1,
            "name": "scripted-monitor-test",
            "input": {"kind": "video", "source": source_id},
            "limits": {
                "max_observation_seconds": duration_seconds,
                "max_frames": max(1, int(duration_seconds * 5)),
                "max_llm_calls": 1,
            },
            "nodes": [
                {
                    "id": "watch",
                    "type": "observe",
                    "params": {
                        "duration_seconds": duration_seconds,
                        "sample_fps": 5,
                        "region": None,
                        "stop_when": stop_when,
                    },
                    "next": ["report"],
                },
                {
                    "id": "report",
                    "type": "report",
                    "params": {"format": "json"},
                    "next": [],
                },
            ],
            "outputs": [],
        },
        registry,
    )
    controller = Controller(
        registry,
        source_factory=lambda input_config, max_frames: source,
        evidence_root=tmp_path / "evidence",
        clock=FakeClock(),
    )
    return controller, workflow, source, handler_calls, produced


def test_missing_nonnumeric_and_boolean_metrics_reset_consecutive_condition(tmp_path):
    samples = [
        {"signal": 1},
        {},
        {"signal": "1"},
        {"signal": True},
        {"signal": 1},
        {"signal": 1},
    ]
    controller, workflow, source, handler_calls, _produced = _scripted_monitor(
        tmp_path,
        samples,
        duration_seconds=3,
        min_consecutive=2,
        source_frame_count=20,
    )

    queued = controller.start(workflow)
    result = controller.wait(queued.run_id, timeout=5)

    stopped = monitor_events(result)[0]
    assert result.status == "completed"
    assert stopped["reason"] == "condition"
    assert stopped["frame_ids"] == [f"{source.source_id}:{index}" for index in range(6)]
    assert handler_calls == stopped["frame_ids"]


def test_early_video_eof_has_source_end_reason_and_actual_frame_ids(tmp_path):
    samples = [{"signal": 1}, {"signal": 1}]
    controller, workflow, source, handler_calls, _produced = _scripted_monitor(
        tmp_path,
        samples,
        duration_seconds=10,
        min_consecutive=3,
        source_frame_count=2,
    )

    queued = controller.start(workflow)
    result = controller.wait(queued.run_id, timeout=5)

    stopped = monitor_events(result)[0]
    assert result.status == "completed"
    assert stopped["reason"] == "source_end"
    assert stopped["frame_ids"] == [f"{source.source_id}:0", f"{source.source_id}:1"]
    assert handler_calls == stopped["frame_ids"]
    assert all(finding.tool == "scripted" for finding in result.findings)


def test_cancel_after_blocking_read_does_not_analyze_or_emit_condition(tmp_path):
    import threading

    read_started = threading.Event()
    read_release = threading.Event()
    controller, workflow, source, handler_calls, _produced = _scripted_monitor(
        tmp_path,
        [{"signal": 1}],
        duration_seconds=3,
        min_consecutive=1,
        source_frame_count=10,
        read_started=read_started,
        read_release=read_release,
    )

    queued = controller.start(workflow)
    assert read_started.wait(timeout=5)
    controller.cancel(queued.run_id)
    read_release.set()
    result = controller.wait(queued.run_id, timeout=5)

    stopped = monitor_events(result)[0]
    assert result.status == "cancelled"
    assert stopped["reason"] == "cancelled"
    assert stopped["frame_ids"] == [f"{source.source_id}:0"]
    assert result.findings == []
    assert handler_calls == []
    assert source.closed



def _camera_observation_config(source_id: str):
    return validate_workflow(
        {
            "schema_version": 1,
            "name": "simulation-camera-sampling-capability",
            "input": {"kind": "camera", "source": source_id},
            "limits": {
                "max_observation_seconds": 1,
                "max_frames": 5,
                "max_llm_calls": 1,
            },
            "nodes": [
                {
                    "id": "watch",
                    "type": "observe",
                    "params": {
                        "duration_seconds": 1,
                        "sample_fps": 5,
                        "region": None,
                    },
                    "next": ["report"],
                },
                {
                    "id": "report",
                    "type": "report",
                    "params": {"format": "json"},
                    "next": [],
                },
            ],
            "outputs": [],
        },
        create_registry(),
    )


def test_camera_without_verified_simulation_capability_remains_limited_to_one_fps():
    from visionflow.budget import Budget
    from visionflow.contracts import ObservationRejected, ObservationRequest
    from visionflow.observation import validate_observation

    source_id = "physical-camera-test"
    config = _camera_observation_config(source_id)
    budget = Budget(max_observation_seconds=1, max_frames=5, max_llm_calls=1)
    one_fps = ObservationRequest(source_id, None, 1, 1, "Check the camera")
    five_fps = ObservationRequest(source_id, None, 1, 5, "Check the camera")

    assert validate_observation(one_fps, config, budget) == one_fps
    with pytest.raises(ObservationRejected, match="one frame per second"):
        validate_observation(five_fps, config, budget)

    class UnboundSimulationSource:
        source_id = "different-camera"
        max_observation_fps = 5.0

    with pytest.raises(ObservationRejected, match="one frame per second"):
        validate_observation(five_fps, config, budget, source=UnboundSimulationSource())

def test_simulation_store_camera_source_allows_real_five_fps_sampling(tmp_path):
    import threading

    from visionflow.runtime import create_controller
    from visionflow.simulation import SimulationStore

    generated = []
    enough_frames = threading.Event()

    def record_frame(frame):
        generated.append(frame)
        if len(generated) >= 8:
            enough_frames.set()

    store = SimulationStore()
    session = store.create("camera-five-fps", seed=17, on_frame=record_frame)
    session.start()
    try:
        assert enough_frames.wait(timeout=5), "simulation producer should yield real frames"
        config = _camera_observation_config(session.source_id)

        def source_override(input_config, max_frames):
            if input_config.kind == "camera" and input_config.source == session.source_id:
                return store.get("camera-five-fps", session.source_id).open_reader()
            return None

        controller = create_controller(
            tmp_path,
            source_override=source_override,
            clock=FakeClock(),
        )
        queued = controller.start(config)
        result = controller.wait(queued.run_id, timeout=5)

        assert result.status == "completed", result.trace
        stopped = monitor_events(result)[0]
        sampled_ids = stopped["frame_ids"]
        generated_ids = {frame.frame_id for frame in generated}
        assert all(finding.tool == "motion" for finding in result.findings)
        assert stopped["reason"] == "duration"
        assert stopped["source_id"] == session.source_id
        assert len(sampled_ids) == 5
        assert all(frame_id in generated_ids for frame_id in sampled_ids)
        assert stopped["source_times"] == sorted(stopped["source_times"])
        assert [finding.frame_ids[0] for finding in result.findings] == sampled_ids
    finally:
        store.close()

