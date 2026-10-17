"""Own loopback servers and run the real browser acceptance probes with receipts."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from browser_support import REPO


def stop_owned(process):
    if process.poll() is None:
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
        else:
            process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=10)
    return process.returncode


def start_server(output, public, env):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    name = "public" if public else "local"
    log = (output / f"server-{name}.log").open("w", encoding="utf-8")
    command = [sys.executable, "-m", "visionflow.cli", "serve", "--port", str(port),
               "--data-dir", str(output / f"data-{name}")]
    if public:
        command.append("--public-demo")
    process = subprocess.Popen(command, cwd=REPO, env=env, stdout=log, stderr=log)
    url = f"http://127.0.0.1:{port}"
    try:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError(f"{name} server exited with {process.returncode}")
            try:
                with urllib.request.urlopen(url + "/tools", timeout=.5) as response:
                    if response.status == 200:
                        return process, log, url
            except OSError:
                time.sleep(.1)
        raise TimeoutError(f"{name} loopback server did not become ready")
    except BaseException:
        stop_owned(process)
        log.close()
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--only", choices=("errors", "guidance", "recovery", "layout", "workflows"))
    parser.add_argument("--probe", choices=("errors", "guidance", "recovery", "layout", "workflows"), help=argparse.SUPPRESS)
    parser.add_argument("--base-url", help=argparse.SUPPRESS)
    parser.add_argument("--public-url", help=argparse.SUPPRESS)
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    result = {"status": "failed", "checks": {}, "browser_errors": [], "exit_codes": {}}
    if args.probe:
        try:
            if args.probe == "errors":
                from gui_recovery_errors import run
                result = run(args.base_url, output, public_url=args.public_url)
            elif args.probe == "guidance":
                from ticket_guidance_browser import run
                result = run(args.base_url, output)
            elif args.probe == "layout":
                from layout_regression_browser import run
                result = run(args.base_url, output)
            elif args.probe == "workflows":
                from public_workflows_browser import run
                result = run(args.public_url, output)
            else:
                from road_recovery_browser import run
                result = run(args.base_url, output)
        except Exception as error:
            result["harness_error"] = {"type": type(error).__name__, "message": str(error)}
        (output / "acceptance.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        return 0 if result["status"] == "passed" else 1

    result["source_commit"] = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPO, text=True).strip()
    from importlib.metadata import version
    result["runtime"] = {"python": sys.version, "executable": sys.executable,
                         "platform": platform.platform(), "playwright": version("playwright")}
    result["source_dirty"] = bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=REPO))
    product = sorted((REPO / "src" / "visionflow").rglob("*"))
    result["source_hashes"] = {p.relative_to(REPO).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                                for p in product if p.is_file() and "__pycache__" not in p.parts}
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}
    servers = []
    try:
        local = start_server(output, False, env)
        servers.append(local)
        public = start_server(output, True, env)
        servers.append(public)
        for probe in ([args.only] if args.only else ["errors", "guidance", "recovery", "layout", "workflows"]):
            command = [sys.executable, str(Path(__file__).resolve()), "--probe", probe,
                       "--base-url", local[2], "--public-url", public[2], "--output", str(output / probe)]
            with (output / f"{probe}.log").open("w", encoding="utf-8") as log:
                process = subprocess.Popen(command, cwd=REPO, env=env, stdout=log, stderr=log)
                try:
                    code = process.wait(timeout=420)
                except subprocess.TimeoutExpired:
                    stop_owned(process)
                    code = 124
            result["exit_codes"][probe] = code
            receipt = output / probe / "acceptance.json"
            child = json.loads(receipt.read_text(encoding="utf-8")) if receipt.exists() else {}
            result["checks"][f"{probe}_completed"] = code == 0 and child.get("status") == "passed"
            result["checks"].update({f"{probe}.{name}": value for name, value in child.get("checks", {}).items()})
            result["browser_errors"].extend(child.get("browser_errors", []))
            if "harness_error" in child:
                result.setdefault("harness_errors", {})[probe] = child["harness_error"]
    except Exception as error:
        result["harness_error"] = {"type": type(error).__name__, "message": str(error)}
    finally:
        result["server_exit_codes"] = []
        for process, log, _ in reversed(servers):
            result["server_exit_codes"].append(stop_owned(process))
            log.close()
        result["checks"]["owned_servers_stopped"] = all(p.poll() is not None for p, _, _ in servers)
    if result["checks"] and all(result["checks"].values()) and not result["browser_errors"] and not result.get("harness_error"):
        result["status"] = "passed"
    (output / "acceptance.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"status": result["status"], "checks": result["checks"], "exit_codes": result["exit_codes"]}, ensure_ascii=True))
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
