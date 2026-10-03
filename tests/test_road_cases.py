from __future__ import annotations

import importlib
import hashlib
import inspect
import json
import threading
import time
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from visionflow.contracts import Finding, RunResult


def _load_module():
    try:
        return importlib.import_module("visionflow.road_cases")
    except ModuleNotFoundError as exc:
        if exc.name == "visionflow.road_cases":
            pytest.fail(
                "Task 3 RED: RoadCaseService is missing; behavior cannot be integrated yet",
                pytrace=False,
            )
        raise

def _road_case_api():
    return _load_module()


def test_road_case_service_exposes_the_case_and_router_contract():
    module = _road_case_api()
    service_type = getattr(module, "RoadCaseService", None)
    router_type = getattr(module, "RoadModelRouter", None)
    assert inspect.isclass(service_type)
    assert inspect.isclass(router_type)
    assert callable(getattr(service_type, "start", None))
    assert callable(getattr(service_type, "snapshot", None))
    assert callable(getattr(service_type, "exchanges_for_case", None))
    assert callable(getattr(service_type, "decide", None))
    assert callable(getattr(service_type, "cancel", None))
    assert callable(getattr(service_type, "close", None))
    assert callable(getattr(router_type, "bind", None))
    assert callable(getattr(router_type, "create_adapter", None))


def test_road_review_workflow_loads_through_the_real_validator():
    from visionflow.config import load_workflow
    from visionflow.runtime import create_registry

    workflow = load_workflow(
        Path(__file__).parents[1] / "examples" / "workflows" / "road-review.json",
        create_registry(),
    )

    assert workflow.input.kind == "camera"
    assert workflow.limits.max_observation_seconds == 75
    assert workflow.limits.max_frames == 400
    assert workflow.limits.max_llm_calls == 2
    assert any(node.type == "observe" and node.params["duration_seconds"] == 60 for node in workflow.nodes)
    assert any(node.type == "observe" and node.params["duration_seconds"] == 5 for node in workflow.nodes)


class _Clock:
    def __init__(self):
        self.value = 100.0
        self.utc = datetime(2026, 10, 2, 12, 0, tzinfo=timezone.utc)
        self._lock = threading.Lock()

    def monotonic(self):
        with self._lock:
            return self.value

    def utcnow(self):
        with self._lock:
            return self.utc

    def advance(self, seconds):
        with self._lock:
            self.value += seconds
            self.utc += timedelta(seconds=seconds)


class _Session:
    def __init__(self, owner_id, source_id):
        self.owner_id = owner_id
        self.source_id = source_id
        self._closed = False
        self.error_code = None

    @property
    def closed(self):
        return self._closed

    def start(self):
        return None


class _RoadSessions:
    def __init__(self, sources):
        self._sessions = {source: _Session(owner, source) for owner, source in sources}
        self._runs = {}
        self._active = {}
        self._callbacks = {}
        self.bind_calls = []

    def get(self, owner_id, source_id):
        session = self._sessions.get(source_id)
        if session is None or session.owner_id != owner_id:
            raise KeyError(source_id)
        return session

    def owner_source_for_run(self, run_id):
        return self._runs.get(run_id)

    def run_for_source(self, owner_id, source_id):
        return self._active.get((owner_id, source_id))

    def bind_run(self, owner_id, source_id, run_id, *, monitor_clip=True, on_stopped=None):
        previous = self._runs.get(run_id)
        if previous is not None:
            assert previous == (owner_id, source_id)
            return
        self._runs[run_id] = (owner_id, source_id)
        self._active[(owner_id, source_id)] = run_id
        self._callbacks[run_id] = on_stopped
        self.bind_calls.append((owner_id, source_id, run_id, monitor_clip))

    def cancel_run(self, owner_id, run_id, *, reason="case_cancelled"):
        if self._runs.get(run_id, (None, None))[0] != owner_id:
            raise KeyError(run_id)
        callback = self._callbacks.get(run_id)
        if callback is not None:
            callback(run_id, reason)

    def stop(self, run_id, reason="source_reset"):
        callback = self._callbacks.get(run_id)
        if callback is not None:
            callback(run_id, reason)


