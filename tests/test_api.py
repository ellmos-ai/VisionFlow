from __future__ import annotations

import http.client
import json
import threading
import urllib.error
import urllib.parse
import urllib.request
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from visionflow.contracts import Finding, RunResult, UnknownRun
from visionflow.registry import ToolDescriptor, ToolRegistry
from visionflow.api import MAX_JSON_BYTES, MAX_UPLOAD_BYTES, create_server
from visionflow.approval import ApprovalStore
from visionflow.outputs import prepare_notification


class FakeController:
    def __init__(self) -> None:
        self.runs: dict[str, RunResult] = {}

    def start(self, config: Any) -> RunResult:
        run_id = f"run-{len(self.runs) + 1}"
        result = RunResult(
            run_id=run_id,
            status="queued",
            findings=[
                Finding(
                    tool="quality",
                    frame_ids=["frame-1"],
                    region=None,
                    metrics={"sharpness_variance": 8.5, "brightness_mean": 121.0},
                    evidence_ref=f"{run_id}/frame-000001.png",
                )
            ],
            trace=[{"type": "run_started", "at": datetime.now(timezone.utc)}],
        )
        self.runs[run_id] = result
        return deepcopy(result)

    def status(self, run_id: str) -> RunResult:
        try:
            return deepcopy(self.runs[run_id])
        except KeyError as exc:
            raise UnknownRun(run_id) from exc

    def cancel(self, run_id: str) -> RunResult:
        result = self.status(run_id)
        result.status = "cancelled"
        self.runs[run_id] = deepcopy(result)
        return result

    def resume(self, run_id: str, payload: dict[str, Any]) -> RunResult:
        result = self.status(run_id)
        result.status = "queued"
        self.runs[run_id] = deepcopy(result)
        return result


class ApiClient:
    def __init__(self, base_url: str) -> None:
        self.base_url = base_url
        self.cookie: str | None = None
        self.csrf_token: str | None = None

    def request(
        self,
        method: str,
        path: str,
        *,
        body: bytes | None = None,
        headers: dict[str, str] | None = None,
        origin: str | None = None,
    ) -> tuple[int, dict[str, Any] | bytes, dict[str, str]]:
        request_headers = dict(headers or {})
        if origin is not None:
            request_headers["Origin"] = origin
        if self.cookie:
            request_headers["Cookie"] = self.cookie
        if self.csrf_token and method in {"POST", "PUT", "PATCH", "DELETE"}:
            request_headers["X-CSRF-Token"] = self.csrf_token
        if body is not None and "Content-Type" not in request_headers:
            request_headers["Content-Type"] = "application/json"
        request = urllib.request.Request(
            self.base_url + path,
            data=body,
            headers=request_headers,
            method=method,
        )
        try:
            response = urllib.request.urlopen(request, timeout=3)
        except urllib.error.HTTPError as exc:
            response = exc
        payload = response.read()
        response_headers = {key.lower(): value for key, value in response.headers.items()}
        content_type = response.headers.get("Content-Type", "")
        decoded: dict[str, Any] | bytes
        if content_type.startswith("application/json"):
            decoded = json.loads(payload.decode("utf-8"))
        else:
            decoded = payload
        return response.status, decoded, response_headers

    def bootstrap(self) -> dict[str, Any]:
        status, payload, headers = self.request("GET", "/session", origin=self.base_url)
        assert status == 200
        assert isinstance(payload, dict)
        cookie = headers.get("set-cookie", "").split(";", 1)[0]
        assert cookie
        assert "httponly" in headers.get("set-cookie", "").lower()
        self.cookie = cookie
        self.csrf_token = payload["csrf_token"]
        return payload


def post_declared_length(
    client: ApiClient,
    server: Any,
    path: str,
    length: int,
    *,
    content_type: str,
    extra_headers: dict[str, str] | None = None,
) -> tuple[int, dict[str, Any]]:
    address, port = server.server_address[:2]
    connection = http.client.HTTPConnection(address, port, timeout=3)
    connection.putrequest("POST", path)
    connection.putheader("Origin", client.base_url)
    connection.putheader("Cookie", client.cookie or "")
    connection.putheader("X-CSRF-Token", client.csrf_token or "")
    connection.putheader("Content-Type", content_type)
    connection.putheader("Content-Length", str(length))
    connection.putheader("Connection", "close")
    for name, value in (extra_headers or {}).items():
        connection.putheader(name, value)
    connection.endheaders()
    response = connection.getresponse()
    payload = json.loads(response.read().decode("utf-8"))
    status = response.status
    connection.close()
    return status, payload


