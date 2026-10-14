# VisionFlow – instructions for judges

## 1. Live demo (no installation)

<!-- LIVE_DEMO_URL -->

1. Open the URL in a desktop browser. The top section explains the loop; below it is the
   **wrong-way driver warning** control room.
2. In room 1, choose a fixed offline answer under *Fixed offline test answer (profile)*, then
   press **Inject wrong-way driver** (or bird / leaf). Injecting is not a detection: the server
   analyses the rendered frames with OpenCV and opens a case only if it finds opposing motion.
3. Follow the cable to room 2 (OpenCV stage, clip and model answer), room 3 (human review of the
   hash-verified clip: confirm or deny) and room 4 (the real deadline if nobody decides).
4. Image and video workflows are available in the locally installed app (section 2).
   The hosted road demo does not accept uploads.

The public demo uses synthetic traffic, fixed offline model answers and a simulated authority
inbox. Each camera session ends after two minutes; at most four sessions run at once (“All demo
cameras are busy” means: try again a minute later).

Each detection creates a numbered ticket at its current station: room 2 for model review,
room 3 for human review, and room 4 after the deadline expires. Injecting a second event during
review starts the next case on the same camera; stopping it keeps sealed tickets decidable.

## 2. Run it locally

Python 3.12+ and FFmpeg with `libx264` (for road clips). From the source archive or repository:

```bash
python -m venv .venv
.venv/bin/python -m pip install -r requirements.lock      # Windows: .venv\Scripts\python.exe
.venv/bin/python -m pip install --no-deps -e .
.venv/bin/python -m visionflow.cli serve --port 8765      # open http://127.0.0.1:8765/
```

Offline end-to-end demo without a server: `.venv/bin/python -m visionflow.cli demo run
--output-dir ./demo-out-01`. Expected JSON: `status: passed`, `model_mode: fixture`,
`network_used: false`, measured sharpness values, real frame IDs and trace/evidence paths.

The local app's **Reusable workflows** tab runs the same controller on a document photo:
use *Sample document (blurred)*, start the run and upload a sharper image when asked.

Optional live model review: set `GEMINI_API_KEY`, start `serve --allow-gemini-costs` and choose
the Gemini model in room 1 (provider costs may apply; at most two requests per case).

## 3. Verify

```bash
.venv/bin/python -m pytest -q
.venv/bin/python evaluation/roadworld_eval.py
```

The evaluation prints the RoadWorld counts (normal 0/4, wrong-way 4/4, bird 4/4, leaf 4/4) and
the first candidate frame per sequence. Browser acceptance scripts are described in
[TESTING.md](TESTING.md).

## 4. Where to look

* [Technical report](TECHNICAL_REPORT.md) – problem, users, architecture, OpenCV 5, AWS,
  evaluation, limitations, responsible use.
* [Architecture](ARCHITECTURE.md) – components, agent workflow, AWS deployment.
* [Synthetic evaluation](SYNTHETIC_EVALUATION.md) – protocol and failure cases.
* [AWS acceptance](AWS_ACCEPTANCE.md) and `cloud/demo/` – reproducible cloud runs.

Contact for a live screen-share walkthrough: through the Devpost project page.
