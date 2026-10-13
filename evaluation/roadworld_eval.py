"""Reproducible RoadWorld direction evaluation (portable, no network).

Protocol (unchanged from the frozen 2026-10-08 evaluation): seeds 17, 18, 101,
102; events none, wrong_way, bird, leaf; the event is injected before the first
frame; 24 frames at 5 fps; the detector receives only Frame pixels. A sequence
is a candidate when any frame reports a confirmed opposing track.

    python evaluation/roadworld_eval.py [--out results.json]
"""

from __future__ import annotations

import argparse
import json
import sys

from visionflow.contracts import Frame
from visionflow.road_scene import RoadWorld
from visionflow.tools.direction import DirectionAnalyzer

SEEDS = (17, 18, 101, 102)
EVENTS = (None, "wrong_way", "bird", "leaf")
FRAMES, FPS = 24, 5
PARAMS = {
    "lanes": [
        {"id": "northbound", "region": [0, 90, 480, 450], "expected": [0, -1]},
        {"id": "southbound", "region": [480, 90, 480, 450], "expected": [0, 1]},
    ],
    "min_frames": 3,
    "min_displacement_px": 4.0,
    "opposition_cosine": -0.75,
}


def run_sequence(seed: int, event: str | None) -> dict:
    world = RoadWorld(seed=seed)
    if event:
        world.inject(event)
    analyzer = DirectionAnalyzer()
    name = f"s{seed}-{event or 'normal'}"
    first = None
    for index in range(FRAMES):
        t = index / FPS
        result = analyzer.update(Frame(f"{name}:{index}", world.render(t), t), PARAMS)
        if first is None and result.get("opposition_count", 0) > 0:
            first = index
    return {
        "sequence": name,
        "seed": seed,
        "event": event,
        "truth_wrong_way": event == "wrong_way",
        "candidate": first is not None,
        "first_candidate_frame": first,
    }


def summarize(rows: list[dict]) -> dict:
    by_event = {}
    for event in EVENTS:
        hits = [r for r in rows if r["event"] == event]
        by_event[event or "normal"] = f"{sum(r['candidate'] for r in hits)}/{len(hits)}"
    tp = sum(r["candidate"] and r["truth_wrong_way"] for r in rows)
    fp = sum(r["candidate"] and not r["truth_wrong_way"] for r in rows)
    fn = sum(not r["candidate"] and r["truth_wrong_way"] for r in rows)
    tn = len(rows) - tp - fp - fn
    return {"candidates_by_event": by_event, "as_wrong_way_vehicle_detector": {"TP": tp, "FP": fp, "TN": tn, "FN": fn}}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out")
    args = parser.parse_args(argv)
    rows = [run_sequence(seed, event) for seed in SEEDS for event in EVENTS]
    report = {"protocol": {"seeds": SEEDS, "events": EVENTS, "frames": FRAMES, "fps": FPS, "params": PARAMS},
              "sequences": rows, "summary": summarize(rows)}
    text = json.dumps(report, indent=2)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as handle:
            handle.write(text + "\n")
    print(json.dumps(report["summary"]))
    for r in rows:
        print(r["sequence"], r["candidate"], r["first_candidate_frame"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
