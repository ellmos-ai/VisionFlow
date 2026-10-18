"""Public demo: the second header button opens the reusable workflows on bundled samples.

Run through ``run_browser_acceptance.py`` (probe ``workflows``, public server). Uploads,
file pickers and camera inputs stay unavailable; a real OpenCV run completes.
"""
from __future__ import annotations

from pathlib import Path
from urllib.parse import urlparse

from browser_support import launch_browser


def run(public_url: str, output: Path) -> dict:
    from playwright.sync_api import sync_playwright

    errors: list[str] = []
    external: list[str] = []
    uploads: list[str] = []
    checks: dict[str, bool] = {}
    result: dict = {"checks": checks}
    with sync_playwright() as playwright:
        browser, result["browser"] = launch_browser(playwright)
        context = browser.new_context(viewport={"width": 1280, "height": 1000})

        def guard(route):
            if urlparse(route.request.url).hostname not in {"127.0.0.1", "localhost"}:
                external.append(route.request.url)
                route.abort()
                return
            if urlparse(route.request.url).path == "/media":
                uploads.append(route.request.url)
            route.continue_()

        context.route("**/*", guard)
        page = context.new_page()
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.on("console", lambda message: errors.append(message.text) if message.type == "error" else None)
        try:
            # A language switch reloads the page and forgets the open run; starting
            # again must replace it instead of locking the session (review I-1).
            page.goto(public_url.rstrip("/") + "/?lang=en", wait_until="networkidle")
            page.locator("#analysis-tab").click()
            page.locator("#start-button").click()
            page.locator("#input-panel").wait_for(timeout=30000)
            page.goto(public_url.rstrip("/") + "/?lang=de", wait_until="networkidle")
            page.locator("#analysis-tab").click()
            page.locator("#start-button").click()
            page.locator("#input-panel").wait_for(timeout=30000)
            assert not page.locator("#setup-message").evaluate("el => el.classList.contains('error-message')")
            page.locator("#cancel-button").click()
            page.wait_for_function("() => document.querySelector('#status-badge').dataset.state === 'cancelled'", timeout=15000)
            checks["restart_after_language_switch_replaces_the_open_run"] = True
            for lang, done in (("en", "Completed"), ("de", "Abgeschlossen")):
                page.goto(public_url.rstrip("/") + f"/?lang={lang}", wait_until="networkidle")
                page.locator(".road-cartoon-stage").wait_for()
                tab = page.locator("#analysis-tab")
                assert tab.is_visible() and tab.is_enabled()
                tab.click()
                assert page.locator("#public-sample-group").is_visible()
                for hidden in ("#source-file", "#camera-source", "#input-kind", ".limits-grid", "#approval-draft"):
                    assert not page.locator(hidden).is_visible(), hidden
                samples = page.locator("#public-sample option").evaluate_all("els => els.map(e => e.value)")
                assert samples == ["samples/document-blurred.png", "samples/document-sharp.png"], samples
                page.locator("#start-button").click()
                page.locator("#input-panel").wait_for(timeout=30000)
                assert page.locator("#public-replacement").is_visible() and not page.locator("#replacement-file").is_visible()
                page.locator("#resume-button").click()
                page.wait_for_function("() => document.querySelector('#status-badge').dataset.state === 'completed'", timeout=30000)
                assert done in page.locator("#status-badge").inner_text()
                assert page.locator("#findings-list .finding-card").count() == 2
                page.screenshot(path=str(output / f"public-document-{lang}.png"), full_page=True)
                page.locator("#workflow-preset").select_option("motion-observation")
                assert page.locator("#public-sample option").evaluate_all("els => els.map(e => e.value)") == ["samples/motion.avi"]
                page.locator("#start-button").click()
                page.wait_for_function("() => document.querySelector('#status-badge').dataset.state === 'completed'", timeout=30000)
                page.screenshot(path=str(output / f"public-motion-{lang}.png"), full_page=True)
            checks["second_header_button_opens_workflows"] = True
            checks["document_and_motion_samples_complete_with_opencv"] = True
            checks["no_upload_or_camera_controls_in_public_demo"] = True
            checks["no_media_upload_requests"] = not uploads
            checks["no_browser_or_csp_errors"] = not errors
            checks["no_external_requests"] = not external
            result["status"] = "passed" if all(checks.values()) else "failed"
        except Exception as exc:
            result.update(status="failed", error_type=type(exc).__name__, error=str(exc))
            page.screenshot(path=str(output / "failure.png"), full_page=True)
        finally:
            result["browser_errors"] = errors
            result["external_requests"] = external
            context.close()
            browser.close()
    return result
