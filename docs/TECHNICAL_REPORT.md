# VisionFlow technical report

OpenCV AI Competition 2026, powered by AWS · Track: Agentic Vision with OpenCV 5 ·
Author: Lukas Geiger (solo).

## 1. Problem

Many vision tasks end with "and then a human has to look at it": a traffic camera sees
something moving the wrong way, a scanned form is too blurry, a video shows movement where
there should be none. Pixel measurements are fast and cheap, but they are not meaning. If every
hit raises an alarm, operators drown in false alarms; if hits are ignored, the rare real event is
missed. What is missing is a **controlled loop** between measurement and action.

VisionFlow is that loop as reusable software: OpenCV 5 tools measure; the measurement decides
what the workflow does next (observe more, ask for a new input, open a case, ask a model, ask a
human, act); every step is bounded, logged and bound to the evidence it was based on.

## 2. Users

* **Operators in control rooms** (traffic, facilities, inspection) who need fewer, better-founded
  alerts with the evidence attached, and a deadline-driven fallback when nobody answers.
* **Developers and integrators** who want to configure such loops for new use cases in JSON
  instead of writing a new application each time.

## 3. Architecture

See [ARCHITECTURE.md](ARCHITECTURE.md) for the component, agent-workflow and deployment
diagrams. In short:

* A **bounded workflow controller** executes a DAG of `analyze`, `observe`, `branch`,
  `ask_model`, `request_input`, `approve`, `output` and `report` nodes. Each run has frame,
  observation-time and model-call budgets.
* An **OpenCV 5 tool registry** provides `quality` (Laplacian sharpness, brightness), `motion`
  (difference images, contours) and `direction` (patch tracks against lane vectors).
* The **road case service** adds recording (immutable H.264 clips with SHA-256), a model router
  (offline profiles or Gemini video), a decision policy with deadlines, and a simulated
  authority inbox.
* The same core is reachable through the Python API, the CLI and a browser interface served by a
  loopback-only HTTP server with session cookies and CSRF tokens.

## 4. OpenCV 5 implementation

* Image decoding, Laplacian variance, grayscale statistics, difference images, contour and
  region extraction, video cursor handling and evidence-frame encoding use OpenCV 5.0.0.
* **Direction analysis** (`src/visionflow/tools/direction.py`): Shi–Tomasi corners
  (`goodFeaturesToTrack`, eight-pixel spacing, at most 240 per lane) on the previous frame;
  21×21 native-resolution grayscale patches searched within ±80 px with `matchTemplate`
  (`TM_CCOEFF_NORMED`); a match needs NCC ≥ 0.80 and a 0.05 gap to any distinct second peak; a
  reverse match must return within 1.5 px; the end point needs local corner support
  (`cornerMinEigenVal`). Features are clustered into tracks that need three distinct frames, at
  most 0.75 s and 80 px between observations. A track is *opposing* when its motion has a cosine
  of at most −0.75 with the configured lane vector. Scenario labels never reach the detector.
* Patch matches run on a small shared thread pool; `matchTemplate` releases the GIL. Each match is
  independent, so results are identical; median per-frame analysis time on a loaded laptop
  dropped from 484 ms to 243 ms.
* The **synthetic road** (`road_scene.py`) is rendered with OpenCV drawing primitives:
  perspective road, lanes, cars with shading and lights, a bird and a leaf as distractors. Static
  scenery is rendered once and kept smooth so it does not compete with moving cars for the
  per-lane corner budget.
* The recorder encodes evidence clips (5 s before, 10 s after a finding) with FFmpeg/libx264 and
  binds their SHA-256 to the case; model and human see the same bytes.

## 5. Agentic loop: how visual results change what happens next

1. The road workflow observes the camera at 5 frames per second. As long as the direction tool
   reports no opposing track, the observation simply continues.
2. An opposing track stops the observation (`stop_when`), selects the candidate branch and
   triggers the clip export.
3. The model stage receives the hash-bound clip and evidence frames (at most two calls per case).
   Its typed answer (`object_class`, `hazard`, `confidence`) is validated.
4. The decision policy acts on it: `hazard = false` dismisses the case; a vehicle hazard with
   confidence ≥ 0.90, OpenCV confirmation and a valid clip is reported at once; everything else,
   including model errors and timeouts, goes to a human with a 20-second server deadline. No
   decision means an automatic report; a later rejection sends a linked all-clear.
