from __future__ import annotations

import json
from pathlib import Path

import cv2
import pytest

from visionflow.demo import run_demo


def _read_trace(result: dict, scenario: str) -> list[dict]:
    path = Path(result["trace_paths"][scenario])
    assert path.is_file()
    data = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(data, list)
    assert data
    return data


def _frame_index(frame_id: str) -> int:
    return int(frame_id.rsplit(":", 1)[1])


def test_demo_proves_follow_up_and_two_use_cases(tmp_path):
    output_dir = tmp_path / "Prüfung" / "demo"
    result = run_demo(output_dir)

    assert result["status"] == "passed"
    assert result["opencv_version"].split(".")[0] == "5"
    assert set(result["scenario_results"]) == {
        "document-quality",
        "motion-observation",
    }
    assert result["model_mode"] == "fixture"
    assert result["network_used"] is False
    assert set(result["trace_paths"]) == set(result["scenario_results"])

    document = result["scenario_results"]["document-quality"]
    assert document["status"] == "completed"
    assert document["input_resume_status"] == "completed"
    assert document["initial_frame_ids"]
    assert document["replacement_frame_ids"]
    assert set(document["initial_frame_ids"]).isdisjoint(
        document["replacement_frame_ids"]
    )
    assert document["replacement_sharpness_variance"] > document["initial_sharpness_variance"]
    evidence_root = (output_dir.resolve() / "evidence").resolve()
    assert all(
        Path(path).resolve().is_relative_to(evidence_root)
        for path in document["evidence_paths"]
    )
    assert all(Path(path).is_file() for path in document["evidence_paths"])

    document_trace = _read_trace(result, "document-quality")
    document_nodes = [event["node_id"] for event in document_trace if event["type"] == "node_started"]
    assert document_nodes == [
        "analyze_initial",
        "request_replacement",
        "analyze_replacement",
        "report",
    ]
    assert any(
        event.get("state") == "waiting_for_input"
        for event in document_trace
        if event["type"] == "state_change"
    )
    assert any(event["type"] == "frame_analyzed" for event in document_trace)

    motion = result["scenario_results"]["motion-observation"]
    assert motion["status"] == "completed"
    assert motion["follow_up_observed"] is True
    assert motion["initial_frame_ids"]
    assert motion["follow_up_frame_ids"]
    assert set(motion["initial_frame_ids"]).isdisjoint(motion["follow_up_frame_ids"])
    assert max(map(_frame_index, motion["initial_frame_ids"])) < min(
        map(_frame_index, motion["follow_up_frame_ids"])
    )
    assert all(Path(path).is_file() for path in motion["evidence_paths"])

    motion_trace = _read_trace(result, "motion-observation")
    assert any(
        event.get("state") == "observing"
        for event in motion_trace
        if event["type"] == "state_change"
    )
    assert len([event for event in motion_trace if event["type"] == "frame_observed"]) >= len(
        motion["follow_up_frame_ids"]
    )


def test_demo_samples_video_on_its_media_timeline(tmp_path):
    result = run_demo(tmp_path / "media-timeline")
    motion = result["scenario_results"]["motion-observation"]
    sampled_indices = sorted(map(_frame_index, motion["follow_up_frame_ids"]))
    video_path = motion["initial_frame_ids"][0].rsplit(":", 1)[0]
    capture = cv2.VideoCapture(video_path)
    try:
        assert capture.isOpened()
        assert capture.get(cv2.CAP_PROP_FPS) == pytest.approx(10.0)
        assert int(capture.get(cv2.CAP_PROP_FRAME_COUNT)) == 30
    finally:
        capture.release()

    assert len(sampled_indices) == 3
    assert [right - left for left, right in zip(sampled_indices, sampled_indices[1:])] == [5, 5]


def test_demo_does_not_overwrite_existing_output(tmp_path):
    target = tmp_path / "demo"
    target.mkdir()
    sentinel = target / "keep.txt"
    sentinel.write_text("unchanged", encoding="utf-8")

    with pytest.raises(FileExistsError):
        run_demo(target)

    assert sentinel.read_text(encoding="utf-8") == "unchanged"
    assert sorted(path.name for path in target.iterdir()) == ["keep.txt"]
