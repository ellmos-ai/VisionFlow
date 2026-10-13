"""One reproducible AWS CodeBuild acceptance run with full readback and cleanup.

    python cloud/run_codebuild_acceptance.py --region eu-central-1 --stack visionflow-acceptance-YYYYMMDD

Steps (the same as docs/AWS_ACCEPTANCE.md): package HEAD -> deploy cloud/template.json ->
upload the source ZIP with a SHA-256 checksum to the versioned bucket -> read it back and compare
bytes -> start exactly one build pinned to that object version -> wait for a terminal state ->
save the readback -> download the evidence ZIP -> cloud/acceptance.py verify with all five
inputs -> empty the bucket (all versions) and delete the stack. Nothing is retried.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import subprocess
import sys
import time
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def aws(*args: str, region: str, parse: bool = True):
    out = subprocess.check_output(["aws", *args, "--region", region, "--output", "json"], text=True)
    return json.loads(out) if parse and out.strip() else out


def save(path: Path, data) -> None:
    path.write_text(json.dumps(data, indent=2, default=str) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--region", default="eu-central-1")
    parser.add_argument("--stack", required=True)
    parser.add_argument("--workdir", type=Path, default=REPO / ".local" / "aws-acceptance")
    parser.add_argument("--keep", action="store_true", help="skip cleanup")
    args = parser.parse_args()
    region, stack = args.region, args.stack
    work = args.workdir / stack
    work.mkdir(parents=True, exist_ok=False)

    bundle = work / "bundle"
    subprocess.check_call([sys.executable, str(REPO / "cloud" / "acceptance.py"), "package", "--repo", str(REPO),
                           "--output", str(bundle)])
    package = json.loads((bundle / "package.json").read_text(encoding="utf-8"))
    sha = package["source_zip_sha256"]
    archive = Path(package["archive"])
    assert hashlib.sha256(archive.read_bytes()).hexdigest() == sha

    template = REPO / "cloud" / "template.json"
    aws("cloudformation", "validate-template", "--template-body", f"file://{template}", region=region)
    subprocess.check_call(["aws", "cloudformation", "deploy", "--stack-name", stack, "--template-file", str(template),
                           "--parameter-overrides", f"SourceZipSha256={sha}", "--capabilities", "CAPABILITY_IAM",
                           "--region", region])
    stack_readback = aws("cloudformation", "describe-stacks", "--stack-name", stack, region=region)
    outputs = {o["OutputKey"]: o["OutputValue"] for o in stack_readback["Stacks"][0]["Outputs"]}
    bucket, project = outputs["BucketName"], outputs["ProjectName"]

    checksum = base64.b64encode(bytes.fromhex(sha)).decode()
    key = f"sources/{sha}.zip"
    upload = aws("s3api", "put-object", "--bucket", bucket, "--key", key, "--body", str(archive),
                 "--server-side-encryption", "AES256", "--checksum-algorithm", "SHA256", "--checksum-sha256", checksum,
                 region=region)
    version = upload.get("VersionId")
    if not version or version == "null":
        raise SystemExit("a versioned S3 source is required")
    head = aws("s3api", "head-object", "--bucket", bucket, "--key", key, "--version-id", version,
               "--checksum-mode", "ENABLED", region=region)
    returned = work / "aws-source.zip"
    subprocess.check_call(["aws", "s3api", "get-object", "--bucket", bucket, "--key", key, "--version-id", version,
                           "--checksum-mode", "ENABLED", "--region", region, str(returned)],
                          stdout=subprocess.DEVNULL)
    if hashlib.sha256(returned.read_bytes()).hexdigest() != sha:
        raise SystemExit("AWS source bytes differ from the packaged ZIP")

    launch = aws("codebuild", "start-build", "--project-name", project, "--source-version", version, region=region)
    context = {"stack": stack_readback, "launch": launch, "upload": upload, "source_head": head}
    save(work / "launch-context.json", context)
    build_id = launch["build"]["id"]
    print("build", build_id, flush=True)

    deadline = time.monotonic() + 30 * 60
    while True:
        readback = aws("codebuild", "batch-get-builds", "--ids", build_id, region=region)
        status = readback["builds"][0]["buildStatus"]
        if status != "IN_PROGRESS" or time.monotonic() > deadline:
            break
        time.sleep(20)
    save(work / "aws-build-readback.json", readback)
    print("status", status, flush=True)
    result = {"stack": stack, "region": region, "build_id": build_id, "status": status,
              "source_commit": package["source_commit"], "source_zip_sha256": sha}
    if status == "SUCCEEDED":
        location = readback["builds"][0]["artifacts"]["location"].replace("arn:aws:s3:::", "s3://")
        evidence_zip = work / "evidence.zip"
        subprocess.check_call(["aws", "s3", "cp", location, str(evidence_zip), "--region", region])
        evidence_dir = work / "evidence"
        with zipfile.ZipFile(evidence_zip) as archive_file:
            archive_file.extractall(evidence_dir)
        verify = subprocess.run(
            [sys.executable, str(REPO / "cloud" / "acceptance.py"), "verify", "--evidence", str(evidence_dir),
             "--evidence-zip", str(evidence_zip), "--cloud-readback", str(work / "aws-build-readback.json"),
             "--source-zip", str(returned), "--launch-context", str(work / "launch-context.json")],
            capture_output=True, text=True,
        )
        (work / "verify.txt").write_text(verify.stdout + verify.stderr, encoding="utf-8")
        result["verify_exit"] = verify.returncode
        try:
            result["verify"] = json.loads(verify.stdout.strip().splitlines()[-1])
        except (IndexError, json.JSONDecodeError):
            result["verify"] = None
    save(work / "result.json", result)

    if not args.keep:
        versions = aws("s3api", "list-object-versions", "--bucket", bucket, region=region)
        objects = [{"Key": v["Key"], "VersionId": v["VersionId"]}
                   for v in (versions.get("Versions") or []) + (versions.get("DeleteMarkers") or [])]
        if objects:
            spec = work / "delete-own-versions.json"
            save(spec, {"Objects": objects, "Quiet": True})
            aws("s3api", "delete-objects", "--bucket", bucket, "--delete", f"file://{spec}", region=region)
        subprocess.check_call(["aws", "cloudformation", "delete-stack", "--stack-name", stack, "--region", region])
        subprocess.check_call(["aws", "cloudformation", "wait", "stack-delete-complete", "--stack-name", stack,
                               "--region", region])
        result["cleanup"] = "stack deleted"
        save(work / "result.json", result)
    print(json.dumps(result, indent=2, default=str))
    return 0 if status == "SUCCEEDED" and result.get("verify_exit") == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
