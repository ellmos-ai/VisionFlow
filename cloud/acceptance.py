"""Optional AWS acceptance tooling; never provisions resources or reads credentials."""
from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import math
import os
import re
import subprocess
import stat
import sys
import tarfile
import zipfile
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

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


def portable_references(value, root: Path):
    """Convert only paths owned by this run; preserve the core's actual values."""
    prefix = str(root.resolve()) + os.sep
    if isinstance(value, dict):
        return {key: portable_references(item, root) for key, item in value.items()}
    if isinstance(value, list):
        return [portable_references(item, root) for item in value]
    return value[len(prefix):].replace("\\", "/") if isinstance(value, str) and value.startswith(prefix) else value


def referenced_file(root: Path, reference: str, manifest: dict, prefix: str) -> Path:
    if (not isinstance(reference, str) or reference not in manifest["files"]
            or not reference.startswith(prefix) or "\\" in reference or ":" in reference
            or PurePosixPath(reference).is_absolute() or ".." in PurePosixPath(reference).parts):
        raise ValueError("missing or unsafe referenced demo file")
    path = (root / reference).resolve(strict=True)
    if not path.is_relative_to(root.resolve()) or path.is_symlink():
        raise ValueError("referenced demo file outside evidence root")
    return path


def verify_demo(root: Path, manifest: dict, demo: dict, receipt: dict) -> None:
    import cv2
    import numpy as np
    if (demo.get("model_mode") != "fixture" or demo.get("network_used") is not False
            or receipt.get("model_mode") != "fixture" or receipt.get("provider_calls") != 0):
        raise ValueError("demo requires fixture mode and zero provider calls")
    scenarios = demo.get("scenario_results", {})
    if set(scenarios) != {"document-quality", "motion-observation"}:
        raise ValueError("missing document or motion scenario")
    if set(demo.get("trace_paths", {})) != set(scenarios):
        raise ValueError("missing scenario trace references")

    def png(reference, prefix="demo/evidence/"):
        path = referenced_file(root, reference, manifest, prefix)
        data = path.read_bytes()
        if not data.startswith(b"\x89PNG\r\n\x1a\n"):
            raise ValueError("frame evidence is not PNG bytes")
        image = cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_COLOR)
        if image is None or not image.size:
            raise ValueError("frame evidence is not a decodable PNG")
        return image

    traces, evidence = {}, {}
    for name, scenario in scenarios.items():
        if scenario.get("status") != "completed":
            raise ValueError("scenario did not complete")
        reference = demo["trace_paths"][name]
        if reference != f"demo/traces/{name}.json":
            raise ValueError("wrong scenario trace reference")
        trace = json.loads(referenced_file(root, reference, manifest, "demo/traces/").read_text(encoding="utf-8"))
        if not isinstance(trace, list) or not trace or not all(isinstance(event, dict) for event in trace):
            raise ValueError("missing nonempty scenario trace")
        states = [event.get("state") for event in trace if event.get("type") == "state_change"]
        if not states or states[0] != "queued" or states[-1] != "completed":
            raise ValueError("trace does not prove successful scenario states")
        references = scenario.get("evidence_paths", [])
        if not references or len(set(references)) != len(references):
            raise ValueError("missing unique scenario frame references")
        evidence[name] = [(ref, png(ref)) for ref in references]
        traced_ids = [event.get("evidence_id") for event in trace
                      if event.get("type") in {"frame_analyzed", "frame_observed"}]
        if [Path(ref).stem for ref in references] != traced_ids:
            raise ValueError("frame references do not match traced evidence sequence")
        traces[name] = trace

    def frames(ids):
        if not isinstance(ids, list) or not ids or len(set(ids)) != len(ids):
            raise ValueError("missing unique scenario frame IDs")
        parsed = []
        for value in ids:
            try:
                reference, index = value.rsplit(":", 1)
                index = int(index)
            except (AttributeError, ValueError):
                raise ValueError("invalid scenario frame ID") from None
            if index < 0:
                raise ValueError("invalid negative media frame")
            parsed.append((reference, index, referenced_file(root, reference, manifest, "demo/media/")))
        return parsed

    doc = scenarios["document-quality"]
    initial, replacement = frames(doc.get("initial_frame_ids")), frames(doc.get("replacement_frame_ids"))
    if (doc.get("input_resume_status") != "completed" or len(initial) != 1 or len(replacement) != 1
            or initial[0][0] == replacement[0][0] or initial[0][1] != 0 or replacement[0][1] != 0
            or len(evidence["document-quality"]) != 2):
        raise ValueError("document scenario did not prove a distinct input replacement")
    scores = [doc.get("initial_sharpness_variance"), doc.get("replacement_sharpness_variance")]
    if any(isinstance(score, bool) or not isinstance(score, (float, int)) or not math.isfinite(score) for score in scores) or scores[1] <= scores[0]:
        raise ValueError("document scenario requires finite improved sharpness")
    for frame, (_, saved), score in zip(initial + replacement, evidence["document-quality"], scores):
        original = png(frame[0], "demo/media/")
        measured = cv2.Laplacian(cv2.cvtColor(original, cv2.COLOR_BGR2GRAY), cv2.CV_64F).var()
        if not np.array_equal(original, saved) or not math.isclose(float(measured), score, rel_tol=1e-9, abs_tol=1e-9):
            raise ValueError("document frame bytes or sharpness disagree with source media")
    doc_trace = traces["document-quality"]
    waiting = [i for i, event in enumerate(doc_trace) if event.get("state") == "waiting_for_input"]
    analyzed = [(i, event.get("node_id")) for i, event in enumerate(doc_trace) if event.get("type") == "frame_analyzed"]
    if (len(waiting) != 1 or len(analyzed) != 2 or analyzed[0][1] != "analyze_initial"
            or analyzed[1][1] != "analyze_replacement" or not analyzed[0][0] < waiting[0] < analyzed[1][0]):
        raise ValueError("document trace does not prove analyze/pause/reanalyze order")

    motion = scenarios["motion-observation"]
    first, follow = frames(motion.get("initial_frame_ids")), frames(motion.get("follow_up_frame_ids"))
    indices = [item[1] for item in first + follow]
    if (motion.get("follow_up_observed") is not True or len({item[0] for item in first + follow}) != 1
            or indices != sorted(set(indices)) or len(evidence["motion-observation"]) != len(indices)):
        raise ValueError("motion scenario does not prove a forward media cursor")
    trace = traces["motion-observation"]
    decisions = [(i, event.get("state")) for i, event in enumerate(trace) if event.get("type") == "model_decision"]
    observed = [i for i, event in enumerate(trace) if event.get("type") == "frame_observed"]
    analyzed = [event for event in trace if event.get("type") == "frame_analyzed"]
    stopped = [event for event in trace if event.get("type") == "monitor_stopped"]
    if ([decision for _, decision in decisions] != ["observe", "report"] or len(observed) != len(follow)
            or len(analyzed) != len(first) or not all(decisions[0][0] < i < decisions[1][0] for i in observed)
            or len(stopped) != 1 or stopped[0].get("frame_ids") != motion["follow_up_frame_ids"]
            or stopped[0].get("source_id") != first[0][0]
            or any(event.get("model_mode") != "fixture" for event in trace if event.get("type") == "model_mode")):
        raise ValueError("motion trace does not prove fixture observe/frames/report sequence")
    capture = cv2.VideoCapture(str(first[0][2]))
    try:
        if not capture.isOpened():
            raise ValueError("motion source video is not decodable")
        for (_, index, _), (_, saved) in zip(first + follow, evidence["motion-observation"]):
            if not capture.set(cv2.CAP_PROP_POS_FRAMES, index):
                raise ValueError("motion source cannot seek to proven frame")
            success, frame = capture.read()
            if not success or not np.array_equal(frame, saved):
                raise ValueError("motion frame evidence disagrees with actual source frame")
    finally:
        capture.release()


