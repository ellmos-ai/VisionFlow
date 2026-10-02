from __future__ import annotations

import importlib
import json

import pytest

from visionflow.contracts import ModelDecision
from visionflow.recording import ClipRef


def _require_exchange_components():
    try:
        video_model = importlib.import_module("visionflow.video_model")
        exchange = importlib.import_module("visionflow.model_exchange")
    except ImportError as exc:
        pytest.fail(
            f"Review Task 1 behavior is missing: model exchange modules are unavailable ({exc})",
            pytrace=False,
        )
    return video_model, exchange


def _request(video_model, tmp_path, exchange_id="exchange-owner-1"):
    clip = ClipRef(
        clip_id="clip-owner-1",
        source_id="camera-owner-1",
        version=2,
        path=tmp_path / "private" / "sensitive-local-name.mp4",
        sha256="b" * 64,
        start_seconds=2.0,
        end_seconds=4.0,
        frame_ids=("frame-a", "frame-b", "frame-c"),
        source_fps=20.0,
        available_preroll=1.5,
    )
    return video_model.VideoRequest(
        exchange_id=exchange_id,
        case_id="case-owner-1",
        run_id="run-owner-1",
        clip=clip,
        prompt="Look at the entire clip and the trigger evidence.",
        evidence_refs=("run-owner-1/frame-000001.bin",),
        model_id="fixture-video",
        processing_fps=5.0,
    )


def _assessment(video_model):
    return video_model.VideoAssessment(
        object_class="bird",
        hazard=False,
        confidence=0.9,
        summary="A bird is visible.",
        evidence_frame_ids=("frame-b",),
        decision=ModelDecision("report", {"summary": "No wrong-way vehicle."}),
    )


def test_public_exchange_is_owner_scoped_pathless_and_returns_detached_snapshots(tmp_path):
    video_model, exchange_module = _require_exchange_components()
    request = _request(video_model, tmp_path)
    store = exchange_module.ModelExchangeStore()

    assert store.record_request("alice", request) is None
    store.append_delta(request.exchange_id, '{"object_class":')
    store.append_delta(request.exchange_id, '"bird"}')
    store.complete(
        request.exchange_id,
        _assessment(video_model),
        raw_output='{"object_class":"bird"}',
        usage={"total_input_tokens": 25, "total_output_tokens": 9},
    )

    snapshot = store.snapshot("alice", request.exchange_id)
    assert snapshot["prompt"] == request.prompt
    assert snapshot["clip"] == video_model.clip_public_metadata(request.clip)
    assert snapshot["deltas"] == ['{"object_class":', '"bird"}']
    assert snapshot["raw_output"] == '{"object_class":"bird"}'
    assert snapshot["assessment"]["object_class"] == "bird"
    assert snapshot["usage"] == {
        "total_input_tokens": 25,
        "total_output_tokens": 9,
    }
    assert set(snapshot["clip"]) == {
        "clip_id",
        "source_id",
        "version",
        "sha256",
        "start_seconds",
        "end_seconds",
        "frame_ids",
        "source_fps",
        "available_preroll",
    }
    public_json = json.dumps(snapshot)
    assert str(request.clip.path) not in public_json
    assert "sensitive-local-name.mp4" not in public_json
    assert "api_key" not in public_json.lower()
    assert "authorization" not in public_json.lower()

    snapshot["clip"]["clip_id"] = "mutated"
    assert store.snapshot("alice", request.exchange_id)["clip"]["clip_id"] == "clip-owner-1"
    with pytest.raises(KeyError):
        store.snapshot("bob", request.exchange_id)


def test_failed_exchange_exposes_only_a_safe_code(tmp_path):
    video_model, exchange_module = _require_exchange_components()
    request = _request(video_model, tmp_path, exchange_id="exchange-failed-1")
    store = exchange_module.ModelExchangeStore()
    store.record_request("alice", request)

    store.fail(request.exchange_id, "provider_failed")

    snapshot = store.snapshot("alice", request.exchange_id)
    assert snapshot["error_code"] == "provider_failed"
    assert "exception" not in snapshot
    assert "secret" not in json.dumps(snapshot).lower()
