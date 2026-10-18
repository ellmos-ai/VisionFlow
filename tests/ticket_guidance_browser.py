"""Manual acceptance for the ticket guidance UI against an existing local server.

Uses real rendered frames, cases, decisions and server deadlines. No model calls.
Run with the project interpreter and --base-url http://127.0.0.1:8798/.
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from urllib.parse import urlparse

from browser_support import (
    assert_camera_image_always_painted,
    assert_event_cooldown,
    assert_overlay_bound_to_frames,
    launch_browser,
)

# WCAG 2.x contrast of every visible intro text against its first opaque background.
INTRO_CONTRAST_JS = """() => {
  const parse = (c) => { const m = c.match(/rgba?\\(([^)]+)\\)/); if (!m) return null;
    const p = m[1].split(',').map(Number); return {r: p[0], g: p[1], b: p[2], a: p.length > 3 ? p[3] : 1}; };
  const lum = (c) => { const f = (v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); };
    return 0.2126 * f(c.r) + 0.7152 * f(c.g) + 0.0722 * f(c.b); };
  const back = (el) => { for (let n = el; n; n = n.parentElement) { const c = parse(getComputedStyle(n).backgroundColor);
    if (c && c.a > 0.5) return c; } return {r: 255, g: 255, b: 255}; };
  const rows = [];
  for (const el of document.querySelectorAll('.intro-details :is(#hero-title, .hero-lead, #public-demo-note, #hero-reuse, .hero-steps li > span)')) {
    if (el.offsetParent === null) continue;
    const cs = getComputedStyle(el), fg = parse(cs.color), bg = back(el);
    const a = lum(fg), b = lum(bg), size = parseFloat(cs.fontSize), weight = Number(cs.fontWeight);
    rows.push({text: el.textContent.trim().slice(0, 30), ratio: (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05),
               required: size >= 24 || (size >= 18.66 && weight >= 700) ? 3 : 4.5});
  }
  return rows;
}"""


def run(base_url: str, output: Path) -> dict:
    from playwright.sync_api import sync_playwright

    errors: list[str] = []
    external: list[str] = []
    checks: dict[str, bool] = {}
    result: dict = {"checks": checks}
    with sync_playwright() as playwright:
        browser, result["browser"] = launch_browser(playwright)
        context = browser.new_context(viewport={"width": 1440, "height": 1050})

        def guard(route):
            if urlparse(route.request.url).hostname not in {"127.0.0.1", "localhost"}:
                external.append(route.request.url)
                route.abort()
            else:
                route.continue_()

        context.route("**/*", guard)
        page = context.new_page()
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.on("console", lambda message: errors.append(message.text) if message.type == "error" else None)
        try:
            page.goto(base_url.rstrip("/") + "/?lang=en", wait_until="networkidle")
            page.locator(".road-cartoon-stage").wait_for()
            assert page.evaluate("document.documentElement.lang") == "en"
            assert not page.locator(".intro-details").evaluate("el => el.open")
            assert page.locator(".intro-details > summary").is_visible()
            page.locator(".intro-details > summary").click()
            assert page.locator("#hero-title").is_visible()
            contrast = page.evaluate(INTRO_CONTRAST_JS)
            result["intro_contrast"] = contrast
            assert len(contrast) >= 7 and all(row["ratio"] >= row["required"] for row in contrast), contrast
            checks["intro_text_contrast_wcag_aa"] = True
            page.locator(".intro-details > summary").click()
            checks["hero_closed_by_default_and_accessible"] = True
            assert page.locator(".traffic-monitor #btn-toggle-cam").count() == 1
            assert page.locator(".traffic-control-panel #btn-toggle-cam").count() == 0
            boxes = [page.locator(selector).bounding_box() for selector in ("#btn-sim-wrongway", "#btn-sim-bird", "#btn-sim-leaf")]
            assert max(b["width"] for b in boxes) - min(b["width"] for b in boxes) < 1
            assert boxes[0]["y"] < boxes[1]["y"] < boxes[2]["y"]
            checks["camera_control_and_equal_vertical_injectors"] = True
            assert page.locator("#room-back-1").is_disabled()
            assert page.locator("#room-next-4").is_disabled()
            for station in (1, 2, 3, 4):
                page.locator(f"#nav-node-room-{station}").click()
                assert page.locator(f"#cartoon-room-{station}").is_visible()
                assert page.locator(f"#nav-node-room-{station}").get_attribute("aria-current") == "step"
                page.screenshot(path=str(output / f"desktop-station-{station}.png"), full_page=True)
                if station < 4:
                    page.locator(f"#room-next-{station}").click()
                    assert page.locator(f"#cartoon-room-{station + 1}").is_visible()
                    page.locator(f"#room-back-{station + 1}").click()
            checks["four_station_arrows_and_boundaries"] = True
            assert not page.locator("#cartoon-room-2 details").evaluate("el => el.open")
            assert not page.locator("#datacenter-console").is_visible()
            checks["technical_data_closed_by_default"] = True
            assert page.locator(".scenario-icon").evaluate_all("els => els.every(el => el.complete && el.naturalWidth > 0)")
            assert page.locator(".cartoon-avatar img").evaluate_all("els => els.every(el => el.complete && el.naturalWidth > 0)")
            checks["generated_assets_loaded"] = True
            for width in (320, 375):
                page.set_viewport_size({"width": width, "height": 900})
                for station in (1, 2, 3, 4):
                    page.locator(f"#nav-node-room-{station}").click()
                    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), (width, station)
                    page.screenshot(path=str(output / f"mobile-{width}-station-{station}.png"), full_page=True)
            checks["mobile_320_375_no_horizontal_overflow"] = True
            page.set_viewport_size({"width": 1440, "height": 1050})
            page.locator("#nav-node-room-1").click()
            page.locator("#cartoon-fixture-profile").select_option("pending")
            page.locator("#btn-sim-wrongway").click()
            result["overlay"] = assert_overlay_bound_to_frames(page)
            checks["analysis_overlay_bound_to_its_frame"] = True
            result["cooldown"] = assert_event_cooldown(page)
            checks["second_event_waits_for_countdown_and_server_rejects_it"] = True
            result["painted"] = assert_camera_image_always_painted(page)
            checks["live_camera_image_never_unpainted"] = True
            page.wait_for_function("() => document.querySelector('#journey-ticket-title').textContent === 'Ticket #1'", timeout=90000)
            page.wait_for_function("() => document.querySelector('#journey-ticket-status').textContent.includes('Human review')", timeout=90000)
            case_id = page.locator("#journey-ticket-select").input_value()
            snapshot = page.evaluate("id => window.roadApiClient.get('/road/cases/' + encodeURIComponent(id))", case_id)
            result["first_case_id"] = case_id
            result["first_case_deadline_at"] = snapshot["deadline_at"]
            result["first_case_remaining_seconds"] = snapshot["remaining_seconds"]
            assert snapshot["phase"] == "human_review" and 45 < snapshot["remaining_seconds"] <= 60
            checks["real_sixty_second_server_deadline"] = True
            for station in (2, 3, 4):
                page.locator(f"#nav-node-room-{station}").click()
                assert page.locator("#journey-ticket-select").input_value() == case_id
                assert page.locator("#journey-ticket-title").inner_text() == "Ticket #1"
                page.screenshot(path=str(output / f"ticket1-station-{station}.png"), full_page=True)
            checks["same_ticket_follows_all_stations"] = True
            page.locator("#nav-node-room-2").click()
            assert "0.80" in page.locator("#datacenter-verdict-box").inner_text()
            assert "Fixed offline" in page.locator("#transparency-pill-text").inner_text()
            assert "no image assessment" in page.locator("#transparency-notice").inner_text()
            checks["actual_assessment_and_honest_offline_label"] = True
            page.locator("#nav-node-room-3").click()
            page.locator("#btn-schmidt-radio").wait_for()
            page.wait_for_function("() => !document.querySelector('#btn-schmidt-radio').disabled", timeout=10000)
            page.locator("#cartoon-review-video-box video").wait_for(timeout=15000)
            video = page.locator("#cartoon-review-video-box video")
            assert video.evaluate("el => Boolean(el.canPlayType('video/mp4; codecs=\"avc1.42E01E\"'))"), "H.264 decoder required"
            page.wait_for_function("() => { const v = document.querySelector('#cartoon-review-video-box video'); return v && !v.error && v.currentTime > 0 && v.videoWidth > 0; }", timeout=15000)
            checks["actual_h264_playback"] = True
            assert "checksum confirmed by server" in page.locator("#cartoon-review-video-box").inner_text()
            assert "s left" in page.locator("#schmidt-countdown").inner_text()
            page.locator("#btn-schmidt-coffee").click()
            assert page.locator("#schmidt-away").is_visible()
            checks["verified_clip_and_visible_coffee_break"] = True
            page.locator("#nav-node-room-4").click()
            page.wait_for_function("() => !document.querySelector('#beta-alert-msg').hidden", timeout=70000)
            assert page.locator("#journey-ticket-select").input_value() == case_id
            # The automatic report dispatches the patrol car; it must drive left to right.
            car_x = "() => document.querySelector('#cartoon-patrol-car').getBoundingClientRect().left"
            page.wait_for_function("() => document.querySelector('#cartoon-patrol-car').classList.contains('dispatching')", timeout=10000)
            first_x = page.evaluate(car_x)
            time.sleep(1.5)
            second_x = page.evaluate(car_x)
            result["patrol_car_x"] = [first_x, second_x]
            assert second_x > first_x + 40, (first_x, second_x)
            checks["patrol_car_drives_after_report"] = True
            page.screenshot(path=str(output / "ticket1-deadline-expired.png"), full_page=True)
            page.wait_for_function("() => !document.querySelector('#btn-weber-reject').disabled", timeout=10000)
            page.locator("#btn-weber-reject").click()
            page.wait_for_function("() => document.querySelector('#journey-ticket-status').textContent.includes('All-clear sent')", timeout=20000)
            inbox = page.evaluate("() => window.roadApiClient.get('/road/inbox')")
            assert any(item.get("kind") == "first_report" and item.get("case_id") == case_id for item in inbox)
            assert any(item.get("kind") == "correction" and item.get("case_id") == case_id for item in inbox)
            checks["deadline_auto_report_and_late_correction"] = True
            page.locator("#nav-node-room-1").click()
            page.locator("#btn-toggle-cam").click()
            page.wait_for_function("() => document.querySelector('#cartoon-cam-status').textContent.includes('STANDBY')", timeout=20000)
            page.wait_for_function("() => !document.querySelector('#btn-toggle-cam').disabled", timeout=60000)
            page.locator("#cartoon-fixture-profile").select_option("leaf")
            page.locator("#btn-sim-bird").click()
            page.wait_for_function("() => document.querySelector('#journey-ticket-title').textContent === 'Ticket #2' && document.querySelector('#journey-ticket-status').textContent.includes('All-clear by model')", timeout=90000)
            second_case = page.locator("#journey-ticket-select").input_value()
            assert second_case != case_id
            page.locator("#nav-node-room-2").click()
            assert "Leaf" in page.locator("#datacenter-verdict-box").inner_text()
            assert "0.99" in page.locator("#datacenter-verdict-box").inner_text()
            page.locator("#journey-ticket-select").select_option(case_id)
            assert page.locator("#journey-ticket-title").inner_text() == "Ticket #1"
            page.locator("#nav-node-room-3").click()
            assert page.locator("#journey-ticket-select").input_value() == case_id
            checks["multiple_tickets_can_be_selected_without_silent_switch"] = True
            page.locator("#nav-node-room-1").click()
            page.locator("#btn-toggle-cam").click()
            page.wait_for_function("() => document.querySelector('#cartoon-cam-status').textContent.includes('STANDBY')", timeout=20000)
            page.wait_for_function("() => !document.querySelector('#btn-toggle-cam').disabled", timeout=60000)
            page.locator("#cartoon-fixture-profile").select_option("bird")
            page.locator("#btn-sim-bird").click()
            page.wait_for_function("() => [...document.querySelector('#journey-ticket-select').options].some(o => o.textContent.includes('Ticket #3') && o.textContent.includes('All-clear by model'))", timeout=90000)
            options = page.locator("#journey-ticket-select").evaluate("el => [...el.options].map(o => ({value: o.value, text: o.textContent}))")
            bird_case = next(item["value"] for item in options if "Ticket #3" in item["text"])
            page.locator("#journey-ticket-select").select_option(bird_case)
            page.locator("#nav-node-room-2").click()
            assert "Bird" in page.locator("#datacenter-verdict-box").inner_text()
            assert "0.99" in page.locator("#datacenter-verdict-box").inner_text()
            bird_snapshot = page.evaluate("id => window.roadApiClient.get('/road/cases/' + encodeURIComponent(id))", bird_case)
            assert bird_snapshot["fixture_profile"] == "bird" and bird_snapshot["assessment"]["object_class"] == "bird"
            assert bird_snapshot["assessment"]["hazard"] is False and bird_snapshot["phase"] == "dismissed"
            result["bird_case_id"] = bird_case
            page.screenshot(path=str(output / "bird-profile-closed.png"), full_page=True)
            checks["actual_bird_fixture_closes_without_human_review"] = True
            page.goto(base_url.rstrip("/") + "/?lang=de", wait_until="domcontentloaded")
            page.locator(".road-cartoon-stage").wait_for()
            assert page.evaluate("document.documentElement.lang") == "de"
            assert "auswählen" in page.locator(".ticket-picker").inner_text()
            assert "Falschfahrer" in page.locator("#btn-sim-wrongway").inner_text()
            checks["german_labels_and_umlauts"] = True
            checks["no_browser_or_csp_errors"] = not errors
            checks["no_external_requests"] = not external
            result["status"] = "passed" if all(checks.values()) else "failed"
        except Exception as exc:
            result.update(status="failed", error_type=type(exc).__name__, error=str(exc))
            try:
                result["camera_controls"] = page.evaluate("""() => ({
                    state: document.querySelector('#cartoon-cam-status')?.textContent,
                    message: document.querySelector('#speech-alex')?.textContent,
                    stopDisabled: document.querySelector('#btn-toggle-cam')?.disabled,
                    injectDisabled: document.querySelector('#btn-sim-bird')?.disabled,
                    recorder: document.querySelector('.road-camera__status')?.textContent
                })""")
                result["camera_case_id"] = page.locator("section.road-camera").get_attribute("data-case-id")
                result["camera_run_id"] = page.locator("section.road-camera").get_attribute("data-run-id")
                case_id = result["camera_case_id"]
                if case_id:
                    result["failed_case_snapshot"] = page.evaluate("id => window.roadApiClient.get('/road/cases/' + encodeURIComponent(id))", case_id)
            except Exception:
                pass
            page.screenshot(path=str(output / "failure.png"), full_page=True)
        finally:
            result["browser_errors"] = errors
            result["external_requests"] = external
            context.close()
            browser.close()
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if urlparse(args.base_url).hostname not in {"127.0.0.1", "localhost"}:
        parser.error("Use an existing loopback test server")
    args.output.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    result = run(args.base_url, args.output)
    result["elapsed_seconds"] = round(time.monotonic() - started, 2)
    (args.output / "acceptance.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
