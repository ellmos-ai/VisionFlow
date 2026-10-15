"""Rendered GUI checks for empty states and injected HTTP failures (no model calls)."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from urllib.parse import urlparse

from browser_support import launch_browser


def run(base_url: str, output: Path, *, public_url: str | None = None) -> dict:
    from playwright.sync_api import sync_playwright

    result = {"status": "failed", "checks": {}, "browser_errors": [], "failures": {}}

    def check(name, probe):
        try:
            probe()
            result["checks"][name] = True
        except Exception as error:
            result["checks"][name] = False
            result["failures"][name] = f"{type(error).__name__}: {error}"

    def require(condition, message):
        assert condition, message

    with sync_playwright() as pw:
        browser, result["browser"] = launch_browser(pw)
        try:
            for lang in ("en", "de"):
                context = browser.new_context(viewport={"width": 1440, "height": 1000})
                context.route("**/*", lambda route: route.continue_() if urlparse(route.request.url).hostname in {"127.0.0.1", "localhost"} else route.abort())
                baseline = os.environ.get("VISIONFLOW_TEST_BASE_APP_JS")
                if baseline:
                    context.route("**/app.js", lambda route: route.fulfill(path=baseline, content_type="text/javascript"))
                page = context.new_page()
                page.on("pageerror", lambda error: result["browser_errors"].append(str(error)))
                try:
                    page.goto(f"{base_url.rstrip('/')}/?lang={lang}", wait_until="networkidle")
                    page.locator(".road-cartoon-stage").wait_for()
                    page.locator("#nav-node-room-3").click()
                    check(f"{lang}_empty_coffee_disabled", lambda: require(page.locator("#btn-schmidt-coffee").is_disabled(), "coffee must be disabled without a ticket"))
                    check(f"{lang}_single_language_switch", lambda: require(page.locator("#app-lang-btn:visible, #cartoon-lang-btn:visible").count() == 1, "exactly one global switch must be visible"))
                    page.locator("#app-lang-btn").focus()
                    page.keyboard.press("Tab")
                    page.keyboard.press("Shift+Tab")
                    check(f"{lang}_keyboard_focus", lambda: require(page.locator("#app-lang-btn").evaluate("el => el.matches(':focus-visible') && getComputedStyle(el).outlineStyle !== 'none'"), "keyboard focus needs a visible outline"))
                    page.locator("#nav-node-room-1").click()

                    def capacity(route):
                        route.fulfill(status=503, headers={"Retry-After": "60"}, content_type="application/json", body=json.dumps({"error": {"code": "demo_capacity_full", "message": "capacity"}}))

                    page.route("**/road/sessions", capacity)
                    page.locator("#btn-toggle-cam").click()
                    expected = "erneut versuchen" if lang == "de" else "retry"
                    page.wait_for_function("text => document.querySelector('#speech-alex').textContent.toLowerCase().includes(text)", arg=expected)
                    check(f"{lang}_capacity_retry_visible", lambda: require(expected in page.locator("#speech-alex").inner_text().lower() and "503" in page.locator("#speech-alex").inner_text(), "localized capacity error and retry missing"))
                    page.unroute("**/road/sessions", capacity)

                    def expired(route):
                        route.fulfill(status=403, content_type="application/json", body=json.dumps({"error": {"code": "session_required", "message": "A valid local session is required."}}))

                    page.route("**/road/sessions", expired)
                    page.locator("#btn-toggle-cam").click()
                    page.wait_for_function("() => document.querySelector('#speech-alex').textContent.includes('403')")
                    expected_session = "Sitzung" if lang == "de" else "session"
                    check(f"{lang}_expired_session_visible", lambda: require(expected_session in page.locator("#speech-alex").inner_text() and page.locator("#journey-ticket-select option").count() == 0, "expired session must be localized without a false ticket"))
                    page.unroute("**/road/sessions", expired)

                    def disconnected(route):
                        route.abort("failed")

                    page.route("**/road/sessions", disconnected)
                    page.locator("#btn-toggle-cam").click()
                    page.wait_for_function("() => /Failed to fetch|Verbindung|connection/.test(document.querySelector('#speech-alex').textContent)")
                    expected_network = "Verbindung" if lang == "de" else "connection"
                    check(f"{lang}_network_failure_visible", lambda: require(expected_network in page.locator("#speech-alex").inner_text() and page.locator("#journey-ticket-select option").count() == 0, "localized connection failure missing or false ticket created"))
                    page.unroute("**/road/sessions", disconnected)

                    page.locator("#analysis-tab").click()
                    page.evaluate("""() => {
                        window.sampleDrawnText = [];
                        const original = CanvasRenderingContext2D.prototype.fillText;
                        CanvasRenderingContext2D.prototype.fillText = function(text, ...rest) {
                            window.sampleDrawnText.push(String(text));
                            return original.call(this, text, ...rest);
                        };
                    }""")
                    page.locator("#sample-sharp-btn").click()
                    page.wait_for_function("() => document.querySelector('#source-file').files.length === 1")
                    sample = page.evaluate("() => window.sampleDrawnText.join(' ')")
                    check(f"{lang}_sample_has_no_invented_measurements", lambda: require(not any(value in sample for value in ("24.1", "420.8", "0.88", "0.02")), "sample contains invented measurements"))
                    page.screenshot(path=str(output / f"errors-and-sample-{lang}.png"), full_page=True)
                finally:
                    context.close()

            if public_url:
                page = browser.new_page()
                try:
                    for lang, title in (("en", "VisionFlow – public road demo"), ("de", "VisionFlow – öffentliche Straßendemo")):
                        page.goto(f"{public_url.rstrip('/')}/?lang={lang}", wait_until="networkidle")
                        page.locator(".road-cartoon-stage").wait_for()
                        check(f"{lang}_public_document_title", lambda: require(page.title() == title, f"unexpected title: {page.title()}"))
                finally:
                    page.close()
        finally:
            browser.close()
    result["checks"]["no_page_errors"] = not result["browser_errors"]
    result["status"] = "passed" if all(result["checks"].values()) else "failed"
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--public-url")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    for url in (args.base_url, args.public_url):
        if url and urlparse(url).hostname not in {"127.0.0.1", "localhost"}:
            parser.error("Only owned loopback servers are supported")
    args.output.mkdir(parents=True, exist_ok=False)
    result = run(args.base_url, args.output, public_url=args.public_url)
    (args.output / "acceptance.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=True))
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
