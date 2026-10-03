from __future__ import annotations

import importlib
import threading
import time
from types import SimpleNamespace

import pytest


def _scenario_service_type():
    try:
        module = importlib.import_module("visionflow.road_scenarios")
    except ModuleNotFoundError as exc:
        if exc.name == "visionflow.road_scenarios":
            pytest.fail(
                "RED: scenario jobs are missing; enqueue/jobs cannot run a real case",
                pytrace=False,
            )
        raise
    service_type = getattr(module, "RoadScenarioService", None)
    assert isinstance(service_type, type), (
        "RED: RoadScenarioService must own source-scoped asynchronous scenario jobs"
    )
    return service_type


class _Controller:
    def __init__(self):
        self.runs: dict[str, dict] = {}
        self.status_calls: dict[str, int] = {}

    def add_run(self, run_id: str) -> None:
        self.runs[run_id] = {"status": "running", "trace": []}

    def observe(self, run_id: str, count: int) -> None:
        self.runs[run_id]["status"] = "observing"
        trace = self.runs[run_id]["trace"]
        while sum(
            item.get("type") == "frame_observed"
            and item.get("node_id") == "monitor"
            for item in trace
        ) < count:
            trace.append(
                {
                    "type": "frame_observed",
                    "node_id": "monitor",
                    "state": "observing",
                    "evidence_id": f"{run_id}-frame-{len(trace) + 1}",
                }
            )

    def complete(self, run_id: str) -> None:
        self.runs[run_id]["status"] = "completed"

    def stop_monitor(self, run_id: str) -> None:
        self.runs[run_id]["trace"].append(
            {"type": "monitor_stopped", "node_id": "monitor"}
        )

    def status(self, run_id: str):
        run = self.runs[run_id]
        self.status_calls[run_id] = self.status_calls.get(run_id, 0) + 1
        return SimpleNamespace(
            run_id=run_id,
            status=run["status"],
            trace=list(run["trace"]),
        )


class _Cases:
    def __init__(self, controller: _Controller):
        self.controller = controller
        self.starts: list[dict] = []
        self.cancelled: list[str] = []
        self._case_counter = 0

    def start(self, owner_id, source_id, mode, *, fixture_profile=None):
        self._case_counter += 1
        case_id = f"case-{self._case_counter}"
        run_id = f"run-{self._case_counter}"
        self.controller.add_run(run_id)
        self.starts.append(
            {
                "owner_id": owner_id,
                "source_id": source_id,
                "mode": mode,
                "fixture_profile": fixture_profile,
                "case_id": case_id,
                "run_id": run_id,
            }
        )
        return {"case_id": case_id, "run_id": run_id}

    def snapshot(self, owner_id, case_id):
        started = next(item for item in self.starts if item["case_id"] == case_id)
        status = self.controller.status(started["run_id"]).status
        return {
            **started,
            "phase": "human_review" if status == "completed" else "running",
            "controller_status": status,
        }

    def cancel(self, owner_id, case_id):
        self.cancelled.append(case_id)
        started = next(item for item in self.starts if item["case_id"] == case_id)
        if self.controller.runs[started["run_id"]]["status"] not in {
            "completed", "failed", "cancelled"
        }:
            self.controller.runs[started["run_id"]]["status"] = "cancelled"


class _Sessions:
    def __init__(self, *, ended_on_get_call=None, end_on_inject=False):
        self.injected: list[tuple[str, str, str]] = []
        self.source_age_seconds = 8.0
        self.closed = False
        self.ended_on_get_call = ended_on_get_call
        self.get_calls = 0
        self.end_on_inject = end_on_inject
        self.block_age_probe = False
        self.age_probe_waiting = threading.Event()
        self.allow_age_probe = threading.Event()

    def get(self, owner_id, source_id):
        self.get_calls += 1
        ended = self.closed or (
            self.ended_on_get_call is not None
            and self.get_calls >= self.ended_on_get_call
        )
        return SimpleNamespace(closed=ended, error_code=None)

    def recording(self, owner_id, source_id):
        if self.block_age_probe:
            self.age_probe_waiting.set()
            assert self.allow_age_probe.wait(2.0), "test did not release source-age probe"
        return {
            "source_id": source_id,
            "closed": False,
            "error_code": None,
            "end_seconds": self.source_age_seconds,
            "source_fps": 20.0,
        }

    def inject(self, owner_id, source_id, event):
        self.injected.append((owner_id, source_id, event))
        if self.end_on_inject:
            self.closed = True
        return {"source_id": source_id, "accepted": True}


