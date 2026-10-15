"""Publication boundaries and evidence integrity of the optional AWS runner."""
import importlib.util
import base64
import copy
import json
import os
import shutil
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


def test_source_package_uses_committed_allowlist_and_rejects_tampering(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", str(repo)], check=True, capture_output=True)
    for name, content in {"README.md": "public", "src/visionflow/a.py": "x=1",
                          "THIRD_PARTY_NOTICES.md": "upstream notices",
                          "requirements-browser.lock": "playwright==1.60.0",
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
    original = subprocess.check_output
    def move_head_after_snapshot(command, **kwargs):
        result = original(command, **kwargs)
        if command[-2:] == ["rev-parse", "HEAD"]:
            subprocess.run(["git", "-C", str(repo), "add", "README.md"], check=True)
            subprocess.run(["git", "-C", str(repo), "-c", "user.name=Test", "-c",
                            "user.email=test@example.invalid", "commit", "-m", "new HEAD"],
                           check=True, capture_output=True)
        return result
    monkeypatch.setattr(subprocess, "check_output", move_head_after_snapshot)
    result = cloud().package_source(repo, tmp_path / "bundle")
    unpacked = tmp_path / "unpacked"
    with zipfile.ZipFile(result["archive"]) as archive:
        assert "private.txt" not in archive.namelist()
        assert ".env" not in archive.namelist()
        assert "docs/STATE.md" not in archive.namelist()
        assert archive.read("README.md") == b"public"
        assert "THIRD_PARTY_NOTICES.md" in archive.namelist()
        assert archive.read("THIRD_PARTY_NOTICES.md") == b"upstream notices"
        assert archive.read("requirements-browser.lock") == b"playwright==1.60.0"
        archive.extractall(unpacked)
    cloud().verify_source(unpacked)
    (unpacked / "README.md").write_text("tampered", encoding="utf-8")
    with pytest.raises(ValueError, match="hash"):
        cloud().verify_source(unpacked)


@pytest.fixture(scope="module")
def demo_template(tmp_path_factory):
    from visionflow.demo import run_demo
    root = tmp_path_factory.mktemp("cloud-real-demo")
    summary = run_demo(root / "demo")
    prefix = str(root.resolve()) + os.sep
    def relative(value):
        if isinstance(value, dict):
            return {k: relative(v) for k, v in value.items()}
        if isinstance(value, list):
            return [relative(v) for v in value]
        return value[len(prefix):].replace("\\", "/") if isinstance(value, str) and value.startswith(prefix) else value
    cloud().write_json(root / "demo-summary.json", relative(summary))
    for path in (root / "demo" / "traces").glob("*.json"):
        cloud().write_json(path, relative(json.loads(path.read_text(encoding="utf-8"))))
    # An archived auxiliary PNG lets R1 test resealing without breaking scenario pixels.
    shutil.copyfile(next((root / "demo" / "evidence").rglob("*.bin")), root / "demo" / "evidence" / "proof.png")
    return root


def seed_evidence(tmp_path, demo_template):
    shutil.copytree(demo_template, tmp_path, dirs_exist_ok=True)
    for name, content in {"pytest.xml": '<testsuites><testsuite tests="1" failures="0" errors="0" skipped="0"/></testsuites>',
                          "pytest.log": "1 passed", "source-manifest.json": "{}"}.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


def test_evidence_hash_mutation_and_missing_cloud_readback_fail(tmp_path, demo_template):
    seed_evidence(tmp_path, demo_template)
    (tmp_path / "receipt.json").write_text(json.dumps({"schema_version": 1,
        "execution": "local", "tests_exit_code": 0, "model_mode": "fixture", "provider_calls": 0}), encoding="utf-8")
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


@pytest.fixture
def proof(tmp_path, demo_template):
    module = cloud()
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    seed_evidence(evidence, demo_template)
    source_manifest = {"source_commit": "a" * 40, "files": {"README.md": module.digest(b"source")}}
    source_zip = tmp_path / "source.zip"
    with zipfile.ZipFile(source_zip, "w") as archive:
        archive.writestr("README.md", b"source")
        archive.writestr("source-manifest.json", json.dumps(source_manifest))
    module.write_json(evidence / "source-manifest.json", source_manifest)
    sha = module.digest(source_zip.read_bytes())
    project = "visionflow-test-acceptance"
    receipt = {"execution": "aws_codebuild", "tests_exit_code": 0, "model_mode": "fixture", "provider_calls": 0,
               "build_arn": f"arn:aws:codebuild:eu-central-1:000000000000:build/{project}:uuid",
               "build_id": f"{project}:uuid", "source_zip_sha256": sha, "source_commit": "a" * 40,
               "source_version": "source-version-1"}
    module.write_json(evidence / "receipt.json", receipt)
    module.seal_evidence(evidence)
    artifact = tmp_path / "evidence.zip"
    with zipfile.ZipFile(artifact, "w") as archive:
        for path in sorted(evidence.rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(evidence).as_posix())
    checksum = base64.b64encode(bytes.fromhex(sha)).decode("ascii")
    readback = {"builds": [{"arn": receipt["build_arn"], "id": receipt["build_id"],
        "projectName": project, "sourceVersion": "source-version-1",
        "buildStatus": "SUCCEEDED", "buildComplete": True,
        "source": {"type": "S3", "location": f"visionflow-test-bucket/sources/{sha}.zip", "buildspec": "cloud/buildspec.yml"},
        "artifacts": {"location": "arn:aws:s3:::visionflow-test-bucket/evidence/acceptance.zip", "sha256sum": module.digest(artifact.read_bytes())},
        "environment": {"image": "aws/codebuild/standard:7.0", "environmentVariables": [
            {"name": "VISIONFLOW_SOURCE_ZIP_SHA256", "value": sha}]}}]}
    context = {"stack": {"Stacks": [{"StackId": "arn:aws:cloudformation:eu-central-1:000000000000:stack/visionflow-test/uuid",
        "StackName": "visionflow-test", "StackStatus": "CREATE_COMPLETE",
        "Parameters": [{"ParameterKey": "SourceZipSha256", "ParameterValue": sha}],
        "Outputs": [{"OutputKey": "BucketName", "OutputValue": "visionflow-test-bucket"},
                    {"OutputKey": "ProjectName", "OutputValue": project}]}]},
        "launch": {"build": copy.deepcopy(readback["builds"][0])},
        "upload": {"VersionId": "source-version-1", "ChecksumSHA256": checksum, "ChecksumType": "FULL_OBJECT"},
        "source_head": {"VersionId": "source-version-1", "ChecksumSHA256": checksum,
                        "ChecksumType": "FULL_OBJECT", "ContentLength": source_zip.stat().st_size}}
    return {"module": module, "root": evidence, "readback": readback, "source_zip": source_zip,
            "evidence_zip": artifact, "launch_context": context}


def verify(proof, **overrides):
    args = {"root": proof["root"], "cloud_readback": proof["readback"], "source_zip": proof["source_zip"],
            "evidence_zip": proof["evidence_zip"], "launch_context": proof["launch_context"]}
    args.update(overrides)
    return proof["module"].verify_evidence(**args)


def test_cloud_readback_requires_exact_source_zip_and_successful_identity(proof):
    module, evidence, readback, source_zip = (proof[k] for k in ("module", "root", "readback", "source_zip"))
    with pytest.raises(ValueError, match="missing ZIP"):
        module.verify_evidence(evidence, readback)
    assert verify(proof)["cloud_verified"] is True
    readback["builds"][0]["buildStatus"] = "FAILED"
    with pytest.raises(ValueError, match="identity"):
        verify(proof)


@pytest.mark.parametrize("change", ["missing_zip", "missing_aws_hash", "wrong_zip", "resealed_frame", "extra_file"])
def test_original_aws_artifact_binding_rejects_mutation(proof, change):
    if change == "missing_zip":
        with pytest.raises(ValueError):
            verify(proof, evidence_zip=None)
        return
    if change == "missing_aws_hash":
        proof["readback"]["builds"][0]["artifacts"].pop("sha256sum")
    elif change == "wrong_zip":
        proof["evidence_zip"].write_bytes(b"different ZIP")
    elif change == "resealed_frame":
        path = proof["root"] / "demo/evidence/proof.png"
        path.write_bytes(path.read_bytes() + b"changed auxiliary bytes")
        proof["module"].seal_evidence(proof["root"])
    else:
        (proof["root"] / "unlisted.txt").write_text("extra")
    with pytest.raises(ValueError):
        verify(proof)


@pytest.mark.parametrize("change", ["location", "buildspec", "project", "artifact", "version", "checksum", "launch_id", "stack_account"])
def test_exact_stack_launch_and_versioned_source_are_required(proof, change):
    build = proof["readback"]["builds"][0]
    if change in {"location", "buildspec"}:
        build["source"][change] = "unrelated"
    elif change == "project":
        build["projectName"] = "foreign"
    elif change == "artifact":
        build["artifacts"]["location"] = "arn:aws:s3:::foreign/elsewhere.zip"
    elif change == "version":
        build["sourceVersion"] = "new-object-version"
    elif change == "checksum":
        proof["launch_context"]["source_head"]["ChecksumSHA256"] = "wrong"
    elif change == "launch_id":
        proof["launch_context"]["launch"]["build"]["id"] = "foreign:uuid"
    else:
        proof["launch_context"]["stack"]["Stacks"][0]["StackId"] = "arn:aws:cloudformation:eu-central-1:111111111111:stack/visionflow-test/uuid"
    with pytest.raises(ValueError):
        verify(proof)


@pytest.mark.parametrize("change", ["missing_scenario", "empty_trace", "unreferenced_frame", "invalid_png", "model_mode", "backward_frames", "no_sharpness_gain"])
def test_real_demo_contract_is_required_even_after_resealing(proof, change):
    root, module = proof["root"], proof["module"]
    summary = json.loads((root / "demo-summary.json").read_text(encoding="utf-8"))
    doc = summary["scenario_results"]["document-quality"]
    motion = summary["scenario_results"]["motion-observation"]
    if change == "missing_scenario":
        summary["scenario_results"].pop("motion-observation")
    elif change == "empty_trace":
        module.write_json(root / summary["trace_paths"]["motion-observation"], [])
    elif change == "unreferenced_frame":
        doc["evidence_paths"].append("demo/evidence/missing.bin")
    elif change == "invalid_png":
        (root / doc["evidence_paths"][0]).write_bytes(b"not a PNG")
    elif change == "model_mode":
        summary["model_mode"] = "real_provider"
    elif change == "backward_frames":
        motion["follow_up_frame_ids"] = motion["initial_frame_ids"]
    else:
        doc["replacement_sharpness_variance"] = doc["initial_sharpness_variance"]
    module.write_json(root / "demo-summary.json", summary)
    module.seal_evidence(root)
    # Local integrity must validate scenario contents; no AWS simulation is needed.
    with pytest.raises(ValueError):
        module.verify_evidence(root)
