from __future__ import annotations

from pathlib import Path
from typing import Any

import cv2
import numpy as np
import pytest

from visionflow.contracts import (
    ConfigError,
    Frame,
    InputConfig,
    Limits,
    ModelDecision,
    Node,
    WorkflowConfig,
)
from visionflow.runtime import create_controller, create_registry


def test_runtime_registry_exposes_real_quality_and_motion_tools():
    registry = create_registry()
    descriptors = {descriptor.name: descriptor for descriptor in registry.descriptors}

    assert set(descriptors) == {"quality", "motion", "direction"}
    image = np.zeros((12, 12), dtype=np.uint8)
    quality = registry.resolve("quality")(
        Frame("image-1", image), {}, {"run_id": "quality-run", "tool": "quality", "state": {}}
    )
    assert set(quality) == {"sharpness_variance", "brightness_mean"}
    assert quality["brightness_mean"] == 0.0
    assert quality["sharpness_variance"] == 0.0


def test_motion_state_is_isolated_per_run_context():
    registry = create_registry()
    handler = registry.resolve("motion")
    first_run = {"run_id": "first", "tool": "motion", "state": {}}
    second_run = {"run_id": "second", "tool": "motion", "state": {}}
    blank = np.zeros((20, 20), dtype=np.uint8)
    changed = blank.copy()
    changed[5:10, 5:10] = 255

    first_warmup = handler(Frame("a-0", blank), {}, first_run)
    first_motion = handler(Frame("a-1", changed), {}, first_run)
    second_warmup = handler(Frame("b-0", changed), {}, second_run)

    assert first_warmup["warmup"] is True
    assert first_motion["warmup"] is False
    assert first_motion["motion_pixels"] > 0
    assert second_warmup["warmup"] is True


def test_create_controller_wires_per_run_fixture_and_allowlisted_sources(
    monkeypatch, tmp_path: Path
):
    import visionflow.controller as controller_module
    import visionflow.media as media_module
    import visionflow.model as model_module

    captured: dict[str, Any] = {}

    class ControllerDouble:
        def __init__(self, registry, **kwargs):
            captured["registry"] = registry
            captured.update(kwargs)

    class FixtureDouble:
        def __init__(self, decisions):
            self.decisions = list(decisions)

    source_calls = []
    media_marker = object()

    def fake_open_source(input_config, max_frames, *, allowed_camera_sources=None):
        source_calls.append((input_config, max_frames, dict(allowed_camera_sources or {})))
        return media_marker

    monkeypatch.setattr(controller_module, "Controller", ControllerDouble)
    monkeypatch.setattr(model_module, "FixtureModelAdapter", FixtureDouble)
    monkeypatch.setattr(media_module, "open_source", fake_open_source)
    cameras = {"front": 0}
    decisions = [ModelDecision("report", {"summary": "Prüfung abgeschlossen"})]
    fake_clock = object()

    controller = create_controller(
        tmp_path,
        allowed_camera_sources=cameras,
        fixture_decisions=decisions,
        clock=fake_clock,
    )
    cameras["front"] = 7

    assert isinstance(controller, ControllerDouble)
    assert captured["evidence_root"] == tmp_path / "evidence"
    assert captured["approval_store"] is not None
    assert captured["source_factory"](InputConfig("camera", "front"), 3) is media_marker
    assert source_calls[0][2] == {"front": 0}
    config = WorkflowConfig(
        schema_version=1,
        name="runtime-test",
        input=InputConfig("image", "fixture.png"),
        limits=Limits(),
        nodes=[],
        outputs=[],
    )
    first = captured["model_adapter"](config)
    second = captured["model_adapter"](config)
    assert isinstance(first, FixtureDouble)
    assert first is not second
    assert first.decisions == decisions
    assert captured["clock"] is fake_clock


