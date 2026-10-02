"""Load and strictly validate VisionFlow workflow JSON documents."""

from __future__ import annotations

import json
import math
from copy import deepcopy
from pathlib import Path
from typing import Any

from .contracts import (
    ConfigError,
    InputConfig,
    Limits,
    Node,
    OutputConfig,
    Region,
    WorkflowConfig,
)
from .registry import ToolRegistry


_INPUT_KINDS = frozenset({"image", "video", "camera"})
_NODE_PARAMS: dict[str, tuple[frozenset[str], frozenset[str]]] = {
    "analyze": (frozenset({"tool"}), frozenset({"tool_params"})),
    "branch": (
        frozenset(
            {"finding_tool", "metric", "operator", "value", "then_node", "else_node"}
        ),
        frozenset(),
    ),
    "ask_model": (frozenset({"question"}), frozenset()),
    "observe": (
        frozenset({"duration_seconds", "sample_fps"}),
        frozenset({"region"}),
    ),
    "request_input": (frozenset({"reason"}), frozenset()),
    "report": (frozenset({"format"}), frozenset()),
    "approve": (frozenset({"action_type"}), frozenset()),
    "output": (frozenset({"output_type"}), frozenset()),
}
_LIMIT_FIELDS = (
    "max_observation_seconds",
    "max_frames",
    "max_llm_calls",
)


def load_workflow(path: Path, registry: ToolRegistry) -> WorkflowConfig:
    """Read a JSON workflow file and validate it against the given tool registry."""
    try:
        config_path = Path(path)
        text = config_path.read_text(encoding="utf-8")
        data = json.loads(text, parse_constant=_reject_json_constant)
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
        raise ConfigError(f"could not read workflow configuration: {exc}") from exc
    return validate_workflow(data, registry)


def validate_workflow(data: dict[str, Any], registry: ToolRegistry) -> WorkflowConfig:
    """Validate already parsed workflow data without accessing its media source."""
    if not isinstance(data, dict):
        raise ConfigError("workflow configuration must be a JSON object")
    if not isinstance(registry, ToolRegistry):
        raise ConfigError("registry must be a ToolRegistry")
    _validate_finite_tree(data, "workflow")
    _check_keys(
        data,
        allowed={"schema_version", "name", "input", "limits", "nodes", "outputs"},
        required={"schema_version", "name", "input", "nodes", "outputs"},
        path="workflow",
    )

    version = data["schema_version"]
    if type(version) is not int or version != 1:
        raise ConfigError("schema_version must be the integer 1")
    name = _non_empty_string(data["name"], "workflow.name")

    input_data = data["input"]
    _check_keys(
        input_data,
        allowed={"kind", "source"},
        required={"kind", "source"},
        path="workflow.input",
    )
    input_kind = _non_empty_string(input_data["kind"], "workflow.input.kind")
    if input_kind not in _INPUT_KINDS:
        raise ConfigError(f"unsupported input kind: {input_kind}")
    input_config = InputConfig(
        kind=input_kind,
        source=_non_empty_string(input_data["source"], "workflow.input.source"),
    )

    limits = _parse_limits(data.get("limits", {}))
    nodes = _parse_nodes(data["nodes"], input_kind, registry)
    outputs = _parse_outputs(data["outputs"])
    _validate_references_and_cycles(nodes)

    return WorkflowConfig(
        schema_version=version,
        name=name,
        input=input_config,
        limits=limits,
        nodes=nodes,
        outputs=outputs,
    )


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON number {value}")


