"""Public demo mode: the hosted jury demo may only touch its own media."""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import pytest

from test_api import ApiClient, FakeController
from visionflow.api import create_server
from visionflow.registry import ToolDescriptor, ToolRegistry
from visionflow.simulation import SimulationCapacityError, SimulationStore


def _serve(tmp_path: Path, **options):
    registry = ToolRegistry()
    registry.register(ToolDescriptor("quality", input_kinds=("image", "video", "camera")), lambda f, p, c: {})
    server = create_server(
        FakeController(),
        host="127.0.0.1",
        port=0,
        data_dir=tmp_path / "data",
        evidence_root=tmp_path / "evidence",
        registry=registry,
        **options,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    address, port = server.server_address[:2]
    client = ApiClient(f"http://{address}:{port}")
    client.bootstrap()
    return server, thread, client


@pytest.fixture
def public(tmp_path: Path):
    server, thread, client = _serve(tmp_path, public_demo=True, public_upload_quota_bytes=1000)
    try:
        yield client
    finally:
        server.shutdown()
        thread.join(timeout=3)
        server.server_close()


def _workflow(kind: str, source: str) -> bytes:
    return json.dumps({
        "schema_version": 1,
        "name": "public demo check",
        "input": {"kind": kind, "source": source},
        "nodes": [{"id": "analyze", "type": "analyze", "params": {"tool": "quality"}}],
        "outputs": [],
    }).encode("utf-8")


def _upload(client: ApiClient, size: int) -> tuple[int, dict]:
    status, payload, _ = client.request(
        "POST",
        "/media",
        body=b"x" * size,
        headers={"Content-Type": "application/octet-stream", "X-VisionFlow-Media-Kind": "image"},
        origin=client.base_url,
    )
    return status, payload


def test_session_reports_whether_the_public_demo_mode_is_active(public, tmp_path):
    _status, payload, _ = public.request("GET", "/session", origin=public.base_url)
    assert payload["public_demo"] is True

    server, thread, local = _serve(tmp_path / "local")
    try:
        _status, payload, _ = local.request("GET", "/session", origin=local.base_url)
        assert payload["public_demo"] is False
    finally:
        server.shutdown()
        thread.join(timeout=3)
        server.server_close()


@pytest.mark.parametrize(
    ("kind", "source"),
    [
        ("image", "fixture.png"),
        ("image", "C:/Users/someone/secret.png"),
        ("image", "/etc/passwd"),
        ("image", "uploads/../evidence/x.bin"),
        ("camera", "front"),
    ],
)
def test_public_demo_rejects_server_paths_and_named_cameras(public, kind, source):
    status, payload, _ = public.request("POST", "/runs", body=_workflow(kind, source), origin=public.base_url)
    assert status == 403
    assert payload["error"]["code"] == "public_demo_source_rejected"


def test_public_demo_runs_media_uploaded_in_the_same_service(public):
    status, uploaded = _upload(public, 10)
    assert status == 201
    status, _result, _ = public.request(
        "POST", "/runs", body=_workflow("image", uploaded["source"]), origin=public.base_url
    )
    assert status == 202


def test_public_demo_resume_cannot_switch_to_a_server_path(public):
    _status, uploaded = _upload(public, 10)
    _status, result, _ = public.request(
        "POST", "/runs", body=_workflow("image", uploaded["source"]), origin=public.base_url
    )
    status, payload, _ = public.request(
        "POST",
        f"/runs/{result['run_id']}/input",
        body=json.dumps({"source": "C:/Windows/win.ini"}).encode("utf-8"),
        origin=public.base_url,
    )
    assert status == 403
    assert payload["error"]["code"] == "public_demo_source_rejected"


def test_public_demo_upload_storage_is_capped(public):
    assert _upload(public, 600)[0] == 201
    status, payload = _upload(public, 600)
    assert status == 507
    assert payload["error"]["code"] == "demo_storage_full"


def test_simulation_store_caps_live_sessions_and_frees_closed_slots():
    store = SimulationStore(max_live=2)
    try:
        first = store.create("owner-a")
        store.create("owner-b")
        with pytest.raises(SimulationCapacityError):
            store.create("owner-c")
        first.close()
        deadline = time.monotonic() + 3
        while not first.closed and time.monotonic() < deadline:
            time.sleep(0.01)
        store.create("owner-c")
    finally:
        store.close()


@pytest.mark.parametrize("extra", (["--allow-gemini-costs"], ["--camera", "front=0"]))
def test_cli_refuses_public_demo_with_cameras_or_provider_costs(tmp_path, capsys, extra):
    from visionflow.cli import main

    code = main(["serve", "--public-demo", "--data-dir", str(tmp_path / "d"), *extra])
    assert code != 0
    assert "public_demo_conflict" in capsys.readouterr().err
