"""W1: the public demo runs the reusable workflows on bundled samples only."""
from __future__ import annotations

import copy
import json
import threading
import time
from pathlib import Path

import pytest

from test_api import ApiClient


def _serve(tmp_path: Path, *, public: bool = True, runtime=None):
    from visionflow.api import create_server
    from visionflow.runtime import create_controller

    controller = runtime.controller if runtime is not None else create_controller(tmp_path / "controller", samples=True)
    options = {"road_sessions": runtime.road_sessions, "road_cases": runtime.road_cases} if runtime is not None else {}
    server = create_server(controller, host="127.0.0.1", port=0, data_dir=tmp_path / "api",
                           evidence_root=controller.evidence_root, public_demo=public, **options)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    client = ApiClient(f"http://127.0.0.1:{server.server_address[1]}")
    client.bootstrap()
    return server, thread, client


@pytest.fixture
def public(tmp_path: Path):
    server, thread, client = _serve(tmp_path)
    try:
        yield server, client
    finally:
        server.shutdown()
        thread.join(timeout=3)
        server.server_close()


def _post(client, path, payload):
    return client.request("POST", path, body=json.dumps(payload).encode("utf-8"), origin=client.base_url)


def _templates(client) -> dict:
    status, payload, _ = client.request("GET", "/samples", origin=client.base_url)
    assert status == 200
    return {item["id"]: item for item in payload["workflows"]}


def _config(template: dict, source: str) -> dict:
    config = copy.deepcopy(template["config"])
    config["input"]["source"] = source
    return config


def _wait(client, run_id: str, states: set[str], timeout: float = 20.0) -> dict:
    deadline = time.monotonic() + timeout
    while True:
        status, result, _ = client.request("GET", f"/runs/{run_id}", origin=client.base_url)
        assert status == 200
        if result["status"] in states:
            return result
        assert time.monotonic() < deadline, result["status"]
        time.sleep(0.1)


def test_public_demo_lists_bundled_templates_and_samples(public):
    _server, client = public
    templates = _templates(client)
    assert set(templates) == {"document-quality", "motion-observation"}
    assert templates["document-quality"]["samples"] == ["samples/document-blurred.png", "samples/document-sharp.png"]
    assert templates["motion-observation"]["samples"] == ["samples/motion.avi"]
    assert templates["document-quality"]["config"]["input"]["kind"] == "image"


def test_public_document_run_uses_real_opencv_on_both_samples(public):
    _server, client = public
    template = _templates(client)["document-quality"]
    status, started, _ = _post(client, "/runs", _config(template, "samples/document-blurred.png"))
    assert status == 202, started
    waiting = _wait(client, started["run_id"], {"waiting_for_input"})
    status, resumed, _ = _post(client, f"/runs/{started['run_id']}/input", {"source": "samples/document-sharp.png"})
    assert status == 200, resumed
    final = _wait(client, started["run_id"], {"completed", "failed"})
    assert final["status"] == "completed"
    sharpness = [f["metrics"]["sharpness_variance"] for f in final["findings"] if f["tool"] == "quality"]
    assert len(sharpness) == 2 and sharpness[1] > sharpness[0]  # the sharp sample measures sharper
    assert waiting["findings"][0]["frame_ids"][0].startswith("samples/document-blurred.png")
    reference = final["findings"][0]["evidence_ref"]
    status, image, headers = client.request("GET", f"/runs/{started['run_id']}/evidence?ref={reference}", origin=client.base_url)
    assert status == 200 and headers["content-type"] == "image/png"


def test_public_motion_run_completes_on_the_sample_video(public):
    _server, client = public
    template = _templates(client)["motion-observation"]
    status, started, _ = _post(client, "/runs", _config(template, "samples/motion.avi"))
    assert status == 202, started
    final = _wait(client, started["run_id"], {"completed", "failed"})
    assert final["status"] == "completed"
    assert any(f["tool"] == "motion" for f in final["findings"])


@pytest.mark.parametrize("change", [
    lambda c: c["limits"].update(max_frames=301),
    lambda c: c["nodes"].append({"id": "extra", "type": "report", "params": {"format": "json"}, "next": []}),
    lambda c: c["outputs"].append({"type": "notification_draft", "requires_approval": True}),
    lambda c: c.update(name="renamed"),
    lambda c: c["nodes"][0]["params"]["tool_params"].update(initial_frames=5),
])
def test_public_demo_rejects_any_change_to_the_bundled_template(public, change):
    _server, client = public
    config = _config(_templates(client)["document-quality"], "samples/document-blurred.png")
    change(config)
    status, payload, _ = _post(client, "/runs", config)
    assert status == 403
    assert payload["error"]["code"] == "public_demo_workflow_disabled"


