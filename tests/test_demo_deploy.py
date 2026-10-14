"""cloud/demo/deploy_demo.py: an update must reinstall and read back the served commit."""

from __future__ import annotations

import importlib.util
import io
import json
import subprocess
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "cloud" / "demo" / "deploy_demo.py"


def _load():
    spec = importlib.util.spec_from_file_location("deploy_demo_under_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Response(io.BytesIO):
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


def _fake_aws(calls: list, existing: bool):
    def aws(*args, region):
        calls.append(args)
        if args[:2] == ("cloudformation", "describe-stacks"):
            if not existing and len([c for c in calls if c[:2] == ("cloudformation", "describe-stacks")]) == 1:
                raise subprocess.CalledProcessError(255, "aws")
            return json.dumps({"Stacks": [{"Outputs": [
                {"OutputKey": "DemoUrl", "OutputValue": "https://demo.example.invalid/"},
                {"OutputKey": "SourceBucketName", "OutputValue": "bucket"},
                {"OutputKey": "SourceKey", "OutputValue": "source/visionflow-source.zip"},
                {"OutputKey": "InstanceId", "OutputValue": "i-0123"},
            ]}]})
        if args[:2] == ("ec2", "describe-managed-prefix-lists"):
            return json.dumps({"PrefixLists": [{"PrefixListId": "pl-1"}]})
        if args[:2] == ("ssm", "send-command"):
            return json.dumps({"Command": {"CommandId": "cmd-1"}})
        if args[:2] == ("ssm", "get-command-invocation"):
            return json.dumps({"Status": "Success"})
        raise AssertionError(args)
    return aws


def _run(monkeypatch, tmp_path, *, existing: bool, served_commit: str):
    module = _load()
    calls: list = []
    monkeypatch.setattr(module, "aws", _fake_aws(calls, existing))
    monkeypatch.setattr(module.time, "sleep", lambda _s: None)

    def check_call(cmd, *a, **k):
        if "package" in cmd:
            out = Path(cmd[cmd.index("--output") + 1])
            out.mkdir(parents=True)
            (out / "package.json").write_text(json.dumps(
                {"archive": str(out / "source.zip"), "source_commit": "NEW", "source_zip_sha256": "sha-new"}),
                encoding="utf-8")
        return 0

    monkeypatch.setattr(module.subprocess, "check_call", check_call)

    def urlopen(url, timeout):
        if url.endswith("/deployment.json"):
            return _Response(json.dumps({"source_commit": served_commit, "source_zip_sha256":
                                         "sha-new" if served_commit == "NEW" else "sha-old"}).encode())
        return _Response(b"<title>VisionFlow</title>")

    monkeypatch.setattr(module.urllib.request, "urlopen", urlopen)
    clock = iter(range(0, 10_000, 30))
    monkeypatch.setattr(module.time, "monotonic", lambda: next(clock))
    code = module.deploy("visionflow-demo", "eu-central-1", tmp_path / "work")
    return code, calls, json.loads((tmp_path / "work" / "deployment.json").read_text(encoding="utf-8"))


def test_update_of_an_existing_stack_reinstalls_and_confirms_the_served_commit(monkeypatch, tmp_path):
    code, calls, record = _run(monkeypatch, tmp_path, existing=True, served_commit="NEW")
    assert code == 0
    assert any(c[:2] == ("ssm", "send-command") for c in calls)
    assert record["update"] is True and record["served_readback"]["matches"] is True


def test_an_old_served_commit_is_not_reported_as_ready(monkeypatch, tmp_path):
    code, _calls, record = _run(monkeypatch, tmp_path, existing=True, served_commit="OLD")
    assert code == 1
    assert record["served_readback"]["matches"] is False


def test_first_deploy_relies_on_the_boot_installer(monkeypatch, tmp_path):
    code, calls, record = _run(monkeypatch, tmp_path, existing=False, served_commit="NEW")
    assert code == 0
    assert not any(c[:2] == ("ssm", "send-command") for c in calls)
    assert record["update"] is False