def _validate_finite_tree(value: Any, path: str) -> None:
    if isinstance(value, bool) or value is None or isinstance(value, (str, int)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ConfigError(f"{path} contains a non-finite number")
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _validate_finite_tree(item, f"{path}[{index}]")
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise ConfigError(f"{path} contains a non-string object key")
            _validate_finite_tree(item, f"{path}.{key}")
        return
    raise ConfigError(f"{path} contains a value that is not JSON-compatible")


def _check_keys(
    value: Any,
    *,
    allowed: set[str] | frozenset[str],
    required: set[str] | frozenset[str],
    path: str,
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ConfigError(f"{path} must be an object")
    unknown = set(value) - set(allowed)
    if unknown:
        names = ", ".join(sorted(str(key) for key in unknown))
        raise ConfigError(f"{path} contains unknown keys: {names}")
    missing = set(required) - set(value)
    if missing:
        names = ", ".join(sorted(missing))
        raise ConfigError(f"{path} is missing required keys: {names}")
    return value


def _non_empty_string(value: Any, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{path} must be a non-empty string")
    return value


def _parse_limits(data: Any) -> Limits:
    _check_keys(
        data,
        allowed=set(_LIMIT_FIELDS),
        required=set(),
        path="workflow.limits",
    )
    values: dict[str, int] = {}
    for field in _LIMIT_FIELDS:
        value = data.get(field, getattr(Limits(), field))
        if type(value) is not int or value <= 0:
            raise ConfigError(f"workflow.limits.{field} must be a positive integer")
        values[field] = value
    return Limits(**values)


def _parse_nodes(data: Any, input_kind: str, registry: ToolRegistry) -> list[Node]:
    if not isinstance(data, list) or not data:
        raise ConfigError("workflow.nodes must be a non-empty array")
    nodes: list[Node] = []
    seen_ids: set[str] = set()
    for index, value in enumerate(data):
        path = f"workflow.nodes[{index}]"
        _check_keys(
            value,
            allowed={"id", "type", "params", "next"},
            required={"id", "type", "params"},
            path=path,
        )
        node_id = _non_empty_string(value["id"], f"{path}.id")
        if node_id in seen_ids:
            raise ConfigError(f"duplicate node id: {node_id}")
        seen_ids.add(node_id)

        node_type = _non_empty_string(value["type"], f"{path}.type")
        if node_type not in _NODE_PARAMS:
            raise ConfigError(f"unsupported node type: {node_type}")
        params = _parse_node_params(
            node_type,
            value["params"],
            input_kind,
            registry,
            f"{path}.params",
        )

        next_data = value.get("next")
        if next_data is not None:
            if not isinstance(next_data, list):
                raise ConfigError(f"{path}.next must be an array of node IDs or null")
            next_ids = [
                _non_empty_string(target, f"{path}.next[{target_index}]")
                for target_index, target in enumerate(next_data)
            ]
        else:
            next_ids = None
        nodes.append(Node(id=node_id, type=node_type, params=params, next=next_ids))
    return nodes


def _parse_node_params(
    node_type: str,
    raw_params: Any,
    input_kind: str,
    registry: ToolRegistry,
    path: str,
) -> dict[str, Any]:
    required, optional = _NODE_PARAMS[node_type]
    params = _check_keys(
        raw_params,
        allowed=required | optional,
        required=required,
        path=path,
    )
    parsed = deepcopy(params)

    if node_type == "analyze":
        tool = _non_empty_string(parsed["tool"], f"{path}.tool")
        _check_registered_tool(tool, input_kind, registry, f"{path}.tool")
        if "tool_params" not in parsed:
            parsed["tool_params"] = {}
        if not isinstance(parsed["tool_params"], dict):
            raise ConfigError(f"{path}.tool_params must be an object")
    elif node_type == "branch":
        tool = _non_empty_string(parsed["finding_tool"], f"{path}.finding_tool")
        _check_registered_tool(tool, input_kind, registry, f"{path}.finding_tool")
        _non_empty_string(parsed["metric"], f"{path}.metric")
        operator = _non_empty_string(parsed["operator"], f"{path}.operator")
        if operator not in {"gt", "gte", "lt", "lte", "eq"}:
            raise ConfigError(f"{path}.operator must be gt, gte, lt, lte, or eq")
        value = parsed["value"]
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ConfigError(f"{path}.value must be a finite number")
        if isinstance(value, float) and not math.isfinite(value):
            raise ConfigError(f"{path}.value must be a finite number")
        _non_empty_string(parsed["then_node"], f"{path}.then_node")
        _non_empty_string(parsed["else_node"], f"{path}.else_node")
    elif node_type == "ask_model":
        _non_empty_string(parsed["question"], f"{path}.question")
    elif node_type == "observe":
        for field in ("duration_seconds", "sample_fps"):
            _positive_finite_number(parsed[field], f"{path}.{field}")
        parsed["region"] = _parse_region(parsed.get("region"), f"{path}.region")
    elif node_type == "request_input":
        _non_empty_string(parsed["reason"], f"{path}.reason")
    elif node_type == "report":
        report_format = _non_empty_string(parsed["format"], f"{path}.format")
        if report_format not in {"markdown", "json"}:
            raise ConfigError(f"{path}.format must be markdown or json")
    elif node_type == "approve":
        _non_empty_string(parsed["action_type"], f"{path}.action_type")
    elif node_type == "output":
        _non_empty_string(parsed["output_type"], f"{path}.output_type")
    return parsed


def _positive_finite_number(value: Any, path: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigError(f"{path} must be a positive finite number")
    if isinstance(value, float) and not math.isfinite(value):
        raise ConfigError(f"{path} must be a positive finite number")
    if value <= 0:
        raise ConfigError(f"{path} must be a positive finite number")
    try:
        numeric = float(value)
    except OverflowError as exc:
        raise ConfigError(f"{path} must be a positive finite number") from exc
    if not math.isfinite(numeric):
        raise ConfigError(f"{path} must be a positive finite number")
    return numeric


def _parse_region(value: Any, path: str) -> Region:
    if value is None:
        return None
    if not isinstance(value, list) or len(value) != 4:
        raise ConfigError(f"{path} must be null or a four-number box [x, y, width, height]")
    coordinates = tuple(
        _finite_number(coordinate, f"{path}[{index}]")
        for index, coordinate in enumerate(value)
    )
    if coordinates[2] <= 0 or coordinates[3] <= 0:
        raise ConfigError(f"{path} box width and height must be positive")
    return coordinates  # type: ignore[return-value]


def _finite_number(value: Any, path: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigError(f"{path} must be a finite number")
    try:
        numeric = float(value)
    except OverflowError as exc:
        raise ConfigError(f"{path} must be a finite number") from exc
    if not math.isfinite(numeric):
        raise ConfigError(f"{path} must be a finite number")
    return numeric


def _check_registered_tool(
    name: str,
    input_kind: str,
    registry: ToolRegistry,
    path: str,
) -> None:
    try:
        descriptor = registry.descriptor(name)
    except KeyError as exc:
        raise ConfigError(f"unknown tool: {name}") from exc
    if input_kind not in descriptor.input_kinds:
        raise ConfigError(
            f"tool {name!r} does not support workflow input kind {input_kind!r} at {path}"
        )


def _parse_outputs(data: Any) -> list[OutputConfig]:
    if not isinstance(data, list):
        raise ConfigError("workflow.outputs must be an array")
    outputs: list[OutputConfig] = []
    for index, value in enumerate(data):
        path = f"workflow.outputs[{index}]"
        _check_keys(
            value,
            allowed={"type", "requires_approval"},
            required={"type"},
            path=path,
        )
        approval = value.get("requires_approval", False)
        if not isinstance(approval, bool):
            raise ConfigError(f"{path}.requires_approval must be a boolean")
        outputs.append(
            OutputConfig(
                type=_non_empty_string(value["type"], f"{path}.type"),
                requires_approval=approval,
            )
        )
    return outputs


def _validate_references_and_cycles(nodes: list[Node]) -> None:
    by_id = {node.id: node for node in nodes}
    edges: dict[str, set[str]] = {}
    for node in nodes:
        destinations = set(node.next or [])
        if node.type == "branch":
            destinations.update(
                {node.params["then_node"], node.params["else_node"]}
            )
        missing = destinations - by_id.keys()
        if missing:
            target_list = ", ".join(sorted(missing))
            raise ConfigError(f"node {node.id!r} references unknown node(s): {target_list}")
        edges[node.id] = destinations

    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node_id: str) -> None:
        if node_id in visiting:
            raise ConfigError(f"static cycle detected at node {node_id!r}")
        if node_id in visited:
            return
        visiting.add(node_id)
        for target in edges[node_id]:
            visit(target)
        visiting.remove(node_id)
        visited.add(node_id)

    for node in nodes:
        visit(node.id)


__all__ = ["load_workflow", "validate_workflow"]