def test_default_motion_fixture_never_exceeds_configured_observation(monkeypatch, tmp_path):
    import visionflow.controller as controller_module
    import visionflow.model as model_module

    captured: dict[str, Any] = {}

    class ControllerDouble:
        def __init__(self, registry, **kwargs):
            captured.update(kwargs)

    class FixtureDouble:
        def __init__(self, decisions):
            self.decisions = list(decisions)

    monkeypatch.setattr(controller_module, "Controller", ControllerDouble)
    monkeypatch.setattr(model_module, "FixtureModelAdapter", FixtureDouble)
    create_controller(tmp_path / "unused-data")
    config = WorkflowConfig(
        schema_version=1,
        name="bounded-fixture",
        input=InputConfig("video", "clip.avi"),
        limits=Limits(),
        nodes=[
            Node("motion", "analyze", {"tool": "motion"}, []),
            Node(
                "observe-authority",
                "observe",
                {
                    "duration_seconds": 1.0,
                    "sample_fps": 0.5,
                    "region": (2, 3, 10, 8),
                },
                [],
            ),
        ],
        outputs=[],
    )

    adapter = captured["model_adapter"](config)

    assert adapter.decisions[0].action == "observe"
    assert adapter.decisions[0].payload == {
        "source_id": "clip.avi",
        "region": (2, 3, 10, 8),
        "duration_seconds": 1.0,
        "sample_fps": 0.5,
        "question": "Weitere Bewegung prüfen",
    }
    assert adapter.decisions[1].action == "report"


def test_uploaded_video_source_identity_survives_observation(tmp_path: Path):
    from visionflow.config import validate_workflow

    data_dir = tmp_path / "local-data"
    upload_dir = data_dir / "uploads"
    upload_dir.mkdir(parents=True)
    original = tmp_path / "source.avi"
    writer = cv2.VideoWriter(
        str(original),
        cv2.VideoWriter_fourcc(*"MJPG"),
        5.0,
        (32, 24),
    )
    assert writer.isOpened(), "OpenCV test wheel must provide a working video writer"
    try:
        for value in (20, 80, 150, 230, 60, 180, 30, 90, 210, 140, 50, 220, 170, 40, 100, 245, 80, 15, 190, 120):
            writer.write(np.full((24, 32, 3), value, dtype=np.uint8))
    finally:
        writer.release()
    upload_ref = "uploads/vf_regression.bin"
    (data_dir / upload_ref).write_bytes(original.read_bytes())

    controller = create_controller(data_dir)
    workflow = validate_workflow(
        {
            "schema_version": 1,
            "name": "uploaded-motion",
            "input": {"kind": "video", "source": upload_ref},
            "limits": {"max_observation_seconds": 3, "max_frames": 16, "max_llm_calls": 2},
            "nodes": [
                {
                    "id": "motion",
                    "type": "analyze",
                    "params": {"tool": "motion"},
                    "next": ["question"],
                },
                {
                    "id": "question",
                    "type": "ask_model",
                    "params": {"question": "Gibt es Bewegung?"},
                    "next": [],
                },
                {
                    "id": "observe-authority",
                    "type": "observe",
                    "params": {
                    "duration_seconds": 3,
                    "sample_fps": 1,
                        "region": None,
                    },
                    "next": [],
                },
            ],
            "outputs": [],
        },
        controller.registry,
    )

    result = controller.wait(controller.start(workflow).run_id, timeout=5)

    assert result.status == "completed"
    assert any(event.get("model_mode") == "fixture" for event in result.trace)
    assert result.pending_requests == []
    assert [finding.frame_ids[0] for finding in result.findings] == [
        "uploads/vf_regression.bin:0",
        "uploads/vf_regression.bin:1",
        "uploads/vf_regression.bin:2",
        "uploads/vf_regression.bin:7",
    ]
    observation = next(event for event in result.trace if event.get("type") == "observation_completed")
    assert observation["budget"]["used"]["observation_seconds"] == 2.0


def test_uploaded_source_view_delegates_native_media_timing_capabilities():
    from visionflow.runtime import _SourceIdView

    class TimedSource:
        source_id = r"C:\private\uploads\generated.bin"
        timeline_position_seconds = 4.25
        observation_timeline_position_seconds = 5.0

        def __init__(self):
            self.requested_timestamp = None
            self.closed = False

        def read_at(self, timestamp_seconds):
            self.requested_timestamp = timestamp_seconds
            return Frame(
                f"{self.source_id}:23",
                np.zeros((2, 2), dtype=np.uint8),
                timestamp_seconds,
            )

        def close(self):
            self.closed = True

    inner = TimedSource()
    wrapped = _SourceIdView(inner, "uploads/vf_safe.bin")

    frame = wrapped.read_at(4.0)

    assert inner.requested_timestamp == 4.0
    assert frame.frame_id == "uploads/vf_safe.bin:23"
    assert frame.timestamp_seconds == 4.0
    assert wrapped.timeline_position_seconds == 4.25
    assert wrapped.observation_timeline_position_seconds == 5.0
    wrapped.close()
    assert inner.closed


