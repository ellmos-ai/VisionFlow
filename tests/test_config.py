from __future__ import annotations

from datetime import datetime, timezone

import pytest

from visionflow.config import load_workflow, validate_workflow
from visionflow.contracts import (
    ActionProposal,
    ApprovalReceipt,
    ConfigError,
    Finding,
    Frame,
    InputConfig,
    Limits,
    ModelDecision,
    Node,
    ObservationRequest,
    OutputConfig,
    RunResult,
    WorkflowConfig,
)
from helpers import example_registry, valid_config, write_config


def test_loads_workflow_and_applies_default_limits(tmp_path):
    data = valid_config()
    del data["limits"]

    workflow = load_workflow(write_config(tmp_path, data), example_registry())

    assert workflow.schema_version == 1
    assert workflow.name == "example-workflow"
    assert workflow.input.kind == "image"
    assert workflow.limits == Limits(300, 300, 4)
    assert workflow.nodes[0].next == ["report"]


def test_source_is_retained_as_data_without_opening_it(tmp_path):
    data = valid_config()
    data["input"] = {"kind": "camera", "source": "camera-7"}
    data["nodes"] = [
        {"id": "report", "type": "report", "params": {"format": "json"}, "next": []}
    ]

    workflow = load_workflow(write_config(tmp_path, data), example_registry())

    assert workflow.input.source == "camera-7"


def test_validate_workflow_accepts_data_without_a_temporary_file():
    workflow = validate_workflow(valid_config(), example_registry())

    assert workflow.name == "example-workflow"


def test_validate_workflow_rejects_non_mapping_input():
    with pytest.raises(ConfigError):
        validate_workflow([], example_registry())


def test_unknown_tool_is_rejected(tmp_path):
    with pytest.raises(ConfigError, match="unknown tool"):
        load_workflow(write_config(tmp_path, tool="missing"), example_registry())


def test_tool_must_support_workflow_input_kind(tmp_path):
    data = valid_config()
    data["input"]["kind"] = "video"

    with pytest.raises(ConfigError, match="input kind|video"):
        load_workflow(write_config(tmp_path, data), example_registry())


@pytest.mark.parametrize(
    "mutate",
    [
        lambda data: data.update(extra=True),
        lambda data: data["input"].update(extra=True),
        lambda data: data["limits"].update(extra=True),
        lambda data: data["nodes"][0]["params"].update(extra=True),
        lambda data: data["outputs"][0].update(extra=True),
    ],
)
def test_unknown_configuration_keys_are_rejected(tmp_path, mutate):
    data = valid_config()
    mutate(data)

    with pytest.raises(ConfigError, match="unknown|unexpected|unsupported"):
        load_workflow(write_config(tmp_path, data), example_registry())


@pytest.mark.parametrize("kind", ["audio", "", None, 1])
def test_unsupported_input_kind_is_rejected(tmp_path, kind):
    data = valid_config()
    data["input"]["kind"] = kind

    with pytest.raises(ConfigError):
        load_workflow(write_config(tmp_path, data), example_registry())


@pytest.mark.parametrize("version", [0, 2, True, "1", 1.0])
def test_unsupported_or_mistyped_schema_version_is_rejected(tmp_path, version):
    data = valid_config()
    data["schema_version"] = version

    with pytest.raises(ConfigError):
        load_workflow(write_config(tmp_path, data), example_registry())


@pytest.mark.parametrize("field", ["max_observation_seconds", "max_frames", "max_llm_calls"])
@pytest.mark.parametrize("value", [0, -1, True, "300", 1.5])
def test_limits_must_be_positive_integers_and_reject_bool(tmp_path, field, value):
    with pytest.raises(ConfigError):
        load_workflow(write_config(tmp_path, **{field: value}), example_registry())


@pytest.mark.parametrize("bad_nodes", [
    [
        {"id": "same", "type": "report", "params": {"format": "json"}, "next": []},
        {"id": "same", "type": "report", "params": {"format": "json"}, "next": []},
    ],
    [
        {"id": "only", "type": "report", "params": {"format": "json"}, "next": ["missing"]},
    ],
    [
        {"id": "first", "type": "report", "params": {"format": "json"}, "next": ["second"]},
        {"id": "second", "type": "report", "params": {"format": "json"}, "next": ["first"]},
    ],
])
def test_duplicate_ids_dangling_edges_and_static_cycles_are_rejected(tmp_path, bad_nodes):
    data = valid_config()
    data["nodes"] = bad_nodes

    with pytest.raises(ConfigError):
        load_workflow(write_config(tmp_path, data), example_registry())


def test_static_cycle_through_branch_destinations_is_rejected(tmp_path):
    data = valid_config()
    data["nodes"] = [
        {
            "id": "choose",
            "type": "branch",
            "params": {
                "finding_tool": "quality",
                "metric": "sharpness",
                "operator": "gt",
                "value": 1,
                "then_node": "report",
                "else_node": "report",
            },
            "next": [],
        },
        {"id": "report", "type": "report", "params": {"format": "json"}, "next": ["choose"]},
    ]

    with pytest.raises(ConfigError, match="cycle"):
        load_workflow(write_config(tmp_path, data), example_registry())