class _Controller:
    def __init__(self, model_adapter):
        self.model_adapter = model_adapter
        self._lock = threading.RLock()
        self._runs = {}
        self.start_entered = None
        self.release_start = None

    def start(self, config):
        if self.start_entered is not None:
            self.start_entered.set()
            assert self.release_start.wait(3)
        with self._lock:
            run_id = f"run-{len(self._runs) + 1}"
            self._runs[run_id] = {
                "config": deepcopy(config),
                "result": RunResult(run_id=run_id, status="running"),
                "done": threading.Event(),
            }
            return deepcopy(self._runs[run_id]["result"])

    def status(self, run_id):
        with self._lock:
            return deepcopy(self._runs[run_id]["result"])

    def wait(self, run_id, timeout=None):
        with self._lock:
            done = self._runs[run_id]["done"]
        done.wait(timeout)
        return self.status(run_id)

    def cancel(self, run_id):
        with self._lock:
            item = self._runs[run_id]
            item["result"].status = "cancelled"
            item["done"].set()
            return deepcopy(item["result"])

    def complete(self, run_id, *, status="completed", findings=None, trace=None):
        with self._lock:
            item = self._runs[run_id]
            item["result"].status = status
            item["result"].findings = deepcopy(findings or [])
            item["result"].trace = deepcopy(trace or [])
            item["done"].set()

    def config_for(self, run_id):
        with self._lock:
            return deepcopy(self._runs[run_id]["config"])


class _Recordings:
    def __init__(self, root, sources, *, end_seconds=40.0):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._owners = {source: owner for owner, source in sources}
        self._frames = {}
        self._versions = {}
        self._latest = {}
        self._refs = {}
        self.not_ready_calls = 0
        self.calls = []
        for _owner, source in sources:
            self.append_until(source, end_seconds)

    def append_until(self, source_id, end_seconds):
        frames = self._frames.setdefault(source_id, [])
        next_index = len(frames)
        final_index = int(round(end_seconds * 20))
        for index in range(next_index, final_index + 1):
            frames.append(
                {
                    "frame_id": f"{source_id}:{index}",
                    "timestamp_seconds": index / 20.0,
                    "sha256": hashlib.sha256(f"{source_id}:{index}".encode()).hexdigest(),
                    "size_bytes": 1,
                }
            )

    def manifest(self, owner_id, source_id):
        if self._owners.get(source_id) != owner_id:
            raise KeyError(source_id)
        frames = deepcopy(self._frames[source_id])
        return {
            "source_id": source_id,
            "status": "recording",
            "frame_count": len(frames),
            "source_fps": 20.0,
            "start_seconds": frames[0]["timestamp_seconds"] if frames else None,
            "end_seconds": frames[-1]["timestamp_seconds"] if frames else None,
            "frames": frames,
            "clips": [],
        }

    def event_clip(self, owner_id, source_id, event_time, before=5.0, after=10.0, end_override=None):
        self.calls.append((owner_id, source_id, event_time, before, after, end_override))
        if self.not_ready_calls:
            self.not_ready_calls -= 1
            from visionflow.recording import ClipNotReady

            raise ClipNotReady()
        if self._owners.get(source_id) != owner_id:
            raise KeyError(source_id)
        frames = self._frames[source_id]
        requested_end = event_time + after if end_override is None else end_override
        if frames[-1]["timestamp_seconds"] < requested_end:
            from visionflow.recording import ClipNotReady

            raise ClipNotReady()
        key = (source_id, round(event_time, 9), round(before, 9))
        latest = self._latest.get(key)
        if latest is not None and latest.end_seconds >= requested_end:
            return latest
        requested_start = max(0.0, event_time - before)
        first = next(index for index, frame in enumerate(frames) if frame["timestamp_seconds"] >= requested_start)
        last = next(index for index, frame in enumerate(frames[first:], start=first) if frame["timestamp_seconds"] >= requested_end)
        selected = frames[first : last + 1]
        version = self._versions.get(key, 0) + 1
        clip_id = f"clip-{source_id}-{version}"
        payload = f"immutable clip {clip_id} {selected[0]['frame_id']} {selected[-1]['frame_id']}".encode()
        path = self.root / f"{clip_id}.mp4"
        path.write_bytes(payload)
        from visionflow.recording import ClipRef

        ref = ClipRef(
            clip_id=clip_id,
            source_id=source_id,
            version=version,
            path=path,
            sha256=hashlib.sha256(payload).hexdigest(),
            start_seconds=selected[0]["timestamp_seconds"],
            end_seconds=selected[-1]["timestamp_seconds"],
            frame_ids=tuple(frame["frame_id"] for frame in selected),
            source_fps=20.0,
            available_preroll=max(0.0, event_time - selected[0]["timestamp_seconds"]),
        )
        self._versions[key] = version
        self._latest[key] = ref
        self._refs[clip_id] = ref
        return ref

    def ref(self, clip_id):
        return self._refs[clip_id]


