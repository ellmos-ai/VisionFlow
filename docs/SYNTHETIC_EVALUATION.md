# Synthetic evaluation

## Update 2026-10-17: renderer v4 (lane-keeping traffic, longer event paths)

Regular traffic now uses the two outer lanes of each carriageway at one shared speed per lane, so
cars never drive into each other; the inner lanes stay free for the wrong-way car, which enters
at the start of its path (horizon or bottom edge) instead of mid-frame. The bird also enters at the
bottom edge; bird (75 px/s) and leaf (65 px/s) move more slowly than before. In a separate
diagnostic with five seeds and the event injected after three seconds, at two analyses per second
the bird was detected in 5/5 and the leaf in 4/5 runs (previous renderer: 3/5 and 4/5); at five
analyses per second all events were detected in both renderers. With the unchanged protocol (`evaluation/results/roadworld-renderer-v4.json`) the
counts are unchanged: normal 0/4, wrong-way 4/4, bird 4/4, leaf 4/4 (TP 4, FP 8, TN 4, FN 0).
First-candidate frames moved later for most event sequences (wrong-way seed 18 and 102 from frame
2 to 3; bird 2–3 → 4–8; leaf 3–5 → 5–8); all remain within the 24 analysed frames.

## Update 2026-10-13: portable RoadWorld script and the new scene renderer

`evaluation/roadworld_eval.py` implements the RoadWorld part of the protocol below (seeds 17,
18, 101, 102; events none, wrong-way, bird, leaf injected before the first frame; 24 frames at
5 fps; lanes, minimum frames, displacement and opposition cosine as in the road workflow; a
sequence counts as a candidate when any frame reports a confirmed opposing track). Run on the
renderer of 2026-10-08 it reproduces all 16 frozen sequence results exactly, including the first
candidate frame (`evaluation/results/roadworld-renderer-v1.json`).

The road scene was then re-rendered with richer but deliberately smooth scenery and more
detailed cars. With the same protocol (`evaluation/results/roadworld-renderer-v2.json`) the
counts are unchanged: normal 0/4, wrong-way 4/4, bird 4/4, leaf 4/4 (as a wrong-way-vehicle
detector TP 4, FP 8, TN 4, FN 0). Two first-candidate frames moved earlier: seed-18 bird from
frame 3 to 2, seed-18 leaf from frame 9 to 2. Later changes on the same day (thread-pooled patch
matching, resolution-independent event speeds) left all 16 sequence results identical.

The section below is the original 2026-10-08 report; its 1,430 frozen PNGs describe the earlier
renderer.

## Original report, 2026-10-08

Executed product revision: `e65ef914b4a27727481ad604ee3384ee13f5b27c`. Darwin arm64, Python 3.14.7, OpenCV 5.0.0 and NumPy 2.5.3. Source hashes and tracked status were identical before and after execution. No network or provider calls were made. Evaluation-script logic, seeds, generators, metrics and counting rules stayed fixed; only the repository locator and two protocol source fields changed. All 1,430 original input/ground-truth PNGs are byte-identical to the original Windows/Python 3.12.10 evaluation. The product algorithm and corner spacing changed during development; this is a rerun of known synthetic fixtures, not an external holdout.

| Check | Counting unit | Measured result | Interpretation |
| --- | --- | --- | --- |
| Known Gaussian blur | Three generated pages, twelve images, eighteen dependent within-page pairs | Sharpness ordering correct in 18/18 pairs; mean-brightness ordering correct in 9/18 | Brightness is a negative comparison control, not an equally capable competing sharpness algorithm. |
| Changed motion pixels | Eighteen sequences, ninety transitions and eighteen warmup frames | High contrast: 1,800/1,800 changed pixels measured. Contrast 20: 0/1,800 measured. | The existing contrast threshold is 25. A threshold-free pixel comparator also counts illumination changes. |
| Known horizontal direction | Thirty-six sequences, twelve frames each; twelve negative and twenty-four nonnegative translations | Production: TP=9, FP=0, TN=24, FN=3. Intensity-centroid comparison: TP=12, FP=3, TN=21, FN=0. | All three production misses are untextured negative translations. All three centroid false positives are illumination changes. No general superiority follows. |
| RoadWorld events | Sixteen sequences, twenty-four frames each; four seeds per event | Direction candidates: normal 0/4; wrong-way 4/4; bird 4/4; leaf 4/4. | Geometric candidates do not classify vehicles. If interpreted as a wrong-way-vehicle detector, the matrix would be TP=4, FP=8, TN=4, FN=0. The additional seed-18 bird and leaf candidates remain in the results. |
| Real offline CLI | Document and motion workflows | Exit 0; passed; two and five decodable PNG evidence frames; motion `observe` then `report` | Model decisions are fixed fixtures. This is not live LLM reasoning. |

