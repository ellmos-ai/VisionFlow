"""Global resource admission and expiry, with an injected monotonic clock."""
from __future__ import annotations

import importlib
import threading
import shutil
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

import pytest


def _api():
    try:
        return importlib.import_module("visionflow.retention")
    except ModuleNotFoundError as exc:
        if exc.name != "visionflow.retention":
            raise
        pytest.fail("RetentionManager is missing", pytrace=False)


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def make_manager(*, sources=32, budget=100, idle=1800, cleanup=None):
    api = _api()
    clock = Clock()
    removed = []
    manager = api.RetentionManager(
        api.RetentionPolicy(max_sources=sources, max_bytes=budget, idle_seconds=idle),
        clock=clock, cleanup=cleanup or (lambda owner, source: removed.append((owner, source))),
    )
    return api, manager, clock, removed


def test_source_count_and_bytes_are_both_admission_limits():
    api, manager, _, _ = make_manager()
    for index in range(32):
        manager.reserve_source("alice", f"source-{index}", 1)
    with pytest.raises(api.RetentionCapacityError):
        manager.reserve_source("alice", "source-33", 1)
    assert manager.snapshot()["source_count"] == 32
    _, small, _, _ = make_manager(budget=3)
    small.reserve_source("alice", "a", 3)
    with pytest.raises(api.RetentionCapacityError):
        small.reserve_source("bob", "b", 1)


def test_parallel_reservations_do_not_exceed_global_budget():
    api, manager, _, _ = make_manager(budget=10)
    barrier = threading.Barrier(20)

    def reserve(index):
        barrier.wait(timeout=5)
        try:
            manager.reserve_source("alice", f"s-{index}", 1)
            return True
        except api.RetentionCapacityError:
            return False

    with ThreadPoolExecutor(max_workers=20) as pool:
        assert sum(pool.map(reserve, range(20))) == 10
    assert manager.snapshot()["reserved_bytes"] == 10


def test_active_review_and_export_are_pinned():
    _, manager, clock, removed = make_manager()
    manager.reserve_source("alice", "review", 1)
    manager.reserve_source("alice", "export", 1)
    with manager.protect("review", activity=True):
        with manager.protect("export", activity=True):
            clock.now = 5000
            assert manager.collect(clock.now) == []
        assert manager.collect(clock.now) == []
    assert removed == []
    clock.now += 1800
    assert set(manager.collect(clock.now)) == {"review", "export"}


def test_poll_does_not_extend_retention():
    _, manager, clock, removed = make_manager()
    manager.reserve_source("alice", "camera", 1)
    for timestamp in (100, 500, 1799):
        clock.now = timestamp
        with manager.protect("camera"):
            assert manager.snapshot()["source_count"] == 1
    assert manager.collect(1800) == ["camera"]
    assert removed == [("alice", "camera")]


def test_late_correction_retained_for_thirty_minutes():
    _, manager, clock, _ = make_manager()
    manager.reserve_source("alice", "camera", 1)
    with manager.protect("camera", activity=True):
        clock.now = 60  # first report after the real decision deadline
    assert manager.collect(1859) == []
    clock.now = 1859
    with manager.protect("camera", activity=True):
        pass  # accepted late correction is new activity
    assert manager.collect(3658) == []
    assert manager.collect(3659) == ["camera"]


def test_write_is_rejected_before_side_effect_and_failure_rolls_back(tmp_path):
    api, manager, _, _ = make_manager(budget=10)
    manager.reserve_source("alice", "camera", 5)
    with manager.change_usage("camera", "frames", 8):
        (tmp_path / "accepted").write_bytes(b"12345678")
    assert manager.snapshot()["used_bytes"] == 8
    with pytest.raises(api.RetentionCapacityError):
        with manager.change_usage("camera", "clips", 3):
            (tmp_path / "should-not-exist").write_bytes(b"123")
    assert not (tmp_path / "should-not-exist").exists()
    with pytest.raises(OSError):
        with manager.change_usage("camera", "clips", 2):
            raise OSError("write failed")
    assert manager.snapshot()["used_bytes"] == 8