class _BlockingTransport:
    def __init__(self):
        self.entered = threading.Event()

    def generate(self, request, on_delta, cancelled):
        from visionflow.video_model import VideoModelError

        self.entered.set()
        while not cancelled():
            time.sleep(0.01)
        raise VideoModelError("cancelled")


def _fixture(tmp_path, sources=(("alice", "road-sim-a"),), *, countdown=2.0, native_transport=None):
    module = _road_case_api()
    from visionflow.decision_policy import DecisionPolicy, DecisionStore
    from visionflow.model_exchange import ModelExchangeStore
    from visionflow.simulated_authority import SimulatedAuthorityInbox

    clock = _Clock()
    inbox = SimulatedAuthorityInbox()
    decisions = DecisionStore(
        DecisionPolicy(countdown_seconds=countdown),
        inbox,
        clock,
    )
    sessions = _RoadSessions(sources)
    recordings = _Recordings(tmp_path / "clips", sources)
    exchanges = ModelExchangeStore()
    router = module.RoadModelRouter(native_transport=native_transport)
    controller = _Controller(router)
    service = module.RoadCaseService(sessions, controller, recordings, exchanges, decisions)
    return {
        "service": service,
        "router": router,
        "sessions": sessions,
        "controller": controller,
        "recordings": recordings,
        "exchanges": exchanges,
        "decisions": decisions,
        "inbox": inbox,
        "clock": clock,
    }


def _candidate(run_id, source_id):
    frame_id = f"{source_id}:110"
    return Finding(
        tool="direction",
        frame_ids=[frame_id],
        region=None,
        metrics={
            "opposition_count": 1,
            "warmup": False,
            "coordinate_system": "crop_pixels",
            "tracks": [
                {
                    "track_id": "direction-track-1",
                    "lane_id": "northbound",
                    "region": [250, 220, 32, 28],
                    "dx_px": 0.0,
                    "dy_px": 5.0,
                    "opposition": True,
                    "frame_ids": [
                        f"{source_id}:108",
                        f"{source_id}:109",
                        frame_id,
                    ],
                }
            ],
        },
        evidence_ref=f"{run_id}/frame-000110.png",
    )


def _adapter_for(fixture, case):
    from visionflow.model import ModelRunContext

    run_id = case["run_id"]
    result = fixture["controller"].status(run_id)
    finding = _candidate(run_id, case["source_id"])
    fixture["controller"].complete(run_id, status="running", findings=[finding])
    context = ModelRunContext(
        run_id,
        case["source_id"],
        lambda: fixture["controller"].status(run_id).status == "cancelled",
        lambda: fixture["controller"].status(run_id),
    )
    config = fixture["controller"].config_for(run_id)
    return fixture["router"].create_adapter(config, context), [finding]


