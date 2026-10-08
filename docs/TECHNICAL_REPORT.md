# VisionFlow technical report

Prepared 2026-10-08. This describes the implemented local system and the proposed AWS acceptance path. It is not a completed contest evaluation.

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

Existing tests and demos establish bounded local functional behavior. They do not establish an accuracy benchmark, baseline improvement, deployment latency, throughput, real-camera reliability or dataset generalization. No scores or cloud timings are invented here. A proper contest evaluation still needs a declared dataset/split, ground truth, metrics, baseline, uncertainty/failure analysis and reproducible results. Genuine provider feedback, AWS execution, jury-access agreement and personal GUI acceptance remain separate evidence requirements until completed.

The proposed cloud run uses no LLM provider and no credentials embedded in source. The local web service binds only to loopback and permits server-account-readable local media; use it only with trusted local accounts. Camera access is explicitly mapped. The cloud package provides batch compute rather than a publicly reachable jury demo.
