from __future__ import annotations

import base64
from concurrent.futures import ThreadPoolExecutor
import dataclasses
import importlib
import json
import threading
from pathlib import Path

import cv2
import numpy as np
import pytest

from test_video_model import real_video_request


def _require_gemini_transport():
    try:
        return importlib.import_module("visionflow.gemini_video")
    except ImportError as exc:
        pytest.fail(
            f"Review Task 1 behavior is missing: Gemini video transport is unavailable ({exc})",
            pytrace=False,
        )


def _png_bytes() -> bytes:
    image = np.zeros((32, 48, 3), dtype=np.uint8)
    cv2.rectangle(image, (8, 8), (38, 24), (20, 150, 240), -1)
    ok, encoded = cv2.imencode(".png", image)
    assert ok
    return encoded.tobytes()


def _jpeg_bytes() -> bytes:
    image = np.zeros((32, 48, 3), dtype=np.uint8)
    cv2.circle(image, (23, 16), 9, (240, 120, 20), -1)
    ok, encoded = cv2.imencode(".jpg", image)
    assert ok
    return encoded.tobytes()


def _event(event_type: str, **fields) -> dict:
    return {"event_type": event_type, **fields}


class _Response:
    status = 200
    headers = {"Content-Type": "text/event-stream"}

    def __init__(self, events):
        self.lines = []
        for event in events:
            self.lines.append(b"data: " + json.dumps(event).encode("utf-8") + b"\n")
            self.lines.append(b"\n")
        self.lines.append(b"")
        self.closed = False

    def readline(self):
        if self.closed or not self.lines:
            return b""
        return self.lines.pop(0)

    def close(self):
        self.closed = True


def _successful_events(text: str) -> list[dict]:
    return [
        _event(
            "interaction.created",
            interaction={"id": "native-request-1", "status": "in_progress"},
        ),
        _event("step.start", index=1, step={"type": "thought"}),
        _event("step.delta", index=1, delta={"type": "text", "text": "private thought"}),
        _event("step.start", index=2, step={"type": "model_output"}),
        _event("step.delta", index=2, delta={"type": "text", "text": text[:20]}),
        _event("step.delta", index=2, delta={"type": "text", "text": text[20:]}),
        _event(
            "step.stop",
            index=2,
            step_usage={"total_input_tokens": 45, "total_output_tokens": 11},
        ),
        _event(
            "interaction.completed",
            interaction={
                "id": "native-request-1",
                "status": "completed",
                "usage": {"total_input_tokens": 45, "total_output_tokens": 11},
            },
        ),
    ]


@pytest.fixture(scope="module")
def native_case(tmp_path_factory):
    try:
        importlib.import_module("visionflow.video_model")
    except ImportError:
        return None
    path = tmp_path_factory.mktemp("gemini-video")
    return real_video_request(path, run_id="run-native-1", exchange_id="exchange-native-1")


def test_native_transport_sends_whole_mp4_evidence_images_then_prompt_and_filters_steps(native_case):
    gemini_video = _require_gemini_transport()
    clip, original_request = native_case
    request = dataclasses.replace(
        original_request,
        evidence_refs=(
            "run-native-1/frame-000001.bin",
            "run-native-1/frame-000002.bin",
        ),
    )
    image_by_ref = {
        request.evidence_refs[0]: _png_bytes(),
        request.evidence_refs[1]: _jpeg_bytes(),
    }
    text = '{"object_class":"bird","hazard":false}'
    calls = []

    def http_open(http_request, timeout):
        calls.append((http_request, timeout))
        return _Response(_successful_events(text))

    transport = gemini_video.GeminiVideoTransport(
        api_key="test-secret-never-public",
        cost_approved=True,
        evidence_reader=lambda ref: image_by_ref[ref],
        http_open=http_open,
    )
    deltas = []

    result = transport.generate(request, deltas.append, lambda: False)

    assert len(calls) == 1
    http_request, timeout = calls[0]
    assert http_request.full_url.endswith("/v1beta/interactions")
    assert timeout <= 30.0
    assert http_request.get_header("X-goog-api-key") == "test-secret-never-public"
    body = json.loads(http_request.data.decode("utf-8"))
    assert body["model"] == "gemini-3.8-flash"
    assert body["stream"] is True
    assert "role" not in body
    parts = body["input"]
    assert [part["type"] for part in parts] == ["video", "image", "image", "text"]
    assert parts[0]["mime_type"] == "video/mp4"
    assert parts[0]["processing"] == {"type": "static", "fps": 5.0}
    assert base64.b64decode(parts[0]["data"]) == clip.path.read_bytes()
    assert parts[1]["mime_type"] == "image/png"
    assert base64.b64decode(parts[1]["data"]) == image_by_ref[request.evidence_refs[0]]
    assert parts[2]["mime_type"] == "image/jpeg"
    assert base64.b64decode(parts[2]["data"]) == image_by_ref[request.evidence_refs[1]]
    assert parts[3] == {"type": "text", "text": request.prompt}
    assert result.raw_text == text
    assert result.request_id == "native-request-1"
    assert result.usage == {"total_input_tokens": 45, "total_output_tokens": 11}
    assert "".join(deltas) == text
    assert "private thought" not in "".join(deltas)


