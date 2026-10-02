"""Behavioral regressions from the independent whole-branch review."""
from __future__ import annotations

import json
import threading
from pathlib import Path

import cv2
import numpy as np
import pytest

from visionflow.config import validate_workflow
from visionflow.contracts import ModelDecision, RunResult
from visionflow.controller import Controller
from visionflow.outputs import prepare_notification, render_report
from visionflow.runtime import create_controller, create_registry


def workflow(nodes, source="unused.png", kind="image", outputs=None, frames=100):
    return validate_workflow({
        "schema_version": 1, "name": "review-regression",
        "input": {"kind": kind, "source": str(source)},
        "limits": {"max_frames": frames, "max_llm_calls": 4,
                   "max_observation_seconds": 4},
        "nodes": nodes, "outputs": outputs or [],
    }, create_registry())


def node(node_id, kind, params, next_ids=None):
    return {"id": node_id, "type": kind, "params": params, "next": next_ids or []}


def analyze(node_id, tool="motion", next_ids=None):
    return node(node_id, "analyze", {"tool": tool}, next_ids)


def video(path, size=(32, 32), frames=20, fps=10):
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"MJPG"), fps, size)
    assert writer.isOpened(), "test codec unavailable"
    try:
        for index in range(frames):
            image = np.zeros((size[1], size[0], 3), dtype=np.uint8)
            cv2.rectangle(image, (index % 12, 2), (index % 12 + 5, 8), (255, 255, 255), -1)
            writer.write(image)
    finally:
        writer.release()
    return path


def completed(controller, config):
    result = controller.wait(controller.start(config).run_id, timeout=5)
    assert result.status == "completed", result.trace
    return result


def test_cancel_during_adapter_factory_prevents_a_new_model_call(tmp_path):
    entered, release, factory_returned = threading.Event(), threading.Event(), threading.Event()
    calls = []

    class Adapter:
        mode = "fixture"

        def ask(self, question, findings):
            calls.append(question)
            return ModelDecision("report", {})

    def factory(config):
        entered.set()
        assert release.wait(5)
        factory_returned.set()
        return Adapter()

    controller = Controller(create_registry(), model_adapter=factory, evidence_root=tmp_path)
    run_id = controller.start(workflow([node("ask", "ask_model", {"question": "inspect"})])).run_id
    assert entered.wait(3)
    controller.cancel(run_id)
    release.set()
    assert factory_returned.wait(3)
    controller._get_run(run_id).thread.join(3)
    assert calls == []
    assert controller.status(run_id).status == "cancelled"
    assert not any(event["type"] == "model_call" for event in controller.status(run_id).trace)


@pytest.mark.parametrize("uneven", [False, True])
def test_converging_dag_joins_selected_paths_once_after_all_predecessors(tmp_path, uneven):
    output = {"output_type": "display"}
    nodes = [node("a", "output", output, ["b", "c"]),
             node("b", "output", output, ["join"]),
             node("c", "output", output, ["extra"] if uneven else ["join"])]
    if uneven:
        nodes.append(node("extra", "output", output, ["join"]))
    nodes.append(node("join", "output", output))
    result = completed(create_controller(tmp_path), workflow(nodes))
    sequence = [event["node_id"] for event in result.trace if event["type"] == "node_completed"]
    assert sequence[-1] == "join"
    assert sequence.count("join") == 1
    assert set(sequence) == {item["id"] for item in nodes}


def test_multiple_image_tools_analyze_the_same_input_and_keep_the_frame_budget(tmp_path):
    path = tmp_path / "prüfung.png"
    cv2.imencode(".png", np.full((24, 24, 3), 128, dtype=np.uint8))[1].tofile(path)
    config = workflow([analyze("first", "quality", ["second"]), analyze("second", "quality")],
                      path, frames=2)
    result = completed(create_controller(tmp_path / "data"), config)
    assert len(result.findings) == 2
    assert result.findings[0].frame_ids == result.findings[1].frame_ids
    assert result.findings[0].metrics == result.findings[1].metrics
    assert result.findings[0].evidence_ref != result.findings[1].evidence_ref
    config.limits.max_frames = 1
    bounded = create_controller(tmp_path / "bounded")
    limited = bounded.wait(bounded.start(config).run_id, timeout=5)
    assert limited.status == "failed"
    assert len(limited.findings) == 1
    assert limited.trace[-1]["error_type"] == "BudgetExceeded"


