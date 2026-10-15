"""Real Edge/Chromium recovery journey; never resets the server deadline."""
from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

from browser_support import REPO, launch_browser, venv_python


def run(base_url: str, output: Path) -> dict:
    from playwright.sync_api import sync_playwright

    result = {"checks": {}, "browser_errors": []}
    checks = result["checks"]
    with sync_playwright() as pw:
        browser, result["browser"] = launch_browser(pw)
        context = browser.new_context(viewport={"width": 1440, "height": 1000})
        page = context.new_page()
        page.on("pageerror", lambda e: result["browser_errors"].append(str(e)))
        try:
            page.goto(base_url + "/?lang=en", wait_until="networkidle")
            page.locator("#cartoon-fixture-profile").select_option("pending")
            page.locator("#btn-sim-wrongway").click()
            page.locator("#btn-sim-leaf").click()
            page.wait_for_function("() => document.querySelector('#journey-ticket-status').textContent.includes('Human review')", timeout=100000)
            case_id = page.locator("#journey-ticket-select").input_value()
            before = page.evaluate("id => window.roadApiClient.get('/road/cases/' + id)", case_id)
            assert before["phase"] == "human_review" and 40 < before["remaining_seconds"] <= 60
            checks["real_server_case"] = True
            page.reload(wait_until="domcontentloaded")
            page.locator("#journey-ticket-select option").first.wait_for(state="attached", timeout=10000)
            assert page.locator("#journey-ticket-select").input_value() == case_id
            assert page.locator("#journey-ticket-title").inner_text() == "Ticket #1"
            checks["reload_restores_selected_ticket"] = True
            after = page.evaluate("id => window.roadApiClient.get('/road/cases/' + id)", case_id)
            assert before["content_hash"] == after["content_hash"]
            assert before["clip"] == after["clip"]
            assert before["deadline_at"] == after["deadline_at"]
            assert after["remaining_seconds"] < before["remaining_seconds"]
            checks["deadline_and_clip_unchanged"] = True
            page.locator("#nav-node-room-3").click()
            page.locator("#cartoon-review-video-box video").wait_for(timeout=15000)
            page.get_by_role("button", name="🌐 DE", exact=True).first.click()
            page.wait_for_function("() => document.documentElement.lang === 'de'")
            page.locator("#journey-ticket-select option").first.wait_for(state="attached")
            assert page.locator("#journey-ticket-select").input_value() == case_id
            checks["language_change_restores_ticket"] = True
            foreign = browser.new_context()
            try:
                other = foreign.new_page()
                other.goto(base_url, wait_until="networkidle")
                other.wait_for_function("() => Boolean(window.roadApiClient)")
                other.evaluate("id => sessionStorage.setItem('visionflow.road.selected', id)", case_id)
                other.reload(wait_until="domcontentloaded")
                other.wait_for_function("() => Boolean(window.roadApiClient)")
                assert other.evaluate("() => window.roadApiClient.get('/road/cases')") == {"cases": []}
                assert other.locator("#journey-ticket-select option").count() == 0
                checks["tampered_selection_cannot_cross_owner"] = True
            finally:
                foreign.close()
            page.locator("#nav-node-room-4").click()
            page.wait_for_function("() => !document.querySelector('#beta-alert-msg').hidden", timeout=70000)
            page.reload(wait_until="domcontentloaded")
            page.locator("#journey-ticket-select option").first.wait_for(state="attached")
            page.locator("#nav-node-room-4").click()
            page.locator("#btn-weber-reject").click()
            page.wait_for_function("() => document.querySelector('#journey-ticket-status').textContent.includes('Entwarnung nachgesendet')", timeout=15000)
            inbox = page.evaluate("() => window.roadApiClient.get('/road/inbox')")
            assert sum(r["case_id"] == case_id and r["kind"] == "first_report" for r in inbox) == 1
            assert sum(r["case_id"] == case_id and r["kind"] == "correction" for r in inbox) == 1
            checks["recovery_after_deadline_allows_single_correction"] = True
            page.screenshot(path=str(output / "recovered-and-corrected.png"), full_page=True)
            result["case_id"] = case_id
            result["status"] = "passed" if not result["browser_errors"] else "failed"
        except Exception as exc:
            result.update(status="failed", error=str(exc))
            page.screenshot(path=str(output / "failure.png"), full_page=True)
        finally:
            context.close()
            browser.close()
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    with (args.output / "server.log").open("w", encoding="utf-8") as log:
        process = subprocess.Popen([str(venv_python()), "-m", "visionflow.cli", "serve", "--port", str(port), "--data-dir", str(args.output.resolve() / "data")], cwd=REPO, env=env, stdout=log, stderr=log)
        try:
            for _ in range(150):
                if process.poll() is not None:
                    raise RuntimeError("Server exited during startup")
                try:
                    with socket.create_connection(("127.0.0.1", port), timeout=.2):
                        break
                except OSError:
                    time.sleep(.1)
            result = run(f"http://127.0.0.1:{port}", args.output)
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
    (args.output / "acceptance.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=True))
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