def test_native_transport_requires_credentials_and_explicit_cost_approval():
    gemini_video = _require_gemini_transport()

    with pytest.raises(ValueError):
        gemini_video.GeminiVideoTransport(api_key=None, cost_approved=True)
    with pytest.raises(ValueError):
        gemini_video.GeminiVideoTransport(
            api_key="test-secret",
            cost_approved=False,
        )


def test_cancel_before_transport_io_skips_clip_and_evidence_reads(native_case):
    gemini_video = _require_gemini_transport()
    _, request = native_case
    cancelled = threading.Event()
    cancelled.set()
    evidence_reads = []
    http_calls = []

    transport = gemini_video.GeminiVideoTransport(
        api_key="test-secret",
        cost_approved=True,
        evidence_reader=lambda ref: evidence_reads.append(ref) or _png_bytes(),
        http_open=lambda http_request, timeout: http_calls.append(http_request),
    )
    with pytest.raises(gemini_video.VideoTransportError) as error:
        transport.generate(request, lambda _delta: None, cancelled.is_set)

    assert error.value.code == "cancelled"
    assert evidence_reads == []
    assert http_calls == []


def test_cancel_after_evidence_read_prevents_http_start(native_case):
    gemini_video = _require_gemini_transport()
    _, request = native_case
    cancelled = threading.Event()
    evidence_reads = []
    http_calls = []

    def evidence_reader(ref):
        evidence_reads.append(ref)
        cancelled.set()
        return _png_bytes()

    transport = gemini_video.GeminiVideoTransport(
        api_key="test-secret",
        cost_approved=True,
        evidence_reader=evidence_reader,
        http_open=lambda http_request, timeout: http_calls.append(http_request),
    )
    with pytest.raises(gemini_video.VideoTransportError) as error:
        transport.generate(request, lambda _delta: None, cancelled.is_set)

    assert error.value.code == "cancelled"
    assert evidence_reads == [request.evidence_refs[0]]
    assert http_calls == []


def test_cancel_during_connect_interrupts_the_wait(native_case):
    gemini_video = _require_gemini_transport()
    _, request = native_case
    cancelled = threading.Event()
    connect_started = threading.Event()
    release_connect = threading.Event()
    response_closed = threading.Event()

    class ClosableResponse(_Response):
        def close(self):
            super().close()
            response_closed.set()

    response = ClosableResponse(_successful_events("final"))

    def blocked_open(_http_request, timeout):
        connect_started.set()
        release_connect.wait(2)
        return response

    transport = gemini_video.GeminiVideoTransport(
        api_key="test-secret",
        cost_approved=True,
        evidence_reader=lambda _ref: _png_bytes(),
        http_open=blocked_open,
    )
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(
            transport.generate,
            request,
            lambda _delta: None,
            cancelled.is_set,
        )
        assert connect_started.wait(1)
        cancelled.set()
        try:
            with pytest.raises(gemini_video.VideoTransportError) as error:
                future.result(timeout=1)
        finally:
            release_connect.set()

    assert error.value.code == "cancelled"
    assert response_closed.wait(1)
    assert response.closed is True


