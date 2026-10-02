from __future__ import annotations

import importlib
import json
import shutil
import subprocess
from pathlib import Path

import cv2
import numpy as np
import pytest

from visionflow.contracts import (
    Finding,
    Frame,
    InputConfig,
    Limits,
    ModelDecision,
    Node,
    OutputConfig,
    RunResult,
    WorkflowConfig,
)
from visionflow.recording import ClipRef, RecordingStore


def _require_video_model():
    try:
        return importlib.import_module("visionflow.video_model")
    except ImportError as exc:
        pytest.fail(
            f"Review Task 1 behavior is missing: visionflow.video_model is unavailable ({exc})",
            pytrace=False,
        )


def _require_model_context():
    module = importlib.import_module("visionflow.model")
    context_type = getattr(module, "ModelRunContext", None)
    if context_type is None:
        pytest.fail(
            "Review Task 1 behavior is missing: ModelRunContext is not available",
            pytrace=False,
        )
    return context_type


def _ffmpeg_path() -> Path:
    path = shutil.which("ffmpeg")
    if path is None:
        pytest.skip("FFmpeg libx264 is required to create a real recorded clip")
    result = subprocess.run(
        [path, "-hide_banner", "-encoders"],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    if result.returncode != 0 or "libx264" not in result.stdout:
        pytest.skip("FFmpeg libx264 is required to create a real recorded clip")
    return Path(path)


def _real_clip(tmp_path: Path, owner_id: str = "alice", source_id: str = "camera-1"):
    store = RecordingStore(
        root=tmp_path / "recordings",
        ffmpeg_path=_ffmpeg_path(),
        max_seconds=10,
        max_bytes=16 * 1024 * 1024,
    )
    store.open(owner_id, source_id)
    for index in range(41):
        image = np.full((180, 320, 3), (48, 58, 66), dtype=np.uint8)
        cv2.line(image, (145, 0), (90, 179), (215, 215, 215), 3)
        cv2.line(image, (175, 0), (230, 179), (215, 215, 215), 3)
        left = 18 + index * 4
        cv2.rectangle(image, (left, 90), (left + 46, 116), (40, 170, 230), -1)
        cv2.circle(image, (left + 10, 118), 6, (18, 18, 18), -1)
        cv2.circle(image, (left + 36, 118), 6, (18, 18, 18), -1)
        store.append(
            source_id,
            Frame(
                frame_id=f"road-frame-{index:03d}",
                image=image,
                timestamp_seconds=index / 20,
            ),
        )
    clip = store.event_clip(
        owner_id,
        source_id,
        event_time=0.75,
        before=0.25,
        after=0.25,
    )
    assert clip.path.read_bytes()[4:8] == b"ftyp"
    return store, clip


def real_video_request(
    tmp_path: Path,
    *,
    owner_id: str = "alice",
    source_id: str = "camera-1",
    run_id: str = "run-real-1",
    exchange_id: str = "exchange-real-1",
):
    video_model = _require_video_model()
    _, clip = _real_clip(tmp_path, owner_id, source_id)
    request = video_model.VideoRequest(
        exchange_id=exchange_id,
        case_id="case-real-1",
        run_id=run_id,
        clip=clip,
        prompt="Inspect the supplied road clip.",
        evidence_refs=(f"{run_id}/frame-000001.bin",),
        model_id="gemini-3.8-flash",
        processing_fps=5.0,
    )
    return clip, request


def _dummy_request(tmp_path: Path, *, run_id: str = "run-1", source_id: str = "camera-1"):
    video_model = _require_video_model()
    clip = ClipRef(
        clip_id="clip-1",
        source_id=source_id,
        version=1,
        path=tmp_path / "private-root" / "secret-clip.mp4",
        sha256="a" * 64,
        start_seconds=1.0,
        end_seconds=2.0,
        frame_ids=("road-frame-001", "road-frame-002", "road-frame-003"),
        source_fps=20.0,
        available_preroll=0.5,
    )
    return video_model.VideoRequest(
        exchange_id="exchange-1",
        case_id="case-1",
        run_id=run_id,
        clip=clip,
        prompt="Review the supplied road clip.",
        evidence_refs=(f"{run_id}/frame-000001.bin",),
        model_id="gemini-3.8-flash",
        processing_fps=5.0,
    )


def _assessment_json(request, **overrides) -> str:
    result = {
        "object_class": "bird",
        "hazard": False,
        "confidence": 0.93,
        "summary": "A bird crosses above the roadway.",
        "evidence_frame_ids": [request.clip.frame_ids[1]],
        "decision": {
            "action": "report",
            "payload": {"summary": "No vehicle is traveling against traffic."},
        },
    }
    result.update(overrides)
    return json.dumps(result)


def _finding(request) -> Finding:
    return Finding(
        tool="direction",
        frame_ids=list(request.clip.frame_ids[:2]),
        region=(0, 0, 320, 180),
        metrics={
            "opposition_count": 1,
            "warmup": False,
            "tracks": [
                {
                    "lane_id": "right",
                    "dx_px": 1.0,
                    "dy_px": -12.0,
                    "opposition": True,
                    "frame_ids": list(request.clip.frame_ids[:2]),
                }
            ],
        },
        evidence_ref=request.evidence_refs[0],
    )


def _context(run_id: str = "run-1", source_id: str = "camera-1"):
    context_type = _require_model_context()
    return context_type(
        run_id=run_id,
        source_id=source_id,
        cancelled=lambda: False,
        snapshot=lambda: RunResult(run_id=run_id, status="running"),
    )


def _config() -> WorkflowConfig:
    return WorkflowConfig(
        schema_version=1,
        name="road-video-review",
        input=InputConfig(kind="video", source="camera-1"),
        limits=Limits(),
        nodes=[
            Node(
                id="analyze",
                type="analyze",
                params={"tool": "direction", "tool_params": {}},
                next=[],
            )
        ],
        outputs=[OutputConfig(type="display")],
    )


def _config_with_observations(*regions, duration: float = 5.0, sample_fps: float = 5.0):
    config = _config()
    for index, region in enumerate(regions):
        config.nodes.append(
            Node(
                id=f"observe-{index}",
                type="observe",
                params={
                    "duration_seconds": duration,
                    "sample_fps": sample_fps,
                    "region": region,
                },
            )
        )
    return config


def _ask_with_observation(tmp_path, config, *, region, duration=3.0, sample_fps=2.0):
    video_model = _require_video_model()
    clip, request = real_video_request(tmp_path)
    finding = _finding(request)  # Its full-frame measurement box is not authority.
    raw_text = _assessment_json(
        request,
        decision={
            "action": "observe",
            "payload": {
                "source_id": clip.source_id,
                "region": region,
                "duration_seconds": duration,
                "sample_fps": sample_fps,
                "question": "Check the object direction.",
            },
        },
    )
    transport = _RecordingTransport(video_model, raw_text=raw_text, deltas=[raw_text])
    exchanges_module = importlib.import_module("visionflow.model_exchange")
    factory = video_model.VideoModelFactory(
        request_factory=lambda _context, _question, _findings: request,
        transport=transport,
        exchanges=exchanges_module.ModelExchangeStore(),
        owner_for_run=lambda _run_id: "alice",
    )
    adapter = factory.create_adapter(
        config,
        _context(run_id=request.run_id, source_id=clip.source_id),
    )
    return adapter.ask("Inspect the observation.", [finding])


class _RecordingTransport:
    def __init__(self, video_model, raw_text: str, deltas: list[str]):
        self._result_type = video_model.VideoTransportResult
        self.raw_text = raw_text
        self.deltas = deltas
        self.requests = []

    def generate(self, request, on_delta, cancelled):
        self.requests.append(request)
        if cancelled():
            raise RuntimeError("cancelled")
        for delta in self.deltas:
            on_delta(delta)
        return self._result_type(
            raw_text=self.raw_text,
            usage={"total_input_tokens": 31, "total_output_tokens": 12},
            request_id="offline-request-1",
        )


def test_actual_whole_clip_request_and_output_are_visible(tmp_path):
    video_model = _require_video_model()
    clip, request = real_video_request(tmp_path)
    finding = _finding(request)
    raw_text = _assessment_json(request)
    transport = _RecordingTransport(
        video_model,
        raw_text=raw_text,
        deltas=['{"object_class":', '"bird","hazard":false}'],
    )
    exchanges_module = importlib.import_module("visionflow.model_exchange")
    exchanges = exchanges_module.ModelExchangeStore()
    requested = []

    def request_factory(context, question, findings):
        requested.append((context, question, findings))
        return request

    factory = video_model.VideoModelFactory(
        request_factory=request_factory,
        transport=transport,
        exchanges=exchanges,
        owner_for_run=lambda run_id: "alice",
    )
    context = _context(run_id="run-real-1", source_id=clip.source_id)
    adapter = factory.create_adapter(_config(), context)

    decision = adapter.ask("Is this a wrong-way vehicle?", [finding])

    assert decision == ModelDecision(
        "report", {"summary": "No vehicle is traveling against traffic."}
    )
    assert requested[0][0] is context
    assert requested[0][1] == "Is this a wrong-way vehicle?"
    assert requested[0][2][0] == finding
    assert transport.requests[0].clip.sha256 == request.clip.sha256
    assert transport.requests[0].processing_fps == 5.0
    seen = exchanges.snapshot("alice", request.exchange_id)
    assert seen["prompt"] == transport.requests[0].prompt
    assert "configured lane direction=true" in seen["prompt"]
    assert "Evidence frame time references" in seen["prompt"]
    assert "Source ID: camera-1" in seen["prompt"]
    assert f"Clip ID and version: {request.clip.clip_id} / {request.clip.version}" in seen["prompt"]
    assert "Source time range:" in seen["prompt"]
    assert f"Source capture FPS: {request.clip.source_fps:.3f}" in seen["prompt"]
    assert "road-frame-" in seen["prompt"]
    assert '"duration_seconds":number' in seen["prompt"]
    assert '"sample_fps":number' in seen["prompt"]
    assert '"question":"..."' in seen["prompt"]
    assert "object_class" in seen["prompt"]
    assert "Ignore simulator flags" in seen["prompt"]
    assert seen["clip"]["sha256"] == request.clip.sha256
    assert seen["raw_output"] == raw_text
    assert seen["assessment"]["object_class"] == "bird"
    assert seen["assessment"]["hazard"] is False
    assert decision.action == "report"
    assert str(request.clip.path) not in json.dumps(seen)


def test_video_prompt_keeps_encoded_times_relative_and_uses_source_times_only_from_manifest(tmp_path):
    video_model = _require_video_model()
    request = _dummy_request(tmp_path)
    finding = _finding(request)

    without_manifest = video_model.build_video_prompt(
        "Review this clip.", [finding], request.clip
    )
    assert "road-frame-002: encoded clip time 0.050 seconds from MP4 start" in without_manifest
    assert "recorder manifest source time" not in without_manifest
    assert "clip time 1.050 seconds" not in without_manifest

    with_manifest = video_model.build_video_prompt(
        "Review this clip.",
        [finding],
        request.clip,
        frame_source_times={"road-frame-002": 1.047},
    )
    assert (
        "road-frame-002: encoded clip time 0.050 seconds from MP4 start; "
        "recorder manifest source time 1.047 seconds"
    ) in with_manifest

    for field in (
        '"source_id":"camera-1"',
        '"region":[x,y,width,height]',
        '"duration_seconds":number',
        '"sample_fps":number',
        '"question":"..."',
    ):
        assert field in with_manifest


def test_model_run_context_is_frozen_and_contains_only_run_callbacks():
    context = _context()

    assert context.run_id == "run-1"
    assert context.source_id == "camera-1"
    assert context.cancelled() is False
    assert context.snapshot().status == "running"
    with pytest.raises((AttributeError, TypeError)):
        context.run_id = "other-run"


def test_video_assessment_is_frozen_and_has_a_small_public_dict(tmp_path):
    video_model = _require_video_model()
    request = _dummy_request(tmp_path)
    assessment = video_model.validate_video_assessment(
        _assessment_json(request),
        request,
        permitted_region=(0, 0, 320, 180),
    )

    assert assessment.object_class == "bird"
    assert assessment.hazard is False
    assert assessment.confidence == pytest.approx(0.93)
    assert assessment.evidence_frame_ids == (request.clip.frame_ids[1],)
    assert set(assessment.to_dict()) == {
        "object_class",
        "hazard",
        "confidence",
        "summary",
        "evidence_frame_ids",
        "decision",
    }
    assert assessment.to_dict()["decision"] == {
        "action": "report",
        "payload": {"summary": "No vehicle is traveling against traffic."},
    }
    assert str(request.clip.path) not in json.dumps(assessment.to_dict())
    with pytest.raises((AttributeError, TypeError)):
        assessment.confidence = 0.1


@pytest.mark.parametrize(
    "overrides",
    [
        {"confidence": True},
        {"confidence": float("nan")},
        {"confidence": 1.01},
        {"confidence": None},
        {"hazard": 1},
        {"object_class": "truck"},
        {"evidence_frame_ids": ["frame-from-another-clip"]},
        {"decision": {"action": "request_approval", "payload": {"action_type": "notification_draft"}}},
    ],
)
def test_assessment_validation_rejects_bad_scores_evidence_and_actions(tmp_path, overrides):
    video_model = _require_video_model()
    request = _dummy_request(tmp_path)

    with pytest.raises(ValueError):
        video_model.validate_video_assessment(
            _assessment_json(request, **overrides),
            request,
            permitted_region=(0, 0, 320, 180),
        )


def test_assessment_observe_must_stay_within_clip_source_and_permitted_region(tmp_path):
    video_model = _require_video_model()
    request = _dummy_request(tmp_path)
    allowed = _assessment_json(
        request,
        decision={
            "action": "observe",
            "payload": {
                "source_id": request.clip.source_id,
                "region": [5, 5, 20, 20],
                "duration_seconds": 3,
                "sample_fps": 2,
                "question": "Check whether the object continues moving.",
            },
        },
    )

    assessment = video_model.validate_video_assessment(
        allowed,
        request,
        permitted_region=(0, 0, 40, 40),
    )
    assert assessment.decision.action == "observe"

    for source_id, region in (
        ("another-camera", [5, 5, 20, 20]),
        (request.clip.source_id, [35, 35, 20, 20]),
    ):
        invalid = _assessment_json(
            request,
            decision={
                "action": "observe",
                "payload": {
                    "source_id": source_id,
                    "region": region,
                    "duration_seconds": 3,
                    "sample_fps": 2,
                    "question": "Check movement.",
                },
            },
        )
        with pytest.raises(ValueError):
            video_model.validate_video_assessment(
                invalid,
                request,
                permitted_region=(0, 0, 40, 40),
            )


def test_observe_authority_uses_configured_roi_not_full_frame_finding(tmp_path):
    video_model = _require_video_model()

    with pytest.raises(video_model.VideoModelError) as error:
        _ask_with_observation(
            tmp_path,
            _config_with_observations((40, 40, 80, 80)),
            region=[0, 0, 320, 180],
        )

    assert error.value.code == "invalid_response"


def test_observe_authority_does_not_union_disjoint_configured_rois(tmp_path):
    video_model = _require_video_model()

    with pytest.raises(video_model.VideoModelError) as error:
        _ask_with_observation(
            tmp_path,
            _config_with_observations((0, 0, 40, 40), (80, 0, 40, 40)),
            region=[50, 5, 10, 10],
        )

    assert error.value.code == "invalid_response"


def test_observe_requires_a_configured_authority_node(tmp_path):
    video_model = _require_video_model()

    with pytest.raises(video_model.VideoModelError) as error:
        _ask_with_observation(
            tmp_path,
            _config(),
            region=[5, 5, 10, 10],
        )

    assert error.value.code == "invalid_response"


def test_observe_must_fit_one_nodes_region_duration_and_sample_rate(tmp_path):
    decision = _ask_with_observation(
        tmp_path,
        _config_with_observations((40, 40, 80, 80), duration=4.0, sample_fps=3.0),
        region=[50, 50, 10, 10],
        duration=3.0,
        sample_fps=2.0,
    )

    assert decision.action == "observe"
    assert decision.payload == {
        "source_id": "camera-1",
        "region": (50.0, 50.0, 10.0, 10.0),
        "duration_seconds": 3.0,
        "sample_fps": 2.0,
        "question": "Check the object direction.",
    }


@pytest.mark.parametrize(
    ("duration", "sample_fps"),
    [(4.1, 3.0), (3.0, 3.1)],
)
def test_observe_cannot_exceed_one_nodes_duration_or_sample_rate(
    tmp_path,
    duration,
    sample_fps,
):
    video_model = _require_video_model()

    with pytest.raises(video_model.VideoModelError) as error:
        _ask_with_observation(
            tmp_path,
            _config_with_observations((40, 40, 80, 80), duration=4.0, sample_fps=3.0),
            region=[50, 50, 10, 10],
            duration=duration,
            sample_fps=sample_fps,
        )

    assert error.value.code == "invalid_response"


def test_fixture_video_transport_requires_an_explicit_offline_response(tmp_path):
    video_model = _require_video_model()
    request = _dummy_request(tmp_path)
    raw_text = _assessment_json(request)
    response = video_model.VideoTransportResult(
        raw_text=raw_text,
        usage=None,
        request_id=None,
    )

    with pytest.raises(TypeError):
        video_model.FixtureVideoTransport()

    transport = video_model.FixtureVideoTransport(response=response)
    deltas = []
    actual = transport.generate(request, deltas.append, lambda: False)
    assert actual == response
    assert deltas == [raw_text]


def test_incomplete_assessment_text_is_not_decided_or_saved_as_complete(tmp_path):
    video_model = _require_video_model()
    request = _dummy_request(tmp_path)
    exchanges = importlib.import_module("visionflow.model_exchange").ModelExchangeStore()
    transport = _RecordingTransport(
        video_model,
        raw_text='{"object_class":"bird"',
        deltas=['{"object_class":"bird"'],
    )
    factory = video_model.VideoModelFactory(
        request_factory=lambda _context, _question, _findings: request,
        transport=transport,
        exchanges=exchanges,
        owner_for_run=lambda _run_id: "alice",
    )
    adapter = factory.create_adapter(
        _config(),
        _context(run_id=request.run_id, source_id=request.clip.source_id),
    )

    with pytest.raises(video_model.VideoModelError) as error:
        adapter.ask("Review the clip.", [_finding(request)])

    assert error.value.code == "invalid_response"
    snapshot = exchanges.snapshot("alice", request.exchange_id)
    assert snapshot["status"] == "failed"
    assert snapshot["raw_output"] is None
    assert snapshot["assessment"] is None
    assert snapshot["deltas"] == ['{"object_class":"bird"']


def test_provider_exception_text_is_not_published_in_exchange(tmp_path):
    video_model = _require_video_model()
    request = _dummy_request(tmp_path, run_id="run-secret", source_id="camera-1")
    exchanges = importlib.import_module("visionflow.model_exchange").ModelExchangeStore()

    class SecretFailureTransport:
        def generate(self, _request, _on_delta, _cancelled):
            raise RuntimeError("Authorization: Bearer provider-secret-value")

    factory = video_model.VideoModelFactory(
        request_factory=lambda _context, _question, _findings: request,
        transport=SecretFailureTransport(),
        exchanges=exchanges,
        owner_for_run=lambda _run_id: "alice",
    )
    adapter = factory.create_adapter(
        _config(),
        _context(run_id=request.run_id, source_id=request.clip.source_id),
    )

    with pytest.raises(video_model.VideoModelError) as error:
        adapter.ask("Review the clip.", [_finding(request)])

    snapshot = exchanges.snapshot("alice", request.exchange_id)
    assert error.value.code == "provider_failed"
    assert snapshot["error_code"] == "provider_failed"
    assert "provider-secret-value" not in json.dumps(snapshot)
