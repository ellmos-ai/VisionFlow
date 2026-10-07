"""Optional AWS acceptance tooling; never provisions resources or reads credentials."""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import subprocess
import sys
import tarfile
import zipfile
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

PUBLIC_ROOT = {"README.md", "LICENSE", "pyproject.toml", "requirements.lock"}
PUBLIC_DOCS = {"docs/TESTING.md", "docs/AWS_ACCEPTANCE.md", "docs/TECHNICAL_REPORT.md",
               "docs/ARCHITECTURE.md"}


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def write_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False, allow_nan=False) + "\n",
                    encoding="utf-8")


def public_path(name: str) -> bool:
    parts = Path(name).parts
    if any(part.startswith(".") or part in {"__pycache__", "STATE.md", "CLAUDE.md",
            "AGENTS.md", "DECISIONS.md", "START.md"} for part in parts):
        return False
    if Path(name).suffix.lower() in {".pem", ".key", ".pfx", ".db", ".log", ".pyc"}:
        return False
    return name in PUBLIC_ROOT | PUBLIC_DOCS or name.startswith(("src/", "tests/", "examples/", "cloud/"))


def package_source(repo: Path, output: Path) -> dict:
    """Archive only public files from HEAD; dirty/untracked work is not included."""
    repo = repo.resolve(strict=True)
    commit = subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip()
    snapshot = subprocess.check_output(["git", "-C", str(repo), "archive", "--format=tar", commit])
    files = {}
    with tarfile.open(fileobj=io.BytesIO(snapshot), mode="r:") as archive:
        for member in sorted(archive.getmembers(), key=lambda m: m.name):
            if member.isfile() and public_path(member.name):
                files[member.name] = archive.extractfile(member).read()
    output.mkdir(parents=True, exist_ok=False)
    manifest = {"schema_version": 1, "source_commit": commit,
                "files": {name: digest(data) for name, data in files.items()}}
    archive_path = output / "source.zip"
    with zipfile.ZipFile(archive_path, "x", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in files.items():
            entry = zipfile.ZipInfo(name, (1980, 1, 1, 0, 0, 0))
            archive.writestr(entry, data, compress_type=zipfile.ZIP_DEFLATED)
        archive.writestr("source-manifest.json", json.dumps(manifest, sort_keys=True).encode("utf-8"))
    receipt = {"archive": str(archive_path), "source_commit": commit,
               "source_zip_sha256": digest(archive_path.read_bytes()), "file_count": len(files)}
    write_json(output / "package.json", receipt)
    return receipt


def check_files(root: Path, manifest: dict) -> None:
    for name, expected in manifest["files"].items():
        path = (root / name).resolve(strict=True)
        if not path.is_relative_to(root.resolve()) or not path.is_file():
            raise ValueError("manifest path outside evidence root")
        if digest(path.read_bytes()) != expected:
            raise ValueError(f"hash mismatch: {name}")


def verify_source(root: Path) -> dict:
    manifest = json.loads((root / "source-manifest.json").read_text(encoding="utf-8"))
    check_files(root, manifest)
    if not re.fullmatch(r"[0-9a-f]{40}", manifest["source_commit"]):
        raise ValueError("invalid source commit")
    return manifest


def seal_evidence(root: Path) -> None:
    write_json(root / "evidence-manifest.json", {"schema_version": 1, "files": {
        p.relative_to(root).as_posix(): digest(p.read_bytes()) for p in sorted(root.rglob("*"))
        if p.is_file() and p.name != "evidence-manifest.json"}})


def verify_evidence(root: Path, cloud_readback: dict | None = None, source_zip: Path | None = None) -> dict:
    manifest = json.loads((root / "evidence-manifest.json").read_text(encoding="utf-8"))
    required = {"receipt.json", "demo-summary.json", "pytest.xml", "pytest.log",
                "source-manifest.json", "demo/traces/document-quality.json",
                "demo/traces/motion-observation.json"}
    if not required.issubset(manifest["files"]) or not any(
            name.startswith("demo/evidence/") and name.endswith(".png") for name in manifest["files"]):
        raise ValueError("missing test, trace or frame evidence in manifest")
    check_files(root, manifest)
    suites = list(ET.parse(root / "pytest.xml").getroot().iter("testsuite"))
    if (not suites or sum(int(s.get("tests", "0")) - int(s.get("skipped", "0")) for s in suites) < 1
            or any(int(s.get("failures", "0")) or int(s.get("errors", "0")) for s in suites)):
        raise ValueError("missing successful executed tests")
    receipt = json.loads((root / "receipt.json").read_text(encoding="utf-8"))
    demo = json.loads((root / "demo-summary.json").read_text(encoding="utf-8"))
    if receipt["tests_exit_code"] != 0 or demo["status"] != "passed" or not demo["opencv_version"].startswith("5."):
        raise ValueError("failed tests or OpenCV demo")
    cloud_verified = False
    if cloud_readback is not None:
        if receipt["execution"] != "aws_codebuild":
            raise ValueError("local evidence is not cloud evidence")
        if source_zip is None or digest(source_zip.read_bytes()) != receipt["source_zip_sha256"]:
            raise ValueError("cloud source ZIP hash mismatch or missing ZIP")
        with zipfile.ZipFile(source_zip) as archive:
            packed_source = json.loads(archive.read("source-manifest.json"))
            if any(digest(archive.read(name)) != expected for name, expected in packed_source["files"].items()):
                raise ValueError("cloud source file hash mismatch")
        if (packed_source != json.loads((root / "source-manifest.json").read_text(encoding="utf-8"))
                or packed_source["source_commit"] != receipt["source_commit"]):
            raise ValueError("cloud source manifest or commit mismatch")
        builds = cloud_readback.get("builds", [])
        if len(builds) != 1:
            raise ValueError("cloud readback must describe exactly one build")
        build = builds[0]
        variables = {v["name"]: v["value"] for v in build["environment"]["environmentVariables"]}
        if (build["arn"] != receipt["build_arn"] or build["id"] != receipt["build_id"]
                or build["buildStatus"] != "SUCCEEDED" or not build.get("buildComplete")
                or build["source"]["type"] != "S3"
                or build["environment"]["image"] != "aws/codebuild/standard:7.0"
                or variables.get("VISIONFLOW_SOURCE_ZIP_SHA256") != receipt["source_zip_sha256"]):
            raise ValueError("cloud build identity, status or source hash mismatch")
        cloud_verified = True
    return {"integrity_verified": True, "cloud_verified": cloud_verified,
            "model_mode": demo["model_mode"], "execution": receipt["execution"]}


def run_acceptance(repo: Path, output: Path, source_zip_sha256: str) -> dict:
    source = verify_source(repo)
    if not re.fullmatch(r"[0-9a-f]{64}", source_zip_sha256):
        raise ValueError("exact source ZIP SHA-256 is required")
    output.mkdir(parents=True, exist_ok=False)
    # PYTHONPATH selects this unpacked source, even when launched with an existing venv.
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(repo / "src")
    environment["PYTHONIOENCODING"] = "utf-8"
    started = datetime.now(timezone.utc).isoformat()
    with (output / "pytest.log").open("w", encoding="utf-8") as log:
        tests = subprocess.run([sys.executable, "-m", "pytest", "tests", "-q",
                                f"--junitxml={output / 'pytest.xml'}"], cwd=repo,
                               env=environment, stdout=log, stderr=subprocess.STDOUT, timeout=900)
    if tests.returncode:
        raise RuntimeError(f"test suite failed ({tests.returncode}); inspect pytest.log")
    demo = subprocess.run([sys.executable, "-m", "visionflow.cli", "demo", "run",
                           "--output-dir", str(output / "demo")], cwd=repo, env=environment,
                          capture_output=True, text=True, encoding="utf-8", timeout=90, check=True)
    (output / "demo-summary.json").write_text(demo.stdout, encoding="utf-8")
    (output / "demo-stderr.log").write_text(demo.stderr, encoding="utf-8")
    freeze = subprocess.check_output([sys.executable, "-m", "pip", "freeze"], text=True)
    (output / "pip-freeze.txt").write_text(freeze, encoding="utf-8")
    write_json(output / "source-manifest.json", source)
    arn, build_id = os.environ.get("CODEBUILD_BUILD_ARN"), os.environ.get("CODEBUILD_BUILD_ID")
    receipt = {"schema_version": 1, "execution": "aws_codebuild" if arn and build_id else "local",
               "build_arn": arn, "build_id": build_id, "source_commit": source["source_commit"],
               "source_zip_sha256": source_zip_sha256, "tests_exit_code": tests.returncode,
               "python_version": sys.version, "started_at": started,
               "finished_at": datetime.now(timezone.utc).isoformat(),
               "model_mode": "fixture", "provider_calls": 0}
    write_json(output / "receipt.json", receipt)
    seal_evidence(output)
    return verify_evidence(output)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    package = commands.add_parser("package")
    package.add_argument("--repo", type=Path, default=Path.cwd())
    package.add_argument("--output", type=Path, required=True)
    run = commands.add_parser("run")
    run.add_argument("--repo", type=Path, default=Path.cwd())
    run.add_argument("--output", type=Path, required=True)
    run.add_argument("--source-zip-sha256", required=True)
    verify = commands.add_parser("verify")
    verify.add_argument("--evidence", type=Path, required=True)
    verify.add_argument("--cloud-readback", type=Path)
    verify.add_argument("--source-zip", type=Path)
    args = parser.parse_args()
    if args.command == "package":
        result = package_source(args.repo, args.output)
    elif args.command == "run":
        result = run_acceptance(args.repo.resolve(), args.output.resolve(), args.source_zip_sha256)
    else:
        readback = json.loads(args.cloud_readback.read_text(encoding="utf-8-sig")) if args.cloud_readback else None
        result = verify_evidence(args.evidence, readback, args.source_zip)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
