# VisionFlow jury reproduction

Product revision checked on 2026-10-08: `ac3ca55236d4a3772fa1443b68ea7b91f1b9804f`. The repository is private. The submission's uploaded source archive provides the allowlisted source for local inspection; a repository URL alone does not grant access.

## Install and run the offline demonstration

Use Python 3.12 or newer. FFmpeg with the `libx264` encoder is required for road evidence clips. Git must be on PATH to run the complete testsuite, including source-packaging tests. Extract `source.zip` from the jury bundle into a new directory and run these commands from that extracted source directory.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.lock
.\.venv\Scripts\python.exe -m pip install --no-deps -e .
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe -m visionflow.cli demo run --output-dir .local/jury-demo-01
```

On macOS/Linux, use `.venv/bin/python` instead of `.venv\Scripts\python.exe`. Installation downloads the declared dependencies; the demonstrated document and motion workflows run offline. Choose a new output directory for every run. Expected JSON includes `status: "passed"`, `model_mode: "fixture"`, `network_used: false`, measured sharpness values, actual frame IDs and paths to trace and evidence files. Evidence files with `.bin` extensions contain decodable PNG bytes.

## Open the local interface

```powershell
.\.venv\Scripts\python.exe -m visionflow.cli serve --host 127.0.0.1 --port 8765 --data-dir .local/jury-server-data
```

Open `http://127.0.0.1:8765/`. The road tab shows synthetic traffic and explicitly selected offline model profiles. Inject a wrong-way event into a running case, inspect its immutable server clip and hash, and follow the human confirmation or rejection flow. Notifications and later corrections reach a simulated local inbox. They do not contact an authority. Stop the server with Ctrl+C.

The movie demonstrates the actual local interface at recorded revision `096c085`, with synthetic traffic, offline fixtures and simulated recipients. It is not a live Gemini or AWS recording. The later direction fix has separate source and regression evidence. Personal GUI acceptance remains reserved for the project owner.

## Verification and remaining gates

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

The isolated Mac regression passed 541 checks on the product revision above. A separate isolated Playwright Chromium run passed all three complete road cases and eleven browser checks. The final Windows browser probe is still being diagnosed. See [the technical report](TECHNICAL_REPORT.md), [synthetic evaluation](SYNTHETIC_EVALUATION.md), [testing instructions](TESTING.md) and [architecture](ARCHITECTURE.md) for scope and limitations.

The [AWS recipe](AWS_ACCEPTANCE.md) requires separate approval for a one-attempt CodeBuild execution with exact source/evidence verification and cleanup. No completed AWS or live-provider run is claimed in this bundle. A public jury endpoint or an agreed screenshare demonstration remains a separate access requirement; local reproduction instructions do not prove that arrangement. The entry remains a draft until the outstanding execution, access and submission gates are resolved.