def test_clip_bytes_must_match_the_recorded_clip_hash(native_case, tmp_path):
    gemini_video = _require_gemini_transport()
    clip, original_request = native_case
    changed_path = tmp_path / "changed-after-recording.mp4"
    clip_bytes = bytearray(clip.path.read_bytes())
    clip_bytes[-1] ^= 1
    changed_path.write_bytes(clip_bytes)
    changed_clip = dataclasses.replace(original_request.clip, path=changed_path)
    request = dataclasses.replace(original_request, clip=changed_clip)
    http_calls = []

    transport = gemini_video.GeminiVideoTransport(
        api_key="test-secret",
        cost_approved=True,
        evidence_reader=lambda _ref: _png_bytes(),
        http_open=lambda http_request, timeout: http_calls.append(http_request),
    )

    with pytest.raises(gemini_video.VideoTransportError) as error:
        transport.generate(request, lambda _delta: None, lambda: False)

    assert error.value.code == "clip_integrity_failed"
    assert http_calls == []


def test_late_connect_response_is_closed_after_main_timeout(native_case):
    gemini_video = _require_gemini_transport()
    _, request = native_case
    connect_started = threading.Event()
    release_connect = threading.Event()
    response_closed = threading.Event()
    response_delivered = threading.Event()
    calls = []

    class ClosableResponse(_Response):
        def close(self):
            super().close()
            response_closed.set()

    response = ClosableResponse(_successful_events("late"))

    def blocked_open(http_request, timeout):
        calls.append(http_request)
        connect_started.set()
        release_connect.wait(2)
        response_delivered.set()
        return response

    transport = gemini_video.GeminiVideoTransport(
        api_key="test-secret",
        cost_approved=True,
        evidence_reader=lambda _ref: _png_bytes(),
        http_open=blocked_open,
        timeout_seconds=0.25,
    )
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(transport.generate, request, lambda _delta: None, lambda: False)
            assert connect_started.wait(1)
            with pytest.raises(gemini_video.VideoTransportError) as error:
                future.result(timeout=1)
            assert error.value.code == "timeout"
    finally:
        release_connect.set()

    assert response_delivered.wait(1)
    assert response_closed.wait(1)
    assert response.closed is True
    assert len(calls) == 1


def test_cancel_during_stream_stops_visible_output(native_case):
    gemini_video = _require_gemini_transport()
    _, request = native_case
    cancelled = threading.Event()
    calls = []
    text = '{"object_class":"bird","hazard":false}'

    transport = gemini_video.GeminiVideoTransport(
        api_key="test-secret",
        cost_approved=True,
        evidence_reader=lambda _ref: _png_bytes(),
        http_open=lambda _request, timeout: _Response(_successful_events(text)),
    )

    def stop_after_first_delta(delta):
        calls.append(delta)
        cancelled.set()

    with pytest.raises(gemini_video.VideoTransportError) as error:
        transport.generate(request, stop_after_first_delta, cancelled.is_set)

    assert error.value.code == "cancelled"
    assert len(calls) == 1
    assert calls[0] == text[:20]


def test_deadline_includes_time_spent_reading_evidence(native_case):
    gemini_video = _require_gemini_transport()
    _, request = native_case
    state = {"now": 0.0}
    http_calls = []

    def slow_evidence(_ref):
        state["now"] = 31.0
        return _png_bytes()

    transport = gemini_video.GeminiVideoTransport(
        api_key="test-secret",
        cost_approved=True,
        evidence_reader=slow_evidence,
        http_open=lambda http_request, timeout: http_calls.append(http_request),
        timeout_seconds=30.0,
        clock=lambda: state["now"],
    )

    with pytest.raises(gemini_video.VideoTransportError) as error:
        transport.generate(request, lambda _delta: None, lambda: False)

    assert error.value.code == "timeout"
    assert http_calls == []