def test_branch_operator_is_restricted_to_specified_comparisons(tmp_path):
    data = valid_config()
    data["nodes"] = [
        {
            "id": "choose",
            "type": "branch",
            "params": {
                "finding_tool": "quality",
                "metric": "sharpness",
                "operator": "contains",
                "value": 1,
                "then_node": "report",
                "else_node": "report",
            },
            "next": [],
        },
        {"id": "report", "type": "report", "params": {"format": "json"}, "next": []},
    ]

    with pytest.raises(ConfigError):
        load_workflow(write_config(tmp_path, data), example_registry())


@pytest.mark.parametrize("destination", ["missing", None, 3])
def test_branch_destinations_must_reference_existing_nodes(tmp_path, destination):
    data = valid_config()
    data["nodes"] = [
        {
            "id": "choose",
            "type": "branch",
            "params": {
                "finding_tool": "quality",
                "metric": "sharpness",
                "operator": "gt",
                "value": 1,
                "then_node": destination,
                "else_node": "report",
            },
            "next": [],
        },
        {"id": "report", "type": "report", "params": {"format": "json"}, "next": []},
    ]

    with pytest.raises(ConfigError):
        load_workflow(write_config(tmp_path, data), example_registry())


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf"), True, "1"])
def test_branch_comparison_value_must_be_finite_numeric_data(tmp_path, value):
    data = valid_config()
    data["nodes"] = [
        {
            "id": "choose",
            "type": "branch",
            "params": {
                "finding_tool": "quality",
                "metric": "sharpness",
                "operator": "gt",
                "value": value,
                "then_node": "report",
                "else_node": "report",
            },
            "next": [],
        },
        {"id": "report", "type": "report", "params": {"format": "json"}, "next": []},
    ]

    with pytest.raises(ConfigError):
        load_workflow(write_config(tmp_path, data), example_registry())


@pytest.mark.parametrize("field,value", [
    ("duration_seconds", float("nan")),
    ("duration_seconds", float("inf")),
    ("duration_seconds", True),
    ("sample_fps", float("nan")),
    ("sample_fps", 0),
])
def test_observation_values_must_be_finite_and_positive(tmp_path, field, value):
    data = valid_config()
    data["nodes"] = [
        {
            "id": "observe",
            "type": "observe",
            "params": {"duration_seconds": 5, "sample_fps": 1, "region": None},
            "next": [],
        }
    ]
    data["nodes"][0]["params"][field] = value

    with pytest.raises(ConfigError):
        load_workflow(write_config(tmp_path, data), example_registry())


def test_observation_region_is_normalized_to_a_finite_box(tmp_path):
    data = valid_config()
    data["nodes"] = [
        {
            "id": "observe",
            "type": "observe",
            "params": {
                "duration_seconds": 5,
                "sample_fps": 1,
                "region": [1, 2, 10, 20],
            },
            "next": [],
        }
    ]

    workflow = load_workflow(write_config(tmp_path, data), example_registry())

    assert workflow.nodes[0].params["region"] == (1.0, 2.0, 10.0, 20.0)


@pytest.mark.parametrize(
    "region",
    [[], [1, 2, 3], [1, 2, 0, 4], [1, 2, 3, -1], [True, 2, 3, 4], [1, 2, float("inf"), 4]],
)
def test_observation_region_rejects_invalid_boxes(tmp_path, region):
    data = valid_config()
    data["nodes"] = [
        {
            "id": "observe",
            "type": "observe",
            "params": {"duration_seconds": 5, "sample_fps": 1, "region": region},
            "next": [],
        }
    ]

    with pytest.raises(ConfigError):
        load_workflow(write_config(tmp_path, data), example_registry())


def test_config_file_with_invalid_json_is_reported_as_config_error(tmp_path):
    path = tmp_path / "broken.json"
    path.write_text("{broken", encoding="utf-8")

    with pytest.raises(ConfigError, match="JSON|configuration"):
        load_workflow(path, example_registry())


def test_missing_configuration_file_is_reported_as_config_error(tmp_path):
    with pytest.raises(ConfigError):
        load_workflow(tmp_path / "missing.json", example_registry())


