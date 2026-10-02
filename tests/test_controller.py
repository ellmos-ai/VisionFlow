from __future__ import annotations

import copy
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest
import numpy as np

from visionflow.approval import ApprovalStore
from visionflow.contracts import (
    ApprovalRejected,
    Finding,
    Frame,
    InputConfig,
    InvalidTransition,
    Limits,
    ModelDecision,
    Node,
    ObservationRequest,
    RunResult,
    UnknownRun,
    WorkflowConfig,
)
from visionflow.controller import Controller
from visionflow.registry import ToolDescriptor, ToolRegistry


class FakeSource:
    def __init__(self, frames: list[Frame], *, block_read: bool = False) -> None:
        self.source_id = "fixture-source"
        self.frames = list(frames)
        self.read_calls = 0
        self.close_calls = 0
        self.read_started = threading.Event()
        self.release_read = threading.Event()
        if not block_read:
            self.release_read.set()

    def read(self) -> Frame | None:
        self.read_calls += 1
        self.read_started.set()
        if not self.release_read.wait(timeout=3):
            raise TimeoutError("test source was not released")
        index = self.read_calls - 1
        return self.frames[index] if index < len(self.frames) else None

    def close(self) -> None:
        self.close_calls += 1
        self.release_read.set()


class FakeClock:
    def __init__(self) -> None:
        self.value = 0.0

    def monotonic(self) -> float:
        return self.value

    def sleep(self, seconds: float) -> None:
        self.value += seconds


def make_workflow(
    nodes: list[Node],
    *,
    input_kind: str = "image",
    source: str = "fixture-source",
    limits: Limits | None = None,
) -> WorkflowConfig:
    return WorkflowConfig(
        schema_version=1,
        name="controller-test",
        input=InputConfig(input_kind, source),
        limits=limits or Limits(),
        nodes=nodes,
        outputs=[],
    )


def make_registry(handler: Any = None) -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(
        ToolDescriptor("quality"),
        handler or (lambda frame, tool_params, context: {"score": 1}),
    )
    registry.register(
        ToolDescriptor("chosen"),
        lambda frame, tool_params, context: {"selected": "then"},
    )
    registry.register(
        ToolDescriptor("unchosen"),
        lambda frame, tool_params, context: {"selected": "else"},
    )
    return registry


def analyze_node(*, node_id: str = "analyze", next_ids: list[str] | None = None) -> Node:
    return Node(
        node_id,
        "analyze",
        {"tool": "quality", "tool_params": {}},
        next_ids,
    )


def one_frame_source(*, block_read: bool = False) -> FakeSource:
    return FakeSource([Frame("frame-0", b"fixture image bytes")], block_read=block_read)


