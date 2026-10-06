from __future__ import annotations

import json
import os
import subprocess
import sys
import sysconfig
import threading
import tomllib
from io import StringIO
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from visionflow.contracts import RunResult, UnknownRun
from visionflow.registry import ToolDescriptor, ToolRegistry
from visionflow.api import create_server
from visionflow.cli import main
from helpers import valid_config, write_config


class FakeController:
    def __init__(self) -> None:
        self.runs: dict[str, RunResult] = {}

    def start(self, config: Any) -> RunResult:
        result = RunResult(
            run_id=f"run-{len(self.runs) + 1}",
            status="queued",
            trace=[{"type": "run_started", "at": datetime.now(timezone.utc)}],
        )
        self.runs[result.run_id] = result
        return deepcopy(result)

    def status(self, run_id: str) -> RunResult:
        try:
            return deepcopy(self.runs[run_id])
        except KeyError as exc:
            raise UnknownRun(run_id) from exc

    def cancel(self, run_id: str) -> RunResult:
        result = self.status(run_id)
        result.status = "cancelled"
        self.runs[run_id] = deepcopy(result)
        return result

    def resume(self, run_id: str, payload: dict[str, Any]) -> RunResult:
        result = self.status(run_id)
        result.status = "queued"
        self.runs[run_id] = deepcopy(result)
        return result


def test_cli_invalid_config_exits_nonzero(tmp_path, capsys):
    invalid = tmp_path / "bad.json"
    invalid.write_text("{not valid json", encoding="utf-8")

    code = main(["validate", str(invalid)])

    captured = capsys.readouterr()
    assert code != 0
    assert "configuration" in captured.err.lower()