Quality seeds were 101, 202 and 303, with known Gaussian blur sigma 0, 1, 2 and 4. Motion used the same seeds with known rectangle-mask transitions at contrast 20 and 255 and displacement 0, 1 and 4. Direction used known displacement -6, 0 and 6 at five frames per second across textured, untextured, occluded and illumination-change variants. RoadWorld used seeds 17, 18, 101 and 102 across normal, wrong-way, bird and leaf events. Seeds 17/18 were already used during development; additional seeds from the same generator are not an external holdout.

Ground truth comes from generated masks, applied blur and geometric translations. The detector receives pixels without scenario labels. Sequence-level TP/FP/TN/FN use whether any confirmed opposing track appears in the sequence. Consecutive frames and within-page pairs are dependent; they are not independent samples for a confidence interval. There was no training or arbitrary accuracy pass threshold. Matcher and feature-spacing settings were selected during development after earlier evaluation results were available, with recorded tracking failures and runtime as the motivation. The evaluation logic and counting thresholds were unchanged; no blinded holdout or generalization claim follows.

The complete sanitized evaluation artifact set is provided separately in the jury bundle. It contains `RESULTS.md`, `RESULTS.json`, `PROTOCOL.json`, `READBACK.json`, raw measurement rows, generated inputs, pixel masks, source hashes and CLI traces/evidence with relative paths. Its detailed protocol specifies exact generators, parameters and counting rules for manual reproduction. The private evaluation and readback scripts are identified by hashes; no portable published script execution is claimed.

This rerun's original protocol SHA-256: `dd6c431945c95c72a99aae651c292809251eecf315e5413b4a7be9ca14965403`. Locator-adjusted evaluation-script SHA-256: `765d2c492ea7742b59ff21df8fe5b32cd634f270e133162a4eef866caa3aae21`; unchanged readback-script SHA-256: `bee8e4509c186b158065e600739918c294ee2034110a4dabb430dd51f1d037b8`. The complete sanitized evaluation ZIP SHA-256 is `033942a6ff5124ed0a9ce7cc47d53bb789ece4ec3af1f2fcd24f90002c7f09ed` (23,972,383 bytes, 1,458 files). Its public manifest is regenerated after removing private absolute paths; binary media remain unchanged.

Aggregate directional counts match the earlier product, but detailed tracks are not identical. Compared with the original evaluation, 15 sequence details changed; the bird and leaf proxy false positives rose from six to eight. The final spacing change moves seed-18 bird's first candidate from frame 2 to frame 3. All raw tracks, misses and extra candidates remain available. Platform differences prevent attributing every numerical delta exclusively to the source change.

A separate process verified 1,452 original hashes, 1,432 valid PNG files, 108 reconstructed motion masks, 432 direction-object pixel boxes and 384 road-mask pixel counts. The deliberately invalid PNG remains an error fixture. CLI evidence filenames end in `.bin` but contain actual decodable PNG bytes. Form changes, float arrays and invalid PNGs produce structured media errors.

Known practical limits remain visible: random noise can have high Laplace variance without readability; low-contrast movement is suppressed; untextured counter-motion can be missed; bird and leaf motion requires a separate semantic review. Hashes establish file integrity, not independent provider authenticity. This synthetic evaluation establishes no OCR quality, model accuracy, real-camera reliability, real-traffic safety, deployment latency, throughput or dataset generalization. Live-model, AWS, jury-access and personal GUI acceptance remain separate gates.
