"""Recovery must preserve server decisions and never widen ownership."""
from __future__ import annotations

import threading

from test_road_cases import _fixture, _adapter_for, _wait_for_phase
from test_road_review_api import _ReviewClient


def test_list_cases_is_owner_scoped_and_ordered(tmp_path):
    fixture = _fixture(tmp_path, sources=(("alice", "road-sim-a"), ("bob", "road-sim-b"), ("alice", "road-sim-c")))
    service = fixture["service"]
    try:
        first = service.start("alice", "road-sim-a", "fixture")
        service.start("bob", "road-sim-b", "fixture")
        last = service.start("alice", "road-sim-c", "fixture")
        rows = service.list_cases("alice")
        assert [row["case_id"] for row in rows] == [first["case_id"], last["case_id"]]
        assert service.list_cases("stranger") == []
    finally:
        service.close()


def test_recovery_preserves_deadline_hash_and_single_report(tmp_path):
    fixture = _fixture(tmp_path, countdown=60)
    service = fixture["service"]
    try:
        case = service.start("alice", "road-sim-a", "fixture", fixture_profile="pending")
        adapter, findings = _adapter_for(fixture, case)
        adapter.ask("Review the candidate", findings)
        fixture["controller"].complete(case["run_id"], findings=findings)
        before = _wait_for_phase(service, "alice", case["case_id"], {"human_review"})
        fixture["clock"].advance(59)
        after = service.list_cases("alice")[0]
        assert after["deadline_at"] == before["deadline_at"]
        assert after["content_hash"] == before["content_hash"]
        assert after["clip"] == before["clip"]
        assert after["remaining_seconds"] == 1
        fixture["clock"].advance(2)
        fixture["decisions"].tick()
        for _ in range(3):
            assert service.list_cases("alice")[0]["phase"] == "no_response"
        assert len(fixture["inbox"].list("alice")) == 1
    finally:
        service.close()


def test_case_list_route_requires_session_and_filters_owners(tmp_path):
    from visionflow.api import create_server
    fixture = _fixture(tmp_path)
    service = fixture["service"]
    server = create_server(fixture["controller"], port=0, data_dir=tmp_path,
                           road_sessions=fixture["sessions"], road_cases=service)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        origin = f"http://127.0.0.1:{server.server_address[1]}"
        alice = _ReviewClient(origin)
        bob = _ReviewClient(origin)
        # The fixture source has a stable owner; sessions are authenticated HTTP clients.
        fixture["sessions"]._sessions["road-sim-a"].owner_id = alice.cookie
        fixture["recordings"]._owners["road-sim-a"] = alice.cookie
        case = service.start(alice.cookie, "road-sim-a", "fixture")
        status, body, _ = alice.request("GET", "/road/cases")
        assert status == 200
        assert [row["case_id"] for row in body["cases"]] == [case["case_id"]]
        assert bob.request("GET", "/road/cases")[:2] == (200, {"cases": []})
        alice.cookie = ""
        assert alice.request("GET", "/road/cases")[0] == 403
    finally:
        server.shutdown()
        # The unit fixture provides source lookup, not a producer lifecycle.
        server.state.road_sessions = None
        server.server_close()
        thread.join(3)
        service.close()
