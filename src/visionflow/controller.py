"""Asynchronous, bounded execution of a validated VisionFlow workflow."""

from __future__ import annotations

import copy
import json
import math
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .budget import Budget
from .contracts import (
    ActionProposal,
    ApprovalRejected,
    BudgetExceeded,
    ConfigError,
    Finding,
    Frame,
    InputConfig,
    InvalidTransition,
    MediaError,
    ModelDecision,
    Node,
    ObservationRejected,
    ObservationRequest,
    RunResult,
    UnknownRun,
    WorkflowConfig,
)
from .evidence import store_evidence
from .registry import ToolRegistry
from .trace import TraceRecorder


_TERMINAL = frozenset({"completed", "failed", "cancelled"})
_WAITING = frozenset({"waiting_for_input", "waiting_for_approval"})


class _SystemClock:
    monotonic = staticmethod(time.monotonic)
    sleep = staticmethod(time.sleep)


@dataclass(slots=True)
class _Run:
    config: WorkflowConfig
    result: RunResult
    budget: Budget
    recorder: TraceRecorder
    lock: threading.RLock = field(default_factory=threading.RLock)
    cancel_event: threading.Event = field(default_factory=threading.Event)
    done_event: threading.Event = field(default_factory=threading.Event)
    queue: list[str] = field(default_factory=list)
    visited: set[str] = field(default_factory=set)
    thread: threading.Thread | None = None
    source: Any = None
    image_frame: Frame | None = None
    source_needs_reopen: bool = False
    frames_analyzed: int = 0
    evidence_sequence: int = 0
    adapter: Any = None
    adapter_initialized: bool = False
    user_input: Any = None
    last_tool: str | None = None
    last_tool_params: dict[str, Any] = field(default_factory=dict)
    tool_states: dict[tuple[str, tuple[float, float, float, float] | None], dict[str, Any]] = field(default_factory=dict)
    pending_proposal: ActionProposal | None = None
    output_index: int = 0


