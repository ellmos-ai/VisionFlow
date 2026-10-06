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
        return REPO / ".venv" / "Scripts" / "python.exe"
    return REPO / ".venv" / "bin" / "python"


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


def launch_browser(playwright: object) -> tuple[object, str]:
    executable, label = browser_executable()
    options: dict[str, object] = {
        "headless": True,
        "args": ["--autoplay-policy=no-user-gesture-required"],
    }
    if executable is not None:
        options["executable_path"] = str(executable)
    return playwright.chromium.launch(**options), label  # type: ignore[attr-defined]
