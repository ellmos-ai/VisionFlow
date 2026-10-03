from __future__ import annotations

import http.client
import json
import socket
import threading
import time
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


def test_http_listener_accepts_twelve_pending_local_connections(tmp_path):
    server = create_server(
        object(),
        host="127.0.0.1",
        port=0,
        data_dir=tmp_path / "data",
        registry=object(),
    )
    address = server.server_address
    clients = []
    failures = []
    try:
        for _ in range(12):
            client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            client.settimeout(0.25)
            try:
                client.connect(address)
                clients.append(client)
            except OSError as exc:
                failures.append(f"{type(exc).__name__}: {exc}")
                client.close()
        assert len(clients) == 12, (
            f"only {len(clients)}/12 local connections entered the pending listener queue; "
            f"failures={failures}"
        )
    finally:
        for client in clients:
            client.close()
        server.server_close()


def test_early_rejection_with_delayed_small_body_returns_http_response(api):
    _client, server, controller, evidence_root = api
    address, port = server.server_address[:2]
    body = b"{}"
    request = (
        f"POST /runs/run-1/confirm HTTP/1.1\r\n"
        f"Host: {address}:{port}\r\n"
        f"Origin: http://{address}:{port}\r\n"
        "Content-Type: application/json\r\n"
        f"Content-Length: {len(body)}\r\n"
        "Connection: close\r\n\r\n"
    ).encode("ascii")
    response = bytearray()
    with socket.create_connection((address, port), timeout=2) as connection:
        connection.settimeout(2)
        connection.sendall(request)
        time.sleep(0.05)
        connection.sendall(body)
        while True:
            chunk = connection.recv(4096)
            if not chunk:
                break
            response.extend(chunk)

    status_line = bytes(response).split(b"\r\n", 1)[0]
    assert status_line in {b"HTTP/1.0 403 Forbidden", b"HTTP/1.1 403 Forbidden"}
    assert b"session_required" in response
    assert controller.runs == {}
    assert not evidence_root.exists()


def test_early_bad_origin_rejection_with_delayed_small_body_returns_http_response(api):
    client, server, controller, evidence_root = api
    client.bootstrap()
    address, port = server.server_address[:2]
    body = b"{}"
    request = (
        f"POST /runs/run-1/confirm HTTP/1.1\r\n"
        f"Host: {address}:{port}\r\n"
        "Origin: http://example.com\r\n"
        f"Cookie: {client.cookie}\r\n"
        f"X-CSRF-Token: {client.csrf_token}\r\n"
        "Content-Type: application/json\r\n"
        f"Content-Length: {len(body)}\r\n"
        "Connection: close\r\n\r\n"
    ).encode("ascii")
    response = bytearray()
    with socket.create_connection((address, port), timeout=2) as connection:
        connection.settimeout(2)
        connection.sendall(request)
        time.sleep(0.05)
        connection.sendall(body)
        while True:
            chunk = connection.recv(4096)
            if not chunk:
                break
            response.extend(chunk)

    assert bytes(response).startswith(b"HTTP/1.0 403 Forbidden\r\n")
    assert b"origin_rejected" in response
    assert controller.runs == {}
    assert not evidence_root.exists()


def test_early_rejection_bounds_wait_for_an_incomplete_small_body(api):
    _client, server, controller, evidence_root = api
    address, port = server.server_address[:2]
    request = (
        f"POST /runs/run-1/confirm HTTP/1.1\r\n"
        f"Host: {address}:{port}\r\n"
        f"Origin: http://{address}:{port}\r\n"
        "Content-Type: application/json\r\n"
        "Content-Length: 10\r\n"
        "Connection: close\r\n\r\n"
    ).encode("ascii")
    response = bytearray()
    started = time.monotonic()
    with socket.create_connection((address, port), timeout=2) as connection:
        connection.settimeout(2)
        connection.sendall(request + b"{}")
        while True:
            chunk = connection.recv(4096)
            if not chunk:
                break
            response.extend(chunk)

    elapsed = time.monotonic() - started
    assert bytes(response).startswith(b"HTTP/1.0 403 Forbidden\r\n")
    assert b"session_required" in response
    assert elapsed < 2.0
    assert controller.runs == {}
    assert not evidence_root.exists()


@pytest.mark.parametrize(
    "framing_headers",
    [
        [],
        [("Content-Length", "invalid")],
        [("Content-Length", "2"), ("Content-Length", "3")],
        [("Transfer-Encoding", "chunked")],
        [("Content-Length", "65537")],
        [("Content-Length", "9" * 5000)],
    ],
    ids=["missing-length", "invalid-length", "duplicate-length", "transfer-encoding", "oversized", "long-length"],
)
def test_early_rejection_skips_ambiguous_unbounded_or_invalid_framing(
    api, framing_headers, monkeypatch
):
    from visionflow.api import _VisionFlowHandler

    _client, server, controller, evidence_root = api
    address, port = server.server_address[:2]

    original_drain = _VisionFlowHandler._drain_small_early_reject_body

    class NoBodyRead:
        def __init__(self, reader):
            self._reader = reader

        def read1(self, *_args, **_kwargs):
            raise AssertionError("ambiguous framing must not read the request body")

        def __getattr__(self, name):
            return getattr(self._reader, name)

    class NoTimeoutAccess:
        def __init__(self, connection):
            self._connection = connection

        def gettimeout(self):
            raise AssertionError("ambiguous framing must not inspect the socket timeout")

        def settimeout(self, _timeout):
            raise AssertionError("ambiguous framing must not change the socket timeout")

        def __getattr__(self, name):
            return getattr(self._connection, name)

    def guarded_drain(handler):
        original_reader = handler.rfile
        original_connection = handler.connection
        handler.rfile = NoBodyRead(original_reader)
        handler.connection = NoTimeoutAccess(original_connection)
        try:
            return original_drain(handler)
        finally:
            handler.rfile = original_reader
            handler.connection = original_connection

    monkeypatch.setattr(_VisionFlowHandler, "_drain_small_early_reject_body", guarded_drain)
    headers = [
        f"POST /runs HTTP/1.1",
        f"Host: {address}:{port}",
        f"Origin: http://{address}:{port}",
        "Content-Type: application/json",
        "Connection: close",
        *(f"{name}: {value}" for name, value in framing_headers),
    ]
    request = ("\r\n".join(headers) + "\r\n\r\n").encode("ascii")
    response = bytearray()
    started = time.monotonic()
    with socket.create_connection((address, port), timeout=2) as connection:
        connection.settimeout(2)
        connection.sendall(request)
        while True:
            chunk = connection.recv(4096)
            if not chunk:
                break
            response.extend(chunk)

    elapsed = time.monotonic() - started
    assert bytes(response).startswith(b"HTTP/1.0 403 Forbidden\r\n")
    assert b"session_required" in response
    assert elapsed < 2.0
    assert controller.runs == {}
    assert not evidence_root.exists()


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
