"""Installed-package access to the bundled, validated example workflows."""
from __future__ import annotations

from importlib.resources import as_file, files

from .config import load_workflow
from .contracts import WorkflowConfig
from .registry import ToolRegistry

BUILTIN_WORKFLOWS = (
    "direction-review.json",
    "document-quality.json",
    "motion-observation.json",
    "road-monitor.json",
    "road-review.json",
)


def load_builtin_workflow(name: str, registry: ToolRegistry) -> WorkflowConfig:
    """Load one known workflow from source checkout or installed wheel alike."""
    if name not in BUILTIN_WORKFLOWS:
        raise ValueError("unknown built-in workflow")
    resource = files("visionflow").joinpath("workflows", name)
    with as_file(resource) as path:
        return load_workflow(path, registry)