def test_cleanup_failure_keeps_the_source_charged():
    def broken_cleanup(owner, source):
        raise OSError("permission denied")

    api, manager, _, _ = make_manager(budget=10, cleanup=broken_cleanup)
    manager.reserve_source("alice", "camera", 10)
    assert manager.collect(1800) == []
    assert manager.snapshot()["reserved_bytes"] == 10
    with pytest.raises(api.RetentionCapacityError):
        manager.reserve_source("bob", "next", 1)


def test_unused_reservation_is_released_after_source_finishes():
    _, manager, _, _ = make_manager(budget=10)
    manager.reserve_source("alice", "finished", 10)
    with manager.change_usage("finished", "recording", 3):
        pass
    manager.settle("finished")
    manager.reserve_source("bob", "next", 7)
    assert manager.snapshot()["reserved_bytes"] == 10


def test_cleanup_never_follows_outside_symlink(tmp_path):
    api = _api()
    root = tmp_path / "data"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / "keep.txt"
    sentinel.write_text("keep", encoding="utf-8")
    source = root / "source"
    source.mkdir()
    try:
        (source / "escape").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlinks require Windows privilege")
    with pytest.raises((OSError, ValueError)):
        api.remove_owned_tree(root, source)
    assert sentinel.read_text(encoding="utf-8") == "keep"


def _runtime(tmp_path, policy, clock):
    from visionflow.road_runtime import build_road_runtime
    try:
        runtime = build_road_runtime(tmp_path, retention_policy=policy, retention_clock=clock)
    except TypeError as exc:
        pytest.fail(f"Road runtime has no retention composition: {exc}", pytrace=False)
    assert runtime.road_sessions is not None, "FFmpeg/libx264 is required for retention integration"
    return runtime


def test_runtime_collects_owned_files_and_references_after_expiry(tmp_path):
    api = _api()
    clock = Clock()
    runtime = _runtime(tmp_path, api.RetentionPolicy(max_sources=1), clock)
    try:
        service = runtime.road_sessions
        source = service.create("alice")["source_id"]
        time.sleep(.15)
        clock.now = 2000
        assert runtime.retention.collect(clock.now) == [], "live producer must be pinned"
        case = runtime.road_cases.start("alice", source, "fixture")
        run_id = case["run_id"]
        runtime.road_cases.cancel("alice", case["case_id"])
        runtime.controller.join(run_id, timeout=3)
        service.stop("alice", source)
        assert runtime.retention.snapshot()["source_count"] == 1
        assert runtime.retention.collect(clock.now + 1799) == []
        assert runtime.retention.collect(clock.now + 1800) == [source]
        with pytest.raises(KeyError):
            service.get("alice", source)
        with pytest.raises(Exception, match="case not found"):
            runtime.road_cases.snapshot("alice", case["case_id"])
        assert not (runtime.controller.evidence_root / run_id).exists()
        assert not any((tmp_path / "road-recordings").rglob("*.jpg"))
        assert runtime.retention.snapshot()["reserved_bytes"] == 0
        assert run_id not in runtime.controller._runs
        assert not service.sessions._sessions
        assert not service._bindings
    finally:
        runtime.close()


def test_recorder_budget_rejects_frame_before_writing_bytes(tmp_path):
    import numpy as np
    from visionflow.contracts import Frame
    from visionflow.recording import RecordingStore
    api, manager, _, _ = make_manager(budget=8192)
    manager.reserve_source("alice", "camera", 0)
    try:
        recorder = RecordingStore(tmp_path / "recordings", shutil.which("ffmpeg"), retention=manager)
    except TypeError as exc:
        pytest.fail(f"Recorder does not account to global budget: {exc}", pytrace=False)
    recorder.open("alice", "camera")
    before = {p: p.read_bytes() for p in (tmp_path / "recordings").rglob("*") if p.is_file()}
    random = np.random.default_rng(42).integers(0, 256, (540, 960, 3), dtype=np.uint8)
    with pytest.raises(api.RetentionCapacityError):
        recorder.append("camera", Frame("camera:0", random, 0.0))
    after = {p: p.read_bytes() for p in (tmp_path / "recordings").rglob("*") if p.is_file()}
    assert after == before