@pytest.fixture
def api(tmp_path: Path):
    controller = FakeController()
    registry = ToolRegistry()
    registry.register(ToolDescriptor("quality", input_kinds=("image",)), lambda f, p, c: {})
    evidence_root = tmp_path / "evidence"
    server = create_server(
        controller,
        host="127.0.0.1",
        port=0,
        data_dir=tmp_path / "data",
        evidence_root=evidence_root,
        registry=registry,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    address, port = server.server_address[:2]
    client = ApiClient(f"http://{address}:{port}")
    try:
        yield client, server, controller, evidence_root
    finally:
        server.shutdown()
        thread.join(timeout=3)
        server.server_close()


def test_api_unknown_run_is_404(api):
    client, _server, _controller, _evidence_root = api
    status, payload, _headers = client.request("GET", "/runs/missing")
    assert status == 404
    assert payload["error"]["code"] == "unknown_run"


def _start_owned_run(client):
    status, result, _ = client.request("POST", "/runs", body=json.dumps({
        "schema_version": 1, "name": "session regression",
        "input": {"kind": "image", "source": "fixture.png"},
        "nodes": [{"id": "analyze", "type": "analyze", "params": {"tool": "quality"}}],
        "outputs": [],
    }).encode("utf-8"), origin=client.base_url)
    assert status == 202
    return result["run_id"]


def test_session_bootstrap_preserves_shared_browser_cookie_and_existing_run(api):
    first, _server, _controller, _root = api
    first.bootstrap()
    run_id = _start_owned_run(first)
    second = ApiClient(first.base_url)
    second.cookie = first.cookie
    second.bootstrap()
    assert second.cookie == first.cookie
    assert second.csrf_token == first.csrf_token
    for client in (first, second):
        status, _payload, _headers = client.request("POST", f"/runs/{run_id}/cancel", body=b"{}",
                                                   origin=client.base_url)
        assert status == 200


@pytest.mark.parametrize("suffix", ["", "/report"])
def test_run_reads_require_the_owning_local_session(api, suffix):
    owner, _server, controller, _root = api
    owner.bootstrap()
    run_id = _start_owned_run(owner)
    controller.runs[run_id].trace = [{"type": "run_started", "at": datetime.now(timezone.utc).isoformat()}]
    other = ApiClient(owner.base_url)
    status, payload, _ = other.request("GET", f"/runs/{run_id}{suffix}")
    assert status == 403
    assert payload["error"]["code"] == "session_required"
    other.bootstrap()
    status, payload, _ = other.request("GET", f"/runs/{run_id}{suffix}")
    assert status == 403
    assert payload["error"]["code"] == "session_mismatch"
    assert owner.request("GET", f"/runs/{run_id}{suffix}")[0] == 200


def test_confirm_without_local_session_is_rejected(api):
    client, _server, _controller, _evidence_root = api
    status, payload, _headers = client.request(
        "POST",
        "/runs/run-1/confirm",
        body=b"{}",
        origin=client.base_url,
    )
    assert status == 403
    assert payload["error"]["code"] == "session_required"


def test_api_session_mutation_serialization_and_run_scoping(api):
    client, _server, controller, evidence_root = api
    session = client.bootstrap()
    assert len(session["csrf_token"]) >= 32

    status, result, _headers = client.request(
        "POST",
        "/runs",
        body=json.dumps(
            {
                "schema_version": 1,
                "name": "quality check",
                "input": {"kind": "image", "source": "fixture.png"},
                "nodes": [
                    {"id": "analyze", "type": "analyze", "params": {"tool": "quality"}}
                ],
                "outputs": [],
            }
        ).encode("utf-8"),
        origin=client.base_url,
    )
    assert status == 202
    assert result["run_id"] == "run-1"
    assert result["trace"][0]["at"].endswith("+00:00")
    (evidence_root / "run-1").mkdir(parents=True)
    (evidence_root / "run-1" / "frame-000001.png").write_bytes(
        b"\x89PNG\r\n\x1a\nfixture"
    )

    status, read_result, _headers = client.request("GET", "/runs/run-1")
    assert status == 200
    assert read_result["findings"][0]["metrics"]["brightness_mean"] == 121.0
    assert controller.status("run-1").run_id == "run-1"

    ref = urllib.parse.quote("run-1/frame-000001.png", safe="")
    status, png, headers = client.request(
        "GET", f"/runs/run-1/evidence?ref={ref}"
    )
    assert status == 200
    assert isinstance(png, bytes) and png.startswith(b"\x89PNG\r\n\x1a\n")
    assert headers["content-type"] == "image/png"
    assert headers["x-content-type-options"] == "nosniff"


def test_session_rejects_cross_origin_and_host_headers(api):
    client, server, _controller, _evidence_root = api
    client.bootstrap()

    status, payload, _headers = client.request(
        "POST",
        "/runs/run-1/cancel",
        body=b"{}",
        origin="http://attacker.example",
    )
    assert status == 403
    assert payload["error"]["code"] == "origin_rejected"

    connection = http.client.HTTPConnection(*server.server_address[:2], timeout=3)
    connection.request("GET", "/tools", headers={"Host": "attacker.example"})
    response = connection.getresponse()
    payload = json.loads(response.read().decode("utf-8"))
    connection.close()
    assert response.status == 403
    assert payload["error"]["code"] == "host_rejected"


def test_json_and_upload_bodies_are_bounded(api):
    client, server, _controller, _evidence_root = api
    client.bootstrap()

    status, payload = post_declared_length(
        client,
        server,
        "/runs",
        MAX_JSON_BYTES + 1,
        content_type="application/json",
    )
    assert status == 413
    assert payload["error"]["code"] == "body_too_large"

    status, payload = post_declared_length(
        client,
        server,
        "/media",
        MAX_UPLOAD_BYTES + 1,
        content_type="application/octet-stream",
        extra_headers={"X-VisionFlow-Media-Kind": "video"},
    )
    assert status == 413
    assert payload["error"]["code"] == "upload_too_large"


def test_media_upload_uses_server_generated_reference(api):
    client, _server, _controller, _evidence_root = api
    client.bootstrap()
    status, payload, _headers = client.request(
        "POST",
        "/media",
        body=b"video bytes",
        headers={
            "Content-Type": "application/octet-stream",
            "X-VisionFlow-Media-Kind": "video",
        },
        origin=client.base_url,
    )
    assert status == 201
    reference = payload["source"]
    assert reference.startswith("uploads/")
    assert ".." not in Path(reference).parts
    assert len(reference.rsplit("/", 1)[-1]) >= 24


def test_evidence_requires_run_reference_and_same_session(api, tmp_path):
    client, _server, _controller, evidence_root = api
    client.bootstrap()
    status, _payload, _headers = client.request(
        "POST",
        "/runs",
        body=json.dumps(
            {
                "schema_version": 1,
                "name": "workflow",
                "input": {"kind": "image", "source": "fixture.png"},
                "nodes": [{"id": "analyze", "type": "analyze", "params": {"tool": "quality"}}],
                "outputs": [],
            }
        ).encode(),
        origin=client.base_url,
    )
    assert status == 202
    (evidence_root / "run-1").mkdir(parents=True)
    (evidence_root / "run-1" / "frame-000001.png").write_bytes(b"\x89PNG\r\n\x1a\nimage")
    outside = tmp_path / "secret.png"
    outside.write_bytes(b"\x89PNG\r\n\x1a\nsecret")

    second = ApiClient(client.base_url)
    second.bootstrap()
    status, _payload, _headers = second.request(
        "GET",
        "/runs/run-1/evidence?ref=" + urllib.parse.quote("run-1/frame-000001.png", safe=""),
    )
    assert status == 403

    path = urllib.parse.quote("../secret.png", safe="")
    status, payload, _headers = client.request(
        "GET", f"/runs/run-1/evidence?ref={path}"
    )
    assert status == 404
    assert payload["error"]["code"] == "evidence_not_found"


def test_second_session_cannot_cancel_another_sessions_run(api):
    owner, _server, _controller, _evidence_root = api
    owner.bootstrap()
    status, result, _headers = owner.request(
        "POST",
        "/runs",
        body=json.dumps(
            {
                "schema_version": 1,
                "name": "workflow",
                "input": {"kind": "image", "source": "fixture.png"},
                "nodes": [{"id": "analyze", "type": "analyze", "params": {"tool": "quality"}}],
                "outputs": [],
            }
        ).encode(),
        origin=owner.base_url,
    )
    assert status == 202
    other = ApiClient(owner.base_url)
    other.bootstrap()

    status, payload, _headers = other.request(
        "POST",
        "/runs/run-1/cancel",
        body=b"{}",
        origin=other.base_url,
    )

    assert status == 403
    assert payload["error"]["code"] == "session_mismatch"


def test_web_assets_are_served_with_blob_preview_policy_and_real_api_shell(api):
    client, _server, _controller, _evidence_root = api

    status, html, headers = client.request("GET", "/")

    assert status == 200
    assert isinstance(html, bytes)
    assert b"VisionFlow" in html
    policy = headers.get("content-security-policy", "")
    assert "img-src 'self' blob:" in policy
    assert b"<script src=\"/app.js\" defer></script>" in html
    assert b"onclick=" not in html
    assert b"workflow-preset" in html
    assert "gezielte Neuaufnahme".encode("utf-8") in html
    assert "Bewegungsbeobachtung".encode("utf-8") in html

    status, app, _headers = client.request("GET", "/app.js")
    assert status == 200
    assert isinstance(app, bytes)
    assert b"/runs" in app
    assert b"createObjectURL" in app
    assert b"innerHTML" not in app
    assert b"simulateState" not in app
    assert b"request_input" in app
    assert b"observe-authority" in app

    status, css, _headers = client.request("GET", "/style.css")
    assert status == 200
    assert isinstance(css, bytes)
    assert b"focus-visible" in css


@pytest.mark.parametrize("name", ["alex", "kira", "schmidt", "weber", "leaf", "bird", "wrong-way", "patrol"])
def test_generated_control_room_assets_are_served_as_png(api, name):
    client, _server, _controller, _evidence_root = api
    status, payload, headers = client.request("GET", f"/assets/{name}.png")
    assert status == 200
    assert headers["content-type"] == "image/png"
    assert isinstance(payload, bytes) and payload.startswith(b"\x89PNG\r\n\x1a\n")


@pytest.mark.parametrize("path", [
    "/assets/unknown.png", "/assets/../api.py", "/assets/%2e%2e/api.py",
    "/assets/alex.png/../api.py", "/assets/LOCK.ticket-guidance.txt",
])
def test_control_room_asset_route_does_not_expose_arbitrary_files(api, path):
    client, _server, _controller, _evidence_root = api
    status, _payload, _headers = client.request("GET", path)
    assert status == 404


def test_cancelled_run_cannot_create_a_new_terminal_approval(api):
    client, server, controller, _evidence_root = api
    client.bootstrap()
    status, _result, _headers = client.request(
        "POST",
        "/runs",
        body=json.dumps(
            {
                "schema_version": 1,
                "name": "approval-workflow",
                "input": {"kind": "image", "source": "fixture.png"},
                "nodes": [{"id": "analyze", "type": "analyze", "params": {"tool": "quality"}}],
                "outputs": [],
            }
        ).encode(),
        origin=client.base_url,
    )
    assert status == 202
    controller.runs["run-1"].status = "cancelled"

    store = ApprovalStore()
    proposal = prepare_notification(RunResult("run-1", "cancelled"), now=datetime.now(timezone.utc))
    store.register(proposal)
    server.state.approval_store = store

    status, payload, _headers = client.request(
        "POST",
        "/runs/run-1/confirm",
        body=json.dumps(
            {"action_id": proposal.action_id, "content_hash": proposal.content_hash}
        ).encode(),
        origin=client.base_url,
    )

    assert status == 409
    assert payload["error"]["code"] == "invalid_transition"
    assert store._actions[proposal.action_id].state == "pending"


def test_non_ascii_csrf_token_is_rejected_as_forbidden_not_server_error(api):
    client, server, _controller, _evidence_root = api
    client.bootstrap()

    connection = http.client.HTTPConnection(*server.server_address[:2], timeout=3)
    connection.putrequest("POST", "/runs/run-1/cancel")
    connection.putheader("Origin", client.base_url)
    connection.putheader("Cookie", client.cookie or "")
    connection.putheader("X-CSRF-Token", "éééé")  # sent as Latin-1 bytes
    connection.putheader("Content-Type", "application/json")
    connection.putheader("Content-Length", "2")
    connection.endheaders(b"{}")
    response = connection.getresponse()
    status = response.status
    payload = json.loads(response.read().decode("utf-8"))
    connection.close()

    assert status == 403
    assert payload["error"]["code"] == "session_required"


def test_server_binds_to_ipv6_loopback(tmp_path: Path):
    import socket

    try:
        with socket.socket(socket.AF_INET6, socket.SOCK_STREAM) as probe:
            probe.bind(("::1", 0))
    except (OSError, AttributeError):
        pytest.skip("IPv6 loopback is not available on this host")

    server = create_server(FakeController(), host="::1", port=0, data_dir=tmp_path / "data")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        port = server.server_address[1]
        connection = http.client.HTTPConnection("::1", port, timeout=3)
        connection.request("GET", "/session", headers={"Host": f"[::1]:{port}"})
        response = connection.getresponse()
        payload = json.loads(response.read().decode("utf-8"))
        connection.close()
        assert response.status == 200
        assert payload["csrf_token"]
    finally:
        server.shutdown()
        thread.join(timeout=3)
        server.server_close()
