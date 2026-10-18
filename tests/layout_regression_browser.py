"""Layout regression of the technical case view against a real reported road case.

Run through ``run_browser_acceptance.py`` (probe ``layout``). For widths 1920 to 390 px
and both languages it checks: no horizontal page scroll, no element wider than its
parent column (unless that parent scrolls or clips) and no visible text below 12 px.
"""
from __future__ import annotations

from pathlib import Path
from urllib.parse import urlparse

from browser_support import launch_browser

WIDTHS = (1920, 1440, 1280, 1024, 768, 390)
MIN_FONT_PX = 12.0

LAYOUT_JS = """(minFont) => {
  const visible = (el) => { const r = el.getBoundingClientRect(); const cs = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && cs.visibility !== 'hidden' && cs.display !== 'none'; };
  const name = (el) => el.tagName.toLowerCase() + (el.id ? '#' + el.id : '') +
    (el.className && typeof el.className === 'string' ? '.' + el.className.trim().split(/\\s+/).join('.') : '');
  const overflow = [], tiny = [];
  const scrolls = (el) => getComputedStyle(el).overflowX !== 'visible';
  const roots = [...document.querySelectorAll('.road-workspace-grid')];
  for (const root of roots) {
    const rootBox = root.getBoundingClientRect();
    for (const el of root.querySelectorAll('*')) {
      if (!visible(el)) continue;
      const cs = getComputedStyle(el);
      const parent = el.parentElement;
      const r = el.getBoundingClientRect();
      let clipped = false;
      for (let n = el.parentElement; n && n !== root; n = n.parentElement) if (scrolls(n)) { clipped = true; break; }
      const replaced = ['VIDEO', 'IMG', 'CANVAS', 'SVG', 'IFRAME', 'INPUT', 'SELECT', 'TEXTAREA', 'BUTTON'].includes(el.tagName.toUpperCase());
      const flowBox = !(cs.display === 'inline' && !replaced) && cs.display !== 'contents';
      if (!clipped && flowBox && !['absolute', 'fixed'].includes(cs.position)) {
        const ps = getComputedStyle(parent);
        const p = parent.getBoundingClientRect();
        const inner = { left: p.left + parseFloat(ps.borderLeftWidth), right: p.right - parseFloat(ps.borderRightWidth) };
        // (a) wider than its column, (b) wider than the technical view, (c) text spilling out of its own box.
        const reason = r.right > inner.right + 1 || r.left < inner.left - 1 ? 'exceeds_parent'
          : r.right > rootBox.right + 1 || r.left < rootBox.left - 1 ? 'exceeds_view'
          : cs.overflowX === 'visible' && el.scrollWidth > el.clientWidth + 1 && el.clientWidth > 0 ? 'content_spills' : null;
        if (reason) {
          overflow.push({ reason, element: name(el), parent: name(parent), right: Math.round(r.right), parent_right: Math.round(inner.right),
                          width: Math.round(r.width), parent_width: Math.round(inner.right - inner.left),
                          scroll_width: el.scrollWidth, client_width: el.clientWidth });
        }
      }
      const text = [...el.childNodes].some((n) => n.nodeType === 3 && n.textContent.trim());
      if (text && parseFloat(cs.fontSize) < minFont) tiny.push({ element: name(el), font_px: parseFloat(cs.fontSize), text: el.textContent.trim().slice(0, 40) });
    }
  }
  return { roots: roots.length, scroll_width: document.documentElement.scrollWidth, inner_width: innerWidth,
           overflow: overflow.slice(0, 25), overflow_count: overflow.length, tiny: tiny.slice(0, 25), tiny_count: tiny.length };
}"""


def run(base_url: str, output: Path) -> dict:
    from playwright.sync_api import sync_playwright

    errors: list[str] = []
    external: list[str] = []
    checks: dict[str, bool] = {}
    result: dict = {"checks": checks, "measurements": {}}
    with sync_playwright() as playwright:
        browser, result["browser"] = launch_browser(playwright)
        context = browser.new_context(viewport={"width": 1440, "height": 1000})

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
            page.locator("#cartoon-fixture-profile").select_option("high")
            page.locator("#btn-sim-wrongway").click()
            page.wait_for_function("() => document.querySelector('#journey-ticket-status').textContent.includes('Reported to authority')", timeout=120000)
            checks["real_reported_case"] = True
            failures = []
            for lang in ("en", "de"):
                page.set_viewport_size({"width": 1440, "height": 1000})
                page.goto(base_url.rstrip("/") + f"/?lang={lang}", wait_until="domcontentloaded")
                page.locator(".road-cartoon-stage").wait_for()
                page.locator("#journey-ticket-select option").first.wait_for(state="attached", timeout=15000)
                page.locator("#cartoon-details-btn").click()
                page.wait_for_function("() => [...document.querySelectorAll('.road-workspace-grid video')].some(v => v.readyState >= 1 && v.videoWidth > 0)", timeout=30000)
                page.wait_for_function("() => document.querySelectorAll('.road-inbox-card').length > 0", timeout=15000)
                for width in WIDTHS:
                    page.set_viewport_size({"width": width, "height": 1000})
                    page.wait_for_timeout(400)
                    values = page.evaluate(LAYOUT_JS, MIN_FONT_PX)
                    result["measurements"][f"{lang}-{width}"] = values
                    page.locator(".road-workspace-grid").screenshot(path=str(output / f"technical-view-{lang}-{width}.png"))
                    if values["roots"] != 1 or values["scroll_width"] > values["inner_width"] or values["overflow_count"] or values["tiny_count"]:
                        failures.append(f"{lang}-{width}")
            result["failures"] = failures
            checks["technical_view_no_horizontal_scroll"] = all(
                m["scroll_width"] <= m["inner_width"] for m in result["measurements"].values())
            checks["technical_view_elements_fit_their_columns"] = all(
                m["overflow_count"] == 0 for m in result["measurements"].values())
            checks["technical_view_text_at_least_12px"] = all(
                m["tiny_count"] == 0 for m in result["measurements"].values())
            checks["technical_view_measured_all_widths_both_languages"] = len(result["measurements"]) == 2 * len(WIDTHS)
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