@pytest.mark.parametrize("report_format", ["markdown", "json"])
def test_many_reports_do_not_embed_prior_rendered_content(tmp_path, report_format):
    nodes = [node(f"report-{index}", "report", {"format": report_format},
                  [f"report-{index + 1}"] if index < 11 else []) for index in range(12)]
    result = completed(create_controller(tmp_path), workflow(nodes))
    reports = [item for item in result.outputs if item["type"] == "report"]
    assert len(reports) == 12
    assert max(len(item["content"]) for item in reports) < 50_000
    if report_format == "json":
        payload = json.loads(reports[-1]["content"])
        assert all("content" not in item for item in payload["outputs"])


def test_rendered_reports_and_drafts_are_referenced_without_copying_their_bodies():
    result = RunResult("run-1", "completed", outputs=[
        {"type": "report", "format": "markdown", "content": "old-report-body"},
        {"type": "notification_draft", "action_id": "act-1", "content": "old-draft-body"},
    ])
    for content in [render_report(result), prepare_notification(result).content]:
        assert "old-report-body" not in content
        assert "old-draft-body" not in content
        assert "act-1" in content
    assert result.outputs[0]["content"] == "old-report-body"


def test_roi_finding_keeps_source_region_and_crop_coordinate_metadata(tmp_path):
    path = video(tmp_path / "roi.avi", size=(64, 64))
    config = workflow([node("observe", "observe", {
        "duration_seconds": 1, "sample_fps": 1, "region": [20, 30, 10, 10],
    })], path, "video")
    controller = create_controller(tmp_path / "data")
    result = completed(controller, config)
    finding = result.findings[0]
    assert finding.region == (20.0, 30.0, 10.0, 10.0)
    assert finding.metrics["coordinate_system"] == {
        "region": "source_pixels", "evidence": "crop_pixels", "regions": "crop_pixels",
    }
    evidence = controller.evidence_root / finding.evidence_ref
    decoded = cv2.imdecode(np.frombuffer(evidence.read_bytes(), dtype=np.uint8), cv2.IMREAD_COLOR)
    assert decoded.shape[:2] == (10, 10)


def test_video_sampling_above_native_fps_uses_unique_frames_within_the_time_window(tmp_path):
    path = video(tmp_path / "ten-fps.avi", frames=30)
    config = workflow([node("observe", "observe", {"duration_seconds": 1, "sample_fps": 30})],
                      path, "video")
    result = completed(create_controller(tmp_path / "data"), config)
    ids = [finding.frame_ids[0] for finding in result.findings]
    assert [int(value.rsplit(":", 1)[1]) for value in ids] == list(range(10))
    assert len(ids) == len(set(ids))
    consumed = next(event["budget"]["used"] for event in result.trace
                    if event["type"] == "observation_completed")
    assert consumed["frames"] == 10
    assert consumed["observation_seconds"] == 1


@pytest.mark.parametrize("replacement_size", [(32, 32), (64, 64)])
def test_replacing_video_starts_new_motion_state_even_when_resolution_is_unchanged(tmp_path, replacement_size):
    first = video(tmp_path / "first.avi")
    second = video(tmp_path / "second.avi", size=replacement_size)
    config = workflow([analyze("first", next_ids=["input"]),
                       node("input", "request_input", {"reason": "choose replacement"}, ["second"]),
                       analyze("second")], first, "video")
    controller = create_controller(tmp_path / "data")
    run_id = controller.start(config).run_id
    assert controller.wait(run_id, timeout=5).status == "waiting_for_input"
    controller.resume(run_id, {"source": str(second)})
    result = controller.wait(run_id, timeout=5)
    assert result.status == "completed", result.trace
    assert len(result.findings) == 4
    assert result.findings[2].metrics["warmup"] is True


def test_text_only_resume_preserves_video_cursor_and_motion_state(tmp_path):
    path = video(tmp_path / "continue.avi")
    config = workflow([analyze("first", next_ids=["input"]),
                       node("input", "request_input", {"reason": "comment"}, ["second"]),
                       analyze("second")], path, "video")
    controller = create_controller(tmp_path / "data")
    run_id = controller.start(config).run_id
    assert controller.wait(run_id, timeout=5).status == "waiting_for_input"
    controller.resume(run_id, {"input": {"comment": "continue"}})
    result = controller.wait(run_id, timeout=5)
    assert result.status == "completed", result.trace
    assert [int(f.frame_ids[0].rsplit(":", 1)[1]) for f in result.findings] == [0, 1, 2, 3]
    assert result.findings[2].metrics["warmup"] is False
