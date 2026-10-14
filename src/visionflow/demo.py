"""Reproducible local demos using synthetic media and the fixture model only."""

from __future__ import annotations

import json
import math
import time
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from .contracts import Finding, ModelDecision, RunResult, WorkflowConfig
from .model import FixtureModelAdapter
from .runtime import create_controller, create_registry
from .workflow_resources import load_builtin_workflow


class _DemoClock:
    """Advance virtual observation time without sleeping in a local demo."""

    def __init__(self) -> None:
        self._now = 0.0

    def monotonic(self) -> float:
        return self._now

    def sleep(self, seconds: float) -> None:
        self._now += seconds


def _write_png(path: Path, image: np.ndarray) -> None:
    success, encoded = cv2.imencode(".png", image)
    if not success:
        raise RuntimeError(f"OpenCV could not encode synthetic image: {path.name}")
    with path.open("xb") as stream:
        stream.write(encoded.tobytes())


def _document_images() -> tuple[np.ndarray, np.ndarray]:
    sharp = np.full((240, 320, 3), 255, dtype=np.uint8)
    cv2.rectangle(sharp, (14, 14), (305, 225), (20, 20, 20), 2)
    cv2.putText(
        sharp,
        "VISION FLOW",
        (28, 60),
        cv2.FONT_HERSHEY_SIMPLEX,
        1.0,
        (0, 0, 0),
        2,
        cv2.LINE_AA,
    )
    for y in range(88, 205, 24):
        cv2.line(sharp, (28, y), (288, y), (30, 30, 30), 2, cv2.LINE_8)
        cv2.rectangle(sharp, (28, y + 5), (34 + (y % 31), y + 13), (10, 10, 10), -1)
    blurred = cv2.GaussianBlur(sharp, (19, 19), sigmaX=4.0, sigmaY=4.0)
    return blurred, sharp


def _write_motion_video(path: Path) -> None:
    video_fps = 10.0
    writer = cv2.VideoWriter(
        str(path),
        cv2.VideoWriter_fourcc(*"MJPG"),
        video_fps,
        (32, 24),
    )
    if not writer.isOpened():
        raise RuntimeError("OpenCV MJPG writer is unavailable for the local demo")
    try:
        for frame_index in range(30):
            x = 2 if frame_index < 5 else min(25, frame_index - 2)
            frame = np.zeros((24, 32, 3), dtype=np.uint8)
            cv2.rectangle(frame, (x, 8), (x + 5, 15), (255, 255, 255), -1)
            writer.write(frame)
    finally:
        writer.release()
    if not path.is_file() or path.stat().st_size <= 0:
        raise RuntimeError("OpenCV did not create the synthetic motion video")


def _wait_for_status(controller: Any, run_id: str, expected: str) -> RunResult:
    deadline = time.monotonic() + 10.0
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError(f"demo run did not reach {expected!r}")
        result = controller.wait(run_id, timeout=min(0.1, remaining))
        if result.status == expected:
            if expected == "completed":
                # A terminal snapshot is published before VideoCapture.release;
                # join the daemon worker before this CLI can finalize Python.
                return controller.join(run_id, timeout=max(0.0, deadline - time.monotonic()))
            return result
        if result.status in {"failed", "cancelled"}:
            controller.join(run_id, timeout=max(0.0, deadline - time.monotonic()))
            raise RuntimeError(f"demo run ended in {result.status!r}")


def _frame_ids(findings: list[Finding]) -> list[str]:
    return list(dict.fromkeys(frame_id for finding in findings for frame_id in finding.frame_ids))


def _evidence_paths(findings: list[Finding], evidence_root: Path) -> list[str]:
    root = Path(evidence_root).resolve(strict=True)
    paths: list[str] = []
    for finding in findings:
        if not finding.evidence_ref:
            continue
        candidate = (root / finding.evidence_ref).resolve(strict=True)
        if not candidate.is_relative_to(root) or not candidate.is_file():
            raise RuntimeError("finding evidence is missing or outside the demo evidence root")
        paths.append(str(candidate))
    return list(dict.fromkeys(paths))


def _write_trace(trace_dir: Path, scenario: str, result: RunResult) -> Path:
    path = trace_dir / f"{scenario}.json"
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(result.trace, stream, ensure_ascii=False, allow_nan=False, indent=2)
        stream.write("\n")
    return path.resolve()


def _decisions_for_run(config: WorkflowConfig) -> list[ModelDecision]:
    if config.name != "motion-observation":
        return [ModelDecision("report", {})]
    return [
        ModelDecision(
            "observe",
            {
                "source_id": config.input.source,
                "region": None,
                "duration_seconds": 1.5,
                "sample_fps": 2.0,
                "question": "Prüfe die Bewegung nach dem ersten Ausschnitt.",
            },
        ),
        ModelDecision("report", {}),
    ]


def _quality_value(findings: list[Finding]) -> float:
    matches = [
        finding
        for finding in findings
        if finding.tool == "quality" and "sharpness_variance" in finding.metrics
    ]
    if not matches:
        raise RuntimeError("quality scenario produced no sharpness measurement")
    value = matches[-1].metrics["sharpness_variance"]
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise RuntimeError("quality scenario returned an invalid sharpness measurement")
    return float(value)


