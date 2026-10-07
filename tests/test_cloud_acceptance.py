"""Publication boundaries and evidence integrity of the optional AWS runner."""
import importlib.util
import json
import subprocess
import zipfile
from pathlib import Path

import pytest


def cloud():
    path = Path(__file__).resolve().parents[1] / "cloud" / "acceptance.py"
    assert path.exists(), "cloud runner is not implemented"
    spec = importlib.util.spec_from_file_location("cloud_acceptance", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_source_package_uses_committed_allowlist_and_rejects_tampering(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", str(repo)], check=True, capture_output=True)
    for name, content in {"README.md": "public", "src/visionflow/a.py": "x=1",
                          "cloud/buildspec.yml": "version: 0.2", "private.txt": "secret",
                          "docs/STATE.md": "private plan", ".env": "token"}.items():
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=Test", "-c",
                    "user.email=test@example.invalid", "commit", "-m", "fixture"],
                   check=True, capture_output=True)
    (repo / "README.md").write_text("uncommitted", encoding="utf-8")
    result = cloud().package_source(repo, tmp_path / "bundle")
    unpacked = tmp_path / "unpacked"
    with zipfile.ZipFile(result["archive"]) as archive:
        assert "private.txt" not in archive.namelist()
        assert ".env" not in archive.namelist()
        assert "docs/STATE.md" not in archive.namelist()
        assert archive.read("README.md") == b"public"
        archive.extractall(unpacked)
    cloud().verify_source(unpacked)
    (unpacked / "README.md").write_text("tampered", encoding="utf-8")
    with pytest.raises(ValueError, match="hash"):
        cloud().verify_source(unpacked)


def seed_evidence(tmp_path):
    for name, content in {"pytest.xml": '<testsuites><testsuite tests="1" failures="0" errors="0" skipped="0"/></testsuites>',
                          "pytest.log": "1 passed", "source-manifest.json": "{}",
                          "demo/traces/document-quality.json": "[]",
                          "demo/traces/motion-observation.json": "[]",
                          "demo/evidence/frame.png": "frame"}.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


def test_evidence_hash_mutation_and_missing_cloud_readback_fail(tmp_path):
    seed_evidence(tmp_path)
    (tmp_path / "demo-summary.json").write_text(json.dumps({"status": "passed",
        "opencv_version": "5.0.0", "model_mode": "fixture"}), encoding="utf-8")
    (tmp_path / "receipt.json").write_text(json.dumps({"schema_version": 1,
        "execution": "local", "tests_exit_code": 0}), encoding="utf-8")
    cloud().seal_evidence(tmp_path)
    result = cloud().verify_evidence(tmp_path)
    assert result["cloud_verified"] is False
    with pytest.raises(ValueError, match="cloud"):
        cloud().verify_evidence(tmp_path, {"builds": []})
    (tmp_path / "demo-summary.json").write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="hash"):
        cloud().verify_evidence(tmp_path)


def test_empty_manifest_cannot_prove_tests_or_frame_evidence(tmp_path):
    (tmp_path / "evidence-manifest.json").write_text('{"files": {}}', encoding="utf-8")
    (tmp_path / "demo-summary.json").write_text(json.dumps({"status": "passed",
        "opencv_version": "5.0.0", "model_mode": "fixture"}), encoding="utf-8")
    (tmp_path / "receipt.json").write_text(json.dumps({"execution": "local",
        "tests_exit_code": 0}), encoding="utf-8")
    with pytest.raises(ValueError, match="missing"):
        cloud().verify_evidence(tmp_path)


def test_cloud_readback_requires_exact_source_zip_and_successful_identity(tmp_path):
    module = cloud()
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    seed_evidence(evidence)
    source_manifest = {"source_commit": "a" * 40, "files": {"README.md": module.digest(b"source")}}
    source_zip = tmp_path / "source.zip"
    with zipfile.ZipFile(source_zip, "w") as archive:
        archive.writestr("README.md", b"source")
        archive.writestr("source-manifest.json", json.dumps(source_manifest))
    module.write_json(evidence / "source-manifest.json", source_manifest)
    sha = module.digest(source_zip.read_bytes())
    receipt = {"execution": "aws_codebuild", "tests_exit_code": 0,
               "build_arn": "arn:aws:codebuild:eu-central-1:000000000000:build/example:uuid",
               "build_id": "example:uuid", "source_zip_sha256": sha, "source_commit": "a" * 40}
    module.write_json(evidence / "receipt.json", receipt)
    module.write_json(evidence / "demo-summary.json", {"status": "passed", "opencv_version": "5.0.0", "model_mode": "fixture"})
    module.seal_evidence(evidence)
    readback = {"builds": [{"arn": receipt["build_arn"], "id": receipt["build_id"],
        "buildStatus": "SUCCEEDED", "buildComplete": True, "source": {"type": "S3"},
        "environment": {"image": "aws/codebuild/standard:7.0", "environmentVariables": [
            {"name": "VISIONFLOW_SOURCE_ZIP_SHA256", "value": sha}]}}]}
    with pytest.raises(ValueError, match="missing ZIP"):
        module.verify_evidence(evidence, readback)
    assert module.verify_evidence(evidence, readback, source_zip)["cloud_verified"] is True
    readback["builds"][0]["buildStatus"] = "FAILED"
    with pytest.raises(ValueError, match="identity"):
        module.verify_evidence(evidence, readback, source_zip)