def test_cli_client_uses_existing_server_across_separate_invocations(tmp_path, capsys):
    registry = ToolRegistry()
    registry.register(ToolDescriptor("quality", input_kinds=("image",)), lambda f, p, c: {})
    server = create_server(
        FakeController(),
        host="127.0.0.1",
        port=0,
        data_dir=tmp_path / "data",
        registry=registry,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    address, port = server.server_address[:2]
    server_url = f"http://{address}:{port}"
    workflow_path = write_config(tmp_path, valid_config())
    session_file = tmp_path / "cli-session.json"
    try:
        run_code = main([
            "run", str(workflow_path), "--server", server_url,
            "--session-file", str(session_file),
        ])
        run_output = json.loads(capsys.readouterr().out)
        assert run_code == 0
        run_id = run_output["run_id"]

        status_code = main([
            "status", run_id, "--server", server_url,
            "--session-file", str(session_file),
        ])
        status_output = json.loads(capsys.readouterr().out)
        assert status_code == 0
        assert status_output["run_id"] == run_id

        cancel_code = main([
            "cancel", run_id, "--server", server_url,
            "--session-file", str(session_file),
        ])
        cancel_output = json.loads(capsys.readouterr().out)
        assert cancel_code == 0
        assert cancel_output["status"] == "cancelled"
    finally:
        server.shutdown()
        thread.join(timeout=3)
        server.server_close()


def test_cli_missing_server_returns_structured_error(capsys):
    code = main(["status", "run-1", "--server", "http://127.0.0.1:1"])
    captured = capsys.readouterr()
    payload = json.loads(captured.err)
    assert code != 0
    assert payload["error"]["code"] == "server_unavailable"


def test_tools_cli_is_local_by_default_and_reports_real_tools(monkeypatch, tmp_path, capsys):
    from visionflow import cli

    session_file = tmp_path / "must-not-be-created.json"
    monkeypatch.setattr(cli, "_default_session_file", lambda _url: session_file)

    def unexpected_client(*_args, **_kwargs):
        raise AssertionError("default tools command must not contact a server")

    monkeypatch.setattr(cli, "_ApiClient", unexpected_client)

    code = main(["tools"])

    output = json.loads(capsys.readouterr().out)
    assert code == 0
    assert {tool["name"] for tool in output["tools"]} == {"quality", "motion", "direction"}
    descriptions = {tool["name"]: tool["description"] for tool in output["tools"]}
    assert "Schärfe" in descriptions["quality"]
    assert "Bewegung" in descriptions["motion"]
    assert not session_file.exists()


def test_package_exposes_cli_and_includes_web_assets():
    project_root = Path(__file__).resolve().parents[1]
    metadata = tomllib.loads((project_root / "pyproject.toml").read_text(encoding="utf-8"))

    assert metadata["project"]["scripts"]["visionflow"] == "visionflow.cli:main"
    assert metadata["tool"]["setuptools"]["package-data"]["visionflow"] == [
        "web/*.html",
        "web/*.js",
        "web/*.css",
    ]


def installed_console_script() -> Path:
    """Return the ``visionflow`` entry point of the interpreter running pytest."""
    name = "visionflow.exe" if os.name == "nt" else "visionflow"
    executable = Path(sysconfig.get_path("scripts")) / name
    assert executable.is_file(), "the project console script must be installed in its venv"
    return executable


def test_installed_console_script_tools_output_is_utf8_without_pythonioencoding():
    project_root = Path(__file__).resolve().parents[1]
    executable = installed_console_script()
    environment = os.environ.copy()
    environment.pop("PYTHONIOENCODING", None)
    environment["PYTHONUTF8"] = "0"

    result = subprocess.run(
        [str(executable), "tools"],
        cwd=project_root,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        timeout=15,
    )

    assert result.returncode == 0, result.stderr.decode("utf-8", errors="replace")
    output = result.stdout.decode("utf-8")
    assert "Schärfe" in output
    assert "Bewegung" in output


def test_installed_console_script_help_is_utf8_without_pythonioencoding():
    project_root = Path(__file__).resolve().parents[1]
    executable = installed_console_script()
    environment = os.environ.copy()
    environment.pop("PYTHONIOENCODING", None)
    environment["PYTHONUTF8"] = "0"

    result = subprocess.run(
        [str(executable), "--help"],
        cwd=project_root,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        timeout=15,
    )

    assert result.returncode == 0
    output = result.stdout.decode("utf-8")
    assert "Verfügbare Werkzeugbeschreibungen abrufen" in output


def test_cli_help_keeps_working_with_stringio_streams(monkeypatch):
    from visionflow import cli

    output = StringIO()
    errors = StringIO()
    monkeypatch.setattr(sys, "stdout", output)
    monkeypatch.setattr(sys, "stderr", errors)

    try:
        cli.main(["--help"])
    except SystemExit as exc:
        assert exc.code == 0

    assert "tools" in output.getvalue()
    assert errors.getvalue() == ""


def test_demo_cli_delegates_to_run_demo_and_emits_structured_json(monkeypatch, tmp_path, capsys):
    from types import ModuleType

    calls = []
    demo_module = ModuleType("visionflow.demo")

    def run_demo(output_dir):
        calls.append(output_dir)
        return {
            "status": "completed",
            "opencv_version": "5.test",
            "scenario_results": [],
            "trace_paths": [],
            "model_mode": "fixture",
            "network_used": False,
        }

    demo_module.run_demo = run_demo
    monkeypatch.setitem(sys.modules, "visionflow.demo", demo_module)
    output_dir = tmp_path / "demo-output"

    code = main(["demo", "run", "--output-dir", str(output_dir)])

    assert code == 0
    assert calls == [output_dir]
    assert json.loads(capsys.readouterr().out)["network_used"] is False


def test_demo_cli_reports_a_structured_failure(monkeypatch, tmp_path, capsys):
    from types import ModuleType

    demo_module = ModuleType("visionflow.demo")

    def fail(output_dir):
        raise RuntimeError("fixture failure")

    demo_module.run_demo = fail
    monkeypatch.setitem(sys.modules, "visionflow.demo", demo_module)

    code = main(["demo", "run", "--output-dir", str(tmp_path / "demo-output")])

    captured = capsys.readouterr()
    assert code != 0
    assert json.loads(captured.err) == {
        "error": {"code": "demo_failed", "message": "fixture failure"}
    }


def test_cli_camera_flag_requires_an_explicit_source_mapping(monkeypatch, tmp_path, capsys):
    # The serve command accepts only explicit name=device mappings and defaults to none.
    from visionflow import cli
    from visionflow import road_runtime

    captured: dict[str, Any] = {}

    class FakeServer:
        server_address = ("127.0.0.1", 8765)

        def serve_forever(self):
            return None

        def server_close(self):
            return None

    class FakeRuntime:
        controller = object()
        road_sessions = None
        road_cases = None
        road_unavailable_reason = "ffmpeg_required"

        def close(self):
            return None

    def fake_build_runtime(data_dir, *, allowed_camera_sources=None, allow_native_costs=False):
        captured["allowed_camera_sources"] = allowed_camera_sources
        captured["allow_native_costs"] = allow_native_costs
        return FakeRuntime()
    monkeypatch.setattr(road_runtime, "build_road_runtime", fake_build_runtime)
    monkeypatch.setattr(cli, "create_server", lambda *args, **kwargs: FakeServer(), raising=False)

    code = main(["serve", "--data-dir", str(tmp_path / "data"), "--camera", "front=0"])

    assert code == 0
    assert captured["allowed_camera_sources"] == {"front": 0}
    assert captured["allow_native_costs"] is False


def test_serve_keeps_core_api_available_when_ffmpeg_is_missing(monkeypatch, tmp_path, capsys):
    from visionflow import cli
    from visionflow import road_runtime

    captured: dict[str, Any] = {}

    class FakeServer:
        server_address = ("127.0.0.1", 8765)

        def serve_forever(self):
            return None

        def server_close(self):
            return None

    class FakeRuntime:
        controller = object()
        road_sessions = None
        road_cases = None
        road_unavailable_reason = "ffmpeg_required"

        def close(self):
            return None

    def fake_build_runtime(data_dir, *, allowed_camera_sources=None, allow_native_costs=False):
        captured["allowed_camera_sources"] = allowed_camera_sources
        captured["allow_native_costs"] = allow_native_costs
        return FakeRuntime()

    def fake_create_server(controller, **kwargs):
        captured["server_called"] = True
        captured["road_sessions"] = kwargs.get("road_sessions")
        return FakeServer()

    monkeypatch.setattr(road_runtime, "build_road_runtime", fake_build_runtime)
    monkeypatch.setattr(cli, "create_server", fake_create_server, raising=False)

    code = main(["serve", "--data-dir", str(tmp_path / "data")])

    output = json.loads(capsys.readouterr().out)
    assert code == 0
    assert output["status"] == "serving"
    assert captured["server_called"] is True
    assert captured["road_sessions"] is None
    assert captured["allowed_camera_sources"] == {}
    assert captured["allow_native_costs"] is False