@pytest.mark.parametrize("source", [
    "samples/motion.avi",  # sample of the wrong kind for an image template
    "samples/unknown.png",
    "samples/../uploads/vf_" + "a" * 48 + ".bin",
    "C:/Windows/win.ini",
    "/etc/passwd",
    "uploads/vf_" + "a" * 48 + ".bin",
])
def test_public_demo_rejects_paths_and_foreign_samples(public, source):
    _server, client = public
    config = _config(_templates(client)["document-quality"], source)
    status, payload, _ = _post(client, "/runs", config)
    assert status == 403
    assert payload["error"]["code"] in {"public_demo_source_rejected", "public_demo_workflow_disabled"}


def test_public_resume_accepts_only_a_sample_without_extra_input(public):
    _server, client = public
    template = _templates(client)["document-quality"]
    run_id = _post(client, "/runs", _config(template, "samples/document-blurred.png"))[1]["run_id"]
    _wait(client, run_id, {"waiting_for_input"})
    for body in ({"source": "C:/Windows/win.ini"}, {"source": "samples/motion.avi"},
                 {"source": "samples/document-sharp.png", "input": {"note": "x" * 50}},
                 {"input": {"source": "uploads/vf_" + "b" * 48 + ".bin"}}):
        status, payload, _ = _post(client, f"/runs/{run_id}/input", body)
        assert status == 403, (body, payload)
    status, _payload, _ = _post(client, f"/runs/{run_id}/input", {"source": "samples/document-sharp.png", "input": {}})
    assert status == 200


def _status(client, run_id: str) -> str:
    return client.request("GET", f"/runs/{run_id}", origin=client.base_url)[1]["status"]


def test_a_new_sample_run_replaces_the_sessions_open_run_even_after_reload(public):
    """I-1: a reload or language switch forgets the run ID; starting again must work."""
    _server, client = public
    template = _templates(client)["document-quality"]
    first = _post(client, "/runs", _config(template, "samples/document-blurred.png"))[1]["run_id"]
    _wait(client, first, {"waiting_for_input"})
    reloaded = ApiClient(client.base_url)  # same browser cookie, page state lost
    reloaded.cookie = client.cookie
    assert reloaded.request("GET", "/session", origin=client.base_url)[0] == 200
    reloaded.csrf_token = reloaded.request("GET", "/session", origin=client.base_url)[1]["csrf_token"]
    status, started, _ = _post(reloaded, "/runs", _config(template, "samples/document-blurred.png"))
    assert status == 202, started
    assert _status(client, first) == "cancelled"
    assert _wait(client, started["run_id"], {"waiting_for_input"})["status"] == "waiting_for_input"


def test_abandoned_waiting_sample_runs_are_cancelled_after_the_wait_limit(tmp_path, monkeypatch):
    import visionflow.api as api

    monkeypatch.setattr(api, "MAX_ACTIVE_PUBLIC_SAMPLE_RUNS", 1)
    monkeypatch.setattr(api, "MAX_SAMPLE_WAIT_SECONDS", 0.3)
    server, thread, abandoned = _serve(tmp_path)
    try:
        template = _templates(abandoned)["document-quality"]
        first = _post(abandoned, "/runs", _config(template, "samples/document-blurred.png"))[1]["run_id"]
        _wait(abandoned, first, {"waiting_for_input"})
        time.sleep(0.4)
        other = ApiClient(abandoned.base_url)
        other.bootstrap()
        status, payload, _ = _post(other, "/runs", _config(template, "samples/document-blurred.png"))
        assert status == 202, payload
        assert _status(abandoned, first) == "cancelled"
    finally:
        server.shutdown()
        thread.join(timeout=3)
        server.server_close()