def test_shared_contracts_have_predictable_defaults_and_no_numpy_requirement():
    image = object()
    frame = Frame("frame-1", image)
    decision = ModelDecision("report")
    first_result = RunResult("run-1", "queued")
    second_result = RunResult("run-2", "queued")
    proposal = ActionProposal(
        "notification_draft",
        "run-1",
        "action-1",
        "draft",
        "sha256:hash",
        datetime(2026, 10, 2, tzinfo=timezone.utc),
    )
    receipt = ApprovalReceipt(
        "receipt-1",
        "run-1",
        "action-1",
        "sha256:hash",
        datetime(2026, 10, 2, tzinfo=timezone.utc),
    )

    assert frame.image is image
    assert frame.timestamp_seconds == 0
    assert decision.payload == {}
    decision.payload["result"] = "ok"
    assert ModelDecision("report").payload == {}
    first_result.findings.append(Finding("quality", [], None, {}, "evidence/frame-1"))
    assert second_result.findings == []
    assert Node("report", "report", {"format": "json"}).next is None
    assert OutputConfig("display").requires_approval is False
    assert InputConfig("camera", "camera-1").source == "camera-1"
    assert ObservationRequest("camera-1", None, 1, 1, "inspect").region is None
    assert WorkflowConfig(1, "empty", InputConfig("image", "fixture"), Limits(), [], []).nodes == []
    assert proposal.sent is False
    assert proposal.destination is None
    assert receipt.receipt_id == "receipt-1"


def test_package_exports_registry_public_types():
    from visionflow import ToolDescriptor, ToolRegistry

    assert ToolDescriptor.__name__ == "ToolDescriptor"
    assert ToolRegistry.__name__ == "ToolRegistry"



def _watch_data(stop_when):
    data = valid_config()
    data["input"] = {"kind": "video", "source": "camera.avi"}
    data["nodes"] = [
        {
            "id": "direction",
            "type": "analyze",
            "params": {"tool": "direction", "tool_params": {}},
            "next": ["watch"],
        },
        {
            "id": "watch",
            "type": "observe",
            "params": {
                "duration_seconds": 5,
                "sample_fps": 2,
                "region": None,
                "stop_when": stop_when,
            },
            "next": ["report"],
        },
        {"id": "report", "type": "report", "params": {"format": "json"}, "next": []},
    ]
    return data


def test_stop_when_is_optional_for_existing_workflows():
    workflow = validate_workflow(valid_config(), example_registry())

    assert workflow.nodes[0].type == "analyze"


def test_stop_when_accepts_registered_tool_and_normalizes_threshold():
    workflow = validate_workflow(
        _watch_data(
            {
                "tool": "direction",
                "metric": "opposition_count",
                "operator": "gte",
                "value": 1,
                "min_consecutive": 2,
            }
        ),
        example_registry(),
    )

    assert workflow.nodes[1].params["stop_when"] == {
        "tool": "direction",
        "metric": "opposition_count",
        "operator": "gte",
        "value": 1.0,
        "min_consecutive": 2,
    }


def test_stop_when_rejects_unknown_or_input_incompatible_tool():
    with pytest.raises(ConfigError, match="unknown tool"):
        validate_workflow(
            _watch_data(
                {
                    "tool": "missing",
                    "metric": "opposition_count",
                    "operator": "gte",
                    "value": 1,
                    "min_consecutive": 1,
                }
            ),
            example_registry(),
        )


@pytest.mark.parametrize("value", [True, float("nan"), float("inf"), "1"])
def test_stop_when_rejects_bool_nonfinite_and_nonnumeric_thresholds(value):
    with pytest.raises(ConfigError):
        validate_workflow(
            _watch_data(
                {
                    "tool": "direction",
                    "metric": "opposition_count",
                    "operator": "gte",
                    "value": value,
                    "min_consecutive": 1,
                }
            ),
            example_registry(),
        )


@pytest.mark.parametrize(
    "bad_rule",
    [
        {"tool": "direction", "metric": "", "operator": "gte", "value": 1, "min_consecutive": 1},
        {"tool": "direction", "metric": "opposition_count", "operator": "contains", "value": 1, "min_consecutive": 1},
        {"tool": "direction", "metric": "opposition_count", "operator": "gte", "value": 1, "min_consecutive": True},
        {"tool": "direction", "metric": "opposition_count", "operator": "gte", "value": 1, "min_consecutive": 0},
        {"tool": "direction", "metric": "opposition_count", "operator": "gte", "value": 1},
        {"tool": "direction", "metric": "opposition_count", "operator": "gte", "value": 1, "min_consecutive": 1, "extra": 0},
    ],
)
def test_stop_when_requires_exact_known_fields_and_valid_consecutive_count(bad_rule):
    with pytest.raises(ConfigError):
        validate_workflow(_watch_data(bad_rule), example_registry())


@pytest.mark.parametrize("metric", ["_private", "nested.metric", "has space", "class"])
def test_stop_when_metric_must_be_a_public_structural_identifier(metric):
    with pytest.raises(ConfigError):
        validate_workflow(
            _watch_data(
                {
                    "tool": "direction",
                    "metric": metric,
                    "operator": "gte",
                    "value": 1,
                    "min_consecutive": 1,
                }
            ),
            example_registry(),
        )


def test_stop_when_rejects_registered_tool_that_does_not_support_video():
    with pytest.raises(ConfigError, match="does not support"):
        validate_workflow(
            _watch_data(
                {
                    "tool": "quality",
                    "metric": "sharpness_variance",
                    "operator": "gte",
                    "value": 1,
                    "min_consecutive": 1,
                }
            ),
            example_registry(),
        )