def _wait_for_phase(service, owner_id, case_id, phases, timeout=3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        snapshot = service.snapshot(owner_id, case_id)
        if snapshot["phase"] in phases:
            return snapshot
        time.sleep(0.02)
    raise AssertionError(f"case did not reach one of {phases!r}")


def test_fixture_profile_followup_keeps_clip_versions_and_uses_actual_source_time(tmp_path):
    fixture = _fixture(tmp_path)
    service = fixture["service"]
    case = service.start("alice", "road-sim-a", "fixture", fixture_profile="followup")
    adapter, findings = _adapter_for(fixture, case)

    first_decision = adapter.ask("Review the direction candidate", findings)
    assert first_decision.action == "observe"
    assert first_decision.payload == {
        "source_id": "road-sim-a",
        "region": None,
        "duration_seconds": 5.0,
        "sample_fps": 5.0,
        "question": "Observe the same road region for five more seconds.",
    }
    first_exchange = service.exchanges_for_case("alice", case["case_id"])[0]
    first_clip = first_exchange["clip"]
    first_ref = fixture["recordings"].ref(first_clip["clip_id"])
    first_bytes = first_ref.path.read_bytes()

    # The source continued while the provider responded and during the full five-second observation.
    fixture["recordings"].append_until("road-sim-a", 22.0)
    second_decision = adapter.ask("Review the updated clip", findings)
    assert second_decision.action == "report"
    exchanges = service.exchanges_for_case("alice", case["case_id"])
    assert len(exchanges) == 2
    first_clip, final_clip = exchanges[0]["clip"], exchanges[1]["clip"]
    assert final_clip["version"] > first_clip["version"]
    assert final_clip["end_seconds"] >= first_clip["end_seconds"] + 5.0
    assert fixture["recordings"].ref(first_clip["clip_id"]).path.read_bytes() == first_bytes
    assert all(exchange["run_id"] == case["run_id"] for exchange in exchanges)
    assert all(exchange["case_id"] == case["case_id"] for exchange in exchanges)
    assert all(exchange["clip"]["source_id"] == "road-sim-a" for exchange in exchanges)
    assert "northbound" in exchanges[0]["prompt"]
    assert "manifest source time" in exchanges[0]["prompt"]
    assert "Available preroll" in exchanges[0]["prompt"]

    fixture["controller"].complete(case["run_id"], findings=findings)
    current = _wait_for_phase(service, "alice", case["case_id"], {"human_review"})
    assert current["clip"]["sha256"] == final_clip["sha256"]
    assert current["controller_status"] == "completed"
    assert current["phase"] == "human_review"
    assert current["version"] == current["decision"]["version"]
    assert current["content_hash"] == current["decision"]["content_hash"]
    assert current["assessment"] == current["decision"]["assessment"]
    assert current["deadline_at"] == current["decision"]["deadline_at"]
    assert current["remaining_seconds"] == current["decision"]["remaining_seconds"]
    assert current["journal"] == current["decision"]["journal"]
    assert current["first_receipt"] == current["decision"]["first_receipt"]
    assert current["correction_receipt"] == current["decision"]["correction_receipt"]
    service.close()


@pytest.mark.parametrize(
    ("stop_reason", "expected_phase"),
    [("duration", "normal"), ("source_end", "source_ended")],
)
def test_no_direction_candidate_never_opens_an_alarm_or_decision_deadline(
    tmp_path, stop_reason, expected_phase
):
    fixture = _fixture(tmp_path)
    service = fixture["service"]
    case = service.start("alice", "road-sim-a", "fixture")
    fixture["controller"].complete(
        case["run_id"],
        findings=[],
        trace=[
            {
                "type": "monitor_stopped",
                "reason": stop_reason,
                **({"duration_seconds": 60.0} if stop_reason == "duration" else {}),
            }
        ],
    )

    current = _wait_for_phase(service, "alice", case["case_id"], {expected_phase})
    assert current["phase"] == expected_phase
    assert current["controller_status"] == "completed"
    assert current["exchange_ids"] == []
    assert current.get("decision") is None
    assert fixture["inbox"].list("alice") == []
    service.close()


@pytest.mark.parametrize("profile", ["bird", "leaf"])
def test_fixture_classification_is_selected_explicitly_and_can_disagree_with_pixels(
    tmp_path, profile
):
    fixture = _fixture(tmp_path)
    service = fixture["service"]
    case = service.start("alice", "road-sim-a", "fixture", fixture_profile=profile)
    adapter, findings = _adapter_for(fixture, case)

    decision = adapter.ask("Review the visible direction candidate", findings)
    assert decision.action == "report"
    exchange = service.exchanges_for_case("alice", case["case_id"])[0]
    assert exchange["assessment"]["object_class"] == profile
    assert exchange["assessment"]["hazard"] is False
    assert f"fixture profile '{profile}' explicitly selected" in exchange["assessment"]["summary"]
    assert "scenario metadata" in exchange["assessment"]["summary"]

    fixture["controller"].complete(case["run_id"], findings=findings)
    current = _wait_for_phase(service, "alice", case["case_id"], {"dismissed"})
    assert current["phase"] == "dismissed"
    service.close()


def test_native_mode_is_rejected_before_case_creation_when_transport_is_unavailable(tmp_path):
    fixture = _fixture(tmp_path)
    service = fixture["service"]

    with pytest.raises(_road_case_api().RoadCaseError) as disabled:
        service.start("alice", "road-sim-a", "gemini_video")
    assert disabled.value.code == "native_disabled"
    with pytest.raises(_road_case_api().RoadCaseError) as profile:
        service.start(
            "alice",
            "road-sim-a",
            "gemini_video",
            fixture_profile="high",
        )
    assert profile.value.code == "fixture_profile_not_allowed"
    assert fixture["controller"]._runs == {}
    assert service.capabilities["modes"] == ["fixture"]
    service.close()


def test_one_active_case_per_source_and_owner_scoped_case_reads(tmp_path):
    fixture = _fixture(tmp_path)
    service = fixture["service"]
    case = service.start("alice", "road-sim-a", "fixture")

    with pytest.raises(_road_case_api().RoadCaseError) as conflict:
        service.start("alice", "road-sim-a", "fixture", fixture_profile="bird")
    assert conflict.value.code == "source_in_use"
    with pytest.raises(_road_case_api().RoadCaseError) as hidden:
        service.snapshot("bob", case["case_id"])
    assert hidden.value.code == "case_not_found"
    with pytest.raises(_road_case_api().RoadCaseError) as hidden_exchanges:
        service.exchanges_for_case("bob", case["case_id"])
    assert hidden_exchanges.value.code == "case_not_found"
    service.cancel("alice", case["case_id"])
    service.close()


def test_case_decision_delegates_to_owner_scoped_task_two_store(tmp_path):
    fixture = _fixture(tmp_path)
    service = fixture["service"]
    case = service.start("alice", "road-sim-a", "fixture", fixture_profile="pending")
    adapter, findings = _adapter_for(fixture, case)
    adapter.ask("Review the candidate", findings)
    fixture["controller"].complete(case["run_id"], findings=findings)
    current = _wait_for_phase(service, "alice", case["case_id"], {"human_review"})

    resolved = service.decide(
        "alice",
        case["case_id"],
        current["version"],
        current["content_hash"],
        "reject",
        "road-case-reject-1",
    )

    assert resolved["phase"] == "rejected"
    assert service.snapshot("alice", case["case_id"])["phase"] == "rejected"
    service.close()


def test_service_reserves_source_before_controller_start_and_binds_without_clip_watcher(tmp_path):
    fixture = _fixture(tmp_path)
    controller = fixture["controller"]
    controller.start_entered = threading.Event()
    controller.release_start = threading.Event()
    captured = {}

    def start_case():
        captured["case"] = fixture["service"].start("alice", "road-sim-a", "fixture")

    worker = threading.Thread(target=start_case)
    worker.start()
    assert controller.start_entered.wait(1)
    with pytest.raises(_road_case_api().RoadCaseError) as conflict:
        fixture["service"].start("alice", "road-sim-a", "fixture")
    assert conflict.value.code == "source_in_use"
    controller.release_start.set()
    worker.join(2)
    assert not worker.is_alive()
    assert fixture["sessions"].bind_calls[0][3] is False
    assert fixture["sessions"].run_for_source("alice", "road-sim-a") == captured["case"]["run_id"]
    fixture["service"].cancel("alice", captured["case"]["case_id"])
    fixture["service"].close()


def test_generic_run_on_reserved_source_cannot_hijack_case_factory_context(tmp_path):
    from dataclasses import replace

    fixture = _fixture(tmp_path)
    service = fixture["service"]
    case = service.start("alice", "road-sim-a", "fixture")
    case_config = fixture["controller"].config_for(case["run_id"])
    generic_config = replace(case_config, name="ordinary-camera-check")
    from visionflow.model import ModelRunContext

    generic_context = ModelRunContext(
        "generic-run",
        "road-sim-a",
        lambda: False,
        lambda: {"status": "running"},
    )
    before = len(fixture["sessions"].bind_calls)

    adapter = fixture["router"].create_adapter(generic_config, generic_context)

    assert adapter.mode == "fixture"
    assert len(fixture["sessions"].bind_calls) == before
    assert fixture["sessions"].run_for_source("alice", "road-sim-a") == case["run_id"]
    service.cancel("alice", case["case_id"])
    service.close()


def test_clip_not_ready_is_retried_but_source_end_becomes_unclear_human_case(tmp_path):
    fixture = _fixture(tmp_path)
    service = fixture["service"]
    case = service.start("alice", "road-sim-a", "fixture")
    adapter, findings = _adapter_for(fixture, case)
    fixture["recordings"].not_ready_calls = 1
    decision = adapter.ask("Review the candidate", findings)
    assert decision.action == "report"
    assert len(fixture["recordings"].calls) >= 2

    fixture2 = _fixture(tmp_path / "ended")
    service2 = fixture2["service"]
    case2 = service2.start("alice", "road-sim-a", "fixture")
    adapter2, findings2 = _adapter_for(fixture2, case2)
    fixture2["recordings"].not_ready_calls = 1000
    fixture2["sessions"].get("alice", "road-sim-a")._closed = True
    with pytest.raises(Exception) as ended:
        adapter2.ask("Review the candidate", findings2)
    assert getattr(ended.value, "code", None) == "source_ended"
    fixture2["controller"].complete(case2["run_id"], status="failed", findings=findings2)
    current = _wait_for_phase(service2, "alice", case2["case_id"], {"human_review"})
    assert current["decision"]["model_error"] == "source_ended"
    service.close()
    service2.close()


def test_deadline_ticker_reports_without_a_snapshot_get(tmp_path):
    fixture = _fixture(tmp_path, countdown=0.2)
    service = fixture["service"]
    case = service.start("alice", "road-sim-a", "fixture", fixture_profile="pending")
    adapter, findings = _adapter_for(fixture, case)
    adapter.ask("Review the candidate", findings)
    fixture["controller"].complete(case["run_id"], findings=findings)
    _wait_for_phase(service, "alice", case["case_id"], {"human_review"})

    # No service.snapshot() call is made after the human phase begins.
    fixture["clock"].advance(0.21)
    deadline = time.monotonic() + 1.0
    while not fixture["inbox"].list("alice") and time.monotonic() < deadline:
        time.sleep(0.02)
    assert fixture["inbox"].list("alice")[0]["reason"] == "no_response"
    service.close()


def test_source_reset_cancels_human_countdown_but_preserves_case_history(tmp_path):
    fixture = _fixture(tmp_path)
    service = fixture["service"]
    case = service.start("alice", "road-sim-a", "fixture", fixture_profile="pending")
    adapter, findings = _adapter_for(fixture, case)
    adapter.ask("Review the candidate", findings)
    fixture["controller"].complete(case["run_id"], findings=findings)
    current = _wait_for_phase(service, "alice", case["case_id"], {"human_review"})

    fixture["sessions"].stop(case["run_id"], "source_reset")
    cancelled = _wait_for_phase(service, "alice", case["case_id"], {"cancelled"})
    assert cancelled["decision"]["phase"] == "cancelled"
    assert cancelled["journal"]
    assert cancelled["exchange_ids"] == current["exchange_ids"]
    assert len(service.exchanges_for_case("alice", case["case_id"])) == 1
    service.close()


def test_cancelling_blocked_video_transport_unblocks_run_without_external_calls(tmp_path):
    transport = _BlockingTransport()
    fixture = _fixture(tmp_path, native_transport=transport)
    service = fixture["service"]
    case = service.start("alice", "road-sim-a", "gemini_video")
    adapter, findings = _adapter_for(fixture, case)
    raised = []

    def ask():
        try:
            adapter.ask("Review the candidate", findings)
        except Exception as exc:
            raised.append(exc)

    worker = threading.Thread(target=ask)
    worker.start()
    assert transport.entered.wait(1)
    from visionflow.gemini_video import GEMINI_MODEL_ID

    exchange = service.exchanges_for_case("alice", case["case_id"])[0]
    assert exchange["model_id"] == GEMINI_MODEL_ID
    result = service.cancel("alice", case["case_id"])
    worker.join(1)

    assert not worker.is_alive()
    assert raised and getattr(raised[0], "code", None) == "cancelled"
    assert result["phase"] == "cancelled"
    assert fixture["sessions"].bind_calls[0][3] is False
    service.close()


@pytest.mark.parametrize(
    ("requested_source", "requested_region"),
    [
        ("another-source", None),
        ("road-sim-a", [0.0, 90.0, 960.0, 450.0]),
    ],
)
def test_native_followup_rejects_changed_source_or_full_frame_roi(
    tmp_path, requested_source, requested_region
):
    from visionflow.video_model import VideoModelError, VideoTransportResult

    class _WrongFollowupTransport:
        def generate(self, request, on_delta, cancelled):
            raw = json.dumps(
                {
                    "object_class": "vehicle",
                    "hazard": True,
                    "confidence": 0.8,
                    "summary": "A follow-up request with altered authority.",
                    "evidence_frame_ids": [request.clip.frame_ids[-1]],
                    "decision": {
                        "action": "observe",
                        "payload": {
                            "source_id": requested_source,
                            "region": requested_region,
                            "duration_seconds": 5.0,
                            "sample_fps": 5.0,
                            "question": "Continue observation.",
                        },
                    },
                },
                separators=(",", ":"),
            )
            on_delta(raw)
            return VideoTransportResult(raw_text=raw)

    fixture = _fixture(tmp_path, native_transport=_WrongFollowupTransport())
    service = fixture["service"]
    case = service.start("alice", "road-sim-a", "gemini_video")
    adapter, findings = _adapter_for(fixture, case)

    with pytest.raises(VideoModelError) as rejected:
        adapter.ask("Review the candidate", findings)

    assert rejected.value.code == "observation_not_authorized"
    exchange = service.exchanges_for_case("alice", case["case_id"])[0]
    assert exchange["status"] == "failed"
    assert exchange["error_code"] == "observation_not_authorized"
    service.cancel("alice", case["case_id"])
    service.close()


def test_case_wallclock_watchdog_expires_visibly_without_using_observation_budget(
    tmp_path, monkeypatch
):
    module = _road_case_api()
    monkeypatch.setattr(module, "_CASE_WALL_DEADLINE_SECONDS", 0.03, raising=False)
    fixture = _fixture(tmp_path)
    service = fixture["service"]
    case = service.start("alice", "road-sim-a", "fixture")

    current = _wait_for_phase(service, "alice", case["case_id"], {"cancelled"})

    assert current["stopped_reason"] == "case_timeout"
    assert current["controller_status"] == "cancelled"
    service.close()


def test_source_stop_cannot_interleave_with_final_decision_open(tmp_path, monkeypatch):
    fixture = _fixture(tmp_path)
    service = fixture["service"]
    case = service.start("alice", "road-sim-a", "fixture", fixture_profile="high")
    adapter, findings = _adapter_for(fixture, case)
    assert adapter.ask("Review the candidate", findings).action == "report"
    run_result = fixture["controller"].status(case["run_id"])
    run_result.status = "completed"
    run_result.findings = findings

    entered_open = threading.Event()
    release_open = threading.Event()
    stop_finished = threading.Event()
    original_open = fixture["decisions"].open_case

    def blocked_open(*args, **kwargs):
        entered_open.set()
        assert release_open.wait(2)
        return original_open(*args, **kwargs)

    monkeypatch.setattr(fixture["decisions"], "open_case", blocked_open)
    finalizer = threading.Thread(target=service._finalize, args=(
        service._owned_case("alice", case["case_id"]),
        run_result,
    ))
    finalizer.start()
    assert entered_open.wait(1)

    def stop_source():
        fixture["sessions"].stop(case["run_id"], "source_reset")
        stop_finished.set()

    stopper = threading.Thread(target=stop_source)
    stopper.start()
    # The stop callback must serialize behind the atomic open-or-cancel decision.
    stop_was_serialized = not stop_finished.wait(0.05)
    release_open.set()
    finalizer.join(2)
    stopper.join(2)

    assert not finalizer.is_alive()
    assert not stopper.is_alive()
    assert stop_was_serialized
    assert stop_finished.is_set()
    assert service.snapshot("alice", case["case_id"])["phase"] == "reported"
    service.close()


def test_real_simulation_controller_recording_and_ffmpeg_form_one_source_pipeline(tmp_path):
    import shutil

    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        pytest.skip("FFmpeg is required for the real recording integration scenario")

    from visionflow.controller import Controller
    from visionflow.recording import RecordingStore
    from visionflow.runtime import create_default_fixture_adapter, create_registry
    from visionflow.simulation import SimulationSession
    from visionflow.contracts import InputConfig, Limits, Node, OutputConfig, WorkflowConfig
    from road_helpers import default_lane_params

    owner_id = "integration-owner"
    source_ref = {}
    enough_frames = threading.Event()
    recordings = RecordingStore(tmp_path / "recordings", Path(ffmpeg))

    def on_frame(frame):
        source_id = source_ref["source_id"]
        recordings.append(source_id, frame)
        if recordings.manifest(owner_id, source_id)["frame_count"] >= 4:
            enough_frames.set()

    session = SimulationSession(owner_id, seed=42, on_frame=on_frame)
    source_ref["source_id"] = session.source_id
    recordings.open(owner_id, session.source_id)
    session.start()
    config = WorkflowConfig(
        schema_version=1,
        name="road-pipeline-integration",
        input=InputConfig("camera", session.source_id),
        limits=Limits(max_observation_seconds=2, max_frames=8, max_llm_calls=1),
        nodes=[
            Node(
                "analyze",
                "analyze",
                {"tool": "direction", "tool_params": default_lane_params()},
                ["report"],
            ),
            Node("report", "report", {"format": "markdown"}, []),
        ],
        outputs=[OutputConfig("display")],
    )
    controller = Controller(
        create_registry(),
        model_adapter=lambda workflow: create_default_fixture_adapter(workflow),
        source_factory=lambda input_config, max_frames: session.open_reader(),
        evidence_root=tmp_path / "evidence",
    )
    queued = controller.start(config)
    result = controller.wait(queued.run_id, timeout=3)
    assert result.status == "completed"
    assert enough_frames.wait(2)
    session.close()
    clip = recordings.finish(owner_id, session.source_id)
    assert clip.source_id == session.source_id
    assert len(clip.frame_ids) >= 4
    assert recordings.clip(owner_id, clip.clip_id).sha256 == clip.sha256


def test_real_road_case_reaches_human_review_from_recorded_wrong_way_pixels(tmp_path):
    import shutil
    from datetime import datetime, timezone

    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        pytest.skip("FFmpeg is required for the real road-case integration scenario")

    module = _road_case_api()
    from visionflow.controller import Controller
    from visionflow.decision_policy import DecisionPolicy, DecisionStore
    from visionflow.model_exchange import ModelExchangeStore
    from visionflow.recording import RecordingStore
    from visionflow.road_api import RoadSessionService
    from visionflow.runtime import create_registry
    from visionflow.simulated_authority import SimulatedAuthorityInbox
    from visionflow.simulation import SimulationStore

    class _SystemClock:
        @staticmethod
        def monotonic():
            return time.monotonic()

        @staticmethod
        def utcnow():
            return datetime.now(timezone.utc)

    owner_id = "real-case-owner"
    recordings = RecordingStore(tmp_path / "recordings", Path(ffmpeg))
    router = module.RoadModelRouter()
    road_sessions_ref = {}

    def source_factory(input_config, _max_frames):
        return road_sessions_ref["service"].open_reader(input_config.source)

    controller = Controller(
        create_registry(),
        model_adapter=router,
        source_factory=source_factory,
        evidence_root=tmp_path / "evidence",
    )
    road_sessions = RoadSessionService(
        SimulationStore(), recordings, controller, watcher_poll_seconds=0.05
    )
    road_sessions_ref["service"] = road_sessions
    source_id = road_sessions.create(owner_id, seed=42)["source_id"]
    decisions = DecisionStore(
        DecisionPolicy(countdown_seconds=30.0),
        SimulatedAuthorityInbox(),
        _SystemClock(),
    )
    service = module.RoadCaseService(
        road_sessions,
        controller,
        recordings,
        ModelExchangeStore(),
        decisions,
    )
    try:
        time.sleep(1.0)  # Let the initial road scene warm up before injecting pixels.
        case = service.start(owner_id, source_id, "fixture", fixture_profile="pending")
        warmup_deadline = time.monotonic() + 10.0
        observed_frames = 0
        current = None
        while time.monotonic() < warmup_deadline:
            current = service.snapshot(owner_id, case["case_id"])
            observed_frames = sum(
                1
                for item in current["perception_trace"]
                if isinstance(item, dict)
                and item.get("type") == "frame_observed"
                and item.get("node_id") == "monitor"
            )
            if observed_frames >= 3:
                break
            time.sleep(0.1)
        assert observed_frames >= 3, "the controller must analyze three real warmup frames"
        road_sessions.inject(owner_id, source_id, "wrong_way")

        deadline = time.monotonic() + 75.0
        while time.monotonic() < deadline:
            current = service.snapshot(owner_id, case["case_id"])
            if current["phase"] in {"human_review", "reported", "dismissed", "no_response"}:
                break
            time.sleep(0.1)

        assert current is not None
        assert current["phase"] == "human_review"
        assert current["controller_status"] == "completed"
        assert current["perception_trace"]
        exchange_rows = service.exchanges_for_case(owner_id, case["case_id"])
        assert len(exchange_rows) == 1
        assert exchange_rows[0]["clip"]["source_id"] == source_id
        assert exchange_rows[0]["clip"]["frame_ids"]
        assert exchange_rows[0]["assessment"]["object_class"] == "vehicle"
        assert exchange_rows[0]["assessment"]["confidence"] < 0.9
        next_case = service.start(owner_id, source_id, "fixture", fixture_profile="pending")
        assert next_case["case_id"] != case["case_id"]
        assert service.snapshot(owner_id, case["case_id"])["phase"] == "human_review"
        service.cancel(owner_id, next_case["case_id"])
        assert service.snapshot(owner_id, case["case_id"])["phase"] == "human_review"
    finally:
        service.close()
        road_sessions.close()
