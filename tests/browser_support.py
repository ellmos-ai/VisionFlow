"""Shared helpers for the manual Playwright browser acceptance scripts.

These scripts are not collected by pytest. They need ``playwright`` installed
in the project environment and a Chromium-based browser.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]

_WINDOWS_EDGE = Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe")


def venv_python() -> Path:
    """Return the project virtual-environment interpreter for this platform."""
    if os.name == "nt":
        candidate = REPO / ".venv" / "Scripts" / "python.exe"
    else:
        candidate = REPO / ".venv" / "bin" / "python"
    return candidate if candidate.is_file() else Path(sys.executable)


def browser_executable() -> tuple[Path | None, str]:
    """Return ``(executable, label)`` for the browser used by the acceptance runs.

    ``VISIONFLOW_BROWSER`` selects an explicit executable. Without it, Windows
    uses Microsoft Edge and other platforms use a system Chromium; ``None``
    lets Playwright use its own bundled Chromium.
    """
    explicit = os.environ.get("VISIONFLOW_BROWSER")
    if explicit:
        path = Path(explicit)
        if not path.is_file():
            raise FileNotFoundError("VISIONFLOW_BROWSER does not name a file: " + explicit)
        return path, path.name
    if sys.platform == "win32":
        if not _WINDOWS_EDGE.is_file():
            raise FileNotFoundError("Microsoft Edge executable is missing")
        return _WINDOWS_EDGE, "Microsoft Edge"
    for candidate in ("chromium", "chromium-browser", "google-chrome"):
        found = shutil.which(candidate)
        if found:
            return Path(found), candidate
    return None, "Playwright Chromium"


COOLDOWN_JS = """() => {
  const note = document.querySelector('#event-cooldown-note');
  const leaf = document.querySelector('#btn-sim-leaf');
  return {note: note && !note.hidden ? note.textContent : null, leafDisabled: leaf.disabled, leafLabel: leaf.textContent,
          source: document.querySelector('section.road-camera').dataset.sourceId};
}"""


def assert_event_cooldown(page: object) -> dict:
    """After one injected event: countdown on the buttons and a 409 from the server.

    The server check uses the page's own session outside the page, so the expected
    409 does not appear as a browser console error.
    """
    import json
    from urllib.parse import urlsplit

    page.wait_for_function(  # type: ignore[attr-defined]
        "() => { const n = document.querySelector('#event-cooldown-note'); return n && !n.hidden; }", timeout=15000)
    state = page.evaluate(COOLDOWN_JS)  # type: ignore[attr-defined]
    assert state["leafDisabled"] and " s" in state["leafLabel"], state
    parts = urlsplit(page.url)  # type: ignore[attr-defined]
    origin = f"{parts.scheme}://{parts.netloc}"
    request = page.context.request  # type: ignore[attr-defined]
    csrf = request.get(origin + "/session").json()["csrf_token"]
    response = request.post(
        origin + "/road/sessions/" + state["source"] + "/events",
        data=json.dumps({"event": "leaf"}),
        headers={"Content-Type": "application/json", "Origin": origin, "X-CSRF-Token": csrf},
    )
    error = response.json().get("error", {})
    state["rejected"] = {"status": response.status, "code": error.get("code"),
                         "retry_after": error.get("retry_after_seconds"), "header": response.headers.get("retry-after")}
    assert response.status == 409 and error.get("code") == "event_cooldown", state
    assert error.get("retry_after_seconds", 0) >= 1 and state["rejected"]["header"] == str(error["retry_after_seconds"]), state
    return state


OVERLAY_JS = """() => { const v = document.querySelector('#cartoon-feed-mount .road-camera__viewport');
  return v ? {frame: v.dataset.frameId || '', overlay: v.dataset.overlayFrameId || '', tracks: Number(v.dataset.overlayTracks || 0)} : null; }"""


def assert_overlay_bound_to_frames(page: object, seconds: float = 6.0) -> dict:
    """While the analysis runs, every drawn overlay belongs to the frame it is drawn on."""
    import time

    samples = []
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        samples.append(page.evaluate(OVERLAY_JS))  # type: ignore[attr-defined]
        time.sleep(0.2)
    drawn = [row for row in samples if row and row["overlay"]]
    assert len(drawn) >= 3, samples[-5:]
    assert all(row["overlay"] == row["frame"] for row in drawn), drawn
    return {"samples": len(samples), "with_overlay": len(drawn)}


def launch_browser(playwright: object) -> tuple[object, str]:
    executable, label = browser_executable()
    options: dict[str, object] = {
        "headless": True,
        "args": ["--autoplay-policy=no-user-gesture-required"],
    }
    if executable is not None:
        options["executable_path"] = str(executable)
    return playwright.chromium.launch(**options), label  # type: ignore[attr-defined]
