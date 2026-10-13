# Road case traces (13 October 2026)

Real server responses recorded step by step with the CLI (`road create`, `road case start`,
`road inject`, `road case status`, `road inbox`) against a local VisionFlow server. Each file is a
list of steps; `final` holds the last case snapshot with the controller `perception_trace`, the
case `journal`, the model `assessment` or `model_error`, and the receipts.

How to read the agentic loop in a trace:

1. `node_started direction` / `frame_analyzed` – the OpenCV direction tool runs on real frames.
2. `monitor_stopped` with `reason: "condition"` – the observation stopped **because** the tool
   reported opposing motion; `frame_ids` and `source_times` are the frames it used.
3. `branch_selected` with state `tail` – the visual result selected the candidate path: a
   bounded 10-second follow-up observation that completes the evidence clip (its own
   `monitor_stopped` has `reason: "duration"`). Without opposing motion the branch would have
   selected the `normal` report instead.
4. `exchange_ids`, `assessment` or `model_error` – the model stage on the hash-bound clip.
5. `journal` – `case_opened`, `human_review_started`, `first_report_sent` (reason
   `no_response` when the 20-second deadline expired) or the human decision.

| File | Model stage | Outcome |
|---|---|---|
| `road-case-wrong-way-offline-profile.json` | offline profile `pending` (confidence 0.80) | human review → deadline → automatic report |
| `road-case-wrong-way-gemini-503.json` | Gemini video, HTTP 503 | `model_error: provider_failed` → human review → automatic report |
| `road-case-bird-gemini-503.json` | Gemini video, HTTP 503 | same fallback |
| `road-case-leaf-gemini-503.json` | Gemini video, HTTP 503 | same fallback |
| `road-case-wrong-way-gemini-timeout.json` | Gemini video, over the 30 s bound | `model_error: timeout` → human review → automatic report |

The Gemini files document the failure path: the provider was overloaded during the test window,
and every case fell back to human review as designed. They are not evidence of model accuracy.
Session files with cookies were not included; recipients are simulated.
