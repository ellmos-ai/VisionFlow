"""Native worker cleanup and failure diagnostics; no AWS calls or real models."""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from visionflow.contracts import Frame, InputConfig, Limits, Node, WorkflowConfig
from visionflow.controller import Controller
from visionflow.demo import _wait_for_status
from visionflow.registry import ToolDescriptor, ToolRegistry


def _module(relative):
    path = Path(__file__).resolve().parents[1] / relative
    spec = importlib.util.spec_from_file_location("cloud_cleanup_" + path.stem, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _closing_run(tmp_path):
    closing, release, closed = (threading.Event() for _ in range(3))
    class Source:
        source_id = "fixture-image"
        def read(self):
            return Frame("fixture-image:0", b"fixture pixels")
        def close(self):
            closing.set()
            if not release.wait(timeout=3):
                raise TimeoutError("test cleanup was not released")
            closed.set()
    registry = ToolRegistry()
    registry.register(ToolDescriptor("quality"), lambda *args: {"score": 1})
    controller = Controller(registry, source_factory=lambda *args: Source(), evidence_root=tmp_path)
    config = WorkflowConfig(schema_version=1, name="cleanup-fixture", limits=Limits(),
                            input=InputConfig("image", "fixture-image"),
                            nodes=[Node("analyze", "analyze", {"tool": "quality", "tool_params": {}}, [])],
                            outputs=[])
    run_id = controller.start(config).run_id
    assert closing.wait(timeout=2)
    return controller, run_id, release, closed


def test_demo_terminal_wait_does_not_return_while_native_cleanup_is_active(tmp_path):
    controller, run_id, release, closed = _closing_run(tmp_path)
    returned = threading.Event()
    errors = []
    def wait():
        try:
            result = _wait_for_status(controller, run_id, "completed")
            assert result.status == "completed"
        except (AssertionError, RuntimeError, TimeoutError, AttributeError) as exc:
            errors.append(exc)
        finally:
            returned.set()
    waiter = threading.Thread(target=wait)
    waiter.start()
    try:
        # This is the real race: public terminal status precedes source.close.
        assert controller.status(run_id).status == "completed"
        assert not closed.is_set()
        assert not returned.wait(timeout=.15)
        with pytest.raises(TimeoutError, match="cleanup"):
            controller.join(run_id, timeout=.01)
        release.set()
        waiter.join(timeout=2)
        assert returned.is_set() and closed.is_set() and not errors
        assert controller.join(run_id, timeout=0).status == "completed"
    finally:
        release.set()
        waiter.join(timeout=3)


@pytest.mark.parametrize("value,error", [(True, TypeError), (-1, ValueError), (float("nan"), ValueError)])
def test_join_rejects_invalid_cleanup_timeout(tmp_path, value, error):
    controller, run_id, release, _closed = _closing_run(tmp_path)
    try:
        with pytest.raises(error):
            controller.join(run_id, timeout=value)
    finally:
        release.set()
        controller.join(run_id)


@pytest.mark.parametrize("failure", ["signal", "timeout", "bad-json"])
def test_acceptance_persists_both_streams_before_failure(tmp_path, monkeypatch, failure):
    module = _module("cloud/acceptance.py")
    monkeypatch.setattr(module, "verify_source", lambda repo: {"source_commit": "a" * 40, "files": {}})
    output = tmp_path / "acceptance"
    def child(command, **kwargs):
        if "pytest" in command:
            return subprocess.CompletedProcess(command, 0)
        assert command[1:3] == ["-X", "faulthandler"]
        assert kwargs["check"] is False
        assert kwargs["env"]["PYTHONUTF8"] == "1"
        kwargs["stdout"].write(b"partial native output")
        kwargs["stderr"].write("Prüfung: Fatal Python error: Aborted\n".encode())
        if failure == "timeout":
            raise subprocess.TimeoutExpired(command, 90)
        return subprocess.CompletedProcess(command, -6 if failure == "signal" else 0)
    monkeypatch.setattr(module.subprocess, "run", child)
    expected = json.JSONDecodeError if failure == "bad-json" else RuntimeError
    with pytest.raises(expected):
        module.run_acceptance(tmp_path, output, "b" * 64)
    assert (output / "demo-stdout.log").read_bytes() == b"partial native output"
    assert "Aborted" in (output / "demo-stderr.log").read_text(encoding="utf-8")
    result = json.loads((output / "demo-exit.json").read_text(encoding="utf-8"))
    assert result["timed_out"] is (failure == "timeout")
    assert result["returncode"] == (None if failure == "timeout" else (-6 if failure == "signal" else 0))
    assert not (output / "receipt.json").exists()
    assert not (output / "demo-summary.json").exists()


@pytest.mark.parametrize("relative", ["cloud/run_codebuild_acceptance.py", "cloud/demo/deploy_demo.py"])
def test_aws_child_utf8_and_decoder_are_independent_of_windows_locale(monkeypatch, relative):
    module = _module(relative)
    monkeypatch.setenv("PYTHONUTF8", "0")
    monkeypatch.setenv("PYTHONIOENCODING", "cp1252")
    monkeypatch.setenv("AWS_CLI_FILE_ENCODING", "cp1252")
    real = subprocess.check_output
    payload = json.dumps({"message": "Prüfung Ω 東京"}, ensure_ascii=False).encode("utf-8")
    def fake_aws(command, **kwargs):
        assert command[0] == "aws"
        assert kwargs["encoding"] == "utf-8"
        env = kwargs["env"]
        assert (env["PYTHONUTF8"], env["PYTHONIOENCODING"], env["AWS_CLI_FILE_ENCODING"]) == ("1", "utf-8", "utf-8")
        return real([sys.executable, "-c", f"import sys; sys.stdout.buffer.write(bytes.fromhex('{payload.hex()}'))"],
                    **kwargs)
    monkeypatch.setattr(module.subprocess, "check_output", fake_aws)
    result = module.aws("logs", "get-log-events", region="eu-central-1")
    if isinstance(result, str):
        result = json.loads(result)
    assert result["message"] == "Prüfung Ω 東京"
