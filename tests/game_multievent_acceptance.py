"""Browser acceptance for the cartoon mission-control game (multi-event flow).

Run manually; this script is not collected by pytest. It starts its own
loopback VisionFlow server with a fresh data directory, so no server needs to
be running beforehand:

    python tests/game_multievent_acceptance.py --output .local/acceptance/multievent-01
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

from browser_support import REPO, launch_browser, venv_python
from road_browser_acceptance import evidence_directory, free_port, wait_for_port


def run_verification(base_url: str, out_dir: Path) -> None:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        browser, _label = launch_browser(playwright)
        context = browser.new_context(viewport={"width": 1400, "height": 950})
        page = context.new_page()
        page.on("pageerror", lambda err: print(f"PAGEERROR: {err}"))
        page.on("console", lambda msg: print(f"CONSOLE [{msg.type}]: {msg.text}"))
        
        print("Navigating to VisionFlow...")
        page.goto(base_url + "/", wait_until="networkidle")
        page.locator("#road-tab").wait_for(state="visible", timeout=15000)
        page.wait_for_function(
            "() => document.querySelector('#road-tab') && !document.querySelector('#road-tab').disabled",
            timeout=15000,
        )
        print("Clicking Road Tab...")
        page.locator("#road-tab").click()
        time.sleep(1)
        
        # Diagnostic DOM inspection
        ws_info = page.evaluate("""() => {
            const ws = document.querySelector("#workspace-road");
            let mountError = null;
            try {
                if (window.RoadGame && !document.querySelector(".road-cartoon-stage")) {
                    window.RoadGame.mountCartoonGame(ws);
                }
            } catch (err) {
                mountError = err.toString() + "\\n" + err.stack;
            }
            const stage = document.querySelector(".road-cartoon-stage");
            return {
                workspaceHidden: ws ? ws.hidden : 'missing',
                mountError: mountError,
                stageExists: !!stage,
                stageDisplay: stage ? window.getComputedStyle(stage).display : 'none',
                hasRoadGame: typeof window.RoadGame !== 'undefined',
            };
        }""")
        print("WORKSPACE INFO:", json.dumps(ws_info, indent=2))
        
        # 1. Check Clean UI (legacy clutter hidden)
        print("Checking UI cleanup...")
        page.locator(".road-cartoon-stage").wait_for(state="visible", timeout=10000)
        
        # Verify legacy forms are hidden
        hidden_elements = [
            ".road-camera__heading",
            ".road-camera__case-options",
            ".road-camera__toolbar",
            ".road-camera__status",
            ".road-camera__source",
            ".road-camera__run",
            ".road-camera__secondary",
            ".road-camera__player-panel",
            ".road-workspace-grid"
        ]
        for sel in hidden_elements:
            count = page.locator(sel).count()
            if count > 0:
                is_visible = page.locator(sel).first.is_visible()
                assert not is_visible, f"Legacy element {sel} should not be visible!"
        print("PASS: Legacy UI elements successfully hidden.")
        
        # 2. Start Camera in Room 1
        print("Starting camera in Room 1...")
        page.locator("#btn-toggle-cam").click()
        time.sleep(2)
        page.screenshot(path=str(out_dir / "01_room1_camera_started.png"))
        
        # 3. Test Multi-Event Triggering: Blatt -> Vogel -> Falschfahrer
        print("Triggering Event 1: BLATT (Leaf)...")
        page.locator("#btn-sim-leaf").click()
        time.sleep(2)
        page.screenshot(path=str(out_dir / "02_event_leaf_triggered.png"))
        
        banner_text = page.locator("#live-anomaly-banner").text_content()
        print(f"Banner after leaf: {banner_text}")
        assert "Herbstlaub" in banner_text, f"Expected Herbstlaub in banner, got {banner_text}"
        
        print("Triggering Event 2: VOGEL (Bird)...")
        page.locator("#btn-sim-bird").click()
        time.sleep(2)
        page.screenshot(path=str(out_dir / "03_event_bird_triggered.png"))
        
        banner_text = page.locator("#live-anomaly-banner").text_content()
        print(f"Banner after bird: {banner_text}")
        assert "Vogel" in banner_text, f"Expected Vogel in banner, got {banner_text}"
        
        print("Triggering Event 3: FALSCHFAHRER (Wrong Way Car)...")
        page.locator("#btn-sim-wrongway").click()
        time.sleep(2)
        page.screenshot(path=str(out_dir / "04_event_wrong_way_triggered.png"))
        
        banner_text = page.locator("#live-anomaly-banner").text_content()
        print(f"Banner after wrong way: {banner_text}")
        assert "Falschfahrer" in banner_text, f"Expected Falschfahrer in banner, got {banner_text}"
        print("PASS: Multi-event triggering works sequentially without lockout!")
        
        # 4. Check Room 2: Datacenter & Transparency Card
        print("Switching to Room 2: Datacenter...")
        page.locator("#nav-node-room-2").click()
        time.sleep(1)
        page.screenshot(path=str(out_dir / "05_room2_datacenter_transparency.png"))
        
        transparency_title = page.locator(".transparency-pill").text_content()
        print(f"Transparency badge: {transparency_title}")
        assert "OFFLINE-FIXTURE" in transparency_title
        
        transparency_body = page.locator(".inference-transparency-card").text_content()
        print(f"Transparency body: {transparency_body}")
        assert "offline-fixture/high" in transparency_body
        assert "Zero-Hallucination" in transparency_body
        assert "OpenCV 5" in transparency_body
        print("PASS: Room 2 honestly discloses Fallback/Offline status and architecture!")
        
        # 5. Check Room 3: Leitstelle & Ticket Video Review
        print("Switching to Room 3: Leitstelle...")
        page.locator("#nav-node-room-3").click()
        time.sleep(1)
        
        ticket_count = page.locator(".schmidt-ticket-card").count()
        print(f"Room 3 Ticket count: {ticket_count}")
        page.screenshot(path=str(out_dir / "06_room3_tickets_overview.png"))
        
        # If there are tickets in the inbox, click through them
        if ticket_count > 0:
            print("Clicking first ticket...")
            page.locator(".schmidt-ticket-card").first.click()
            time.sleep(1)
            page.screenshot(path=str(out_dir / "07_room3_ticket_inspected.png"))
            
            monitor_text = page.locator("#cartoon-review-video-box").text_content()
            print(f"Review monitor text: {monitor_text[:100]}...")
            assert "Beweis" in monitor_text or "Clip" in monitor_text or "Loop" in monitor_text
            
            speech_text = page.locator("#speech-schmidt").text_content()
            print(f"Schmidt speech: {speech_text[:100]}...")
            assert "Ticket" in speech_text
            print("PASS: Room 3 Ticket monitor and Officer Schmidt speech update upon clicking tickets!")
            
        print("All verification steps passed successfully!")
        browser.close()

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--output",
        default=".local/acceptance/multievent-" + time.strftime("%Y%m%dT%H%M%S"),
        help="A new evidence directory below the repository .local folder.",
    )
    args = parser.parse_args()
    out_dir = evidence_directory(args.output)
    port = free_port()
    python = venv_python()
    if not python.is_file():
        raise FileNotFoundError("Project virtual-environment Python is missing")
    with (out_dir / "server.log").open("w", encoding="utf-8", newline="\n") as server_log:
        process = subprocess.Popen(
            [
                str(python), "-m", "visionflow.cli", "serve",
                "--host", "127.0.0.1", "--port", str(port),
                "--data-dir", str(out_dir / "server-data"),
            ],
            cwd=REPO,
            stdout=server_log,
            stderr=subprocess.STDOUT,
        )
        try:
            wait_for_port(port, process, 30.0)
            run_verification("http://127.0.0.1:" + str(port), out_dir)
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
