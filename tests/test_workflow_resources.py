"""Bundled workflow compatibility, without depending on a checkout for loading."""
import json
from importlib.resources import files
from pathlib import Path

import pytest

from visionflow.runtime import create_registry
from visionflow.workflow_resources import BUILTIN_WORKFLOWS, load_builtin_workflow


@pytest.mark.parametrize("name", BUILTIN_WORKFLOWS)
def test_packaged_workflows_load_and_match_documented_examples(name):
    resource = files("visionflow").joinpath("workflows", name)
    example = Path(__file__).resolve().parents[1] / "examples" / "workflows" / name
    assert json.loads(resource.read_text(encoding="utf-8")) == json.loads(example.read_text(encoding="utf-8"))
    workflow = load_builtin_workflow(name, create_registry())
    assert workflow.schema_version == 1 and workflow.nodes
    assert workflow.name and workflow.input.source


@pytest.mark.parametrize("name", ["../road-review.json", "missing.json"])
def test_unknown_builtin_workflow_is_rejected(name):
    with pytest.raises(ValueError, match="unknown built-in"):
        load_builtin_workflow(name, create_registry())
