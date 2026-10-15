# Testing and local acceptance

This guide covers the local development checks. Cloud runs are described in
[AWS_ACCEPTANCE.md](AWS_ACCEPTANCE.md) and `cloud/demo/`.

## Environment

From the repository root, install the pinned packages into a virtual environment and make the
source checkout importable:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.lock
.\.venv\Scripts\python.exe -m pip install --no-deps -e .
.\.venv\Scripts\python.exe -m pip check
$env:PYTHONPATH = (Resolve-Path .\src).Path
$env:PYTHONIOENCODING = "utf-8"
```

The editable install is also needed by the real Windows console-program tests: they start
`.venv/Scripts/visionflow.exe` without `PYTHONIOENCODING` and check UTF-8 output. `pyproject.toml`
already puts `src` on the pytest path. Use the interpreter from `.venv` so that the checked
OpenCV version is used. On macOS/Linux use `.venv/bin/python`.

## Full suite

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

Measured on 2026-10-13 (Windows 11, Python 3.12.10, OpenCV 5.0.0, NumPy 2.5.3, commit `bde0e67`):
560 passed, 1 skipped, 0 failed in 220 s. The skipped test needs directory symlinks, which
Windows only allows with an extra privilege. The CLI and API contracts are covered by
`tests/test_cli.py` and `tests/test_api.py`; the hosted demo mode by `tests/test_public_demo.py`.

## Focused demo suite

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_demo.py -q
```

The three tests create real synthetic PNG and AVI files with OpenCV 5 and run them through the
registry, controller and fixture model adapter. They check that:

- the first document measurement raises a real input request and a second image source is then
  analysed again;
- the measured Laplacian variance of the synthetic replacement image is higher;
- video follow-up uses the same running media cursor and, at 10 fps with 2 fps requested,
  delivers the actual frame indices `2, 7, 12` after the initial frames `0, 1`;
- trace and evidence files really exist and evidence paths stay inside the demo output folder;
- an existing output folder is refused without overwriting its contents.

The demo injects a clock that shortens waits. It changes neither the video timeline nor frame
IDs, measurements or budget limits. Model decisions are fixed locally; there is no network
access.

## CLI smoke test

Choose a new output folder and run the CLI demo:

```powershell
$env:PYTHONIOENCODING = "utf-8"
.\.venv\Scripts\python.exe -m visionflow.cli demo run --output-dir "$env:TEMP\visionflow-demo-smoke-01"
```

Expected is a JSON answer with `status: "passed"`, `model_mode: "fixture"` and
`network_used: false`. Open the JSON traces and check that their state changes and frame events
match the returned frame IDs. The evidence references point to the stored OpenCV frames in the
same output folder.

A real local CLI run on Windows x64, Python 3.12.10 and OpenCV 5.0.0 gave the synthetic sharpness
values `4.557734375` and `8000.528359375`. At 10 fps with 2 fps requested, the follow-up frames
were `2, 7, 12`. These values only describe the generated test media.

## Road demo tests

The road tests cover direction analysis, the recording journal, MP4 clip export, model review,
deadlines and the workplace:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_road_api.py tests/test_road_cli.py tests/test_road_review_api.py tests/test_road_review_cli.py -q
.\.venv\Scripts\python.exe -m pytest tests/test_direction.py tests/test_simulation.py -q
```

RoadWorld evaluation (no browser, about a minute):

```powershell
.\.venv\Scripts\python.exe evaluation\roadworld_eval.py --out .local\roadworld.json
```

GitHub Actions runs the same suite and the actual CLI demo on Ubuntu and Windows
(`.github/workflows/ci.yml`). The source-bound run for packaged revision `aeb2148` on
14 October 2026 passed **616 tests on each platform**, with no failures. Both jobs
also built an actual wheel and ran `tests/wheel_consumer.py` outside the checkout:
offline demos passed, road case start returned HTTP 202 and real frames were analyzed.
[run 38491740474](https://github.com/ellmos-ai/VisionFlow/actions/runs/38491740474).

### Browser acceptance

The browser acceptance runs are standalone scripts and are not collected by `pytest`. Each starts
its own loopback server with a fresh data folder and writes evidence only under `.local/`.
Requirements:

- Pinned browser dependencies: `.\.venv\Scripts\python.exe -m pip install -r requirements-browser.lock`.
  Playwright 1.60.0 and its installed dependency versions were verified on 15 October 2026.
- A Chromium-based browser: Microsoft Edge on Windows, otherwise an installed Chromium or the one
  from Playwright. `VISIONFLOW_BROWSER` selects a browser executable explicitly.

```powershell
# Complete own-server harness: errors/empty states, ticket journeys, recovery
.\.venv\Scripts\python.exe tests\run_browser_acceptance.py --output .local/acceptance/final-browser