5. Every transition is in the trace and the case journal; the browser shows the same server
   state in four rooms.

Model output can never add a media source or an output destination. All notifications go to a
simulated inbox.

## 6. AWS deployment

* **Public demo** (`cloud/demo/demo-stack.yaml`, `cloud/demo/deploy_demo.py`): an AWS Graviton
  (arm64) t4g.small instance running Ubuntu 24.04 serves `visionflow serve --public-demo` on
  loopback behind nginx; Amazon CloudFront provides HTTPS and adds a secret origin header that
  nginx checks; the security group only admits the CloudFront origin-facing prefix list; there is
  no SSH (SSM only). The public-demo mode only reads the road simulation and media uploaded to the
  server, caps uploads at 200 MB in total and runs at most four live road sessions; nginx rate
  limits session creation and uploads; systemd restarts the service with an empty data
  directory every 24 hours.
* **Cloud acceptance** (`cloud/template.json`, `cloud/buildspec.yml`, `cloud/acceptance.py`):
  an allowlisted ZIP of the committed source with per-file SHA-256 goes to a private, versioned
  S3 bucket; AWS CodeBuild installs the pinned dependencies, runs the complete pytest suite and
  both OpenCV demos, and returns an evidence ZIP with a receipt that is verified against the
  source hashes ([procedure](AWS_ACCEPTANCE.md)).

<!-- AWS_RESULTS -->

## 7. Evaluation

Full details: [SYNTHETIC_EVALUATION.md](SYNTHETIC_EVALUATION.md).

* **Known blur** (three generated pages, 18 dependent pairs): sharpness ordering correct 18/18.
* **Known horizontal direction** (36 generated sequences): TP 9, FP 0, TN 24, FN 3; all misses
  are untextured negative translations.
* **RoadWorld** (seeds 17, 18, 101, 102; normal, wrong-way, bird, leaf; 24 frames at 5 fps):
  direction candidates normal 0/4, wrong-way 4/4, bird 4/4, leaf 4/4. Read as a wrong-way-vehicle
  detector: TP 4, FP 8, TN 4, FN 0. The pixel stage finds opposing motion; telling vehicles from
  distractors is the job of the model and human stages. The protocol is reproducible with
  `evaluation/roadworld_eval.py`, which reproduces the frozen 2026-10-08 results exactly; the
  re-rendered scene of 2026-10-13 gives the same counts (bird and leaf for seed 18 now trigger at
  frame 2 instead of 3 and 9). Results: `evaluation/results/`.
* **Live model test** (Gemini video, 13 October 2026): five real requests against the
  Interactions API; three cases failed with HTTP 503 ("high demand"), one direct check confirmed
  the 503, one case exceeded the 30-second client bound. In every case the policy fell back to
  human review and the deadline path, as designed. No model accuracy is claimed.
* **Software tests**: On Windows 11 with Python 3.12.10, OpenCV 5.0.0 and NumPy 2.5.3 the suite reports **560 passed, 1 skipped** (the directory-symlink test needs a Windows privilege), 0 failed (2026-10-13, commit bde0e67). Browser acceptance (Playwright with Microsoft Edge):
  multi-event control-room run, camera phase and review phase passed on the integrated tree with
  no browser errors and no external requests.

## 8. Limitations

* All traffic is synthetic and deterministic per seed. The detector parameters were tuned on
  known sequences; there is no blinded holdout and no claim about real roads or cameras.
* The direction stage flags birds and leaves too. The public demo uses fixed offline model
  profiles that do not look at the image; live Gemini review exists but could not be shown
  succeeding during the test window because of provider overload.
* Analysis speed depends on CPU; under heavy load the gap between analysed frames can exceed the
  0.75-second track bound, in which case nothing is detected (a miss, not a false alarm).
* Notifications are simulated; there is no integration with a real authority.

## 9. Responsible use

* VisionFlow is decision support. A human or an explicit, documented policy is responsible for
  every escalation; deadlines and fallbacks are visible and logged.
* Evidence is bound by hash, so the model and the human judge the same clip; corrections are
  linked, not overwritten.
* The server binds to loopback only. Local mode may read files the server account can read; the
  hosted demo uses `--public-demo`. Cameras need an explicit mapping. Provider calls need an
  explicit cost opt-in; keys are never stored in source or logs.
* No personal data is processed in the demo; real deployments with cameras need a privacy
  assessment, retention limits and signage according to local law.
