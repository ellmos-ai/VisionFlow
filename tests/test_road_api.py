from __future__ import annotations

import base64
import hashlib
import json
import shutil
import threading
import time
import urllib.error
import urllib.request
import gc
import weakref
from pathlib import Path

import cv2
import numpy as np
import pytest

from visionflow.api import create_server
from visionflow.recording import RecordingStore
from visionflow.road_api import RoadSessionService
from visionflow.runtime import create_controller
from visionflow.simulation import SimulationStore


class ApiClient:
    def __init__(self, base_url: str) -> None:
        self.base_url = base_url
        self.cookie: str | None = None
        self.csrf: str | None = None

    def request(self, method: str, path: str, payload=None, *, csrf: bool = True):
        headers = {"Origin": self.base_url, "Accept": "application/json"}
        body = None if payload is None else json.dumps(payload, allow_nan=False).encode("utf-8")
        if body is not None:
            headers["Content-Type"] = "application/json"
        if self.cookie:
            headers["Cookie"] = self.cookie
        if csrf and self.csrf and method == "POST":
            headers["X-CSRF-Token"] = self.csrf
        request = urllib.request.Request(self.base_url + path, data=body, headers=headers, method=method)
        try:
            response = urllib.request.urlopen(request, timeout=5)
        except urllib.error.HTTPError as exc:
            response = exc
        raw = response.read()
        content_type = response.headers.get("Content-Type", "")
        value = json.loads(raw.decode("utf-8")) if content_type.startswith("application/json") else raw
        return response.status, value, {key.lower(): val for key, val in response.headers.items()}

    def bootstrap(self):
        status, value, headers = self.request("GET", "/session")
        assert status == 200
        self.cookie = headers["set-cookie"].split(";", 1)[0]
        self.csrf = value["csrf_token"]
        return value