# Camera phase (controls, no browser errors, no external requests)
.\.venv\Scripts\python.exe tests\road_browser_acceptance.py --phase camera --output .local/acceptance/run-camera-01

# Review phase
.\.venv\Scripts\python.exe tests\road_browser_acceptance.py --phase review --output .local/acceptance/run-review-01

# Control room: three complete real cases through the game interface
.\.venv\Scripts\python.exe tests\game_multievent_acceptance.py --output .local/acceptance/multievent-01
```

The interface is English by default. The multi-event script first checks the English default
(`lang="en"`, product name, inject button) and then runs its case assertions on the German
interface via `?lang=de`; waits and assertions are unchanged.

The complete harness owns fresh local and public-mode loopback servers, records commit and
source hashes, interpreter/platform/Playwright versions, child exit codes, browser errors,
screenshots and UTF-8 JSON receipts, and terminates its own process trees even on failure.
`--only errors`, `--only guidance` and `--only recovery` allow targeted diagnostics. Expected
HTTP failures are injected only in the error probe; the ticket, clip, deadline and correction
journeys use the real server. The 60-second deadline is never shortened. In GitHub Actions,
the separate Windows/Edge browser job preserves receipts, screenshots and logs even on failure.
Guidance explicitly requires advancing H.264 playback with decoded video dimensions; a browser
without that decoder fails the check. See [Playwright browser and codec documentation](https://playwright.dev/python/docs/browsers#media-codecs).

`road_browser_acceptance.py` writes `acceptance.json`, the server log and screenshots.
`game_multievent_acceptance.py` plays three cases:

1. **Profile `pending`:** inject a wrong-way driver and a leaf into the running analysis; check
   the real model answer `0.80`; start the next case on the same camera during review; load the
   server clip with confirmed SHA-256 and confirm the hazard.
2. **Profile `leaf` with an injected bird:** the interface shows the fixture answer "leaf", not
   the scenario.
3. **Profile `pending` without a decision:** real deadline in room 4, automatic report after it
   expires even after stopping the camera, late rejection with an all-clear.

Every detection becomes a numbered ticket shown at its current station: model review in
room 2, human review in room 3, and expired deadlines in room 4. The multi-event acceptance
checks room routing, a second event during review, and decidable tickets after camera stop.

A run takes about three minutes. Playwright's open-source Chromium has no H.264 decoder; there
the clip is loaded and checked but not played. Edge and Chrome play it.

Measured on 2026-10-13 on the integrated tree: multi-event, camera and review phases passed with
no browser errors and no external requests.

## Media time

File-video follow-up samples media time, not the next run of native frames. The implementation
needs a positive, reliable FPS value and a backend that confirms the requested frame position.
Unknown FPS or unreliable seeking produce a structured media error. The synthetic AVI demo uses
a constant frame rate; variable-frame-rate videos and real cameras are not covered.

## Core execution rules

Converging workflow branches are merged in topological order; every node runs exactly once.
Several image tools share one loaded image, while every analysis node still counts against the
frame budget. A text-only input keeps the media cursor and tool state; an explicitly chosen
replacement source starts a new analysis state. Sources stay open during an input pause and are
closed on completion or cancellation.

For file videos the native frame rate limits the actual sampling; the configured `sample_fps` is
an upper bound. The frame-budget pre-check stays conservative based on the requested rate. ROI
findings keep the region in original pixels; `metrics.coordinate_system` marks the pixel
coordinates of the crop and the motion regions measured in it. Reports and message drafts only
take metadata of earlier rendered outputs, not nested report contents.

## History

* 2026-10-02: independent local headless-Edge check passed eight CLI/browser cases (upload,
  replacement capture, video follow-up, approval, cancellation, broken media, multiple tabs, a
  390-pixel view) with real server processes, OpenCV and stored frame evidence; no external page
  requests and no browser errors.
* 2026-10-08: product revision `e65ef914` passed 549 tests on an isolated Mac in 101.46 s; actual
  Windows/Edge and Mac/Chromium browser runs each passed three complete cases and eleven checks.
  Earlier failed runs remain documented in the project records.
* 2026-10-13: see the measurements above.