def test_transport_rejects_non_image_evidence_before_http_start(native_case):
    gemini_video = _require_gemini_transport()
    _, request = native_case
    http_calls = []

    transport = gemini_video.GeminiVideoTransport(
        api_key="test-secret",
        cost_approved=True,
        evidence_reader=lambda _ref: b"not an encoded image",
        http_open=lambda http_request, timeout: http_calls.append(http_request),
    )
    with pytest.raises(gemini_video.VideoTransportError) as error:
        transport.generate(request, lambda _delta: None, lambda: False)

    assert error.value.code == "invalid_evidence"
    assert http_calls == []


def test_single_deadline_covers_stream_reads_and_incomplete_streams_fail(native_case):
    gemini_video = _require_gemini_transport()
    _, request = native_case
    state = {"now": 0.0}

    class SlowResponse(_Response):
        def readline(self):
            state["now"] += 16.0
            return super().readline()

    responses = []

    def slow_open(_request, timeout):
        response = SlowResponse(events)
        responses.append(response)
        return response

    events = [
        _event("interaction.created", interaction={"id": "slow-1", "status": "in_progress"}),
        _event("interaction.completed", interaction={"id": "slow-1", "status": "completed"}),
    ]
    transport = gemini_video.GeminiVideoTransport(
        api_key="test-secret",
        cost_approved=True,
        evidence_reader=lambda _ref: _png_bytes(),
        http_open=slow_open,
        timeout_seconds=30.0,
        clock=lambda: state["now"],
    )

    with pytest.raises(gemini_video.VideoTransportError) as error:
        transport.generate(request, lambda _delta: None, lambda: False)

    assert error.value.code == "timeout"
    assert len(responses) == 1
    assert responses[0].closed is True


def test_serialized_request_limit_includes_base64_video_and_rejects_before_http(
    native_case,
    tmp_path,
    monkeypatch,
):
    gemini_video = _require_gemini_transport()
    _, original_request = native_case
    large_clip = tmp_path / "large.mp4"
    with large_clip.open("wb") as stream:
        stream.write(b"\x00\x00\x00\x18ftypmp42")
        stream.truncate(16 * 1024 * 1024)
    clip = dataclasses.replace(original_request.clip, path=large_clip)
    request = dataclasses.replace(original_request, clip=clip)
    http_calls = []

    def unexpected_read(_path):
        pytest.fail("The size preflight should reject before loading the oversized clip")

    monkeypatch.setattr(Path, "read_bytes", unexpected_read)
    transport = gemini_video.GeminiVideoTransport(
        api_key="test-secret",
        cost_approved=True,
        evidence_reader=lambda _ref: _png_bytes(),
        http_open=lambda http_request, timeout: http_calls.append(http_request),
    )

    with pytest.raises(gemini_video.VideoTransportError) as error:
        transport.generate(request, lambda _delta: None, lambda: False)

    assert error.value.code == "media_limit_exceeded"
    assert http_calls == []


def test_provider_errors_are_redacted_to_a_safe_code(native_case):
    gemini_video = _require_gemini_transport()
    _, request = native_case

    def fail_with_secret(_request, _timeout):
        raise RuntimeError("Authorization: Bearer provider-secret-value")

    transport = gemini_video.GeminiVideoTransport(
        api_key="test-secret",
        cost_approved=True,
        evidence_reader=lambda _ref: _png_bytes(),
        http_open=fail_with_secret,
    )
    with pytest.raises(gemini_video.VideoTransportError) as error:
        transport.generate(request, lambda _delta: None, lambda: False)

    assert error.value.code == "provider_failed"
    assert "provider-secret-value" not in str(error.value)


def test_incomplete_interaction_status_is_not_a_success(native_case):
    gemini_video = _require_gemini_transport()
    _, request = native_case
    events = [
        _event("step.start", index=0, step={"type": "model_output"}),
        _event("step.delta", index=0, delta={"type": "text", "text": "partial"}),
        _event(
            "interaction.completed",
            interaction={"id": "unfinished-1", "status": "incomplete"},
        ),
    ]
    transport = gemini_video.GeminiVideoTransport(
        api_key="test-secret",
        cost_approved=True,
        evidence_reader=lambda _ref: _png_bytes(),
        http_open=lambda _request, timeout: _Response(events),
    )

    with pytest.raises(gemini_video.VideoTransportError) as error:
        transport.generate(request, lambda _delta: None, lambda: False)

    assert error.value.code == "incomplete_response"
