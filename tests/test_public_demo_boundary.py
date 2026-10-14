"""The hosted road demo accepts synthetic camera traffic, without user uploads."""

import json

import pytest

from test_public_demo import _open_upload, _serve, _status, _upload, _workflow


@pytest.mark.parametrize("kind", ["image", "video"])
def test_public_upload_is_rejected_without_storing_bytes(tmp_path, kind):
    server, thread, client = _serve(tmp_path, public_demo=True)
    try:
        status, payload, _ = client.request(
            "POST", "/media", body=b"synthetic upload",
            headers={"Content-Type": "application/octet-stream", "X-VisionFlow-Media-Kind": kind},
            origin=client.base_url,
        )
        assert status == 403
        assert payload["error"]["code"] == "public_demo_upload_disabled"
        assert list(server.state.uploads_root.iterdir()) == []
    finally:
        server.shutdown()
        thread.join(timeout=3)
        server.server_close()


@pytest.mark.parametrize("kind", ["image", "video"])
def test_public_run_rejects_even_a_well_formed_old_upload_reference(tmp_path, kind):
    server, thread, client = _serve(tmp_path, public_demo=True)
    try:
        status, payload, _ = client.request(
            "POST", "/runs", body=_workflow(kind, "uploads/vf_" + "a" * 48 + ".bin"),
            origin=client.base_url,
        )
        assert status == 403
        assert payload["error"]["code"] == "public_demo_source_rejected"
    finally:
        server.shutdown()
        thread.join(timeout=3)
        server.server_close()


@pytest.mark.parametrize("nested", [False, True])
def test_upload_from_a_local_run_cannot_be_resumed_in_public_mode(tmp_path, nested):
    server, thread, client = _serve(tmp_path)
    try:
        status, uploaded = _upload(client, 10)
        assert status == 201  # Private local upload is still allowed.
        status, result, _ = client.request(
            "POST", "/runs", body=_workflow("image", uploaded["source"]), origin=client.base_url,
        )
        assert status == 202
        server.state.public_demo = True  # Represents data retained from an earlier deployment.
        replacement = {"kind": "image", "source": uploaded["source"]}
        body = {"input": replacement} if nested else replacement
        status, payload, _ = client.request(
            "POST", f"/runs/{result['run_id']}/input", body=json.dumps(body).encode(),
            origin=client.base_url,
        )
        assert status == 403
        assert payload["error"]["code"] == "public_demo_source_rejected"
    finally:
        server.shutdown()
        thread.join(timeout=3)
        server.server_close()


def test_parallel_public_upload_headers_are_rejected_before_body(tmp_path):
    server, thread, client = _serve(tmp_path, public_demo=True)
    try:
        first = _open_upload(server, client, 600)
        second = _open_upload(server, client, 600)
        assert [_status(first), _status(second)] == [403, 403]
        assert list(server.state.uploads_root.iterdir()) == []
    finally:
        server.shutdown()
        thread.join(timeout=3)
        server.server_close()
