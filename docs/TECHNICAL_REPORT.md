# VisionFlow technical report

Updated 2026-10-08. Product source revision: `ac3ca55236d4a3772fa1443b68ea7b91f1b9804f`. This describes the implemented local system, measured synthetic checks and the prepared AWS acceptance path. It is not a completed contest submission.

## Problem and implementation

VisionFlow reuses a bounded computer-vision workflow across Python API, CLI and a local web interface. A configurable directed acyclic workflow connects analysis, branching, model questions, observation, input replacement, reporting and human approval. The controller accounts for frame, observation-time and model-call budgets. Decisions cannot authorize new media sources or output destinations.

OpenCV 5 performs pixel processing: image decoding, sharpness variance, contours, motion detection, video cursor operations, generated synthetic media and evidence-frame encoding. The road demonstrator adds direction analysis over multiple frames and immutable H.264 clip export through FFmpeg. These are substantive vision operations, not just a version import.

## Functional demonstrations

Document quality: a generated blurred image is measured; a workflow branch pauses for a replacement; a generated sharper image is supplied and analyzed. The result preserves findings and real evidence paths. Laplace variance is a measurement for this synthetic fixture, not a calibrated universal document-quality threshold, OCR result or completeness guarantee.

Motion observation: generated video is read at native media positions, a fixture requests bounded follow-up, and the same media cursor continues. Tests check actual frame IDs, budgets, trace events and stored frame files. The fixture requests `observe` and then `report`; it is deterministic scripted decision logic, not evidence of real LLM reasoning.

Road review: synthetic traffic events feed OpenCV direction analysis, recording, fixture review, a server deadline and content-bound user decisions. Timed notifications and later all-clears appear in a simulated local inbox. This does not establish safety performance on real roads.

## AWS component and reproducibility

The prepared CodeBuild recipe uploads an allowlisted ZIP from one committed revision to private S3, installs the pinned dependencies, executes the full test suite and runs the document and motion workflows. AWS compute therefore executes the full OpenCV workflow, including real synthetic media decoding and trace/evidence generation. This is the intended meaningful AWS component; storage alone is not presented as the contribution.

An exact source ZIP SHA-256, per-file source hashes, source commit, test log/JUnit, dependency versions, demo traces, evidence-frame hashes and a run receipt make the run reproducible. The source is pinned to an S3 object version with an independently returned full-object SHA-256 checksum. Cloud acceptance requires the original evidence ZIP, matching AWS artifact SHA-256 and a byte-exact extracted file set including its manifest. Independently saved stack/start/upload/head responses and the terminal build readback must agree on account, region, project, source location, source version, buildspec and artifact destination. Hashes establish artifact integrity, not provider authenticity by themselves. See [AWS acceptance procedure](AWS_ACCEPTANCE.md).

The evidence checker validates both completed scenarios, the document input replacement and its measured sharpness gain, the motion observe/report ordering and forward frame cursor. Referenced PNG bytes are decoded and compared with the referenced source image or video frames. Core evidence uses `.bin` filenames; file extensions are not accepted as proof of image contents.

## Evaluation status and limitations

The exact integrated product revision passed all 541 pytest checks in 80.12 seconds on an isolated Mac with Python 3.12.13 and OpenCV 5.0.0. The terminal process exit code was zero; JUnit records zero failures, errors and skips. Independent cloud review passed 27 checks and three tampering probes. Independent direction review passed 25 tests and 19 additional boundary probes. The real wrong-way-to-human-review test passed five consecutive author runs under its unchanged 35-second deadline. These are functional regression checks, not independent accuracy samples or a performance benchmark.

Hosted GitHub Actions did not start jobs because GitHub reported account payment/spending restrictions. The original Windows full-suite run had six timing failures under heavy load; those results remain preserved. The final Windows Edge browser run also timed out waiting for case 1 to reach human review within its unchanged 90-second limit. Its recorded frames and timestamps are being diagnosed. A separate isolated Playwright Chromium run on the exact final product revision passed all three complete cases and eleven checks, including clip identity, human confirmation, real deadline expiry and later correction, with no browser errors or external requests. This does not establish a final-revision Windows browser pass or personal GUI acceptance.

The synthetic evaluation protocol declared generators, seeds, sequence ground truth, metrics and comparison methods before execution. All failure cases remain in the results. Frames within a sequence are dependent; no confidence interval treats them as independent samples. The declared blur ordering and geometric translations are ground truth for generated pixels, not labels for document readability, vehicle semantics or real traffic safety. [Final synthetic measurements](SYNTHETIC_EVALUATION.md) describe the comparison results, failure cases and reproducibility boundaries. A separate readback verified 1,452 original artifact hashes and reconstructed pixel ground truth; the sanitized jury artifact set retains the full dataset.

Genuine provider feedback, AWS execution, jury-access agreement and personal GUI acceptance remain separate evidence requirements until completed. The current Gemini transport sends whole inline video and trigger images, with at most two requests per road case, a 20 MiB request bound and a 30-second client deadline. It currently uses provider generation defaults rather than a configurable output-token limit. A fixture response is not evidence of model reasoning. No cloud timings, model accuracy, real-camera reliability or dataset generalization are claimed.

The proposed cloud run uses no LLM provider and no credentials embedded in source. The local web service binds only to loopback and permits server-account-readable local media; use it only with trusted local accounts. Camera access is explicitly mapped. The cloud package provides batch compute rather than a publicly reachable jury demo.
