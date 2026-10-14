"""Deploy, check or remove the hosted VisionFlow jury demo (AWS CLI v2 required).

    python cloud/demo/deploy_demo.py deploy  --stack visionflow-demo --region eu-central-1
    python cloud/demo/deploy_demo.py status  --stack visionflow-demo --region eu-central-1
    python cloud/demo/deploy_demo.py destroy --stack visionflow-demo --region eu-central-1

deploy packages the committed HEAD with cloud/acceptance.py, creates or updates
the CloudFormation stack, uploads the source ZIP and waits until the CloudFront
URL serves the interface. The origin secret is generated once and kept in a
local file outside git; it is never printed.
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
TEMPLATE = HERE / "demo-stack.yaml"


def aws(*args: str, region: str) -> str:
    environment = os.environ.copy()
    environment.update(PYTHONUTF8="1", PYTHONIOENCODING="utf-8", AWS_CLI_FILE_ENCODING="utf-8")
    return subprocess.check_output(["aws", *args, "--region", region, "--output", "json"],
                                   text=True, encoding="utf-8", env=environment)


def outputs(stack: str, region: str) -> dict[str, str]:
    data = json.loads(aws("cloudformation", "describe-stacks", "--stack-name", stack, region=region))
    return {o["OutputKey"]: o["OutputValue"] for o in data["Stacks"][0].get("Outputs", [])}


def stack_exists(stack: str, region: str) -> bool:
    try:
        aws("cloudformation", "describe-stacks", "--stack-name", stack, region=region)
    except subprocess.CalledProcessError:
        return False
    return True


def reinstall(instance_id: str, region: str, minutes: int = 20) -> None:
    """Run the instance's idempotent installer for the newly uploaded source (SSM, no SSH)."""
    command = json.loads(aws(
        "ssm", "send-command", "--instance-ids", instance_id, "--document-name", "AWS-RunShellScript",
        "--comment", "VisionFlow reinstall", "--parameters", 'commands=["/opt/visionflow/install.sh 1"]',
        region=region,
    ))["Command"]["CommandId"]
    deadline = time.monotonic() + minutes * 60
    while time.monotonic() < deadline:
        time.sleep(10)
        try:
            status = json.loads(aws("ssm", "get-command-invocation", "--command-id", command,
                                    "--instance-id", instance_id, region=region))["Status"]
        except subprocess.CalledProcessError:
            continue
        if status == "Success":
            return
        if status in {"Failed", "Cancelled", "TimedOut", "Cancelling"}:
            raise RuntimeError(f"reinstall on {instance_id} ended with {status}")
    raise RuntimeError(f"reinstall on {instance_id} did not finish in {minutes} minutes")


def deploy(stack: str, region: str, workdir: Path) -> int:
    workdir.mkdir(parents=True, exist_ok=True)
    secret_file = workdir / "origin-secret.txt"
    if not secret_file.exists():
        secret_file.write_text(secrets.token_hex(24), encoding="utf-8")
    secret = secret_file.read_text(encoding="utf-8").strip()

    bundle = workdir / f"bundle-{time.strftime('%Y%m%dT%H%M%S')}"
    subprocess.check_call([sys.executable, str(REPO / "cloud" / "acceptance.py"), "package",
                           "--repo", str(REPO), "--output", str(bundle)])
    package = json.loads((bundle / "package.json").read_text(encoding="utf-8"))

    prefix = json.loads(aws(
        "ec2", "describe-managed-prefix-lists", "--filters",
        "Name=prefix-list-name,Values=com.amazonaws.global.cloudfront.origin-facing",
        region=region,
    ))["PrefixLists"][0]["PrefixListId"]

    existed = stack_exists(stack, region)
    subprocess.check_call([
        "aws", "cloudformation", "deploy", "--stack-name", stack, "--template-file", str(TEMPLATE),
        "--capabilities", "CAPABILITY_IAM", "--region", region, "--no-fail-on-empty-changeset",
        "--parameter-overrides", f"CloudFrontPrefixListId={prefix}", f"OriginSecret={secret}",
    ])
    out = outputs(stack, region)
    subprocess.check_call([
        "aws", "s3", "cp", package["archive"], f"s3://{out['SourceBucketName']}/{out['SourceKey']}",
        "--region", region,
    ])
    if existed:
        # An unchanged template does not touch the instance: install the new source explicitly.
        reinstall(out["InstanceId"], region)
    record = {"stack": stack, "region": region, "demo_url": out["DemoUrl"], "instance_id": out["InstanceId"],
              "source_commit": package["source_commit"], "source_zip_sha256": package["source_zip_sha256"],
              "uploaded_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "update": existed}
    status = wait_ready(out["DemoUrl"], package["source_commit"], package["source_zip_sha256"])
    record["served_readback"] = status
    (workdir / "deployment.json").write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(record, indent=2))
    return 0 if status.get("matches") else 1


def served_deployment(url: str) -> dict:
    with urllib.request.urlopen(url.rstrip("/") + "/deployment.json", timeout=10) as response:
        return json.loads(response.read().decode("utf-8"))


def wait_ready(url: str, commit: str | None = None, sha256: str | None = None, minutes: int = 30) -> dict:
    """Ready means: the interface answers AND the installed source is the expected one."""
    deadline = time.monotonic() + minutes * 60
    last: dict = {}
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=10) as response:
                page_ok = response.status == 200 and b"VisionFlow" in response.read()
            served = served_deployment(url)
            last = {"page_ok": page_ok, "served": served}
            matches = page_ok and (commit is None or served.get("source_commit") == commit) and (
                sha256 is None or served.get("source_zip_sha256") == sha256)
            if matches:
                print(f"ready: {url} serves {served.get('source_commit')}")
                return {**last, "matches": True}
        except Exception as exc:
            last = {"error": type(exc).__name__}
        time.sleep(15)
    print(f"not ready after {minutes} minutes: {url} {last}", file=sys.stderr)
    return {**last, "matches": False}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("action", choices=("deploy", "status", "destroy"))
    parser.add_argument("--stack", default="visionflow-demo")
    parser.add_argument("--region", default="eu-central-1")
    parser.add_argument("--workdir", type=Path, default=REPO / ".local" / "demo-deploy")
    args = parser.parse_args(argv)
    if args.action == "deploy":
        return deploy(args.stack, args.region, args.workdir)
    if args.action == "status":
        out = outputs(args.stack, args.region)
        status = wait_ready(out["DemoUrl"], minutes=1)
        print(json.dumps({**out, "served": status}, indent=2))
        return 0 if status.get("matches") else 1
    bucket = outputs(args.stack, args.region)["SourceBucketName"]
    subprocess.check_call(["aws", "s3", "rm", f"s3://{bucket}", "--recursive", "--region", args.region])
    subprocess.check_call(["aws", "cloudformation", "delete-stack", "--stack-name", args.stack, "--region", args.region])
    subprocess.check_call(["aws", "cloudformation", "wait", "stack-delete-complete", "--stack-name", args.stack,
                           "--region", args.region])
    print(f"deleted stack {args.stack}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