class Controller:
    """Run workflow nodes in worker threads and expose copy-isolated snapshots."""

    def __init__(
        self,
        registry: ToolRegistry,
        *,
        model_adapter: Any = None,
        source_factory: Callable[[InputConfig, int], Any] | None = None,
        evidence_root: Path = Path(".local/evidence"),
        clock: Any = None,
        approval_store: Any = None,
        observer: Callable[..., Any] | None = None,
    ) -> None:
        if not isinstance(registry, ToolRegistry):
            raise TypeError("registry must be a ToolRegistry")
        self.registry = registry
        self.model_adapter = model_adapter
        self.source_factory = source_factory
        self.evidence_root = Path(evidence_root)
        self.clock = clock if clock is not None else _SystemClock()
        self.approval_store = approval_store
        self._approval_lock = threading.Lock()
        self.observer = observer
        self._runs: dict[str, _Run] = {}
        self._runs_lock = threading.RLock()

    def start(self, config: WorkflowConfig) -> RunResult:
        """Queue a defensive copy of *config* and return before workflow work begins."""
        if not isinstance(config, WorkflowConfig):
            raise ConfigError("config must be a validated WorkflowConfig")
        run_config = copy.deepcopy(config)
        run_id = uuid.uuid4().hex
        limits = run_config.limits
        budget = Budget(
            max_observation_seconds=limits.max_observation_seconds,
            max_frames=limits.max_frames,
            max_llm_calls=limits.max_llm_calls,
        )
        recorder = TraceRecorder()
        result = RunResult(run_id=run_id, status="queued")
        run = _Run(
            config=run_config,
            result=result,
            budget=budget,
            recorder=recorder,
            queue=[run_config.nodes[0].id] if run_config.nodes else [],
        )
        recorder.append({"type": "state_change", "state": "queued"})
        result.trace = recorder.export()
        with self._runs_lock:
            self._runs[run_id] = run
        queued_snapshot = self._snapshot(run)
        self._launch(run)
        return queued_snapshot

    def status(self, run_id: str) -> RunResult:
        """Return a deep copy of the current result or raise ``UnknownRun``."""
        return self._snapshot(self._get_run(run_id))

    def wait(self, run_id: str, timeout: float | None = None) -> RunResult:
        """Wait for execution, input, or approval to yield, then return a snapshot."""
        run = self._get_run(run_id)
        if timeout is not None:
            if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
                raise TypeError("timeout must be a finite non-negative number or None")
            if not math.isfinite(float(timeout)) or timeout < 0:
                raise ValueError("timeout must be a finite non-negative number")
        run.done_event.wait(timeout=None if timeout is None else float(timeout))
        return self._snapshot(run)

    def join(self, run_id: str, timeout: float = 5.0) -> RunResult:
        """Join the current worker, including native cleanup after terminal status.

        ``wait`` yields a status snapshot; a completed snapshot can precede
        source.close(). Short-lived callers use this barrier before exiting.
        """
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
            raise TypeError("timeout must be a finite non-negative number")
        if not math.isfinite(float(timeout)) or timeout < 0:
            raise ValueError("timeout must be a finite non-negative number")
        run = self._get_run(run_id)
        with run.lock:
            thread = run.thread
        if thread is not None:
            thread.join(timeout=float(timeout))
            if thread.is_alive():
                raise TimeoutError("run worker has not finished its cleanup")
        with run.lock:
            if run.thread is not thread:
                raise InvalidTransition("run worker changed while joining")
        return self._snapshot(run)

    def cancel(self, run_id: str) -> RunResult:
        """Cancel promptly; an in-flight adapter may finish but cannot publish work."""
        run = self._get_run(run_id)
        with run.lock:
            if run.result.status in _TERMINAL:
                return copy.deepcopy(run.result)
            run.cancel_event.set()
            run.result.status = "cancelled"
            run.result.pending_requests.clear()
            run.pending_proposal = None
            source, run.source = run.source, None
            run.image_frame = None
            self._record_locked(run, {"type": "state_change", "state": "cancelled"})
            snapshot = copy.deepcopy(run.result)
        self._close_source(source)
        return snapshot

    def resume(self, run_id: str, payload: dict[str, Any]) -> RunResult:
        """Resume an input or approval wait without resetting cumulative budgets."""
        run = self._get_run(run_id)
        if not isinstance(payload, dict):
            raise InvalidTransition("resume payload must be an object")

        with run.lock:
            current_status = run.result.status
            old_thread = run.thread
            if current_status not in _WAITING:
                raise InvalidTransition(f"run cannot resume from {current_status!r}")

        # A waiting worker marks the wait before it returns. Join outside the run lock
        # so no endpoint is blocked behind a worker finishing its pause transition.
        if old_thread is not None and old_thread.is_alive():
            old_thread.join(timeout=5)
            if old_thread.is_alive():
                raise InvalidTransition("run worker has not finished its wait transition")

        with run.lock:
            if run.result.status != current_status or run.result.status not in _WAITING:
                raise InvalidTransition("run state changed while preparing to resume")
            if current_status == "waiting_for_input":
                self._resume_input_locked(run, payload)
            else:
                self._resume_approval_locked(run, payload)
            run.result.pending_requests.clear()
            run.result.status = "queued"
            run.done_event.clear()
            self._record_locked(run, {"type": "state_change", "state": "queued"})
            snapshot = copy.deepcopy(run.result)

        self._launch(run)
        return snapshot

    def _resume_input_locked(self, run: _Run, payload: dict[str, Any]) -> None:
        unknown = set(payload) - {"source", "input"}
        if unknown:
            raise InvalidTransition("input resume accepts only source and input fields")
        if "source" in payload:
            source = payload["source"]
            if not isinstance(source, str) or not source.strip():
                raise InvalidTransition("resume source must be a non-empty string")
            run.config.input.source = source
            run.source_needs_reopen = True
            run.image_frame = None
            run.tool_states.clear()
        if "input" in payload:
            run.user_input = copy.deepcopy(payload["input"])

    def _resume_approval_locked(self, run: _Run, payload: dict[str, Any]) -> None:
        if set(payload) - {"approved", "decision", "action_id", "content_hash"}:
            raise InvalidTransition("approval resume contains unsupported fields")
        proposal = run.pending_proposal
        if proposal is None:
            raise InvalidTransition("run has no pending approval proposal")
        action_id = payload.get("action_id")
        content_hash = payload.get("content_hash")
        if action_id != proposal.action_id or content_hash != proposal.content_hash:
            raise ApprovalRejected("approval must match the pending action and content hash")
        decision = payload.get("decision")
        approved = payload.get("approved")
        if decision is not None:
            if decision not in {"approve", "reject"}:
                raise InvalidTransition("decision must be 'approve' or 'reject'")
            if approved is not None and approved != (decision == "approve"):
                raise InvalidTransition("decision and approved fields disagree")
            approved = decision == "approve"
        if not isinstance(approved, bool):
            raise InvalidTransition("approval resume requires a boolean approved field")

        store = self._get_approval_store()
        now = self._wall_clock()
        if approved:
            receipt = store.confirm(run.result.run_id, action_id, content_hash, now)
            receipt_data = {
                "receipt_id": receipt.receipt_id,
                "run_id": receipt.run_id,
                "action_id": receipt.action_id,
                "content_hash": receipt.content_hash,
                "confirmed_at": receipt.confirmed_at.isoformat(),
            }
            run.result.outputs.append({"type": "approval_receipt", **receipt_data})
        else:
            store.reject(run.result.run_id, action_id, now)
            run.result.outputs.append({"type": "approval_rejected", "action_id": action_id})
        run.pending_proposal = None

    def _launch(self, run: _Run) -> None:
        thread = threading.Thread(
            target=self._thread_main,
            args=(run,),
            name=f"visionflow-{run.result.run_id[:8]}",
            daemon=True,
        )
        with run.lock:
            run.thread = thread
        thread.start()

    def _thread_main(self, run: _Run) -> None:
        try:
            with run.lock:
                if run.result.status in _TERMINAL:
                    return
                run.result.status = "running"
                self._record_locked(run, {"type": "state_change", "state": "running"})
            self._execute(run)
        except Exception as exc:
            self._fail(run, exc)
        finally:
            with run.lock:
                run.done_event.set()

    def _execute(self, run: _Run) -> None:
        nodes = {node.id: node for node in run.config.nodes}
        order = self._execution_order(nodes)
        while True:
            if run.cancel_event.is_set():
                return
            with run.lock:
                if run.result.status in _TERMINAL:
                    return
                run.queue = list(dict.fromkeys(item for item in run.queue if item not in run.visited))
                if not run.queue:
                    next_node_id = None
                else:
                    next_node_id = min(run.queue, key=lambda item: order.get(item, len(order)))
                    run.queue.remove(next_node_id)
                    run.visited.add(next_node_id)
            if next_node_id is None:
                if self._process_outputs(run):
                    return
                self._finish(run)
                return

            node = nodes.get(next_node_id)
            if node is None:
                raise ConfigError(f"workflow references unknown node {next_node_id!r}")
            self._record(run, {"type": "node_started", "node_id": node.id, "state": "running"})
            successors, waiting = self._execute_node(run, node)
            if run.cancel_event.is_set():
                return
            if waiting:
                return
            with run.lock:
                if run.result.status in _TERMINAL:
                    return
                run.queue.extend(successors)
                self._record_locked(
                    run,
                    {"type": "node_completed", "node_id": node.id, "state": run.result.status},
                )

    @staticmethod
    def _execution_order(nodes: dict[str, Node]) -> dict[str, int]:
        """Order active paths topologically so a shared successor runs only after its ancestors."""
        edges = {}
        predecessors = dict.fromkeys(nodes, 0)
        for node_id, node in nodes.items():
            targets = set(node.next or [])
            if node.type == "branch":
                targets.update((node.params["then_node"], node.params["else_node"]))
            if targets - nodes.keys():
                raise ConfigError(f"node {node_id!r} references an unknown successor")
            edges[node_id] = targets
            for target in targets:
                predecessors[target] += 1
        ready = [node_id for node_id in nodes if predecessors[node_id] == 0]
        order = {}
        while ready:
            current = ready.pop(0)
            order[current] = len(order)
            for target in nodes:
                if target in edges[current]:
                    predecessors[target] -= 1
                    if predecessors[target] == 0:
                        ready.append(target)
        if len(order) != len(nodes):
            raise InvalidTransition("workflow contains a static cycle")
        return order

    def _execute_node(self, run: _Run, node: Node) -> tuple[list[str], bool]:
        params = node.params
        if node.type == "analyze":
            self._analyze(run, node)
            return list(node.next or []), False
        if node.type == "branch":
            target = self._branch_target(run, node)
            self._record(run, {"type": "branch_selected", "node_id": node.id, "state": target})
            return [target], False
        if node.type == "ask_model":
            return list(node.next or []), self._ask_model(run, node)
        if node.type == "observe":
            request = ObservationRequest(
                source_id=self._current_source_id(run),
                region=params.get("region"),
                duration_seconds=params["duration_seconds"],
                sample_fps=params["sample_fps"],
                question="Workflow-requested follow-up observation",
            )
            self._run_observation(
                run,
                request,
                node_id=node.id,
                stop_when=params.get("stop_when"),
            )
            return list(node.next or []), False
        if node.type == "request_input":
            self._pause_for_input(run, params["reason"], node.id, list(node.next or []))
            return [], True
        if node.type == "report":
            self._add_report(run, params["format"])
            return list(node.next or []), False
        if node.type == "approve":
            action_type = params["action_type"]
            if action_type != "notification_draft":
                raise ApprovalRejected(f"unsupported approval action: {action_type!r}")
            proposal = self._prepare_notification(run)
            self._pause_for_approval(run, proposal, node.id, list(node.next or []), reason="Approval required")
            return [], True
        if node.type == "output":
            self._process_output_type(run, params["output_type"], requires_approval=False, node_id=node.id)
            if self._is_waiting(run):
                return [], True
            return list(node.next or []), False
        raise ConfigError(f"unsupported workflow node type: {node.type!r}")

    def _analyze(self, run: _Run, node: Node) -> None:
        tool_name = node.params["tool"]
        handler = self.registry.resolve(tool_name)
        tool_params = copy.deepcopy(node.params.get("tool_params", {}))
        run.last_tool = tool_name
        run.last_tool_params = copy.deepcopy(tool_params)
        source = self._ensure_source(run)
        default_count = 2 if run.config.input.kind == "video" else 1
        requested_count = tool_params.get("initial_frames", default_count)
        if type(requested_count) is not int or requested_count <= 0:
            raise ConfigError("analyze.tool_params.initial_frames must be a positive integer")
        remaining = int(run.budget.remaining["frames"])
        frame_count = min(requested_count, remaining)
        if frame_count <= 0:
            raise BudgetExceeded("run budget exceeded for: frames")

        processed = 0
        for _ in range(frame_count):
            if run.cancel_event.is_set():
                return
            run.budget.consume(frames=1)
            with run.lock:
                cached = run.image_frame if run.config.input.kind == "image" else None
            frame = copy.deepcopy(cached) if cached is not None else source.read()
            if frame is None:
                run.budget._refund(frames=1)
                if processed == 0:
                    raise MediaError("source ended before a usable frame")
                break
            if run.cancel_event.is_set():
                return
            if not isinstance(frame, Frame):
                raise MediaError("source read() must return a Frame or None")
            if run.config.input.kind == "image" and cached is None:
                with run.lock:
                    run.image_frame = copy.deepcopy(frame)
            evidence_id, evidence_ref = self._save_evidence(run, frame)
            if run.cancel_event.is_set():
                return
            context = self._tool_context(run, node, tool_name, source)
            raw_finding = handler(frame, copy.deepcopy(tool_params), context)
            if run.cancel_event.is_set():
                return
            finding = self._normalize_finding(raw_finding, tool_name, frame, evidence_ref)
            with run.lock:
                if run.result.status in _TERMINAL:
                    return
                run.result.findings.append(finding)
                run.frames_analyzed += 1
                run.last_tool = tool_name
                self._record_locked(
                    run,
                    {
                        "type": "frame_analyzed",
                        "node_id": node.id,
                        "state": "running",
                        "evidence_id": evidence_id,
                    },
                )
            processed += 1
        if processed == 0:
            raise MediaError("source produced no analyzable frames")

    def _tool_context(self, run: _Run, node: Node, tool: str, source: Any) -> dict[str, Any]:
        return self._tool_context_for_region(run, node, tool, source, None)

    def _tool_context_for_region(
        self,
        run: _Run,
        node: Node,
        tool: str,
        source: Any,
        region: tuple[float, float, float, float] | None,
    ) -> dict[str, Any]:
        state_key = (tool, region)
        with run.lock:
            tool_state = run.tool_states.setdefault(state_key, {})
        effective_region = None
        if region is not None:
            x, y, box_width, box_height = region
            left = math.floor(x)
            top = math.floor(y)
            right = math.ceil(x + box_width)
            bottom = math.ceil(y + box_height)
            effective_region = (left, top, right - left, bottom - top)
        return {
            "run_id": run.result.run_id,
            "node_id": node.id,
            "state": tool_state,
            "run_status": self._current_status(run),
            "tool": tool,
            "source_id": source.source_id,
            "input_kind": run.config.input.kind,
            "budget": run.budget.snapshot(),
            "user_input": copy.deepcopy(run.user_input),
            "tool_state": tool_state,
            "region": effective_region,
        }

    def _normalize_finding(
        self,
        raw: Finding | dict[str, Any],
        tool: str,
        frame: Frame,
        evidence_ref: str,
        region: tuple[float, float, float, float] | None = None,
    ) -> Finding:
        if isinstance(raw, Finding):
            finding = Finding(
                tool=raw.tool or tool,
                frame_ids=copy.deepcopy(raw.frame_ids) or [frame.frame_id],
                region=copy.deepcopy(region if region is not None else raw.region),
                metrics=copy.deepcopy(raw.metrics),
                evidence_ref=evidence_ref,
            )
        elif isinstance(raw, dict):
            finding = Finding(tool=tool, frame_ids=[frame.frame_id], region=copy.deepcopy(region),
                              metrics=copy.deepcopy(raw), evidence_ref=evidence_ref)
        else:
            raise TypeError("tool handler must return a Finding or metrics dictionary")
        if region is not None:
            finding.metrics["coordinate_system"] = {
                "region": "source_pixels", "evidence": "crop_pixels", "regions": "crop_pixels",
            }
        return finding

    def _save_evidence(self, run: _Run, frame: Frame) -> tuple[str, str]:
        with run.lock:
            run.evidence_sequence += 1
            evidence_id = f"frame-{run.evidence_sequence:06d}"
        image = frame.image
        if isinstance(image, bytes):
            image_bytes = image
        elif isinstance(image, bytearray):
            image_bytes = bytes(image)
        else:
            try:
                import cv2
                import numpy as np

                if not isinstance(image, np.ndarray):
                    raise TypeError("frame image must be bytes or a NumPy array")
                ok, encoded = cv2.imencode(".png", image)
                if not ok:
                    raise MediaError("could not encode frame evidence as PNG")
                image_bytes = encoded.tobytes()
            except ImportError as exc:
                raise MediaError("NumPy/OpenCV is required to store array evidence") from exc
        path = store_evidence(self.evidence_root, run.result.run_id, evidence_id, image_bytes)
        relative_ref = path.resolve(strict=True).relative_to(
            self.evidence_root.resolve(strict=True)
        )
        return evidence_id, relative_ref.as_posix()

    def _branch_target(self, run: _Run, node: Node) -> str:
        params = node.params
        with run.lock:
            finding = next(
                (item for item in reversed(run.result.findings) if item.tool == params["finding_tool"]),
                None,
            )
            metric_value = None if finding is None else finding.metrics.get(params["metric"])
        matched = False
        if metric_value is not None:
            expected = params["value"]
            operator = params["operator"]
            try:
                if operator == "gt":
                    matched = metric_value > expected
                elif operator == "gte":
                    matched = metric_value >= expected
                elif operator == "lt":
                    matched = metric_value < expected
                elif operator == "lte":
                    matched = metric_value <= expected
                elif operator == "eq":
                    matched = metric_value == expected
            except (TypeError, ValueError):
                matched = False
        return params["then_node"] if matched else params["else_node"]

    def _ask_model(self, run: _Run, node: Node) -> bool:
        question = node.params["question"]
        while not run.cancel_event.is_set():
            adapter = self._get_model_adapter(run)
            with run.lock:
                if run.cancel_event.is_set() or run.result.status in _TERMINAL:
                    return True
                run.budget.consume(llm_calls=1)
                self._record_locked(run, {
                    "type": "model_call",
                    "node_id": node.id,
                    "state": "running",
                    "budget": run.budget.snapshot(),
                })
                findings = copy.deepcopy(run.result.findings)
            if run.cancel_event.is_set():
                return True
            raw_decision = adapter.ask(question, findings)
            if run.cancel_event.is_set():
                return True
            decision = self._validate_model_decision(raw_decision)
            self._record(
                run,
                {"type": "model_decision", "node_id": node.id, "state": decision.action},
            )
            if decision.action == "report":
                summary = decision.payload.get("summary")
                if summary is not None:
                    self._append_output(run, {"type": "model_summary", "summary": summary})
                self._add_report(run, "markdown")
                return False
            if decision.action == "observe":
                request = self._observation_request(run, decision.payload)
                before = len(findings)
                observed = self._run_observation(run, request, node_id=node.id)
                if run.cancel_event.is_set():
                    return True
                if observed and len(observed) > 0 and len(self._findings(run)) > before:
                    continue
                return False
            if decision.action == "request_input":
                self._pause_for_input(
                    run,
                    decision.payload["reason"],
                    node.id,
                    list(node.next or []),
                )
                return True
            if decision.action == "request_approval":
                if decision.payload["action_type"] != "notification_draft":
                    raise ApprovalRejected("only notification_draft approvals are supported")
                proposal = self._prepare_notification(run)
                self._pause_for_approval(
                    run,
                    proposal,
                    node.id,
                    list(node.next or []),
                    reason="Model requested approval for a notification draft",
                )
                return True
            raise ConfigError(f"unsupported model action: {decision.action!r}")
        return True

    def _get_model_adapter(self, run: _Run) -> Any:
        with run.lock:
            if run.adapter_initialized:
                if run.adapter is None:
                    raise RuntimeError("no model adapter is configured")
                return run.adapter
            configured = self.model_adapter
        if configured is None:
            with run.lock:
                run.adapter_initialized = True
            raise RuntimeError("no model adapter is configured")
        create_adapter = getattr(configured, "create_adapter", None)
        if callable(create_adapter):
            from .model import ModelRunContext

            context = ModelRunContext(
                run_id=run.result.run_id,
                source_id=self._current_source_id(run),
                cancelled=run.cancel_event.is_set,
                snapshot=lambda: self._snapshot(run),
            )
            adapter = create_adapter(copy.deepcopy(run.config), context)
        elif hasattr(configured, "ask"):
            adapter = configured
        elif callable(configured):
            adapter = configured(copy.deepcopy(run.config))
        else:
            raise TypeError(
                "model_adapter must implement ask(), create_adapter(config, context), "
                "or be a config factory"
            )
        if not callable(getattr(adapter, "ask", None)):
            raise TypeError("model adapter factory must return an object with ask()")
        with run.lock:
            if run.cancel_event.is_set() or run.result.status in _TERMINAL:
                return adapter
            run.adapter = adapter
            run.adapter_initialized = True
        mode = getattr(adapter, "mode", "unknown")
        if not isinstance(mode, str) or not mode.strip():
            mode = "unknown"
        self._record(run, {"type": "model_mode", "model_mode": mode})
        return adapter

    def _validate_model_decision(self, raw: Any) -> ModelDecision:
        from .model import validate_model_decision

        decision = validate_model_decision(raw)
        if not isinstance(decision, ModelDecision):
            raise ConfigError("validate_model_decision must return a ModelDecision")
        return copy.deepcopy(decision)

    def _observation_request(self, run: _Run, payload: dict[str, Any]) -> ObservationRequest:
        current_source = self._current_source_id(run)
        if payload["source_id"] != current_source:
            raise ObservationRejected("model observation cannot switch the active source")
        return ObservationRequest(
            source_id=current_source,
            region=payload["region"],
            duration_seconds=payload["duration_seconds"],
            sample_fps=payload["sample_fps"],
            question=payload["question"],
        )

    def _run_observation(
        self,
        run: _Run,
        request: ObservationRequest,
        *,
        node_id: str,
        stop_when: dict[str, Any] | None = None,
    ) -> list[Finding]:
        source = self._ensure_source(run)
        if request.source_id != source.source_id:
            raise ObservationRejected("observation source must match the active source")
        from .observation import validate_observation

        request = validate_observation(request, run.config, run.budget, source=source)
        if self.observer is not None and stop_when is not None:
            raise ObservationRejected(
                "custom observer cannot enforce the configured stop_when condition"
            )
        with run.lock:
            if run.result.status not in _TERMINAL:
                run.result.status = "observing"
                self._record_locked(
                    run,
                    {
                        "type": "state_change",
                        "node_id": node_id,
                        "state": "observing",
                        "budget": run.budget.snapshot(),
                    },
                )
        if run.cancel_event.is_set():
            with run.lock:
                self._record_locked(
                    run,
                    {
                        "type": "monitor_stopped",
                        "node_id": node_id,
                        "source_id": source.source_id,
                        "reason": "cancelled",
                        "frame_ids": [],
                        "source_times": [],
                        "duration_seconds": request.duration_seconds,
                        "budget": run.budget.snapshot(),
                    },
                )
            return []

        stop_predicate = None
        if stop_when is not None:
            rule = copy.deepcopy(stop_when)
            consecutive_hits = 0

            def stop_predicate(frame_findings: list[Finding]) -> bool:
                nonlocal consecutive_hits
                matched = False
                for finding in frame_findings:
                    if finding.tool != rule["tool"]:
                        continue
                    metric_value = finding.metrics.get(rule["metric"])
                    if isinstance(metric_value, bool) or not isinstance(
                        metric_value, (int, float)
                    ):
                        continue
                    try:
                        metric_value = float(metric_value)
                    except OverflowError:
                        continue
                    if not math.isfinite(metric_value):
                        continue
                    expected = rule["value"]
                    operator = rule["operator"]
                    if operator == "gt":
                        matched = metric_value > expected
                    elif operator == "gte":
                        matched = metric_value >= expected
                    elif operator == "lt":
                        matched = metric_value < expected
                    elif operator == "lte":
                        matched = metric_value <= expected
                    elif operator == "eq":
                        matched = metric_value == expected
                    if matched:
                        break
                consecutive_hits = consecutive_hits + 1 if matched else 0
                return consecutive_hits >= rule["min_consecutive"]

        def analyzer(frame: Frame, observation: ObservationRequest) -> Finding | list[Finding]:
            if run.cancel_event.is_set():
                return []
            tool = run.last_tool or self._first_tool_for_input(run.config.input.kind)
            params = copy.deepcopy(run.last_tool_params)
            handler = self.registry.resolve(tool)
            evidence_id, evidence_ref = self._save_evidence(run, frame)
            if run.cancel_event.is_set():
                return []
            fake_node = Node(node_id, "observe", {}, [])
            context = self._tool_context_for_region(
                run, fake_node, tool, source, observation.region
            )
            raw = handler(frame, params, context)
            if run.cancel_event.is_set():
                return []
            finding = self._normalize_finding(raw, tool, frame, evidence_ref, observation.region)
            self._record(
                run,
                {
                    "type": "frame_observed",
                    "node_id": node_id,
                    "state": "observing",
                    "evidence_id": evidence_id,
                },
            )
            return finding

        if self.observer is not None and stop_predicate is None:
            raw_findings = self.observer(
                source,
                copy.deepcopy(request),
                run.budget,
                run.cancel_event.is_set,
                self.clock,
            )
        else:
            raw_findings = self._default_observe(
                source,
                request,
                run,
                analyzer,
                stop_predicate=stop_predicate,
            )

        stop_reason = getattr(raw_findings, "stop_reason", None)
        frame_ids = getattr(raw_findings, "frame_ids", [])
        source_times = getattr(raw_findings, "source_times", [])
        if run.cancel_event.is_set():
            stop_reason = "cancelled"
        if stop_reason is not None:
            with run.lock:
                self._record_locked(
                    run,
                    {
                        "type": "monitor_stopped",
                        "node_id": node_id,
                        "source_id": source.source_id,
                        "reason": stop_reason,
                        "frame_ids": list(frame_ids),
                        "source_times": list(source_times),
                        "duration_seconds": request.duration_seconds,
                        "budget": run.budget.snapshot(),
                    },
                )
        if run.cancel_event.is_set():
            return []

        findings = self._normalize_observation_findings(raw_findings)
        if findings:
            with run.lock:
                if run.result.status not in _TERMINAL:
                    run.result.findings.extend(copy.deepcopy(findings))
        with run.lock:
            self._record_locked(
                run,
                {
                    "type": "observation_completed",
                    "node_id": node_id,
                    "state": "observing",
                    "budget": run.budget.snapshot(),
                },
            )
            if run.result.status == "observing":
                run.result.status = "running"
                self._record_locked(run, {"type": "state_change", "node_id": node_id, "state": "running"})
        return findings


    def _default_observe(
        self,
        source: Any,
        request: ObservationRequest,
        run: _Run,
        analyzer: Callable[[Frame, ObservationRequest], Finding | list[Finding]],
        stop_predicate: Callable[[list[Finding]], bool] | None = None,
    ) -> Any:
        try:
            from .observation import observe
        except ImportError as exc:
            raise ObservationRejected("observation support is unavailable") from exc
        return observe(
            source,
            copy.deepcopy(request),
            run.budget,
            run.cancel_event.is_set,
            self.clock,
            analyzer=analyzer,
            stop_predicate=stop_predicate,
        )

    @staticmethod
    def _normalize_observation_findings(raw: Any) -> list[Finding]:
        if raw is None:
            return []
        if isinstance(raw, Finding):
            return [raw]
        if isinstance(raw, (list, tuple)) and all(isinstance(item, Finding) for item in raw):
            return list(raw)
        if hasattr(raw, "findings"):
            findings = raw.findings
            if isinstance(findings, (list, tuple)) and all(isinstance(item, Finding) for item in findings):
                return list(findings)
        raise TypeError("observer must return Finding, a sequence of Findings, or None")

    def _first_tool_for_input(self, input_kind: str) -> str:
        for descriptor in self.registry.descriptors:
            if input_kind in descriptor.input_kinds:
                return descriptor.name
        raise ObservationRejected(f"no registered tool supports input kind {input_kind!r}")

    def _current_source_id(self, run: _Run) -> str:
        source = run.source
        if source is not None:
            return source.source_id
        if run.config.input.source:
            return run.config.input.source
        raise ObservationRejected("no active input source is available")

    def _ensure_source(self, run: _Run) -> Any:
        with run.lock:
            if run.result.status in _TERMINAL:
                raise InvalidTransition("cannot open media for a terminal run")
            if run.source is not None and not run.source_needs_reopen:
                return run.source
            if run.source is not None:
                old_source, run.source = run.source, None
            else:
                old_source = None
            run.source_needs_reopen = False
            input_config = copy.deepcopy(run.config.input)
            remaining = int(run.budget.remaining["frames"])
        self._close_source(old_source)
        if run.cancel_event.is_set():
            raise InvalidTransition("run was cancelled before opening media")
        if remaining <= 0:
            raise BudgetExceeded("run budget exceeded for: frames")
        if self.source_factory is None:
            from .media import open_source

            source = open_source(input_config, remaining)
        else:
            source = self.source_factory(input_config, remaining)
        if source is None or not callable(getattr(source, "read", None)) or not callable(getattr(source, "close", None)):
            raise MediaError("source factory must return an object with read() and close()")
        if not isinstance(getattr(source, "source_id", None), str) or not source.source_id:
            self._close_source(source)
            raise MediaError("source must expose a non-empty source_id")
        if run.cancel_event.is_set():
            self._close_source(source)
            raise InvalidTransition("run was cancelled while opening media")
        with run.lock:
            if run.result.status in _TERMINAL:
                cancel_source = True
            else:
                run.source = source
                cancel_source = False
        if cancel_source:
            self._close_source(source)
            raise InvalidTransition("run was cancelled while opening media")
        return source

    def _pause_for_input(self, run: _Run, reason: str, node_id: str, successors: list[str]) -> None:
        with run.lock:
            if run.result.status in _TERMINAL:
                return
            run.queue.extend(successors)
            run.result.pending_requests = [{"type": "request_input", "node_id": node_id, "reason": reason}]
            run.result.status = "waiting_for_input"
            self._record_locked(
                run,
                {"type": "state_change", "node_id": node_id, "state": "waiting_for_input"},
            )
        run.done_event.set()

    def _pause_for_approval(
        self,
        run: _Run,
        proposal: ActionProposal,
        node_id: str,
        successors: list[str],
        *,
        reason: str,
    ) -> None:
        with run.lock:
            if run.result.status in _TERMINAL:
                return
            run.queue.extend(successors)
            run.pending_proposal = copy.deepcopy(proposal)
            run.result.pending_requests = [
                {
                    "type": "request_approval",
                    "node_id": node_id,
                    "reason": reason,
                    "action_id": proposal.action_id,
                    "content_hash": proposal.content_hash,
                    "expires_at": proposal.expires_at.isoformat(),
                }
            ]
            run.result.status = "waiting_for_approval"
            self._record_locked(
                run,
                {"type": "state_change", "node_id": node_id, "state": "waiting_for_approval"},
            )
        run.done_event.set()

    def _prepare_notification(self, run: _Run) -> ActionProposal:
        try:
            from .outputs import prepare_notification
        except ImportError as exc:
            raise ApprovalRejected("notification draft support is unavailable") from exc
        proposal = prepare_notification(self._snapshot(run), now=self._wall_clock())
        store = self._get_approval_store()
        store.register(proposal)
        self._append_output(
            run,
            {
                "type": proposal.type,
                "action_id": proposal.action_id,
                "content_hash": proposal.content_hash,
                "content": proposal.content,
                "expires_at": proposal.expires_at.isoformat(),
                "sent": False,
            },
        )
        self._record(run, {"type": "approval_prepared", "state": "pending"})
        return proposal

    def _get_approval_store(self) -> Any:
        with self._approval_lock:
            if self.approval_store is None:
                try:
                    from .approval import ApprovalStore
                except ImportError as exc:
                    raise ApprovalRejected("approval support is unavailable") from exc
                self.approval_store = ApprovalStore()
            return self.approval_store

    def _process_outputs(self, run: _Run) -> bool:
        while run.output_index < len(run.config.outputs):
            if run.cancel_event.is_set():
                return True
            output_config = run.config.outputs[run.output_index]
            run.output_index += 1
            if self._process_output_type(
                run,
                output_config.type,
                requires_approval=output_config.requires_approval,
                node_id="outputs",
            ):
                return True
        return False

    def _process_output_type(
        self,
        run: _Run,
        output_type: str,
        *,
        requires_approval: bool,
        node_id: str,
    ) -> bool:
        if output_type == "display":
            self._append_output(
                run,
                {"type": "display", "findings": [self._finding_dict(item) for item in self._findings(run)]},
            )
            return False
        if output_type in {"markdown", "json", "report"}:
            self._add_report(run, "json" if output_type == "json" else "markdown")
            return False
        if output_type == "notification_draft":
            proposal = self._prepare_notification(run)
            if requires_approval:
                self._pause_for_approval(run, proposal, node_id, [], reason="Approval required for notification draft")
                return True
            return False
        raise ConfigError(f"unsupported workflow output type: {output_type!r}")

    def _add_report(self, run: _Run, report_format: str) -> None:
        from .outputs import report_snapshot

        snapshot = report_snapshot(self._snapshot(run))
        if report_format == "markdown":
            from .outputs import render_report

            content = render_report(snapshot)
        elif report_format == "json":
            content = json.dumps(
                self._json_safe(asdict(snapshot)),
                ensure_ascii=False,
                sort_keys=True,
                indent=2,
                allow_nan=False,
            )
        else:
            raise ConfigError(f"unsupported report format: {report_format!r}")
        self._append_output(run, {"type": "report", "format": report_format, "content": content})
        self._record(run, {"type": "report_created", "state": report_format})

    @staticmethod
    def _json_safe(value: Any) -> Any:
        if is_dataclass(value) and not isinstance(value, type):
            return Controller._json_safe(asdict(value))
        if isinstance(value, dict):
            return {str(key): Controller._json_safe(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [Controller._json_safe(item) for item in value]
        if isinstance(value, datetime):
            return value.isoformat()
        if value is None or type(value) in (str, int, float, bool):
            return value
        raise TypeError(f"value of type {type(value).__name__} is not JSON serializable")

    @staticmethod
    def _finding_dict(finding: Finding) -> dict[str, Any]:
        return {
            "tool": finding.tool,
            "frame_ids": copy.deepcopy(finding.frame_ids),
            "region": copy.deepcopy(finding.region),
            "metrics": copy.deepcopy(finding.metrics),
            "evidence_ref": finding.evidence_ref,
        }

    def _append_output(self, run: _Run, output: dict[str, Any]) -> None:
        with run.lock:
            if run.result.status not in _TERMINAL:
                run.result.outputs.append(copy.deepcopy(output))

    def _findings(self, run: _Run) -> list[Finding]:
        with run.lock:
            return copy.deepcopy(run.result.findings)

    def _is_waiting(self, run: _Run) -> bool:
        with run.lock:
            return run.result.status in _WAITING

    def _current_status(self, run: _Run) -> str:
        with run.lock:
            return run.result.status

    def _finish(self, run: _Run) -> None:
        source = None
        with run.lock:
            if run.result.status in _TERMINAL:
                return
            run.result.status = "completed"
            run.result.pending_requests.clear()
            source, run.source = run.source, None
            run.image_frame = None
            self._record_locked(run, {"type": "state_change", "state": "completed"})
            run.done_event.set()
        self._close_source(source)

    def _fail(self, run: _Run, exc: Exception) -> None:
        source = None
        with run.lock:
            if run.result.status in _TERMINAL:
                return
            run.result.status = "failed"
            run.result.pending_requests.clear()
            run.pending_proposal = None
            source, run.source = run.source, None
            run.image_frame = None
            self._record_locked(
                run,
                {"type": "state_change", "state": "failed", "error_type": type(exc).__name__},
            )
            run.done_event.set()
        self._close_source(source)

    def _snapshot(self, run: _Run) -> RunResult:
        with run.lock:
            run.result.trace = run.recorder.export()
            return copy.deepcopy(run.result)

    def _record(self, run: _Run, event: dict[str, Any]) -> None:
        with run.lock:
            self._record_locked(run, event)

    @staticmethod
    def _record_locked(run: _Run, event: dict[str, Any]) -> None:
        run.recorder.append(event)
        run.result.trace = run.recorder.export()

    def _get_run(self, run_id: str) -> _Run:
        if not isinstance(run_id, str):
            raise UnknownRun(str(run_id))
        with self._runs_lock:
            run = self._runs.get(run_id)
        if run is None:
            raise UnknownRun(run_id)
        return run

    def _wall_clock(self) -> datetime:
        now = getattr(self.clock, "now", None)
        if callable(now):
            value = now()
            if isinstance(value, datetime):
                return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc)

    @staticmethod
    def _close_source(source: Any) -> None:
        if source is None:
            return
        close = getattr(source, "close", None)
        if callable(close):
            try:
                close()
            except Exception:
                # Cleanup failure must not revive or overwrite a terminal run state.
                pass


__all__ = ["Controller"]