def test_capacity_error_is_retriable_and_local_mode_is_unchanged(tmp_path):
    from visionflow.api import create_server
    from test_road_review_api import _ReviewClient
    api = _api()
    runtime = _runtime(tmp_path, api.RetentionPolicy(max_sources=1), Clock())
    server = create_server(runtime.controller, port=0, data_dir=tmp_path, public_demo=True,
                           road_sessions=runtime.road_sessions, road_cases=runtime.road_cases)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        client = _ReviewClient(f"http://127.0.0.1:{server.server_address[1]}")
        assert client.request("POST", "/road/sessions", {})[0] == 201
        status, body, headers = client.request("POST", "/road/sessions", {})
        assert status == 503 and body["error"]["code"] == "demo_capacity_full"
        assert headers["Retry-After"] == "60"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(3)
        runtime.close()


def test_controller_evidence_uses_global_budget_and_still_records_failure(tmp_path):
    import numpy as np
    from test_controller import FakeSource, make_workflow
    from visionflow.contracts import Frame, Node
    from visionflow.controller import Controller
    from visionflow.runtime import create_registry
    _, manager, _, _ = make_manager(budget=8192)
    manager.reserve_source("alice", "fixture-source", 0)
    random = np.random.default_rng(17).integers(0, 256, (540, 960, 3), dtype=np.uint8)
    try:
        controller = Controller(create_registry(), evidence_root=tmp_path / "evidence", retention=manager,
                                source_factory=lambda config, max_frames: FakeSource([Frame("fixture-source:0", random, 0.0)]))
    except TypeError as exc:
        pytest.fail(f"Controller does not account global evidence: {exc}", pytrace=False)
    config = make_workflow([Node("inspect", "analyze", {"tool": "quality", "tool_params": {}}, [])])
    queued = controller.start(config)
    result = controller.wait(queued.run_id, timeout=5)
    assert result.status == "failed"
    assert any(row.get("state") == "failed" for row in result.trace)
    assert not list((tmp_path / "evidence").rglob("*.bin"))
    assert manager.snapshot()["reserved_bytes"] <= 8192


def test_failed_shrinking_write_does_not_lend_its_rollback_bytes():
    api, manager, _, _ = make_manager(budget=10)
    manager.reserve_source("alice", "a", 0)
    manager.reserve_source("bob", "b", 0)
    with manager.change_usage("a", "frames", 8):
        pass
    with pytest.raises(OSError):
        with manager.change_usage("a", "frames", 2):
            with pytest.raises(api.RetentionCapacityError):
                with manager.change_usage("b", "frames", 3):
                    pass
            raise OSError("replacement failed")
    assert manager.snapshot()["used_bytes"] == 8


def test_public_generic_runs_cannot_bypass_case_retention(tmp_path):
    from visionflow.api import create_server
    from visionflow.road_api import road_monitor_workflow
    from test_road_review_api import _ReviewClient
    api = _api()
    runtime = _runtime(tmp_path, api.RetentionPolicy(), Clock())
    server = create_server(runtime.controller, port=0, data_dir=tmp_path, public_demo=True,
                           road_sessions=runtime.road_sessions, road_cases=runtime.road_cases)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        client = _ReviewClient(f"http://127.0.0.1:{server.server_address[1]}")
        source = client.request("POST", "/road/sessions", {})[1]["source_id"]
        config = road_monitor_workflow()
        config["input"]["source"] = source
        status, body, _ = client.request("POST", "/runs", config)
        assert status == 403 and body["error"]["code"] == "public_demo_workflow_disabled"
        assert runtime.controller._runs == {}
    finally:
        server.shutdown()
        server.server_close()
        thread.join(3)
        runtime.close()


