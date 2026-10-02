from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import time
import traceback
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


REPO = Path(__file__).resolve().parents[1]
EDGE = Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe")
VENV_PYTHON = REPO / ".venv" / "Scripts" / "python.exe"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run VisionFlow road-camera acceptance in native headless Edge."
    )
    parser.add_argument("--phase", choices=("camera", "review"), required=True)
    parser.add_argument(
        "--scenario",
        choices=("normal", "wrong_way", "bird", "leaf"),
        default="wrong_way",
    )
    parser.add_argument(
        "--profile",
        choices=("pending", "high", "bird", "leaf", "followup", "unclear", "error"),
        default="high",
    )
    parser.add_argument(
        "--output",
        required=True,
        help="A new evidence directory below the repository .local folder.",
    )
    parser.add_argument("--timeout", type=float, default=100.0)
    return parser.parse_args()


def evidence_directory(raw: str) -> Path:
    root = (REPO / ".local").resolve()
    output = Path(raw)
    if not output.is_absolute():
        output = REPO / output
    output = output.resolve()
    if output == root or root not in output.parents:
        raise ValueError("--output must resolve inside the repository .local directory")
    if output.exists():
        raise FileExistsError("Evidence directory already exists: " + str(output))
    output.mkdir(parents=True)
    return output


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def wait_for_port(port: int, process: subprocess.Popen[bytes], timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError("VisionFlow server exited during startup")
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.25):
                return
        except OSError:
            time.sleep(0.1)
    raise TimeoutError("VisionFlow server did not open its loopback port")


def browser_probe(base_url: str, output: Path, phase: str) -> dict[str, Any]:
    from playwright.sync_api import sync_playwright

    errors: list[str] = []
    external_hosts: set[str] = set()
    report: dict[str, Any] = {
        "phase": phase,
        "base_url": base_url,
        "browser": "Microsoft Edge",
        "status": "running",
        "checks": {},
    }

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            executable_path=str(EDGE),
            headless=True,
            args=["--autoplay-policy=no-user-gesture-required"],
        )
        context = browser.new_context(viewport={"width": 1280, "height": 900})

        def guard_external(route: Any) -> None:
            host = urlparse(route.request.url).hostname or ""
            if host not in {"127.0.0.1", "localhost"}:
                external_hosts.add(host)
                route.abort()
                return
            route.continue_()

        context.route("**/*", guard_external)
        page = context.new_page()
        page.on("pageerror", lambda _error: errors.append("pageerror"))
        page.on(
            "console",
            lambda message: errors.append("console_error")
            if message.type == "error"
            else None,
        )
        page.goto(base_url + "/", wait_until="domcontentloaded", timeout=30000)
        page.locator("#road-tab").wait_for(state="visible", timeout=15000)
        page.wait_for_function(
            "() => document.querySelector('#road-tab') && !document.querySelector('#road-tab').disabled",
            timeout=15000,
        )
        page.screenshot(path=str(output / "landing.png"), full_page=True)
        page.locator("#road-tab").click()
        page.locator(".road-camera").wait_for(state="visible", timeout=15000)

        required = {
            "model_mode": "#road-model-mode",
            "fixture_profile": "#road-fixture-profile",
            "model_workspace": "#road-model-root",
            "workplace_workspace": "#road-workplace-root",
        }
        missing = [name for name, selector in required.items() if page.locator(selector).count() != 1]
        report["checks"]["camera_case_controls"] = not missing
        report["missing_camera_case_controls"] = missing
        page.screenshot(path=str(output / "road-camera-before-start.png"), full_page=True)
        if missing:
            raise AssertionError(
                "CameraCase UI controls/areas are missing: " + ", ".join(missing)
            )

        report["checks"]["no_browser_errors"] = not errors
        report["checks"]["no_external_requests"] = not external_hosts
        report["browser_errors"] = errors
        report["external_hosts"] = sorted(external_hosts)
        report["status"] = "passed"
        page.screenshot(path=str(output / "road-camera-ready.png"), full_page=True)
        context.close()
        browser.close()
    return report


def main() -> int:
    args = parse_args()
    output = evidence_directory(args.output)
    port = free_port()
    base_url = "http://127.0.0.1:" + str(port)
    server_log_path = output / "server.log"
    server_log = server_log_path.open("w", encoding="utf-8", newline="\n")
    process: subprocess.Popen[bytes] | None = None
    result: dict[str, Any] = {
        "phase": args.phase,
        "scenario": args.scenario,
        "fixture_profile": args.profile,
        "status": "failed",
        "server_exit_code": None,
    }
    try:
        if not VENV_PYTHON.is_file():
            raise FileNotFoundError("Project virtual-environment Python is missing")
        if not EDGE.is_file():
            raise FileNotFoundError("Microsoft Edge executable is missing")
        process = subprocess.Popen(
            [
                str(VENV_PYTHON),
                "-m",
                "visionflow.cli",
                "serve",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
                "--data-dir",
                str(output / "server-data"),
            ],
            cwd=REPO,
            stdout=server_log,
            stderr=subprocess.STDOUT,
        )
        wait_for_port(port, process, min(args.timeout, 30.0))
        result.update(browser_probe(base_url, output, args.phase))
        result["server_exit_code"] = process.poll()
    except Exception as exc:
        result["status"] = "failed"
        result["error_type"] = type(exc).__name__
        result["error"] = str(exc)
        result["traceback"] = traceback.format_exc()
        result["server_exit_code"] = process.poll() if process is not None else None
    finally:
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        server_log.close()
        result["server_log"] = str(server_log_path)
        (output / "acceptance.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
