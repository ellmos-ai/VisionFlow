"""Small reusable workflow fixtures for tests."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

from visionflow.registry import ToolDescriptor, ToolRegistry


def valid_config() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "name": "example-workflow",
        "input": {"kind": "image", "source": "fixture-image.png"},
        "limits": {
            "max_observation_seconds": 300,
            "max_frames": 300,
            "max_llm_calls": 4,
        },
        "nodes": [
            {
                "id": "analyze",
                "type": "analyze",
                "params": {"tool": "quality", "tool_params": {}},
                "next": ["report"],
            },
            {
                "id": "report",
                "type": "report",
                "params": {"format": "markdown"},
                "next": [],
            },
        ],
        "outputs": [{"type": "display", "requires_approval": False}],
    }


def write_config(
    tmp_path: Path,
    config: dict[str, Any] | None = None,
    **overrides: Any,
) -> Path:
    data = copy.deepcopy(config if config is not None else valid_config())
    for key, value in overrides.items():
        if key == "tool":
            data["nodes"][0]["params"]["tool"] = value
        elif key in {"max_observation_seconds", "max_frames", "max_llm_calls"}:
            data.setdefault("limits", {})[key] = value
        else:
            data[key] = value
    path = tmp_path / "workflow.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def example_registry() -> ToolRegistry:
    registry = ToolRegistry()
    for name, kinds in (
        ("quality", ("image",)),
        ("motion", ("video", "camera")),
        ("direction", ("video",)),
    ):
        registry.register(
            ToolDescriptor(name=name, input_kinds=kinds),
            lambda frame, _name=name: {"tool": _name, "frame": frame},
        )
    return registry