def test_public_session_tokens_are_bounded(tmp_path):
    from visionflow.api import create_server
    from visionflow.runtime import create_controller
    from test_road_review_api import _ReviewClient
    server = create_server(create_controller(tmp_path), port=0, data_dir=tmp_path, public_demo=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        origin = f"http://127.0.0.1:{server.server_address[1]}"
        for _ in range(128):
            _ReviewClient(origin)
        with pytest.raises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(origin + "/session", timeout=5)
        assert error.value.code == 503
        assert error.value.headers["Retry-After"] == "60"
        assert len(server.state.sessions) == 128
    finally:
        server.shutdown()
        server.server_close()
        thread.join(3)


def test_collection_removes_http_run_owner_references(tmp_path):
    from visionflow.api import create_server
    from test_road_review_api import _ReviewClient
    api = _api()
    clock = Clock()
    runtime = _runtime(tmp_path, api.RetentionPolicy(), clock)
    server = create_server(runtime.controller, port=0, data_dir=tmp_path, public_demo=True,
                           road_sessions=runtime.road_sessions, road_cases=runtime.road_cases)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        client = _ReviewClient(f"http://127.0.0.1:{server.server_address[1]}")
        source = client.request("POST", "/road/sessions", {})[1]["source_id"]
        case = client.request("POST", "/road/cases", {"source_id": source, "mode": "fixture"})[1]["case"]
        assert case["run_id"] in server.state.run_sessions
        client.request("POST", f"/road/cases/{case['case_id']}/cancel", {})
        runtime.controller.join(case["run_id"], timeout=3)
        client.request("POST", f"/road/sessions/{source}/stop", {})
        assert runtime.retention.collect(1800) == [source]
        assert server.state.run_sessions == {}
    finally:
        server.shutdown()
        server.server_close()
        thread.join(3)
        runtime.close()


def test_bounded_decision_cache_preserves_replay_and_late_correction(tmp_path):
    from test_decision_policy import _assessment, _real_clip, FakeClock
    from visionflow.decision_policy import DecisionStore, DecisionPolicy
    from visionflow.simulated_authority import SimulatedAuthorityInbox
    api = _api()
    try:
        store = DecisionStore(DecisionPolicy(), SimulatedAuthorityInbox(), FakeClock(),
                              max_idempotency_keys=3)
    except TypeError as exc:
        pytest.fail(f"Decision cache is unbounded: {exc}", pytrace=False)
    case = store.open_case("alice", "case", "run", _real_clip(tmp_path), _assessment(), True,
                           source_id="camera-1")
    first_body = (case["version"], case["content_hash"], "confirm", "first")
    first = store.resolve("alice", "case", *first_body)
    second = store.resolve("alice", "case", first["version"], first["content_hash"], "confirm", "second")
    with pytest.raises(api.RetentionCapacityError):
        store.resolve("alice", "case", second["version"], second["content_hash"], "confirm", "overflow")
    assert store.resolve("alice", "case", *first_body) == first
    corrected = store.resolve("alice", "case", second["version"], second["content_hash"], "reject", "correction")
    assert corrected["phase"] == "corrected"
    assert len(store._cases[("alice", "case")].idempotency) == 3


def test_windows_junction_cleanup_refuses_outside_tree(tmp_path):
    import os
    import subprocess
    if os.name != "nt":
        pytest.skip("Windows junction regression")
    root, outside = tmp_path / "root", tmp_path / "outside"
    source = root / "source"
    source.mkdir(parents=True)
    outside.mkdir()
    sentinel = outside / "sentinel"
    sentinel.write_bytes(b"keep")
    junction = source / "escape"
    result = subprocess.run(["cmd", "/c", "mklink", "/J", str(junction), str(outside)],
                            capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    try:
        with pytest.raises(ValueError):
            _api().remove_owned_tree(root, source)
        assert sentinel.read_bytes() == b"keep"
    finally:
        junction.rmdir()  # remove the junction itself, never traverse its target


def test_closed_preview_remains_accounted_after_producer_reservation_settles(tmp_path):
    api = _api()
    runtime = _runtime(tmp_path, api.RetentionPolicy(), Clock())
    try:
        source = runtime.road_sessions.create("alice")["source_id"]
        runtime.road_sessions.stop("alice", source)
        preview = runtime.road_sessions.get("alice", source).final_preview_jpeg()
        assert preview
        entry = runtime.retention._sources[source]
        assert entry.usage.get("volatile", 0) >= len(preview)
        assert entry.floor == 0
    finally:
        runtime.close()


def test_live_reservation_covers_full_raw_buffer_and_rendering_scratch(tmp_path):
    from visionflow.simulation import _RAW_BUFFER_SIZE
    api = _api()
    runtime = _runtime(tmp_path, api.RetentionPolicy(), Clock())
    try:
        source = runtime.road_sessions.create("alice")["source_id"]
        frame = runtime.road_sessions.get("alice", source).latest_frame()
        # The deque, background and next rendered full frame can coexist.
        required = (_RAW_BUFFER_SIZE + 2) * frame.image.nbytes
        entry = runtime.retention._sources[source]
        assert entry.usage["volatile"] >= required
        assert entry.floor >= runtime.road_sessions.recordings.max_bytes + required
    finally:
        runtime.close()


def test_clip_download_is_pinned_until_its_bytes_are_read(tmp_path, monkeypatch):
    from pathlib import Path
    api = _api()
    clock = Clock()
    runtime = _runtime(tmp_path, api.RetentionPolicy(), clock)
    entered, release = threading.Event(), threading.Event()
    result = []
    reader = None
    try:
        service = runtime.road_sessions
        source = service.create("alice")["source_id"]
        stopped = service.stop("alice", source)
        clip_id = stopped["clip"]["clip_id"]
        ref = runtime.road_sessions.recordings.clip("alice", clip_id)
        original = Path.read_bytes

        def paused_read(path):
            if path == ref.path:
                entered.set()
                assert release.wait(5)
            return original(path)

        monkeypatch.setattr(Path, "read_bytes", paused_read)

        def download():
            try:
                result.append(service.clip("alice", clip_id))
            except Exception as exc:
                result.append(exc)

        reader = threading.Thread(target=download)
        reader.start()
        assert entered.wait(5)
        clock.now = 1800
        assert runtime.retention.collect(clock.now) == []
        assert ref.path.exists()
        release.set()
        reader.join(5)
        assert len(result) == 1 and isinstance(result[0], tuple)
        assert result[0][0]["sha256"] == ref.sha256
        assert runtime.retention.collect(clock.now) == [source]
    finally:
        release.set()
        if reader:
            reader.join(5)
        runtime.close()


def test_forty_case_local_sequence_preserves_protected_clip_and_reclaims_sources(tmp_path):
    api = _api()
    clock = Clock()
    runtime = _runtime(tmp_path, api.RetentionPolicy(), clock)
    service, cases = runtime.road_sessions, runtime.road_cases
    try:
        keeper = service.create("bob")["source_id"]
        clip = service.stop("bob", keeper)["clip"]
        clip_bytes = service.clip("bob", clip["clip_id"])[1]
        completed = 0
        with runtime.retention.protect(keeper):
            for batch in range(4):
                source = service.create("alice")["source_id"]
                for _ in range(10):
                    case = cases.start("alice", source, "fixture")
                    cases.cancel("alice", case["case_id"])
                    runtime.controller.join(case["run_id"], timeout=3)
                    completed += 1
                    assert runtime.retention.snapshot()["reserved_bytes"] <= 536870912
                service.stop("alice", source)
                clock.now += 1800
                assert runtime.retention.collect(clock.now) == [source]
                assert cases.list_cases("alice") == []
                assert service.clip("bob", clip["clip_id"])[1] == clip_bytes
                assert runtime.retention.snapshot()["source_count"] == 1
        assert completed == 40
        assert runtime.retention.collect(clock.now) == [keeper]
        assert runtime.retention.snapshot()["reserved_bytes"] == 0
        assert not runtime.controller._runs
        assert not service._bindings_by_source
    finally:
        runtime.close()


def test_real_decision_lifecycle_is_protected_and_late_correction_restarts_retention(tmp_path):
    from test_road_cases import _fixture, _adapter_for, _wait_for_phase
    api = _api()
    rig = _fixture(tmp_path, countdown=60.0)
    service = rig["service"]
    clock = Clock()
    manager = api.RetentionManager(api.RetentionPolicy(), clock=clock,
                                   cleanup=lambda owner, source: service.forget_source(owner, source))
    manager.reserve_source("alice", "road-sim-a", 0)
    service.retention = manager
    try:
        case = service.start("alice", "road-sim-a", "fixture", fixture_profile="pending")
        adapter, findings = _adapter_for(rig, case)
        adapter.ask("Review the candidate", findings)
        rig["controller"].complete(case["run_id"], findings=findings)
        before = _wait_for_phase(service, "alice", case["case_id"], {"human_review"})
        ref = rig["recordings"].ref(before["clip"]["clip_id"])
        original = ref.path.read_bytes()
        clock.now = 5000
        assert manager.collect(clock.now) == []
        assert ref.path.read_bytes() == original
        rig["clock"].advance(60.1)
        expired = _wait_for_phase(service, "alice", case["case_id"], {"no_response"})
        assert manager.collect(6799) == []
        clock.now = 6799
        corrected = service.decide("alice", case["case_id"], expired["version"], before["content_hash"],
                                   "reject", "late-correction")
        assert corrected["phase"] == "corrected"
        assert len(rig["inbox"].list("alice")) == 2
        assert ref.path.read_bytes() == original
        assert manager.collect(8598) == []
        assert manager.collect(8599) == ["road-sim-a"]
        assert not rig["exchanges"]._records and not rig["decisions"]._cases
        assert rig["inbox"].list("alice") == []
    finally:
        service.close()
        manager.close()


def test_partial_file_cleanup_failure_keeps_all_store_references_and_charge(tmp_path, monkeypatch):
    api = _api()
    clock = Clock()
    runtime = _runtime(tmp_path, api.RetentionPolicy(), clock)
    try:
        source = runtime.road_sessions.create("alice")["source_id"]
        case = runtime.road_cases.start("alice", source, "fixture")
        runtime.road_cases.cancel("alice", case["case_id"])
        runtime.controller.join(case["run_id"], timeout=3)
        runtime.road_sessions.stop("alice", source)
        run_id = case["run_id"]
        evidence = runtime.controller.evidence_root / run_id
        evidence.mkdir(parents=True, exist_ok=True)
        runtime.retention.ensure_usage(source, "evidence:" + run_id, 4)
        (evidence / "test.bin").write_bytes(b"keep")
        original = api.remove_owned_tree

        def fail_evidence(root, target):
            if target == evidence:
                raise OSError("simulated evidence permission failure")
            original(root, target)

        monkeypatch.setattr(api, "remove_owned_tree", fail_evidence)
        charged = runtime.retention.snapshot()["reserved_bytes"]
        clock.now = 1800
        assert runtime.retention.collect(clock.now) == []
        assert runtime.retention.snapshot()["reserved_bytes"] == charged
        assert ("alice", source) in runtime.road_sessions.recordings._sessions
        assert run_id in runtime.controller._runs
        assert ("alice", case["case_id"]) in runtime.road_cases._cases
        monkeypatch.setattr(api, "remove_owned_tree", original)
        assert runtime.retention.collect(clock.now) == [source]
    finally:
        runtime.close()


def test_hosted_metadata_rejects_oversize_before_publishing_state(tmp_path):
    from dataclasses import replace
    from test_model_exchange import _request, _require_exchange_components
    from test_decision_policy import _assessment, _real_clip, FakeClock
    from visionflow.decision_policy import DecisionStore, DecisionPolicy
    from visionflow.simulated_authority import SimulatedAuthorityInbox
    video, exchange = _require_exchange_components()
    api = _api()
    try:
        exchanges = exchange.ModelExchangeStore(max_record_bytes=65536)
        decisions = DecisionStore(DecisionPolicy(), SimulatedAuthorityInbox(), FakeClock(),
                                   max_idempotency_keys=64, max_snapshot_bytes=65536)
    except TypeError as exc:
        pytest.fail(f"Hosted metadata has no byte bounds: {exc}", pytrace=False)
    request = replace(_request(video, tmp_path), prompt="x" * 65536)
    with pytest.raises(api.RetentionCapacityError):
        exchanges.record_request("alice", request)
    assert not exchanges._records and not exchanges._owners
    clip = replace(_real_clip(tmp_path), frame_ids=tuple("x" * 100 + str(n) for n in range(400)))
    with pytest.raises(api.RetentionCapacityError):
        decisions.open_case("alice", "large", "run", clip, _assessment(), True, source_id="camera-1")
    assert not decisions._cases


def test_failed_case_binding_does_not_leave_controller_runs_after_source_expiry(tmp_path, monkeypatch):
    api = _api()
    clock = Clock()
    runtime = _runtime(tmp_path, api.RetentionPolicy(), clock)
    try:
        source = runtime.road_sessions.create("alice")["source_id"]

        def fail_binding(*args):
            raise RuntimeError("simulated binding failure")

        monkeypatch.setattr(runtime.road_cases, "_ensure_bound", fail_binding)
        with pytest.raises(Exception, match="run start failed"):
            runtime.road_cases.start("alice", source, "fixture")
        assert runtime.controller._runs
        for run_id in list(runtime.controller._runs):
            runtime.controller.cancel(run_id)
            runtime.controller.join(run_id, timeout=3)
        runtime.road_sessions.stop("alice", source)
        clock.now = 1800
        assert runtime.retention.collect(clock.now) == [source]
        assert not runtime.controller._runs
    finally:
        runtime.close()