def _ids_for_source(findings: list[Finding], source: Path) -> list[str]:
    prefix = str(source.resolve()) + ":"
    return [frame_id for frame_id in _frame_ids(findings) if frame_id.startswith(prefix)]


def run_demo(output_dir: Path) -> dict[str, Any]:
    """Run both local examples and write their actual controller traces.

    The target directory must not exist. Synthetic media, evidence, and trace JSON
    are written beneath it; the examples use OpenCV and the fixture adapter only.
    """
    target = Path(output_dir).expanduser().resolve()
    target.mkdir(parents=True, exist_ok=False)
    media_dir = target / "media"
    trace_dir = target / "traces"
    media_dir.mkdir()
    trace_dir.mkdir()

    if cv2.__version__.split(".", 1)[0] != "5":
        raise RuntimeError(f"the local demo requires OpenCV 5, found {cv2.__version__}")

    blurred_path = media_dir / "Prüfseite_unscharf.png"
    sharp_path = media_dir / "Prüfseite_scharf.png"
    blurred, sharp = _document_images()
    _write_png(blurred_path, blurred)
    _write_png(sharp_path, sharp)

    motion_path = media_dir / "synthetic_motion.avi"
    _write_motion_video(motion_path)

    registry = create_registry()
    document_config = load_builtin_workflow("document-quality.json", registry)
    document_config.input.source = str(blurred_path.resolve())
    motion_config = load_builtin_workflow("motion-observation.json", registry)
    motion_config.input.source = str(motion_path.resolve())

    controller = create_controller(
        target,
        fixture_decisions=_decisions_for_run,
        clock=_DemoClock(),
    )

    document_queued = controller.start(document_config)
    document_waiting = _wait_for_status(controller, document_queued.run_id, "waiting_for_input")
    if not document_waiting.findings:
        raise RuntimeError("document demo did not analyze its initial image")
    input_resume = controller.resume(document_queued.run_id, {"source": str(sharp_path.resolve())})
    document_final = _wait_for_status(controller, input_resume.run_id, "completed")
    initial_ids = _ids_for_source(document_waiting.findings, blurred_path)
    replacement_ids = _ids_for_source(document_final.findings, sharp_path)
    initial_sharpness = _quality_value(document_waiting.findings)
    replacement_sharpness = _quality_value(
        [finding for finding in document_final.findings if any(
            frame_id in replacement_ids for frame_id in finding.frame_ids
        )]
    )
    if not initial_ids or not replacement_ids or replacement_sharpness <= initial_sharpness:
        raise RuntimeError("document recheck did not measure a sharper replacement image")

    motion_queued = controller.start(motion_config)
    motion_final = _wait_for_status(controller, motion_queued.run_id, "completed")
    motion_source = Path(motion_config.input.source)
    initial_motion_ids: list[str] = []
    follow_up_ids: list[str] = []
    for frame_id in _frame_ids(motion_final.findings):
        if not frame_id.startswith(str(motion_source.resolve()) + ":"):
            continue
        index = int(frame_id.rsplit(":", 1)[1])
        (initial_motion_ids if index < 2 else follow_up_ids).append(frame_id)
    motion_decisions = [
        event.get("state")
        for event in motion_final.trace
        if event.get("type") == "model_decision"
    ]
    observed_events = [
        event for event in motion_final.trace if event.get("type") == "frame_observed"
    ]
    if (
        not initial_motion_ids
        or not follow_up_ids
        or max(int(item.rsplit(":", 1)[1]) for item in initial_motion_ids)
        >= min(int(item.rsplit(":", 1)[1]) for item in follow_up_ids)
        or motion_decisions != ["observe", "report"]
        or len(observed_events) < len(follow_up_ids)
    ):
        raise RuntimeError("motion demo did not prove a cursor-preserving model follow-up")

    document_trace_path = _write_trace(trace_dir, "document-quality", document_final)
    motion_trace_path = _write_trace(trace_dir, "motion-observation", motion_final)
    evidence_root = target / "evidence"
    document_evidence = _evidence_paths(document_final.findings, evidence_root)
    motion_evidence = _evidence_paths(motion_final.findings, evidence_root)
    if not document_evidence or not motion_evidence:
        raise RuntimeError("demo findings are missing stored frame evidence")

    return {
        "status": "passed",
        "opencv_version": cv2.__version__,
        "scenario_results": {
            "document-quality": {
                "status": document_final.status,
                "input_resume_status": document_final.status,
                "initial_frame_ids": initial_ids,
                "replacement_frame_ids": replacement_ids,
                "initial_sharpness_variance": initial_sharpness,
                "replacement_sharpness_variance": replacement_sharpness,
                "evidence_paths": document_evidence,
            },
            "motion-observation": {
                "status": motion_final.status,
                "follow_up_observed": bool(follow_up_ids and observed_events),
                "initial_frame_ids": initial_motion_ids,
                "follow_up_frame_ids": follow_up_ids,
                "evidence_paths": motion_evidence,
            },
        },
        "trace_paths": {
            "document-quality": str(document_trace_path),
            "motion-observation": str(motion_trace_path),
        },
        "model_mode": FixtureModelAdapter.MODE,
        "network_used": False,
    }


__all__ = ["run_demo"]
