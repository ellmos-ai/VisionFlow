"""HTTP integration for the actual road-camera review workflow."""

from __future__ import annotations

import json
import shutil
import threading
import time
import urllib.error
import urllib.request
from http.cookies import SimpleCookie
from pathlib import Path

import pytest


class _ReviewClient:
    def __init__(self, base_url: str) -> None:
        self.base_url = base_url
        self.cookie = ""
        self.csrf = ""
        self.bootstrap()

    def bootstrap(self) -> None:
        request = urllib.request.Request(self.base_url + "/session", method="GET")
        with urllib.request.urlopen(request, timeout=5) as response:
            payload = json.loads(response.read().decode("utf-8"))
            cookie = SimpleCookie()
            cookie.load(response.headers.get("Set-Cookie", ""))
            self.cookie = cookie["vf_session"].value
            self.csrf = payload["csrf_token"]

    def request(self, method: str, path: str, payload=None):
        headers = {
            "Origin": self.base_url,
            "Cookie": f"vf_session={self.cookie}",
            "X-CSRF-Token": self.csrf,
        }
        body = None
        if payload is not None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(
            self.base_url + path,
            data=body,
            headers=headers,
            method=method,
        )
        try:
            response = urllib.request.urlopen(request, timeout=10)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            raw = response.read()
            decoded = json.loads(raw.decode("utf-8")) if raw else None
            return response.status, decoded, response.headers


def _road_runtime_api():
    import importlib

    try:
        return importlib.import_module("visionflow.road_runtime")
    except ModuleNotFoundError as error:
        if error.name == "visionflow.road_runtime":
            pytest.fail(
                "Task 4 RED: public road runtime construction is missing; "
                "the actual case workflow cannot be assembled",
                pytrace=False,
            )
        raise


