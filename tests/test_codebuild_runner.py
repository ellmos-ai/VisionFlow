"""Failure evidence must survive the one-command runner's destructive cleanup."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest


def _run(monkeypatch, tmp_path, *, capture_failure=None, unsafe_archive=False):
    script = Path(__file__).resolve().parents[1] / "cloud/run_codebuild_acceptance.py"
    spec = importlib.util.spec_from_file_location("codebuild_runner_under_test", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    work = tmp_path / "synthetic-stack"
    calls = []
    source = b"synthetic committed source archive"
    sha = hashlib.sha256(source).hexdigest()
    build = {
        "id": "synthetic-project:build-1", "buildStatus": "FAILED", "buildComplete": True,
        "artifacts": {"location": "arn:aws:s3:::synthetic-bucket/evidence/acceptance.zip"},
        "logs": {"groupName": "/visionflow/synthetic-stack", "streamName": "build-1"},
    }
    if capture_failure == "missing_artifact":
        build.pop("artifacts")

    def aws(*args, region, parse=True):
        assert region == "eu-central-1"
        calls.append(args)
        if args[:2] == ("cloudformation", "validate-template"):
            return {}
        if args[:2] == ("cloudformation", "describe-stacks"):
            return {"Stacks": [{"Outputs": [
                {"OutputKey": "BucketName", "OutputValue": "synthetic-bucket"},
                {"OutputKey": "ProjectName", "OutputValue": "synthetic-project"},
            ]}]}
        if args[:2] in {("s3api", "put-object"), ("s3api", "head-object")}:
            return {"VersionId": "synthetic-version"}
        if args[:2] == ("codebuild", "start-build"):
            return {"build": {"id": build["id"]}}
        if args[:2] == ("codebuild", "batch-get-builds"):
            return {"builds": [build]}
        if args[:2] == ("logs", "get-log-events"):
            assert args[args.index("--log-group-name") + 1] == build["logs"]["groupName"]
            assert args[args.index("--log-stream-name") + 1] == "build-1"
            assert "--start-from-head" in args
            if capture_failure == "logs":
                raise subprocess.CalledProcessError(1, ["aws", "logs", "get-log-events"])
            token = args[args.index("--next-token") + 1] if "--next-token" in args else None
            if token is None:
                return {"events": [{"timestamp": 1, "message": "BUILD started"}], "nextForwardToken": "page-1"}
            if token == "page-1":
                return {"events": [{"timestamp": 2, "message": "pytest failed"}], "nextForwardToken": "page-2"}
            assert token == "page-2"
            return {"events": [], "nextForwardToken": "page-2"}
        if args[:2] == ("s3api", "list-object-versions"):
            return {"Versions": [{"Key": "evidence/acceptance.zip", "VersionId": "v1"}]}
        if args[:2] == ("s3api", "delete-objects"):
            assert (work / "failure/cloud-evidence/pytest.log").read_text() == "1 failed\n"
            assert len(json.loads((work / "failure/cloudwatch-events.json").read_text())["events"]) == 2
            return {}
        raise AssertionError(args)

    def check_call(cmd, *args, **kwargs):
        calls.append(tuple(cmd))
        if "package" in cmd:
            bundle = Path(cmd[cmd.index("--output") + 1])
            bundle.mkdir(parents=True)
            archive = bundle / "source.zip"
            archive.write_bytes(source)
            (bundle / "package.json").write_text(json.dumps({
                "archive": str(archive), "source_commit": "a" * 40, "source_zip_sha256": sha,
            }))
        elif cmd[1:3] == ["s3api", "get-object"]:
            Path(cmd[-1]).write_bytes(source)
        elif cmd[1:3] == ["s3", "cp"]:
            assert cmd[3] == "s3://synthetic-bucket/evidence/acceptance.zip"
            if capture_failure == "artifact":
                raise subprocess.CalledProcessError(1, cmd)
            with zipfile.ZipFile(cmd[4], "w") as archive:
                archive.writestr("pytest.log", "1 failed\n")
                archive.writestr("pytest.xml", '<testsuite tests="1" failures="1"/>')
                if unsafe_archive:
                    archive.writestr("../escaped.txt", "unsafe")
        elif cmd[1:3] == ["cloudformation", "delete-stack"]:
            assert (work / "failure/cloud-evidence/pytest.log").is_file()
            assert (work / "failure/cloudwatch-events.json").is_file()
        elif cmd[1:3] not in (["cloudformation", "deploy"], ["cloudformation", "wait"]):
            raise AssertionError(cmd)
        return 0

    monkeypatch.setattr(module, "aws", aws)
    monkeypatch.setattr(module.subprocess, "check_call", check_call)
    monkeypatch.setattr(sys, "argv", [str(script), "--stack", "synthetic-stack", "--workdir", str(tmp_path)])
    code = module.main()
    result = json.loads((work / "result.json").read_text())
    return code, work, result, calls


def test_failed_build_preserves_artifact_and_all_log_pages_before_cleanup(monkeypatch, tmp_path):
    code, work, result, _calls = _run(monkeypatch, tmp_path)
    assert code == 1 and result["status"] == "FAILED"
    assert result["cleanup"] == "stack deleted"
    assert (work / "failure/evidence.zip").is_file()
    assert (work / "failure/cloud-evidence/pytest.log").read_text() == "1 failed\n"
    assert [e["message"] for e in json.loads((work / "failure/cloudwatch-events.json").read_text())["events"]] == ["BUILD started", "pytest failed"]


@pytest.mark.parametrize("capture_failure", ["artifact", "logs", "missing_artifact"])
def test_capture_failure_keeps_remote_evidence_and_saves_other_channel(monkeypatch, tmp_path, capture_failure):
    code, work, result, calls = _run(monkeypatch, tmp_path, capture_failure=capture_failure)
    assert code == 1 and result["status"] == "FAILED"
    assert result["cleanup"] == "skipped: failure evidence incomplete"
    assert result["failure_evidence"]["errors"]
    assert not any("delete-stack" in call or "delete-objects" in call for call in calls)
    if capture_failure == "logs":
        assert (work / "failure/cloud-evidence/pytest.log").is_file()
    else:
        assert (work / "failure/cloudwatch-events.json").is_file()


def test_failure_artifact_cannot_extract_outside_its_evidence_directory(monkeypatch, tmp_path):
    code, work, result, calls = _run(monkeypatch, tmp_path, unsafe_archive=True)
    assert code == 1 and result["failure_evidence"]["errors"]
    assert not (work / "failure/escaped.txt").exists()
    assert not any("delete-stack" in call for call in calls)
    assert (work / "failure/cloudwatch-events.json").is_file()