def test_busy_sample_slots_report_their_own_code(tmp_path, monkeypatch):
    import visionflow.api as api

    monkeypatch.setattr(api, "MAX_ACTIVE_PUBLIC_SAMPLE_RUNS", 1)
    server, thread, first = _serve(tmp_path)
    try:
        template = _templates(first)["document-quality"]
        run_id = _post(first, "/runs", _config(template, "samples/document-blurred.png"))[1]["run_id"]
        _wait(first, run_id, {"waiting_for_input"})
        second = ApiClient(first.base_url)
        second.bootstrap()
        status, payload, headers = _post(second, "/runs", _config(template, "samples/document-blurred.png"))
        assert status == 503
        assert payload["error"]["code"] == "public_demo_samples_busy"
        assert int(headers["retry-after"]) >= 1
    finally:
        server.shutdown()
        thread.join(timeout=3)
        server.server_close()


def test_parallel_sample_starts_respect_the_limits(tmp_path, monkeypatch):
    """M-1: admission, start and registration are one step."""
    import visionflow.api as api

    monkeypatch.setattr(api, "MAX_ACTIVE_PUBLIC_SAMPLE_RUNS", 3)
    server, thread, client = _serve(tmp_path)
    try:
        template = _templates(client)["document-quality"]
        config = _config(template, "samples/document-blurred.png")
        clients = [client] + [ApiClient(client.base_url) for _ in range(5)]
        for other in clients[1:]:
            other.bootstrap()
        barrier = threading.Barrier(12)
        results = []
        lock = threading.Lock()

        def start(owner):
            barrier.wait()
            outcome = _post(owner, "/runs", config)
            with lock:
                results.append((owner, outcome[0], outcome[1]))

        # Six sessions start twice each, all at once.
        workers = [threading.Thread(target=start, args=(owner,)) for owner in clients for _ in range(2)]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(timeout=20)
        open_runs = 0
        for owner in clients:
            mine = [payload["run_id"] for who, status, payload in results if who is owner and status == 202]
            states = [_status(owner, run_id) for run_id in mine]
            assert sum(state not in {"completed", "failed", "cancelled"} for state in states) <= 1, states
            open_runs += sum(state not in {"completed", "failed", "cancelled"} for state in states)
        assert open_runs <= 3
        assert all(status in {202, 503} for _who, status, _payload in results)
    finally:
        server.shutdown()
        thread.join(timeout=3)
        server.server_close()


def test_public_demo_keeps_only_a_bounded_number_of_finished_sample_runs(tmp_path, monkeypatch):
    import visionflow.api as api

    monkeypatch.setattr(api, "MAX_PUBLIC_SAMPLE_RUNS", 3)
    server, thread, client = _serve(tmp_path)
    try:
        template = _templates(client)["motion-observation"]
        run_ids = []
        for _ in range(5):
            status, started, _ = _post(client, "/runs", _config(template, "samples/motion.avi"))
            assert status == 202, started
            _wait(client, started["run_id"], {"completed"})
            run_ids.append(started["run_id"])
        status, _payload, _ = client.request("GET", f"/runs/{run_ids[0]}", origin=client.base_url)
        assert status == 404  # the oldest finished sample run and its evidence were removed
        assert not (server.state.evidence_root / run_ids[0]).exists()
        assert client.request("GET", f"/runs/{run_ids[-1]}", origin=client.base_url)[0] == 200
    finally:
        server.shutdown()
        thread.join(timeout=3)
        server.server_close()


def test_hosted_runtime_with_retention_runs_sample_workflows(tmp_path):
    from visionflow.retention import RetentionPolicy
    from visionflow.road_runtime import build_road_runtime

    runtime = build_road_runtime(tmp_path / "data", max_live_sessions=4, retention_policy=RetentionPolicy())
    server, thread, client = _serve(tmp_path, runtime=runtime)
    try:
        template = _templates(client)["document-quality"]
        status, started, _ = _post(client, "/runs", _config(template, "samples/document-blurred.png"))
        assert status == 202, started
        assert _wait(client, started["run_id"], {"waiting_for_input", "failed"})["status"] == "waiting_for_input"
    finally:
        server.shutdown()
        thread.join(timeout=3)
        server.server_close()
        runtime.close()


def test_local_mode_still_accepts_uploads_and_free_workflows(tmp_path):
    server, thread, client = _serve(tmp_path, public=False)
    try:
        status, payload, _ = client.request("GET", "/samples", origin=client.base_url)
        assert status == 200 and payload["workflows"]
        status, uploaded, _ = client.request(
            "POST", "/media", body=b"x" * 10,
            headers={"Content-Type": "application/octet-stream", "X-VisionFlow-Media-Kind": "image"},
            origin=client.base_url,
        )
        assert status == 201
    finally:
        server.shutdown()
        thread.join(timeout=3)
        server.server_close()