def verify_original_archive(root: Path, archive_path: Path, expected: str) -> None:
    if not isinstance(expected, str) or digest(archive_path.read_bytes()) != expected.lower():
        raise ValueError("AWS artifact ZIP hash mismatch or missing SHA-256")
    with zipfile.ZipFile(archive_path) as archive:
        members = [info for info in archive.infolist() if not info.is_dir()]
        names = [info.filename for info in members]
        if len(names) != len(set(names)):
            raise ValueError("duplicate artifact ZIP members")
        actual = {p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()}
        if set(names) != actual:
            raise ValueError("artifact ZIP and extracted file set differ")
        for info in members:
            name = info.filename
            if (PurePosixPath(name).is_absolute() or ".." in PurePosixPath(name).parts or "\\" in name
                    or ":" in name or stat.S_ISLNK(info.external_attr >> 16)):
                raise ValueError("unsafe artifact ZIP member")
            path = root / name
            if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()) or archive.read(info) != path.read_bytes():
                raise ValueError("extracted artifact bytes differ from original ZIP")


def verify_launch_context(build: dict, receipt: dict, source_zip: Path, context: dict) -> None:
    try:
        stacks = context["stack"]["Stacks"]
        if len(stacks) != 1:
            raise ValueError("one independent stack readback required")
        stack = stacks[0]
        match = re.fullmatch(r"arn:(aws):cloudformation:([^:]+):(\d{12}):stack/([^/]+)/[^/]+", stack["StackId"])
        if not match or match[4] != stack["StackName"] or stack["StackStatus"] not in {"CREATE_COMPLETE", "UPDATE_COMPLETE"}:
            raise ValueError("invalid independent stack identity")
        outputs = {item["OutputKey"]: item["OutputValue"] for item in stack["Outputs"]}
        parameters = {item["ParameterKey"]: item["ParameterValue"] for item in stack["Parameters"]}
        bucket, project, sha = outputs["BucketName"], outputs["ProjectName"], receipt["source_zip_sha256"]
        launch, upload, head = context["launch"]["build"], context["upload"], context["source_head"]
        version = upload["VersionId"]
        checksum = base64.b64encode(bytes.fromhex(sha)).decode("ascii")
        if (not isinstance(version, str) or not version or version == "null"
                or parameters["SourceZipSha256"] != sha or project != stack["StackName"] + "-acceptance"
                or any(response.get("ChecksumSHA256") != checksum or response.get("ChecksumType") != "FULL_OBJECT" for response in (upload, head))
                or head["VersionId"] != version or head["ContentLength"] != source_zip.stat().st_size):
            raise ValueError("versioned S3 source checksum or stack parameter mismatch")
        expected_source = {"type": "S3", "location": f"{bucket}/sources/{sha}.zip", "buildspec": "cloud/buildspec.yml"}
        expected_arn = f"arn:{match[1]}:codebuild:{match[2]}:{match[3]}:build/{build['id']}"
        for record in (build, launch):
            if (record["projectName"] != project or record["id"] != receipt["build_id"]
                    or not record["id"].startswith(project + ":") or record["arn"] != expected_arn
                    or record["sourceVersion"] != version or receipt.get("source_version") != version
                    or any(record["source"].get(key) != value for key, value in expected_source.items())):
                raise ValueError("exact stack/launch/project/source identity mismatch")
        if build["artifacts"]["location"] != f"arn:{match[1]}:s3:::{bucket}/evidence/acceptance.zip":
            raise ValueError("unexpected AWS artifact destination")
    except (KeyError, TypeError, IndexError) as exc:
        raise ValueError("missing independent stack/launch/source identity fields") from exc