def test_start_returns_queued_snapshot_and_worker_completes(tmp_path):
    calls: list[tuple[str, dict[str, Any], dict[str, Any]]] = []

    def handler(frame: Frame, tool_params: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        calls.append((frame.frame_id, copy.deepcopy(tool_params), copy.deepcopy(context)))
        return {"sharpness": 12.0}

    source = one_frame_source()
    registry = make_registry(handler)
    controller = Controller(
        registry,
        source_factory=lambda input_config, max_frames: source,
        evidence_root=tmp_path / "evidence",
    )

    started = controller.start(make_workflow([analyze_node()]))

    assert isinstance(started, RunResult)
    assert started.status == "queued"
    result = controller.wait(started.run_id, timeout=2)
    assert result.status == "completed"
    assert len(result.findings) == 1
    assert result.findings[0].metrics == {"sharpness": 12.0}
    assert result.findings[0].frame_ids == ["frame-0"]
    assert Path(tmp_path / "evidence" / started.run_id / "frame-000001.bin").read_bytes() == b"fixture image bytes"
    assert result.findings[0].evidence_ref == f"{started.run_id}/frame-000001.bin"
    assert calls[0][0] == "frame-0"
    assert calls[0][2]["run_id"] == started.run_id
    assert calls[0][2]["tool"] == "quality"
    assert calls[0][2]["state"] is calls[0][2]["tool_state"]
    assert calls[0][2]["run_status"] == "running"
    assert source.read_calls == 1
    assert source.close_calls == 1


def test_status_returns_deep_copies_of_results(tmp_path):
    source = one_frame_source()
    controller = Controller(
        make_registry(),
        source_factory=lambda input_config, max_frames: source,
        evidence_root=tmp_path / "evidence",
    )
    run_id = controller.start(make_workflow([analyze_node()])).run_id
    controller.wait(run_id, timeout=2)

    snapshot = controller.status(run_id)
    snapshot.findings[0].metrics["score"] = 999
    snapshot.trace[0]["state"] = "corrupted"
    fresh = controller.status(run_id)

    assert fresh.findings[0].metrics == {"score": 1}
    assert fresh.trace[0]["state"] != "corrupted"


def test_status_and_cancel_remain_available_during_blocking_read(tmp_path):
    source = one_frame_source(block_read=True)
    handler_calls: list[str] = []
    registry = make_registry(lambda frame, params, context: handler_calls.append(frame.frame_id) or {})
    controller = Controller(
        registry,
        source_factory=lambda input_config, max_frames: source,
        evidence_root=tmp_path / "evidence",
    )
    run_id = controller.start(make_workflow([analyze_node()])).run_id

    assert source.read_started.wait(timeout=2)
    before = time.monotonic()
    assert controller.status(run_id).status == "running"
    assert time.monotonic() - before < 0.5
    cancelled = controller.cancel(run_id)
    assert cancelled.status == "cancelled"
    result = controller.wait(run_id, timeout=2)

    assert result.status == "cancelled"
    assert handler_calls == []
    assert source.close_calls == 1


def test_cancel_after_model_call_prevents_follow_up_observation():
    entered = threading.Event()
    release = threading.Event()
    observation_calls: list[str] = []

    class BlockingModel:
        def ask(self, question: str, findings: list[Finding]) -> ModelDecision:
            entered.set()
            release.wait(timeout=3)
            return ModelDecision(
                "observe",
                {
                    "source_id": "fixture-source",
                    "region": None,
                    "duration_seconds": 2,
                    "sample_fps": 1,
                    "question": "inspect the next moment",
                },
            )

    def observer(source, request, budget, cancelled, clock):
        observation_calls.append(request.source_id)
        return []

    node = Node("ask", "ask_model", {"question": "detect motion"}, None)
    controller = Controller(make_registry(), model_adapter=BlockingModel(), observer=observer)
    run_id = controller.start(make_workflow([node])).run_id

    assert entered.wait(timeout=2)
    assert controller.cancel(run_id).status == "cancelled"
    release.set()

    assert controller.wait(run_id, timeout=2).status == "cancelled"
    assert observation_calls == []


def test_input_resume_can_change_source_only_after_user_wait(tmp_path):
    source = one_frame_source()
    opened_sources: list[str] = []

    def source_factory(input_config: InputConfig, max_frames: int) -> FakeSource:
        opened_sources.append(input_config.source)
        return source

    workflow = make_workflow(
        [
            Node("input", "request_input", {"reason": "choose a source"}, ["analyze"]),
            analyze_node(next_ids=[]),
        ]
    )
    controller = Controller(
        make_registry(),
        source_factory=source_factory,
        evidence_root=tmp_path / "evidence",
    )
    run_id = controller.start(workflow).run_id

    waiting = controller.wait(run_id, timeout=2)
    assert waiting.status == "waiting_for_input"
    assert opened_sources == []
    assert waiting.pending_requests[0]["reason"] == "choose a source"

    controller.resume(run_id, {"source": "user-selected-source", "input": {"path": "new.png"}})
    assert controller.wait(run_id, timeout=2).status == "completed"
    assert opened_sources == ["user-selected-source"]


def test_resume_does_not_reset_frame_budget(tmp_path):
    source = FakeSource([Frame("same-id", b"one"), Frame("same-id", b"two")])
    opened_sources: list[str] = []

    def source_factory(input_config: InputConfig, max_frames: int) -> FakeSource:
        opened_sources.append(input_config.source)
        return source

    workflow = make_workflow(
        [
            analyze_node(node_id="first", next_ids=["input"]),
            Node("input", "request_input", {"reason": "continue"}, ["second"]),
            analyze_node(node_id="second", next_ids=[]),
        ],
        limits=Limits(max_observation_seconds=10, max_frames=1, max_llm_calls=1),
    )
    controller = Controller(
        make_registry(),
        source_factory=source_factory,
        evidence_root=tmp_path / "evidence",
    )
    run_id = controller.start(workflow).run_id

    assert controller.wait(run_id, timeout=2).status == "waiting_for_input"
    assert source.close_calls == 1
    controller.resume(run_id, {"input": "continue"})

    result = controller.wait(run_id, timeout=2)
    assert result.status == "failed"
    assert source.read_calls == 1
    assert source.close_calls == 1
    assert opened_sources == ["fixture-source"]


def test_model_triggered_observation_uses_same_source_and_reasks_with_new_findings(tmp_path):
    source = FakeSource([Frame("frame-0", b"first"), Frame("frame-0", b"second")])

    class FollowUpModel:
        def __init__(self) -> None:
            self.findings_per_call: list[list[Finding]] = []

        def ask(self, question: str, findings: list[Finding]) -> ModelDecision:
            self.findings_per_call.append(copy.deepcopy(findings))
            if len(self.findings_per_call) == 1:
                return ModelDecision(
                    "observe",
                    {
                        "source_id": "fixture-source",
                        "region": None,
                        "duration_seconds": 2,
                        "sample_fps": 1,
                        "question": "check again",
                    },
                )
            return ModelDecision("report")

    model = FollowUpModel()
    requests: list[ObservationRequest] = []

    def observer(observed_source, request, budget, cancelled, clock):
        requests.append(request)
        if cancelled():
            return []
        budget.consume(observation_seconds=request.duration_seconds)
        budget.consume(frames=1)
        frame = observed_source.read()
        if frame is None:
            return []
        return [Finding("quality", [frame.frame_id], None, {"score": 2}, "fixture/follow-up")]

    workflow = make_workflow(
        [
            analyze_node(next_ids=["ask"]),
            Node("ask", "ask_model", {"question": "is this event important?"}, []),
            Node(
                "observe-authority",
                "observe",
                {"duration_seconds": 2, "sample_fps": 1, "region": None},
                [],
            ),
        ],
        limits=Limits(max_observation_seconds=10, max_frames=5, max_llm_calls=4),
    )
    controller = Controller(
        make_registry(),
        model_adapter=model,
        source_factory=lambda input_config, max_frames: source,
        observer=observer,
        evidence_root=tmp_path / "evidence",
    )

    run_id = controller.start(workflow).run_id
    result = controller.wait(run_id, timeout=2)

    assert result.status == "completed"
    assert len(model.findings_per_call) == 2
    assert len(model.findings_per_call[0]) == 1
    assert len(model.findings_per_call[1]) == 2
    assert requests[0].source_id == "fixture-source"
    assert requests[0].duration_seconds == 2
    assert len(result.findings) == 2
    assert result.findings[0].evidence_ref != result.findings[1].evidence_ref
    assert source.read_calls == 2


def test_model_receives_a_copy_of_findings_and_original_config_is_isolated(tmp_path):
    source = one_frame_source()
    registry = make_registry()
    config = make_workflow(
        [
            analyze_node(next_ids=["ask"]),
            Node("ask", "ask_model", {"question": "original question"}, []),
        ]
    )

    class MutatingModel:
        def ask(self, question: str, findings: list[Finding]) -> ModelDecision:
            assert question == "original question"
            findings[0].metrics["score"] = 999
            config.nodes[1].params["question"] = "changed by model"
            return ModelDecision("report")

    controller = Controller(
        registry,
        model_adapter=MutatingModel(),
        source_factory=lambda input_config, max_frames: source,
        evidence_root=tmp_path / "evidence",
    )
    run_id = controller.start(config).run_id
    result = controller.wait(run_id, timeout=2)

    assert result.status == "completed"
    assert result.findings[0].metrics == {"score": 1}
    assert config.nodes[1].params["question"] == "changed by model"
    mode_event = next(event for event in result.trace if event["type"] == "model_mode")
    assert mode_event["model_mode"] == "unknown"


def test_model_adapter_factory_creates_an_adapter_for_each_run():
    created_for: list[str] = []

    class ReportModel:
        mode = "fixture"

        def ask(self, question: str, findings: list[Finding]) -> ModelDecision:
            return ModelDecision("report")

    def adapter_factory(config: WorkflowConfig) -> ReportModel:
        created_for.append(config.name)
        return ReportModel()

    controller = Controller(make_registry(), model_adapter=adapter_factory)
    workflow = make_workflow([Node("ask", "ask_model", {"question": "report?"}, [])])

    first = controller.start(workflow)
    second = controller.start(workflow)

    first_result = controller.wait(first.run_id, timeout=2)
    second_result = controller.wait(second.run_id, timeout=2)
    assert first_result.status == "completed"
    assert second_result.status == "completed"
    assert next(event for event in first_result.trace if event["type"] == "model_mode")["model_mode"] == "fixture"
    assert next(event for event in second_result.trace if event["type"] == "model_mode")["model_mode"] == "fixture"
    assert created_for == ["controller-test", "controller-test"]


def test_video_initial_frame_count_is_bounded_by_remaining_budget(tmp_path):
    source = FakeSource([Frame("frame-0", b"only frame")])
    calls: list[str] = []
    workflow = make_workflow(
        [analyze_node()],
        input_kind="video",
        limits=Limits(max_observation_seconds=10, max_frames=1, max_llm_calls=1),
    )
    controller = Controller(
        make_registry(lambda frame, params, context: calls.append(frame.frame_id) or {"score": 1}),
        source_factory=lambda input_config, max_frames: source,
        evidence_root=tmp_path / "evidence",
    )

    result = controller.wait(controller.start(workflow).run_id, timeout=2)

    assert result.status == "completed"
    assert calls == ["frame-0"]
    assert source.read_calls == 1


def test_branch_executes_only_selected_destination(tmp_path):
    called: list[str] = []

    def handler(frame, tool_params, context):
        called.append(context["tool"])
        return {"score": 2}

    registry = make_registry(handler)
    registry.register(ToolDescriptor("then-tool"), lambda f, p, c: called.append("then-tool") or {})
    registry.register(ToolDescriptor("else-tool"), lambda f, p, c: called.append("else-tool") or {})
    workflow = make_workflow(
        [
            analyze_node(next_ids=["branch"]),
            Node(
                "branch",
                "branch",
                {
                    "finding_tool": "quality",
                    "metric": "score",
                    "operator": "gte",
                    "value": 1,
                    "then_node": "then",
                    "else_node": "else",
                },
                ["else"],
            ),
            Node("then", "analyze", {"tool": "then-tool", "tool_params": {}}, []),
            Node("else", "analyze", {"tool": "else-tool", "tool_params": {}}, []),
        ]
    )
    source = FakeSource([Frame("frame-0", b"first"), Frame("frame-1", b"second")])
    controller = Controller(
        registry,
        source_factory=lambda input_config, max_frames: source,
        evidence_root=tmp_path / "evidence",
    )

    result = controller.wait(controller.start(workflow).run_id, timeout=2)

    assert result.status == "completed"
    assert called == ["quality", "then-tool"]


def test_unknown_run_and_terminal_resume_raise_structured_errors():
    model = type("ReportModel", (), {"ask": lambda self, question, findings: ModelDecision("report")})()
    controller = Controller(make_registry(), model_adapter=model)

    with pytest.raises(UnknownRun):
        controller.status("missing")

    run_id = controller.start(
        make_workflow([Node("ask", "ask_model", {"question": "report?"}, [])])
    ).run_id
    result = controller.wait(run_id, timeout=2)
    assert result.status == "completed"

    with pytest.raises(InvalidTransition):
        controller.resume(run_id, {"input": "too late"})


def test_resume_outside_waiting_state_is_rejected(tmp_path):
    source = one_frame_source(block_read=True)
    controller = Controller(
        make_registry(),
        source_factory=lambda input_config, max_frames: source,
        evidence_root=tmp_path / "evidence",
    )
    run_id = controller.start(make_workflow([analyze_node()])).run_id
    assert source.read_started.wait(timeout=2)

    with pytest.raises(InvalidTransition):
        controller.resume(run_id, {"source": "switch"})

    controller.cancel(run_id)


def test_default_observer_consumes_two_seconds_once_before_sampling(tmp_path):
    source = FakeSource(
        [
            Frame("first", np.zeros((4, 4, 3), dtype=np.uint8)),
            Frame("second", np.ones((4, 4, 3), dtype=np.uint8)),
            Frame("third", np.full((4, 4, 3), 2, dtype=np.uint8)),
        ]
    )

    class ObserveThenReport:
        def __init__(self) -> None:
            self.calls = 0

        def ask(self, question: str, findings: list[Finding]) -> ModelDecision:
            self.calls += 1
            if self.calls == 1:
                return ModelDecision(
                    "observe",
                    {
                        "source_id": "fixture-source",
                        "region": None,
                        "duration_seconds": 2,
                        "sample_fps": 1,
                        "question": "inspect the follow-up",
                    },
                )
            return ModelDecision("report")

    workflow = make_workflow(
        [
            analyze_node(next_ids=["ask"]),
            Node("ask", "ask_model", {"question": "inspect"}, []),
            Node(
                "observe-authority",
                "observe",
                {"duration_seconds": 2, "sample_fps": 1, "region": None},
                [],
            ),
        ],
        limits=Limits(max_observation_seconds=2, max_frames=3, max_llm_calls=4),
    )
    controller = Controller(
        make_registry(),
        model_adapter=ObserveThenReport(),
        source_factory=lambda input_config, max_frames: source,
        evidence_root=tmp_path / "evidence",
        clock=FakeClock(),
    )

    result = controller.wait(controller.start(workflow).run_id, timeout=2)

    assert result.status == "completed"
    event = next(item for item in result.trace if item["type"] == "observation_completed")
    assert event["budget"]["used"] == {
        "frames": 3,
        "observation_seconds": 2.0,
        "llm_calls": 1,
    }
    assert event["budget"]["remaining"]["observation_seconds"] == 0


def test_fifth_model_call_is_blocked_before_adapter_invocation():
    class AlwaysObserve:
        def __init__(self) -> None:
            self.calls = 0

        def ask(self, question: str, findings: list[Finding]) -> ModelDecision:
            self.calls += 1
            return ModelDecision(
                "observe",
                {
                    "source_id": "fixture-source",
                    "region": None,
                    "duration_seconds": 1,
                    "sample_fps": 1,
                    "question": "observe more",
                },
            )

    model = AlwaysObserve()

    def observer(source, request, budget, cancelled, clock):
        budget.consume(observation_seconds=request.duration_seconds)
        return [Finding("quality", [f"sample-{model.calls}"], None, {"score": model.calls}, "fixture")]

    workflow = make_workflow(
        [
            Node("ask", "ask_model", {"question": "observe"}, []),
            Node("observe-authority", "observe", {"duration_seconds": 1, "sample_fps": 1, "region": None}, []),
        ],
        limits=Limits(max_observation_seconds=4, max_frames=10, max_llm_calls=4),
    )
    controller = Controller(
        make_registry(),
        model_adapter=model,
        source_factory=lambda input_config, max_frames: one_frame_source(),
        observer=observer,
    )

    result = controller.wait(controller.start(workflow).run_id, timeout=2)

    assert result.status == "failed"
    assert model.calls == 4
    assert sum(event["type"] == "model_call" for event in result.trace) == 4


def test_cumulative_observations_cannot_exceed_remaining_time():
    class ObserveTwice:
        def __init__(self) -> None:
            self.calls = 0

        def ask(self, question: str, findings: list[Finding]) -> ModelDecision:
            self.calls += 1
            return ModelDecision(
                "observe",
                {
                    "source_id": "fixture-source",
                    "region": None,
                    "duration_seconds": 2,
                    "sample_fps": 1,
                    "question": f"observe {self.calls}",
                },
            )

    model = ObserveTwice()
    observer_calls: list[float] = []

    def observer(source, request, budget, cancelled, clock):
        observer_calls.append(request.duration_seconds)
        budget.consume(observation_seconds=request.duration_seconds)
        return [Finding("quality", [str(len(observer_calls))], None, {}, "fixture")]

    workflow = make_workflow(
        [
            Node("ask", "ask_model", {"question": "observe"}, []),
            Node("observe-authority", "observe", {"duration_seconds": 2, "sample_fps": 1, "region": None}, []),
        ],
        limits=Limits(max_observation_seconds=3, max_frames=10, max_llm_calls=4),
    )
    controller = Controller(make_registry(), model_adapter=model, observer=observer)

    result = controller.wait(controller.start(workflow).run_id, timeout=2)

    assert result.status == "failed"
    assert model.calls == 2
    assert observer_calls == [2]


@pytest.mark.parametrize("approved", [True, False])
def test_approval_is_bound_to_its_draft_and_resume_does_not_duplicate_it(approved):
    class RequestApproval:
        def ask(self, question: str, findings: list[Finding]) -> ModelDecision:
            return ModelDecision("request_approval", {"action_type": "notification_draft"})

    store = ApprovalStore()
    workflow = make_workflow(
        [
            Node("ask", "ask_model", {"question": "prepare a draft"}, ["report"]),
            Node("report", "report", {"format": "json"}, []),
        ]
    )
    controller = Controller(make_registry(), model_adapter=RequestApproval(), approval_store=store)
    run_id = controller.start(workflow).run_id
    waiting = controller.wait(run_id, timeout=2)
    pending = waiting.pending_requests[0]
    draft = next(output for output in waiting.outputs if output["type"] == "notification_draft")

    assert waiting.status == "waiting_for_approval"
    with pytest.raises(ApprovalRejected):
        store.confirm("another-run", pending["action_id"], pending["content_hash"], datetime.now(timezone.utc))
    with pytest.raises(ApprovalRejected):
        controller.resume(
            run_id,
            {
                "approved": approved,
                "action_id": pending["action_id"],
                "content_hash": "sha256:" + "0" * 64,
            },
        )

    controller.resume(
        run_id,
        {
            "approved": approved,
            "action_id": pending["action_id"],
            "content_hash": pending["content_hash"],
        },
    )
    result = controller.wait(run_id, timeout=2)

    assert result.status == "completed"
    drafts = [output for output in result.outputs if output["type"] == "notification_draft"]
    assert len(drafts) == 1
    assert drafts[0]["action_id"] == draft["action_id"]
    assert drafts[0]["sent"] is False
    assert len([output for output in result.outputs if output["type"] == "approval_receipt"]) == int(approved)


def test_observer_tool_state_is_separate_for_full_frame_and_roi(tmp_path):
    source = FakeSource(
        [
            Frame("same-frame-id", np.zeros((4, 4, 3), dtype=np.uint8)),
            Frame("same-frame-id", np.ones((4, 4, 3), dtype=np.uint8)),
        ]
    )

    def stateful_handler(frame, tool_params, context):
        tool_state = context["tool_state"]
        tool_state["calls"] = tool_state.get("calls", 0) + 1
        return {"calls": tool_state["calls"], "shape": list(frame.image.shape[:2])}

    class ObserveROIThenReport:
        def __init__(self) -> None:
            self.calls = 0

        def ask(self, question: str, findings: list[Finding]) -> ModelDecision:
            self.calls += 1
            if self.calls == 1:
                return ModelDecision(
                    "observe",
                    {
                        "source_id": "fixture-source",
                        "region": [0, 0, 2, 2],
                        "duration_seconds": 1,
                        "sample_fps": 1,
                        "question": "inspect this region",
                    },
                )
            return ModelDecision("report")

    workflow = make_workflow(
        [
            analyze_node(next_ids=["ask"]),
            Node("ask", "ask_model", {"question": "inspect"}, []),
            Node(
                "observe-authority",
                "observe",
                {"duration_seconds": 1, "sample_fps": 1, "region": [0, 0, 2, 2]},
                [],
            ),
        ],
        limits=Limits(max_observation_seconds=1, max_frames=2, max_llm_calls=4),
    )
    controller = Controller(
        make_registry(stateful_handler),
        model_adapter=ObserveROIThenReport(),
        source_factory=lambda input_config, max_frames: source,
        evidence_root=tmp_path / "evidence",
        clock=FakeClock(),
    )

    result = controller.wait(controller.start(workflow).run_id, timeout=2)

    assert result.status == "completed"
    assert len(result.findings) == 2
    assert result.findings[0].metrics == {"calls": 1, "shape": [4, 4]}
    assert result.findings[1].metrics == {"calls": 1, "shape": [2, 2]}
    assert result.findings[0].frame_ids == result.findings[1].frame_ids == ["same-frame-id"]
    assert result.findings[0].evidence_ref != result.findings[1].evidence_ref
