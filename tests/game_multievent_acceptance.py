"""Browser acceptance for the cartoon mission-control game against real cases.

Run manually; this script is not collected by pytest. It starts its own
loopback VisionFlow server with a fresh data directory (FFmpeg with libx264 is
required) and drives two complete road cases through the game:

1. Profile ``pending``: inject a wrong-way driver, check that a second event
   (leaf) waits for the countdown and the server rejects it while the first is
   analysed, wait for the real human review, check that the game shows the
   backend's assessment, the server-verified clip hash and that a second event
   starts the next case on the same camera during review, then confirm in room 3.
2. Profile ``leaf``: inject a bird into a fresh case and check that the game
   shows the fixture's leaf verdict, not the injected scenario.
3. Profile ``pending`` without a decision: room 4 shows the real deadline, the
   server reports automatically on expiry, and a late rejection in room 3
   sends the correction to the simulated inbox.

    python tests/game_multievent_acceptance.py --output .local/acceptance/multievent-01
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

from browser_support import REPO, assert_event_cooldown, launch_browser, venv_python
from road_browser_acceptance import evidence_directory, free_port, wait_for_port


def _text(page: Any, selector: str) -> str:
    return page.locator(selector).first.text_content() or ""


def _wait_text(page: Any, selector: str, needle: str, timeout_ms: int) -> str:
    page.wait_for_function(
        "([selector, needle]) => { const el = document.querySelector(selector);"
        " return Boolean(el && el.textContent.includes(needle)); }",
        arg=[selector, needle],
        timeout=timeout_ms,
    )
    return _text(page, selector)


def run_verification(base_url: str, out_dir: Path) -> dict[str, Any]:
    from playwright.sync_api import sync_playwright

    errors: list[str] = []
    external_hosts: set[str] = set()
    checks: dict[str, bool] = {}

    with sync_playwright() as playwright:
        browser, label = launch_browser(playwright)
        context = browser.new_context(viewport={"width": 1400, "height": 950})

        def guard_external(route: Any) -> None:
            host = urlparse(route.request.url).hostname or ""
            if host not in {"127.0.0.1", "localhost"}:
                external_hosts.add(host)
                route.abort()
                return
            route.continue_()

        context.route("**/*", guard_external)
        page = context.new_page()
        page.on("pageerror", lambda err: errors.append(f"pageerror: {err}"))
        page.on("console", lambda msg: errors.append(f"console: {msg.text}") if msg.type == "error" else None)

        # English is the default UI language.
        page.goto(base_url + "/", wait_until="networkidle")
        page.locator(".road-cartoon-stage").wait_for(state="visible", timeout=15000)
        assert page.evaluate("document.documentElement.lang") == "en"
        title_en = page.locator("#cartoon-title").inner_text()
        assert "VisionFlow" in title_en and "FlowVision" not in title_en, title_en
        assert "Inject wrong-way driver" in page.locator("#btn-sim-wrongway").inner_text()
        checks["english_default_ui"] = True

        # The remaining checks assert the German texts.
        page.goto(base_url + "/?lang=de", wait_until="networkidle")
        page.locator(".road-cartoon-stage").wait_for(state="visible", timeout=15000)

        # Legacy controls are hidden while the game drives the camera; the real
        # review panels are only hidden until "Technische Fallansicht" is opened.
        for selector in (".road-camera__toolbar", ".road-camera__case-options", ".road-workspace-grid"):
            if page.locator(selector).count():
                assert not page.locator(selector).first.is_visible(), f"{selector} should be hidden"
        page.locator("#cartoon-details-btn").click()
        assert page.locator(".road-workspace-grid").first.is_visible()
        page.locator("#cartoon-details-btn").click()
        checks["legacy_controls_hidden_details_toggle"] = True

        # --- Ticket 1: profile pending --------------------------------------------------
        page.wait_for_function("() => document.querySelectorAll('#cartoon-fixture-profile option').length > 0", timeout=15000)
        page.locator("#cartoon-fixture-profile").select_option("pending")
        page.locator("#btn-sim-wrongway").click()
        banner = _wait_text(page, "#live-anomaly-banner", "Falschfahrer", 40000)
        print("Banner:", banner)
        assert "Ticket" not in banner and "detektiert" not in banner, banner
        page.screenshot(path=str(out_dir / "01_wrong_way_injected.png"))
        # One event per analysis: the leaf waits for the countdown; the server enforces it.
        cooldown = assert_event_cooldown(page)
        print("Cooldown:", cooldown)
        checks["second_event_waits_for_countdown"] = True

        print("Waiting for the detection that creates ticket #1 ...")
        created = _wait_text(page, "#live-anomaly-banner", "Ticket #1 erstellt", 90000)
        print("Created:", created)
        checks["detection_creates_ticket"] = True
        page.wait_for_function(
            "() => { const b = document.querySelector('#btn-schmidt-radio'); return b && !b.disabled; }",
            timeout=90000,
        )
        _wait_text(page, "#nav-badge-3", "1", 10000)
        checks["room_badge_counts_open_ticket"] = True
        page.locator("#nav-node-room-2").click()
        verdict = _text(page, "#datacenter-verdict-box")
        print("Verdict:", verdict)
        assert "Ticket #1" in verdict and "Beweisclip prüfen und entscheiden" in verdict, verdict
        assert "Fahrzeug · Gefahr · Sicherheit 0,80" in verdict, verdict
        page.locator("#cartoon-room-2 details > summary").click()
        technical = _text(page, "#datacenter-case-detail")
        assert "Profil „pending“" in technical and "Falschfahrer" in technical
        assert "Feste Offline-Testantwort" in _text(page, "#transparency-pill-text")
        assert "fixture/pending" in _text(page, "#transparency-profile-val")
        page.screenshot(path=str(out_dir / "02_room2_real_assessment.png"))
        checks["room2_shows_backend_assessment"] = True

        # --- Ticket 2 starts on the same camera while ticket 1 waits for a human ------
        source_before = page.locator("section.road-camera").first.get_attribute("data-source-id")
        page.locator("#nav-node-room-1").click()
        page.locator("#cartoon-fixture-profile").select_option("leaf")
        page.locator("#btn-sim-bird").click()
        _wait_text(page, "#speech-alex", "Kamera läuft weiter", 10000)
        _wait_text(page, "#live-anomaly-banner", "Vogel", 40000)
        source_after = page.locator("section.road-camera").first.get_attribute("data-source-id")
        print("Source:", source_before, "->", source_after)
        assert source_before and source_before == source_after
        checks["second_event_during_review_starts_next_case_on_same_camera"] = True

        page.locator("#nav-node-room-3").click()
        if page.locator("#journey-ticket-select").is_visible():
            page.locator("#journey-ticket-select").select_option(label="Ticket #1 · Menschliche Prüfung offen")
        page.wait_for_function(
            "() => { const b = document.querySelector('#btn-schmidt-radio'); return b && !b.disabled; }",
            timeout=20000,
        )
        assert "Ticket #1" in _text(page, "#schmidt-ticket-list")
        page.locator("#cartoon-review-video-box video").wait_for(state="attached", timeout=20000)
        monitor = _wait_text(page, "#cartoon-review-video-box", "Prüfsumme vom Server bestätigt", 20000)
        print("Monitor:", monitor)
        assert page.locator("#cartoon-review-video-box video").get_attribute("src").startswith("blob:")
        page.screenshot(path=str(out_dir / "03_room3_server_clip.png"))
        checks["room3_plays_verified_server_clip"] = True

        page.locator("#btn-schmidt-radio").click()
        accepted = _wait_text(page, "#speech-schmidt", "angenommen", 20000)
        print("Decision:", accepted)
        _wait_text(page, "#schmidt-ticket-list", "An Behörde gemeldet", 20000)
        inbox = page.evaluate("() => window.roadApiClient.get('/road/inbox')")
        assert any(record.get("human_confirmed") is True for record in inbox), inbox
        page.screenshot(path=str(out_dir / "04_room3_confirmed.png"))
        checks["confirm_reaches_simulated_inbox"] = True

        # Ticket 2 (bird under the leaf profile) ends in the datacenter: model all-clear.
        print("Waiting for ticket #2 ...")
        page.locator("#nav-node-room-2").click()
        page.wait_for_function(
            "() => [...document.querySelector('#journey-ticket-select').options].some(o => o.textContent.includes('Ticket #2') && o.textContent.includes('Entwarnung durch Modell'))",
            timeout=90000,
        )
        page.locator("#journey-ticket-select").select_option(label="Ticket #2 · Entwarnung durch Modell")
        page.wait_for_function(
            "() => { const v = document.querySelector('#datacenter-verdict-box');"
            " return v && v.textContent.includes('Ticket #2') && v.textContent.includes('Entwarnung durch Modell'); }",
            timeout=90000,
        )
        verdict = _text(page, "#datacenter-verdict-box")
        print("Verdict #2:", verdict)
        assert "Blatt · keine Gefahr · Sicherheit 0,99" in verdict
        assert "Vogel" in _text(page, "#datacenter-case-detail")
        assert "Ticket #2" in _text(page, "#datacenter-queue-list")
        assert "Ticket #2" not in _text(page, "#schmidt-ticket-list")
        page.screenshot(path=str(out_dir / "05_room2_ticket2.png"))
        checks["second_ticket_stays_in_datacenter"] = True

        # --- Ticket 3: profile pending, nobody decides ---------------------------------------
        page.locator("#nav-node-room-1").click()
        page.locator("#cartoon-fixture-profile").select_option("pending")
        page.locator("#btn-sim-wrongway").click()
        _wait_text(page, "#live-anomaly-banner", "Ticket #3 erstellt", 90000)
        page.locator("#nav-node-room-4").click()
        print("Waiting for the real deadline of ticket #3 ...")
        page.wait_for_function(
            "() => /^\\d+s$/.test(document.querySelector('#beta-countdown-clock').textContent)",
            timeout=90000,
        )
        first = _text(page, "#beta-countdown-clock")
        # Stop the real camera while this sealed ticket waits for a human.
        page.locator("#nav-node-room-1").click()
        page.locator("#btn-toggle-cam").click()
        _wait_text(page, "#speech-alex", "Kamera gestoppt", 20000)
        page.locator("#nav-node-room-3").click()
        assert "Ticket #3" in _text(page, "#schmidt-ticket-list")
        assert not page.locator("#btn-schmidt-radio").is_disabled()
        checks["camera_stop_keeps_ticket_decidable"] = True
        page.locator("#nav-node-room-4").click()
        page.wait_for_timeout(2500)
        second = _text(page, "#beta-countdown-clock")
        print("Countdown:", first, "->", second)
        assert int(second.rstrip("s")) < int(first.rstrip("s"))
        page.screenshot(path=str(out_dir / "06_room4_real_deadline.png"))
        alert = _wait_text(page, "#beta-alert-msg", "automatisch", 70000)
        print("Deadline alert:", alert)
        page.screenshot(path=str(out_dir / "07_room4_no_response.png"))
        checks["room4_shows_real_deadline_and_auto_report"] = True

        # The unanswered ticket landed at desk 2; the all-clear is sent from there.
        assert "Ticket #3" in _text(page, "#beta-ticket-list")
        page.wait_for_function(
            "() => { const b = document.querySelector('#btn-weber-reject');"
            " return b && !b.disabled && b.textContent.includes('Entwarnung'); }",
            timeout=20000,
        )
        page.locator("#btn-weber-reject").click()
        _wait_text(page, "#speech-weber", "Verneinung", 20000)
        inbox = page.evaluate("() => window.roadApiClient.get('/road/inbox')")
        kinds = sorted({record.get("kind") for record in inbox})
        print("Inbox kinds:", kinds)
        assert len(inbox) >= 3, inbox
        checks["late_rejection_sends_correction"] = True

        checks["no_browser_errors"] = not errors
        checks["no_external_requests"] = not external_hosts
        context.close()
        browser.close()

    return {
        "status": "passed" if all(checks.values()) else "failed",
        "browser": label,
        "checks": checks,
        "browser_errors": errors,
        "external_hosts": sorted(external_hosts),
    }


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
    result: dict[str, Any] = {"status": "failed"}
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
            result = run_verification("http://127.0.0.1:" + str(port), out_dir)
        except Exception as exc:
            result = {"status": "failed", "error_type": type(exc).__name__, "error": str(exc)}
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
    (out_dir / "acceptance.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