def test_runtime_source_override_precedes_media_and_none_falls_back(monkeypatch, tmp_path):
    import visionflow.media as media_module

    class Source:
        source_id = "simulation-1"

        def read(self):
            return None

        def close(self):
            pass

    overridden = Source()
    fallback = Source()
    calls = []

    def fake_open_source(input_config, max_frames, *, allowed_camera_sources=None):
        calls.append(("media", input_config, max_frames))
        return fallback

    def source_override(input_config, max_frames):
        calls.append(("override", input_config, max_frames))
        return overridden

    monkeypatch.setattr(media_module, "open_source", fake_open_source)
    controller = create_controller(tmp_path / "override", source_override=source_override)
    config = InputConfig("camera", "simulation-1")

    assert controller.source_factory(config, 13) is overridden
    assert calls == [("override", config, 13)]

    calls.clear()
    fallback_controller = create_controller(
        tmp_path / "fallback",
        source_override=lambda input_config, max_frames: (
            calls.append(("override", input_config, max_frames)) or None
        ),
    )

    assert fallback_controller.source_factory(config, 17) is fallback
    assert calls == [
        ("override", config, 17),
        ("media", config, 17),
    ]


def test_runtime_rejects_foreign_source_override_and_closes_it(tmp_path):
    from visionflow.contracts import ConfigError

    class ForeignSource:
        source_id = "another-session"

        def __init__(self):
            self.closed = False

        def read(self):
            return None

        def close(self):
            self.closed = True

    foreign = ForeignSource()
    controller = create_controller(
        tmp_path,
        source_override=lambda input_config, max_frames: foreign,
    )

    with pytest.raises(ConfigError, match="source"):
        controller.source_factory(InputConfig("camera", "simulation-1"), 5)
    assert foreign.closed


def test_public_default_fixture_adapter_keeps_motion_observation_strategy():
    import visionflow.runtime as runtime_module

    fixture_helper = getattr(runtime_module, "create_default_fixture_adapter", None)
    if not callable(fixture_helper):
        pytest.fail("runtime does not expose create_default_fixture_adapter", pytrace=False)

    config = WorkflowConfig(
        schema_version=1,
        name="routed-motion",
        input=InputConfig("video", "clip.avi"),
        limits=Limits(),
        nodes=[
            Node("motion", "analyze", {"tool": "motion"}, []),
            Node(
                "observe-authority",
                "observe",
                {
                    "duration_seconds": 4.0,
                    "sample_fps": 3.0,
                    "region": (2, 3, 10, 8),
                },
                [],
            ),
        ],
        outputs=[],
    )

    adapter = fixture_helper(config)

    assert adapter.mode == "fixture"
    assert adapter.ask("observe?", []).payload == {
        "source_id": "clip.avi",
        "region": (2.0, 3.0, 10.0, 8.0),
        "duration_seconds": 2.0,
        "sample_fps": 1.0,
        "question": "Weitere Bewegung prüfen",
    }
    assert adapter.ask("finish?", []).action == "report"

    image_config = WorkflowConfig(
        schema_version=1,
        name="routed-image",
        input=InputConfig("image", "still.png"),
        limits=Limits(),
        nodes=[],
        outputs=[],
    )
    image_adapter = fixture_helper(image_config)
    assert image_adapter.ask("report?", []).action == "report"


def test_create_controller_passes_run_model_factory_as_full_override(monkeypatch, tmp_path):
    import visionflow.controller as controller_module

    captured = {}

    class ControllerDouble:
        def __init__(self, registry, **kwargs):
            captured.update(kwargs)

    class RunFactory:
        def create_adapter(self, config, context):
            raise AssertionError("the fake controller should not start a run")

    factory = RunFactory()
    monkeypatch.setattr(controller_module, "Controller", ControllerDouble)

    controller = create_controller(tmp_path / "factory-override", model_factory=factory)

    assert isinstance(controller, ControllerDouble)
    assert captured["model_adapter"] is factory


def test_create_controller_rejects_fixture_decisions_with_model_factory(tmp_path):
    class RunFactory:
        def create_adapter(self, config, context):
            raise AssertionError("the factory must not be invoked")

    with pytest.raises(ConfigError, match="fixture_decisions"):
        create_controller(
            tmp_path / "factory-conflict",
            fixture_decisions=[],
            model_factory=RunFactory(),
        )