@pytest.fixture
def road_server(tmp_path: Path):
    ffmpeg = shutil.which("ffmpeg")
    assert ffmpeg, "the local FFmpeg installation is required for the real clip test"
    sessions = SimulationStore()
    recordings = RecordingStore(tmp_path / "recordings", Path(ffmpeg))
    holder: dict[str, RoadSessionService] = {}

    def source_override(config, _max_frames):
        if config.kind != "camera" or not config.source.startswith("road-sim-"):
            return None
        service = holder.get("service")
        if service is None:
            return None
        return service.open_reader(config.source)

    controller = create_controller(tmp_path / "controller", source_override=source_override)
    service = RoadSessionService(sessions, recordings, controller)
    holder["service"] = service
    server = create_server(
        controller,
        host="127.0.0.1",
        port=0,
        data_dir=tmp_path / "api",
        road_sessions=service,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address[:2]
    try:
        yield f"http://{host}:{port}", service, controller, sessions
    finally:
        service.close()
        server.shutdown()
        thread.join(timeout=3)
        server.server_close()


def _decode_clip(tmp_path: Path, data: bytes) -> int:
    clip_path = tmp_path / "received.mp4"
    clip_path.write_bytes(data)
    capture = cv2.VideoCapture(str(clip_path))
    assert capture.isOpened(), "downloaded clip must be a decodable MP4"
    count = 0
    try:
        while True:
            ok, _frame = capture.read()
            if not ok:
                break
            count += 1
    finally:
        capture.release()
    return count


def test_stopped_source_keeps_compressed_preview_without_live_frames(road_server):
    base_url, service, _, _ = road_server
    client = ApiClient(base_url)
    client.bootstrap()
    status, created, _ = client.request("POST", "/road/sessions", {})
    assert status == 201
    source_id = created["source_id"]
    owner = client.cookie.split("=", 1)[1]
    session = service.get(owner, source_id)
    image_ref = weakref.ref(session.latest_frame().image)
    time.sleep(0.2)
    assert client.request("POST", f"/road/sessions/{source_id}/stop", {})[0] == 200
    gc.collect()
    assert image_ref() is None, "stopped source retains uncompressed frames"
    status, preview, _ = client.request("GET", f"/road/sessions/{source_id}/frame")
    assert status == 200 and preview["closed"] is True
    image = cv2.imdecode(np.frombuffer(base64.b64decode(preview["image_base64"]), dtype=np.uint8), cv2.IMREAD_COLOR)
    assert image.shape == (540, 960, 3)
    assert client.request("GET", f"/road/sessions/{source_id}/frame")[1]["frame_id"] == preview["frame_id"]


def test_road_source_serves_real_jpeg_and_owner_scoped_h264_clip(road_server, tmp_path):
    base_url, _service, _controller, _sessions = road_server
    owner = ApiClient(base_url)
    other = ApiClient(base_url)
    owner.bootstrap()
    other.bootstrap()

    status, created, _ = owner.request("POST", "/road/sessions", {"seed": 17})
    assert status == 201
    source_id = created["source_id"]
    assert source_id.startswith("road-sim-")

    status, frame, _ = owner.request("GET", f"/road/sessions/{source_id}/frame")
    assert status == 200
    assert set(frame) >= {"source_id", "frame_id", "timestamp_seconds", "image_base64"}
    assert not ({"event", "world", "vehicle_id", "finding"} & set(frame))
    image_bytes = base64.b64decode(frame["image_base64"], validate=True)
    pixels = cv2.imdecode(np.frombuffer(image_bytes, dtype=np.uint8), cv2.IMREAD_COLOR)
    assert pixels is not None and pixels.shape == (540, 960, 3)
    assert frame["source_id"] == source_id
    assert frame["frame_id"].startswith(source_id + ":")

    status, ack, _ = owner.request("POST", f"/road/sessions/{source_id}/events", {"event": "bird"})
    assert status == 200
    assert set(ack) <= {"source_id", "accepted"}
    assert ack["accepted"] is True
    assert "findings" not in ack and "model_output" not in ack

    time.sleep(0.35)
    status, stopped, _ = owner.request("POST", f"/road/sessions/{source_id}/stop", {})
    assert status == 200
    assert stopped["source_id"] == source_id
    assert stopped["clip"]["frame_ids"]
    clip_id = stopped["clip"]["clip_id"]
    clip_hash = stopped["clip"]["sha256"]

    status, media, headers = owner.request("GET", f"/road/clips/{clip_id}")
    assert status == 200
    assert headers["content-type"].startswith("video/mp4")
    assert hashlib.sha256(media).hexdigest() == clip_hash
    assert _decode_clip(tmp_path, media) == len(stopped["clip"]["frame_ids"])

    status, _foreign, _ = other.request("GET", f"/road/sessions/{source_id}/frame")
    assert status == 403
    status, _foreign_clip, _ = other.request("GET", f"/road/clips/{clip_id}")
    assert status == 403


def test_road_post_routes_require_origin_session_and_csrf(road_server):
    base_url, _service, _controller, _sessions = road_server
    client = ApiClient(base_url)
    client.bootstrap()

    status, rejected, _ = client.request("POST", "/road/sessions", {"seed": 3}, csrf=False)
    assert status == 403
    assert rejected["error"]["code"] == "session_required"

    request = urllib.request.Request(
        base_url + "/road/sessions",
        data=b'{"seed":3}',
        headers={
            "Origin": "http://attacker.example",
            "Cookie": client.cookie or "",
            "X-CSRF-Token": client.csrf or "",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        response = urllib.request.urlopen(request, timeout=5)
    except urllib.error.HTTPError as exc:
        response = exc
    assert response.status == 403
    assert json.loads(response.read().decode("utf-8"))["error"]["code"] == "origin_rejected"


def test_owner_lookup_and_optional_run_stop_callback_are_threadsafe(road_server):
    _base_url, service, controller, _sessions = road_server
    source = service.create("owner-a", seed=11)
    source_id = source["source_id"]
    assert service.get("owner-a", source_id).source_id == source_id
    with pytest.raises(KeyError):
        service.get("owner-b", source_id)

    from visionflow.config import validate_workflow
    from visionflow.runtime import create_registry

    workflow = validate_workflow({
        "schema_version": 1,
        "name": "lookup",
        "input": {"kind": "camera", "source": source_id},
        "limits": {"max_observation_seconds": 1, "max_frames": 2, "max_llm_calls": 1},
        "nodes": [{"id": "wait", "type": "request_input", "params": {"reason": "review"}, "next": []}],
        "outputs": [],
    }, create_registry())
    queued = controller.start(workflow)
    stopped = []
    service.bind_run("owner-a", source_id, queued.run_id, monitor_clip=False,
                     on_stopped=lambda run_id, reason: stopped.append((run_id, reason)))
    assert service.owner_source_for_run(queued.run_id) == ("owner-a", source_id)
    service.stop("owner-a", source_id)
    assert stopped == [(queued.run_id, "source_stopped")]


def _completed_camera_run(controller, source_id: str) -> str:
    from visionflow.config import validate_workflow
    from visionflow.runtime import create_registry

    workflow = validate_workflow({
        "schema_version": 1,
        "name": "terminal-camera",
        "input": {"kind": "camera", "source": source_id},
        "limits": {"max_observation_seconds": 1, "max_frames": 2, "max_llm_calls": 1},
        "nodes": [{"id": "report", "type": "report", "params": {"format": "json"}, "next": []}],
        "outputs": [],
    }, create_registry())
    queued = controller.start(workflow)
    completed = controller.wait(queued.run_id, timeout=3)
    assert completed.status == "completed"
    return queued.run_id


def test_completed_binding_stays_source_stop_notified_exactly_once(road_server, monkeypatch):
    _base_url, service, controller, _sessions = road_server
    source_id = service.create("owner-a", seed=13)["source_id"]
    run_id = _completed_camera_run(controller, source_id)
    notifications = []

    def forbidden_cancel(_run_id):
        raise AssertionError("a completed controller run must not be cancelled")

    monkeypatch.setattr(controller, "cancel", forbidden_cancel)
    service.bind_run(
        "owner-a", source_id, run_id, monitor_clip=False,
        on_stopped=lambda run, reason: notifications.append((run, reason)),
    )
    binding = service._bindings[run_id]
    binding.watcher.join(timeout=1)
    assert not binding.watcher.is_alive()
    assert service.run_for_source("owner-a", source_id) is None
    service.stop("owner-a", source_id)
    service._stop_bound_source("owner-a", source_id, "source_stopped")
    service.close()

    assert notifications == [(run_id, "source_stopped")]


def test_completed_run_waits_boundedly_for_event_clip_tail(road_server, monkeypatch):
    from types import SimpleNamespace

    from visionflow.recording import ClipNotReady

    _base_url, service, controller, _sessions = road_server
    source_id = service.create("owner-tail", seed=19)["source_id"]
    frame_id = service.get("owner-tail", source_id).latest_frame().frame_id
    run_id = _completed_camera_run(controller, source_id)
    findings = [SimpleNamespace(
        tool="direction", metrics={"opposition_count": 1}, frame_ids=[frame_id]
    )]
    monkeypatch.setattr(
        controller, "status",
        lambda _run_id: SimpleNamespace(status="completed", findings=findings),
    )
    attempts = []

    def clip_not_ready(_owner, _source, _event_time):
        attempts.append(time.monotonic())
        raise ClipNotReady("tail is still being recorded")

    monkeypatch.setattr(service.recordings, "event_clip", clip_not_ready)
    service._clip_wait_seconds = 0.2
    notifications = []
    service.bind_run(
        "owner-tail", source_id, run_id, monitor_clip=True,
        on_stopped=lambda run, reason: notifications.append((run, reason)),
    )
    binding = service._bindings[run_id]
    binding.watcher.join(timeout=1.5)

    assert not binding.watcher.is_alive(), "completed runs must not leave an unbounded clip watcher"
    assert len(attempts) >= 1
    assert notifications == [(run_id, "clip_timeout")]


def test_completed_run_can_cancel_pending_clip_watcher(road_server, monkeypatch):
    from types import SimpleNamespace

    from visionflow.recording import ClipNotReady

    _base_url, service, controller, _sessions = road_server
    source_id = service.create("owner-cancel", seed=23)["source_id"]
    frame_id = service.get("owner-cancel", source_id).latest_frame().frame_id
    run_id = _completed_camera_run(controller, source_id)
    monkeypatch.setattr(controller, "status", lambda _run_id: SimpleNamespace(
        status="completed",
        findings=[SimpleNamespace(tool="direction", metrics={"opposition_count": 1}, frame_ids=[frame_id])],
    ))
    entered = threading.Event()

    def clip_not_ready(_owner, _source, _event_time):
        entered.set()
        raise ClipNotReady("tail is still being recorded")

    monkeypatch.setattr(service.recordings, "event_clip", clip_not_ready)
    notifications = []
    service.bind_run(
        "owner-cancel", source_id, run_id, monitor_clip=True,
        on_stopped=lambda run, reason: notifications.append((run, reason)),
    )
    binding = service._bindings[run_id]
    assert entered.wait(timeout=1)
    assert binding.watcher.is_alive(), "the watcher must remain active while required tail frames are missing"
    service.cancel_run("owner-cancel", run_id, reason="source_reset")
    binding.watcher.join(timeout=1)

    assert not binding.watcher.is_alive()
    assert notifications == [(run_id, "source_reset")]


def test_completed_run_keeps_watching_until_event_clip_is_ready(road_server, monkeypatch):
    from types import SimpleNamespace

    from visionflow.recording import ClipNotReady

    _base_url, service, controller, _sessions = road_server
    source_id = service.create("owner-ready", seed=29)["source_id"]
    frame_id = service.get("owner-ready", source_id).latest_frame().frame_id
    run_id = _completed_camera_run(controller, source_id)
    monkeypatch.setattr(controller, "status", lambda _run_id: SimpleNamespace(
        status="completed",
        findings=[SimpleNamespace(tool="direction", metrics={"opposition_count": 1}, frame_ids=[frame_id])],
    ))
    service._poll_seconds = 0.02
    ready_clip = object()
    attempts = []
    first_attempt = threading.Event()

    def event_clip(_owner, _source, _event_time):
        attempts.append(1)
        if len(attempts) == 1:
            first_attempt.set()
            raise ClipNotReady("tail is still being recorded")
        return ready_clip

    monkeypatch.setattr(service.recordings, "event_clip", event_clip)
    notifications = []
    service.bind_run(
        "owner-ready", source_id, run_id, monitor_clip=True,
        on_stopped=lambda run, reason: notifications.append((run, reason)),
    )
    binding = service._bindings[run_id]
    assert first_attempt.wait(timeout=1)
    binding.watcher.join(timeout=1)

    assert not binding.watcher.is_alive()
    assert len(attempts) >= 2
    assert binding.clip_ref is ready_clip
    assert notifications == []


def test_completed_run_exits_on_real_source_end_when_tail_is_unavailable(road_server, monkeypatch):
    from types import SimpleNamespace

    from visionflow.recording import ClipNotReady

    _base_url, service, controller, _sessions = road_server
    source_id = service.create("owner-ended", seed=31)["source_id"]
    session = service.get("owner-ended", source_id)
    frame_id = session.latest_frame().frame_id
    run_id = _completed_camera_run(controller, source_id)
    monkeypatch.setattr(controller, "status", lambda _run_id: SimpleNamespace(
        status="completed",
        findings=[SimpleNamespace(tool="direction", metrics={"opposition_count": 1}, frame_ids=[frame_id])],
    ))
    entered = threading.Event()

    def event_clip(_owner, _source, _event_time):
        entered.set()
        raise ClipNotReady("tail is still being recorded")

    monkeypatch.setattr(service.recordings, "event_clip", event_clip)
    notifications = []
    service.bind_run(
        "owner-ended", source_id, run_id, monitor_clip=True,
        on_stopped=lambda run, reason: notifications.append((run, reason)),
    )
    binding = service._bindings[run_id]
    assert entered.wait(timeout=1)
    session.close()
    binding.watcher.join(timeout=1)

    assert not binding.watcher.is_alive()
    assert notifications == [(run_id, "source_end")]


def _report_camera_workflow(source_id: str) -> dict:
    return {
        "schema_version": 1,
        "name": "road-owner-gate",
        "input": {"kind": "camera", "source": source_id},
        "limits": {"max_observation_seconds": 1, "max_frames": 2, "max_llm_calls": 1},
        "nodes": [{"id": "report", "type": "report", "params": {"format": "json"}, "next": []}],
        "outputs": [],
    }


def test_road_workflow_owner_is_checked_before_controller_start(road_server, monkeypatch):
    base_url, service, controller, _sessions = road_server
    owner = ApiClient(base_url)
    other = ApiClient(base_url)
    owner.bootstrap()
    other.bootstrap()
    owner_id = owner.cookie.split("=", 1)[1]
    source_id = service.create(owner_id, seed=37)["source_id"]
    starts = []
    original_start = controller.start

    def counted_start(workflow):
        starts.append(workflow)
        return original_start(workflow)

    monkeypatch.setattr(controller, "start", counted_start)
    status, rejected, _ = other.request("POST", "/runs", _report_camera_workflow(source_id))

    assert status == 403
    assert rejected["error"]["code"] == "session_mismatch"
    assert starts == [], "the controller must not start a camera workflow before owner authorization"

    status, started, _ = owner.request("POST", "/runs", _report_camera_workflow(source_id))
    assert status == 202
    assert len(starts) == 1
    assert service.owner_source_for_run(started["run_id"]) == (owner.cookie.split("=", 1)[1], source_id)


def test_road_resume_source_is_checked_before_controller_resume(road_server, monkeypatch):
    base_url, service, controller, _sessions = road_server
    run_owner = ApiClient(base_url)
    source_owner = ApiClient(base_url)
    run_owner.bootstrap()
    source_owner.bootstrap()
    road_owner_id = source_owner.cookie.split("=", 1)[1]
    road_source = service.create(road_owner_id, seed=41)["source_id"]
    waiting_workflow = {
        "schema_version": 1,
        "name": "resume-owner-gate",
        "input": {"kind": "image", "source": "unused.png"},
        "limits": {"max_observation_seconds": 1, "max_frames": 2, "max_llm_calls": 1},
        "nodes": [{"id": "input", "type": "request_input", "params": {"reason": "continue"}, "next": []}],
        "outputs": [],
    }
    status, started, _ = run_owner.request("POST", "/runs", waiting_workflow)
    assert status == 202
    run_id = started["run_id"]
    assert run_owner.request("GET", f"/runs/{run_id}")[0] == 200
    original_resume = controller.resume
    resumed = []

    def counted_resume(identifier, payload):
        resumed.append(identifier)
        return original_resume(identifier, payload)

    monkeypatch.setattr(controller, "resume", counted_resume)
    status, rejected, _ = run_owner.request("POST", f"/runs/{run_id}/input", {"source": road_source})

    assert status == 403
    assert rejected["error"]["code"] == "session_mismatch"
    assert resumed == [], "the controller must not reopen another session's road source"


def test_road_assets_are_allowlisted_and_video_blob_csp_is_enabled(road_server):
    base_url, _service, _controller, _sessions = road_server
    client = ApiClient(base_url)
    status, page, headers = client.request("GET", "/")
    assert status == 200
    assert "media-src 'self' blob:" in headers["content-security-policy"]
    assert "road-camera-root" in page.decode("utf-8")

    for route, supported_types in (
        ("/road-camera.js", {"text/javascript", "application/javascript"}),
        ("/road-model.js", {"text/javascript", "application/javascript"}),
        ("/road-workplace.js", {"text/javascript", "application/javascript"}),
        ("/road.css", {"text/css"}),
    ):
        status, payload, asset_headers = client.request("GET", route)
        assert status == 200
        assert asset_headers["content-type"].split(";", 1)[0] in supported_types
        assert payload

    assert client.request("GET", "/road-private.js")[0] == 404


def test_road_monitor_workflow_template_is_session_scoped_and_schema_valid(road_server):
    from visionflow.config import validate_workflow
    from visionflow.runtime import create_registry

    base, _service, _server, _thread = road_server
    client = ApiClient(base)
    client.bootstrap()

    status, workflow, _headers = client.request("GET", "/road/workflow")
    assert status == 200
    assert workflow["name"] == "road-monitor"
    assert workflow["input"] == {"kind": "camera", "source": "USER_PROVIDED"}
    assert [node["type"] for node in workflow["nodes"]] == [
        "analyze", "observe", "branch", "observe", "report",
    ]
    assert workflow["nodes"][1]["params"]["stop_when"]["min_consecutive"] == 3
    config = validate_workflow(workflow, create_registry())
    assert config.nodes[2].params["then_node"] == "candidate-tail"
    assert config.nodes[3].params["duration_seconds"] == 10


def test_road_capability_reports_encoder_requirement_when_core_server_has_no_road_service(tmp_path):
    controller = create_controller(tmp_path / "controller")
    server = create_server(
        controller,
        host="127.0.0.1",
        port=0,
        data_dir=tmp_path / "api",
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address[:2]
    owner = ApiClient(f"http://{host}:{port}")
    try:
        owner.bootstrap()
        status, payload, _ = owner.request("GET", "/road/capabilities")
        assert status == 200
        assert payload["available"] is False
        assert payload["reason_code"] == "ffmpeg_required"
        assert "FFmpeg" in payload["message"]
    finally:
        server.shutdown()
        thread.join(timeout=3)
        server.server_close()


def test_stop_and_reset_without_recorded_frames_report_empty_recording(road_server, monkeypatch):
    from visionflow.recording import ClipNotReady

    base_url, service, _controller, _sessions = road_server
    client = ApiClient(base_url)
    client.bootstrap()

    def no_frames(_owner, _source_id):
        raise ClipNotReady("Es wurden noch keine Bilder für einen vollständigen Clip aufgezeichnet.")

    monkeypatch.setattr(service.recordings, "finish", no_frames)

    status, created, _ = client.request("POST", "/road/sessions", {"seed": 5})
    assert status == 201
    status, stopped, _ = client.request("POST", f"/road/sessions/{created['source_id']}/stop", {})
    assert status == 200
    assert stopped == {"source_id": created["source_id"], "closed": True, "error_code": "recording_empty"}

    status, created, _ = client.request("POST", "/road/sessions", {"seed": 6})
    assert status == 201
    status, reset, _ = client.request("POST", f"/road/sessions/{created['source_id']}/reset", {})
    assert status == 200
    assert reset["previous_source_id"] == created["source_id"]
    assert reset["source_id"].startswith("road-sim-")
    assert reset["source_id"] != created["source_id"]


@pytest.mark.parametrize("key", ["retry\n1", "tab\tkey", "bell\x07"])
def test_decision_idempotency_key_with_control_characters_is_rejected(key):
    from visionflow.road_api import RoadSessionError, dispatch_road_post

    class Cases:
        decided = False

        def snapshot(self, _owner, _case_id):
            return {}

        def decide(self, *_args):
            Cases.decided = True
            return {}

    body = {"version": 1, "content_hash": "sha256:abc", "decision": "confirm", "idempotency_key": key}
    with pytest.raises(RoadSessionError) as raised:
        dispatch_road_post(None, "owner", "/road/cases/case-1/decision", body, cases=Cases())

    assert raised.value.code == "invalid_idempotency_key"
    assert raised.value.status == 422
    assert not Cases.decided