def test_case_review_routes_follow_real_pixels_and_preserve_owner_decision_flow(tmp_path):
    """Use Simulation, Controller, Recorder, FFmpeg, API, and the real CaseService."""
    from visionflow.api import create_server

    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        pytest.skip("FFmpeg is required for the real road-case HTTP scenario")

    runtime_module = _road_runtime_api()
    runtime = runtime_module.build_road_runtime(
        tmp_path,
        ffmpeg_path=ffmpeg,
        allowed_camera_sources={},
        allow_native_costs=False,
    )
    if runtime.road_sessions is None or runtime.road_cases is None:
        pytest.skip("FFmpeg with libx264 is required for the road-case HTTP scenario")

    try:
        server = create_server(
            runtime.controller,
            host="127.0.0.1",
            port=0,
            data_dir=tmp_path,
            evidence_root=runtime.controller.evidence_root,
            road_sessions=runtime.road_sessions,
            road_cases=runtime.road_cases,
            road_unavailable_reason=runtime.road_unavailable_reason,
        )
    except TypeError:
        runtime.close()
        pytest.fail(
            "Task 4 RED: create_server does not yet accept the RoadCaseService",
            pytrace=False,
        )

    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address[:2]
    owner = _ReviewClient(f"http://{host}:{port}")
    other = _ReviewClient(f"http://{host}:{port}")
    try:
        status, created, _ = owner.request("POST", "/road/sessions", {"seed": 42})
        assert status == 201
        source_id = created["source_id"]

        # A foreign local session cannot bind a case to the owner's source.
        status, rejected, _ = other.request(
            "POST",
            "/road/cases",
            {"source_id": source_id, "mode": "fixture", "fixture_profile": "pending"},
        )
        assert status in {403, 404}
        assert rejected["error"]["code"] in {"owner_mismatch", "session_mismatch", "road_source_not_found"}

        status, rejected_profile, _ = owner.request(
            "POST",
            "/road/cases",
            {"source_id": source_id, "mode": "fixture", "fixture_profile": "scenario-derived"},
        )
        assert status == 422
        assert rejected_profile["error"]["code"] == "fixture_profile_not_allowed"
        status, rejected_native_profile, _ = owner.request(
            "POST",
            "/road/cases",
            {"source_id": source_id, "mode": "gemini_video", "fixture_profile": None},
        )
        assert status == 422
        assert rejected_native_profile["error"]["code"] == "fixture_profile_not_allowed"

        # Let the road texture stabilize before generating real wrong-way pixels.
        time.sleep(1.0)
        status, accepted, _ = owner.request(
            "POST",
            "/road/cases",
            {"source_id": source_id, "mode": "fixture", "fixture_profile": "pending"},
        )
        assert status == 202
        case = accepted["case"]
        assert case["source_id"] == source_id
        assert case["mode"] == "fixture"
        assert case["fixture_profile"] == "pending"
        assert case["run_id"]

        # The browser injects only after three actual analysis frames. Source
        # startup alone does not establish that the detector has warmed up.
        warmup_deadline = time.monotonic() + 30.0
        analysed = []
        while time.monotonic() < warmup_deadline:
            status, ready, _ = owner.request("GET", f"/road/cases/{case['case_id']}")
            assert status == 200
            analysed = [row for row in ready["perception_trace"] if row.get("type") in {
                "frame_analyzed", "frame_observed", "observation_frame",
            }]
            if len(analysed) >= 3:
                break
            time.sleep(0.1)
        assert len(analysed) >= 3, "detector did not reach the UI readiness gate"

        status, injected, _ = owner.request(
            "POST", f"/road/sessions/{source_id}/events", {"event": "wrong_way"}
        )
        assert status == 200
        assert injected["accepted"] is True

        deadline = time.monotonic() + 60.0
        current = None
        while time.monotonic() < deadline:
            status, current, _ = owner.request("GET", f"/road/cases/{case['case_id']}")
            assert status == 200
            if current["phase"] in {"human_review", "reported", "dismissed", "no_response"}:
                break
            time.sleep(0.1)

        assert current is not None
        assert current["phase"] == "human_review"
        assert current["controller_status"] == "completed"
        assert current["clip"]["source_id"] == source_id
        assert "path" not in current["clip"]

        status, model, _ = owner.request("GET", f"/road/cases/{case['case_id']}/model")
        assert status == 200
        assert model["case_id"] == case["case_id"]
        assert len(model["exchanges"]) == 1
        exchange = model["exchanges"][0]
        assert exchange["status"] == "complete"
        assert exchange["mode"] == "fixture"
        assert exchange["fixture_profile"] == "pending"
        assert exchange["clip"]["source_id"] == source_id
        assert "path" not in exchange["clip"]
        assert exchange["assessment"]["object_class"] == "vehicle"
        assert exchange["assessment"]["confidence"] < 0.9

        # Case and evidence endpoints never authorize the second local session.
        status, foreign_case, _ = other.request("GET", f"/road/cases/{case['case_id']}")
        assert status == 404
        assert foreign_case["error"]["code"] == "case_not_found"
        status, foreign_model, _ = other.request("GET", f"/road/cases/{case['case_id']}/model")
        assert status == 404
        assert foreign_model["error"]["code"] == "case_not_found"
        status, foreign_decision, _ = other.request(
            "POST", f"/road/cases/{case['case_id']}/decision", {}
        )
        assert status == 404
        assert foreign_decision["error"]["code"] == "case_not_found"
        evidence_ref = exchange["evidence_refs"][0]
        evidence_path = f"/runs/{case['run_id']}/evidence?ref={evidence_ref}"
        status, foreign_evidence, _ = other.request("GET", evidence_path)
        assert status == 403
        assert foreign_evidence["error"]["code"] == "session_mismatch"

        decision_url = f"/road/cases/{case['case_id']}/decision"
        status, invalid_decision, _ = owner.request(
            "POST",
            decision_url,
            {
                "version": current["version"],
                "content_hash": current["content_hash"],
                "decision": {"unexpected": "object"},
                "idempotency_key": "invalid-decision-shape",
            },
        )
        assert status == 422
        assert invalid_decision["error"]["code"] == "invalid_decision"
        stale_body = {
            "version": current["version"] - 1,
            "content_hash": current["content_hash"],
            "decision": "confirm",
            "idempotency_key": "road-http-confirm-1",
        }
        status, stale, _ = owner.request("POST", decision_url, stale_body)
        assert status == 409
        assert stale["error"]["code"] == "stale_version"

        body = {
            "version": current["version"],
            "content_hash": current["content_hash"],
            "decision": "confirm",
            "idempotency_key": "road-http-confirm-1",
        }
        status, decided, _ = owner.request("POST", decision_url, body)
        assert status == 200
        assert decided["phase"] == "reported"
        status, duplicate, _ = owner.request("POST", decision_url, body)
        assert status == 200
        assert duplicate["first_receipt"] == decided["first_receipt"]

        status, inbox, _ = owner.request("GET", "/road/inbox")
        assert status == 200
        assert isinstance(inbox, list)
        assert len(inbox) == 1
        assert inbox[0]["case_id"] == case["case_id"]
        assert inbox[0]["reason"] == "human_confirmed"
        assert "path" not in inbox[0]["clip"]
        status, other_inbox, _ = other.request("GET", "/road/inbox")
        assert status == 200
        assert other_inbox == []
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_review_capabilities_are_owner_protected_and_do_not_expose_native_secrets(tmp_path):
    """Native availability is a boolean capability, never a credential echo."""
    from visionflow.api import create_server

    runtime_module = _road_runtime_api()
    runtime = runtime_module.build_road_runtime(
        tmp_path,
        ffmpeg_path=shutil.which("ffmpeg"),
        allowed_camera_sources={},
        gemini_api_key="test-secret-do-not-echo",
        allow_native_costs=False,
    )
    try:
        server = create_server(
            runtime.controller,
            host="127.0.0.1",
            port=0,
            data_dir=tmp_path,
            road_sessions=runtime.road_sessions,
            road_cases=runtime.road_cases,
            road_unavailable_reason=runtime.road_unavailable_reason,
        )
    except TypeError:
        runtime.close()
        pytest.fail("Task 4 RED: create_server does not expose road review capabilities", pytrace=False)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address[:2]
    client = _ReviewClient(f"http://{host}:{port}")
    try:
        no_session = urllib.request.Request(f"http://{host}:{port}/road/capabilities", method="GET")
        try:
            response = urllib.request.urlopen(no_session, timeout=5)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            assert response.status == 403

        status, capabilities, _ = client.request("GET", "/road/capabilities")
        assert status == 200
        assert capabilities["available"] is True
        assert capabilities["encoder_available"] is True
        assert capabilities["native_enabled"] is False
        assert "fixture" in capabilities["modes"]
        assert "test-secret-do-not-echo" not in json.dumps(capabilities)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_native_runtime_needs_both_key_and_explicit_cost_gate_and_missing_encoder_keeps_core(tmp_path, monkeypatch):
    runtime_module = _road_runtime_api()
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        pytest.skip("FFmpeg is required to exercise the enabled native capability")

    default_runtime = runtime_module.build_road_runtime(
        tmp_path / "default",
        ffmpeg_path=ffmpeg,
        gemini_api_key="test-secret-do-not-echo",
        allow_native_costs=False,
    )
    enabled_runtime = runtime_module.build_road_runtime(
        tmp_path / "enabled",
        ffmpeg_path=ffmpeg,
        gemini_api_key="test-secret-do-not-echo",
        allow_native_costs=True,
    )
    try:
        assert default_runtime.controller.model_adapter.capabilities["native_enabled"] is False
        assert "gemini_video" not in default_runtime.controller.model_adapter.capabilities["modes"]
        assert enabled_runtime.controller.model_adapter.capabilities["native_enabled"] is True
        assert "gemini_video" in enabled_runtime.controller.model_adapter.capabilities["modes"]
    finally:
        default_runtime.close()
        enabled_runtime.close()

    monkeypatch.setattr(runtime_module.shutil, "which", lambda _name: None)
    no_encoder_runtime = runtime_module.build_road_runtime(
        tmp_path / "without-ffmpeg",
        allowed_camera_sources={},
        gemini_api_key="test-secret-do-not-echo",
        allow_native_costs=True,
    )
    try:
        assert no_encoder_runtime.road_sessions is None
        assert no_encoder_runtime.road_cases is None
        assert no_encoder_runtime.road_unavailable_reason == "ffmpeg_required"
        assert no_encoder_runtime.controller.model_adapter.capabilities["native_enabled"] is False
        assert callable(no_encoder_runtime.controller.model_adapter.create_adapter)
    finally:
        no_encoder_runtime.close()