def _wait_for(predicate, *, timeout=2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = predicate()
        if result:
            return result
        time.sleep(0.01)
    pytest.fail("scenario worker did not reach the expected observable state")


def test_job_waits_for_three_real_monitor_frames_before_injection():
    service_type = _scenario_service_type()
    controller = _Controller()
    cases = _Cases(controller)
    sessions = _Sessions()
    sessions.source_age_seconds = 4.0
    service = service_type(
        cases,
        sessions,
        controller,
        poll_interval_seconds=0.01,
        warmup_timeout_seconds=1.0,
    )
    try:
        queued = service.enqueue(
            "owner-a", "road-sim-a", "wrong_way", "fixture", "pending"
        )
        started = _wait_for(lambda: cases.starts)
        run_id = started[0]["run_id"]

        assert sessions.injected == []
        controller.observe(run_id, 2)
        time.sleep(0.05)
        assert sessions.injected == [], (
            "two observed controller frames cannot authorize event injection"
        )

        controller.observe(run_id, 3)
        time.sleep(0.05)
        assert sessions.injected == [], (
            "three monitor frames still need five seconds of source pre-roll"
        )
        sessions.source_age_seconds = 5.0
        _wait_for(lambda: len(sessions.injected) == 1)
        assert sessions.injected == [("owner-a", "road-sim-a", "wrong_way")]

        controller.complete(run_id)
        completed = _wait_for(
            lambda: next(
                (job for job in service.jobs("owner-a", "road-sim-a")
                 if job["job_id"] == queued["job_id"] and job["state"] == "completed"),
                None,
            )
        )
        assert completed["case_id"] == started[0]["case_id"]
        assert completed["run_id"] == run_id
    finally:
        service.close()


def test_jobs_serialize_controller_analysis_but_not_open_human_review():
    service_type = _scenario_service_type()
    controller = _Controller()
    cases = _Cases(controller)
    sessions = _Sessions()
    service = service_type(
        cases,
        sessions,
        controller,
        poll_interval_seconds=0.01,
        warmup_timeout_seconds=1.0,
    )
    try:
        first = service.enqueue("owner-a", "road-sim-a", "wrong_way", "fixture", "pending")
        second = service.enqueue("owner-a", "road-sim-a", "bird", "fixture", "pending")
        _wait_for(lambda: len(cases.starts) == 1)
        first_run = cases.starts[0]["run_id"]
        controller.observe(first_run, 3)
        _wait_for(lambda: len(sessions.injected) == 1)
        time.sleep(0.05)
        assert len(cases.starts) == 1, "the second case cannot overlap active analysis"

        controller.complete(first_run)
        _wait_for(lambda: len(cases.starts) == 2)
        assert cases.starts[0]["case_id"] != cases.starts[1]["case_id"]
        assert cases.starts[0]["run_id"] != cases.starts[1]["run_id"]
        completed_first = _wait_for(
            lambda: next(
                (job for job in service.jobs("owner-a", "road-sim-a")
                 if job["job_id"] == first["job_id"] and job["state"] == "completed"),
                None,
            )
        )
        assert cases.snapshot("owner-a", completed_first["case_id"])["phase"] == "human_review"
        assert next(job for job in service.jobs("owner-a", "road-sim-a")
                    if job["job_id"] == second["job_id"])["state"] in {"starting", "running"}
    finally:
        service.close()


def test_event_label_does_not_choose_fixture_profile():
    service_type = _scenario_service_type()
    controller = _Controller()
    cases = _Cases(controller)
    sessions = _Sessions()
    service = service_type(
        cases,
        sessions,
        controller,
        poll_interval_seconds=0.01,
        warmup_timeout_seconds=1.0,
    )
    try:
        job = service.enqueue("owner-a", "road-sim-a", "bird", "fixture", "pending")
        started = _wait_for(lambda: cases.starts)
        assert job["event"] == "bird"
        assert job["fixture_profile"] == "pending"
        assert started[0]["fixture_profile"] == "pending"
        assert sessions.injected == []
    finally:
        service.close()


def test_source_ending_after_dequeue_cancels_starting_job_without_case():
    service_type = _scenario_service_type()
    controller = _Controller()
    cases = _Cases(controller)
    # enqueue() observes the open source; the worker sees it ended immediately
    # after changing the dequeued job to "starting".
    sessions = _Sessions(ended_on_get_call=2)
    service = service_type(cases, sessions, controller, poll_interval_seconds=0.01)
    try:
        queued = service.enqueue("owner-a", "road-sim-a", "wrong_way", "fixture", "pending")
        terminal = _wait_for(
            lambda: next(
                (job for job in service.jobs("owner-a", "road-sim-a")
                 if job["job_id"] == queued["job_id"] and job["state"] == "cancelled"),
                None,
            )
        )
        assert terminal["error"] == "source_ended"
        assert cases.starts == []
    finally:
        service.close()


def test_source_ending_after_injection_cancels_active_case():
    service_type = _scenario_service_type()
    controller = _Controller()
    cases = _Cases(controller)
    sessions = _Sessions(end_on_inject=True)
    service = service_type(
        cases, sessions, controller, poll_interval_seconds=0.01,
        warmup_timeout_seconds=1.0,
    )
    try:
        queued = service.enqueue("owner-a", "road-sim-a", "wrong_way", "fixture", "pending")
        started = _wait_for(lambda: cases.starts)
        controller.observe(started[0]["run_id"], 3)
        terminal = _wait_for(
            lambda: next(
                (job for job in service.jobs("owner-a", "road-sim-a")
                 if job["job_id"] == queued["job_id"] and job["state"] == "cancelled"),
                None,
            )
        )
        assert terminal["error"] == "source_ended"
        assert sessions.injected == [("owner-a", "road-sim-a", "wrong_way")]
        assert cases.cancelled == [started[0]["case_id"]]
    finally:
        service.close()


def test_monitor_stop_after_warmup_prevents_late_injection():
    service_type = _scenario_service_type()
    controller = _Controller()
    cases = _Cases(controller)
    sessions = _Sessions()
    sessions.block_age_probe = True
    service = service_type(
        cases, sessions, controller, poll_interval_seconds=0.01,
        warmup_timeout_seconds=1.0,
    )
    try:
        queued = service.enqueue("owner-a", "road-sim-a", "wrong_way", "fixture", "pending")
        started = _wait_for(lambda: cases.starts)
        controller.observe(started[0]["run_id"], 3)
        assert sessions.age_probe_waiting.wait(1.0), "warmup never reached preroll check"
        # The warmup status was already read as observing. Stopping the monitor
        # while the preroll check is paused must be caught by the final recheck.
        controller.stop_monitor(started[0]["run_id"])
        sessions.allow_age_probe.set()
        terminal = _wait_for(
            lambda: next(
                (job for job in service.jobs("owner-a", "road-sim-a")
                 if job["job_id"] == queued["job_id"] and job["state"] == "failed"),
                None,
            )
        )
        assert terminal["error"] == "run_ended_before_injection"
        assert sessions.injected == []
        assert cases.cancelled == [started[0]["case_id"]]
    finally:
        sessions.allow_age_probe.set()
        service.close()


def test_actual_direction_pixels_can_candidate_all_three_scene_events():
    from visionflow.contracts import Frame
    from visionflow.road_scene import RoadWorld
    from visionflow.tools.direction import DirectionAnalyzer

    params = {
        "lanes": [
            {"id": "northbound", "region": [0, 90, 480, 450], "expected": [0, -1]},
            {"id": "southbound", "region": [480, 90, 480, 450], "expected": [0, 1]},
        ],
        "min_frames": 3,
        "min_displacement_px": 4.0,
        "opposition_cosine": -0.75,
    }
    for event, lane_id, sign, start, end in (
        ("wrong_way", "northbound", 1, 5.0, 9.0),
        ("bird", "southbound", -1, 5.0, 6.2),
        ("leaf", "northbound", 1, 5.0, 6.2),
    ):
        world = RoadWorld(seed=42)
        analyzer = DirectionAnalyzer()
        prime = world.render(4.8)
        analyzer.update(Frame(f"{event}-prime", prime, 4.8), params)
        world.render(5.0)
        world.inject(event)
        confirmed = []
        index = 0
        timestamp = start
        while timestamp <= end:
            image = world.render(timestamp)
            result = analyzer.update(
                Frame(f"{event}-{index}", image, timestamp), params
            )
            confirmed.extend(
                track
                for track in result["tracks"]
                if track["opposition"]
                and track["lane_id"] == lane_id
                and len(track["frame_ids"]) >= 3
                and sign * track["dy_px"] > 0
            )
            timestamp = round(timestamp + 0.2, 1)
            index += 1
        assert confirmed, f"actual {event} pixels did not form a 3-frame direction candidate"