def verify_evidence(root: Path, cloud_readback: dict | None = None, source_zip: Path | None = None,
                    evidence_zip: Path | None = None, launch_context: dict | None = None) -> dict:
    manifest = json.loads((root / "evidence-manifest.json").read_text(encoding="utf-8"))
    required = {"receipt.json", "demo-summary.json", "pytest.xml", "pytest.log",
                "source-manifest.json", "demo/traces/document-quality.json",
                "demo/traces/motion-observation.json"}
    if not required.issubset(manifest["files"]):
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
    verify_demo(root, manifest, demo, receipt)
    cloud_verified = False
    if cloud_readback is not None:
        if receipt["execution"] != "aws_codebuild":
            raise ValueError("local evidence is not cloud evidence")
        if source_zip is None or digest(source_zip.read_bytes()) != receipt["source_zip_sha256"]:
            raise ValueError("cloud source ZIP hash mismatch or missing ZIP")
        if evidence_zip is None or launch_context is None:
            raise ValueError("original AWS evidence ZIP and independent launch context are required")
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
        verify_original_archive(root, evidence_zip, build.get("artifacts", {}).get("sha256sum"))
        verify_launch_context(build, receipt, source_zip, launch_context)
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
    write_json(output / "demo-summary.json", portable_references(json.loads(demo.stdout), output))
    for path in (output / "demo" / "traces").glob("*.json"):
        write_json(path, portable_references(json.loads(path.read_text(encoding="utf-8")), output))
    (output / "demo-stderr.log").write_text(demo.stderr, encoding="utf-8")
    freeze = subprocess.check_output([sys.executable, "-m", "pip", "freeze"], text=True)
    (output / "pip-freeze.txt").write_text(freeze, encoding="utf-8")
    write_json(output / "source-manifest.json", source)
    arn, build_id = os.environ.get("CODEBUILD_BUILD_ARN"), os.environ.get("CODEBUILD_BUILD_ID")
    receipt = {"schema_version": 1, "execution": "aws_codebuild" if arn and build_id else "local",
               "build_arn": arn, "build_id": build_id, "source_commit": source["source_commit"],
               "source_zip_sha256": source_zip_sha256, "tests_exit_code": tests.returncode,
               "source_version": os.environ.get("CODEBUILD_SOURCE_VERSION"),
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
    verify.add_argument("--evidence-zip", type=Path)
    verify.add_argument("--launch-context", type=Path)
    args = parser.parse_args()
    if args.command == "package":
        result = package_source(args.repo, args.output)
    elif args.command == "run":
        result = run_acceptance(args.repo.resolve(), args.output.resolve(), args.source_zip_sha256)
    else:
        readback = json.loads(args.cloud_readback.read_text(encoding="utf-8-sig")) if args.cloud_readback else None
        context = json.loads(args.launch_context.read_text(encoding="utf-8-sig")) if args.launch_context else None
        result = verify_evidence(args.evidence, readback, args.source_zip, args.evidence_zip, context)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
