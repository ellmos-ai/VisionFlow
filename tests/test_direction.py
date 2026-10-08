from __future__ import annotations

from copy import deepcopy

import numpy as np
import pytest

from visionflow.contracts import Frame
from road_helpers import (
    default_lane_params,
    recolored_normal_frames,
    reverse_pixel_frames,
    road_frames,
)


def _load_direction_types():
    try:
        from visionflow.tools.direction import DirectionAnalyzer, direction_handler
    except ModuleNotFoundError as exc:
        if exc.name == "visionflow.tools.direction":
            pytest.fail("Task 2 RED: DirectionAnalyzer is missing from visionflow.tools.direction")
        raise
    return DirectionAnalyzer, direction_handler


def _opposing_tracks(values):
    return [
        track
        for value in values
        for track in value["tracks"]
        if track["opposition"]
    ]


def _analyze_sequence(frames, params=None):
    DirectionAnalyzer, _direction_handler = _load_direction_types()
    analyzer = DirectionAnalyzer()
    selected_params = default_lane_params() if params is None else params
    return [analyzer.update(frame, selected_params) for frame in frames]


def _opposing_flow_candidate(center=(250.0, 220.0)):
    return {
        "lane_id": "northbound",
        "center": center,
        "region": (250, 220, 32, 28),
        "dx_px": 0.0,
        "dy_px": 5.0,
        "opposition": True,
        "motion_norm": 5.0,
    }


def test_direction_is_derived_from_pixels_in_both_lanes_and_recolored_video():
    params = default_lane_params()
    for normal_frames in (
        road_frames(None, count=24, seed=7),
        recolored_normal_frames(count=24, seed=7),
    ):
        values = _analyze_sequence(normal_frames, params)
        assert all(value["opposition_count"] == 0 for value in values)

    values = _analyze_sequence(road_frames("wrong_way", count=24, seed=17), params)
    suspect = next(
        track
        for track in _opposing_tracks(values)
        if track["lane_id"] == "southbound" and len(track["frame_ids"]) >= 3
    )
    assert suspect["lane_id"] == "southbound"
    assert len(suspect["frame_ids"]) >= 3
    assert not suspect["track_id"].startswith("road-frames-")


@pytest.mark.parametrize(
    ("seed", "expected_lane"),
    [(17, "southbound"), (18, "northbound")],
)
def test_wrong_way_detection_covers_both_normal_lane_directions(seed, expected_lane):
    values = _analyze_sequence(
        road_frames("wrong_way", count=24, seed=seed),
        default_lane_params(),
    )

    assert any(
        track["lane_id"] == expected_lane and len(track["frame_ids"]) >= 3
        for track in _opposing_tracks(values)
    )


def test_wrong_way_track_survives_irregular_camera_sampling():
    from visionflow.road_scene import RoadWorld

    def sampled_frames(inject_wrong_way):
        world = RoadWorld(seed=42)
        for source_tick in range(1, 9):
            world.render(source_tick / 8.4)
        if inject_wrong_way:
            world.inject("wrong_way")

        frames = []
        next_observation_time = 1.0
        for source_tick in range(9, 51):
            timestamp = source_tick / 8.4
            pixels = world.render(timestamp)
            if timestamp + 1e-9 < next_observation_time:
                continue
            frames.append(
                Frame(
                    frame_id=f"irregular-road:{source_tick}",
                    image=pixels,
                    timestamp_seconds=timestamp,
                )
            )
            next_observation_time += 0.2
        return frames

    frames = sampled_frames(inject_wrong_way=True)
    intervals = [
        right.timestamp_seconds - left.timestamp_seconds
        for left, right in zip(frames, frames[1:])
    ]
    assert min(intervals) < 0.13
    assert max(intervals) > 0.23

    params = default_lane_params()
    normal_values = _analyze_sequence(sampled_frames(inject_wrong_way=False), params)
    assert all(value["opposition_count"] == 0 for value in normal_values)

    values = _analyze_sequence(frames, params)

    assert any(
        track["lane_id"] == "northbound" and len(track["frame_ids"]) >= 3
        for track in _opposing_tracks(values)
    )
    observed_frame_ids = {frame.frame_id for frame in frames}
    assert all(
        set(track["frame_ids"]) <= observed_frame_ids
        for track in _opposing_tracks(values)
    )


def test_confirmed_opposition_is_not_reported_without_a_current_candidate(monkeypatch):
    DirectionAnalyzer, _direction_handler = _load_direction_types()
    params = default_lane_params()
    params["lanes"] = params["lanes"][:1]
    initial = road_frames(None, count=1, seed=43)[0]
    analyzer = DirectionAnalyzer()
    candidates = iter(
        [
            [_opposing_flow_candidate()],
            [_opposing_flow_candidate((251.0, 225.0))],
            [],
            [],
        ]
    )
    monkeypatch.setattr(
        analyzer,
        "_lane_candidates",
        lambda *_args, **_kwargs: next(candidates),
    )
    frames = [
        Frame(f"coasting:{index}", initial.image, index / 5.0)
        for index in range(5)
    ]

    values = [analyzer.update(frame, params) for frame in frames]

    assert values[2]["opposition_count"] == 1
    assert values[3]["opposition_count"] == 0
    assert values[4]["opposition_count"] == 0
    assert values[3]["tracks"] == []
    assert values[4]["tracks"] == []


def test_long_camera_pause_expires_track_evidence(monkeypatch):
    DirectionAnalyzer, _direction_handler = _load_direction_types()
    params = default_lane_params()
    params["lanes"] = params["lanes"][:1]
    initial = road_frames(None, count=1, seed=47)[0]
    analyzer = DirectionAnalyzer()
    candidates = iter(
        [
            [_opposing_flow_candidate()],
            [_opposing_flow_candidate((251.0, 225.0))],
            [_opposing_flow_candidate((252.0, 230.0))],
        ]
    )
    monkeypatch.setattr(
        analyzer,
        "_lane_candidates",
        lambda *_args, **_kwargs: next(candidates),
    )

    analyzer.update(Frame("long-pause:0", initial.image, 0.0), params)
    analyzer.update(Frame("long-pause:1", initial.image, 0.2), params)
    confirmed = analyzer.update(Frame("long-pause:2", initial.image, 0.4), params)
    resumed = analyzer.update(Frame("long-pause:3", initial.image, 10.0), params)

    old_track_id = confirmed["tracks"][0]["track_id"]
    resumed_track = resumed["tracks"][0]
    assert confirmed["opposition_count"] == 1
    assert resumed["opposition_count"] == 0
    assert resumed_track["track_id"] != old_track_id
    assert resumed_track["frame_ids"] == ["long-pause:2", "long-pause:3"]


def test_distant_candidate_after_gap_does_not_reuse_old_evidence(monkeypatch):
    DirectionAnalyzer, _direction_handler = _load_direction_types()
    params = default_lane_params()
    params["lanes"] = params["lanes"][:1]
    initial = road_frames(None, count=1, seed=53)[0]
    analyzer = DirectionAnalyzer()
    candidates = iter(
        [
            [_opposing_flow_candidate()],
            [],
            [_opposing_flow_candidate((400.0, 220.0))],
        ]
    )
    monkeypatch.setattr(
        analyzer,
        "_lane_candidates",
        lambda *_args, **_kwargs: next(candidates),
    )

    analyzer.update(Frame("far-gap:0", initial.image, 0.0), params)
    original = analyzer.update(Frame("far-gap:1", initial.image, 0.2), params)
    analyzer.update(Frame("far-gap:2", initial.image, 0.4), params)
    distant = analyzer.update(Frame("far-gap:3", initial.image, 0.6), params)

    old_track_id = original["tracks"][0]["track_id"]
    assert len(distant["tracks"]) == 1
    assert distant["tracks"][0]["track_id"] != old_track_id
    assert distant["tracks"][0]["frame_ids"] == ["far-gap:2", "far-gap:3"]
    assert distant["opposition_count"] == 0


def test_reversing_saved_pixels_changes_the_detected_directions():
    params = default_lane_params()
    forward = _analyze_sequence(
        road_frames("wrong_way", count=24, seed=17),
        params,
    )
    reversed_values = _analyze_sequence(
        reverse_pixel_frames(road_frames("wrong_way", count=24, seed=17)),
        params,
    )

    forward_lanes = {track["lane_id"] for track in _opposing_tracks(forward)}
    reversed_lanes = {track["lane_id"] for track in _opposing_tracks(reversed_values)}

    assert "southbound" in forward_lanes
    assert reversed_lanes != forward_lanes
@pytest.mark.parametrize(
    ("event", "expected_lane", "seed"),
    [
        ("bird", "southbound", 17),
        ("leaf", "northbound", 17),
        ("bird", "southbound", 42),
        ("leaf", "northbound", 42),
    ],
)
def test_bird_and_leaf_pixels_can_produce_direction_candidates(event, expected_lane, seed):
    values = _analyze_sequence(
        road_frames(event, count=24, seed=seed),
        default_lane_params(),
    )

    assert any(
        track["lane_id"] == expected_lane and len(track["frame_ids"]) >= 3
        for track in _opposing_tracks(values)
    )


def test_warmup_static_frames_and_short_tracks_do_not_raise_an_alarm():
    DirectionAnalyzer, _direction_handler = _load_direction_types()
    params = default_lane_params()
    initial = road_frames(None, count=1, seed=29)[0]
    analyzer = DirectionAnalyzer()
    warmup = analyzer.update(initial, params)

    assert warmup["warmup"] is True
    assert warmup["opposition_count"] == 0

    static_frames = [
        Frame(
            frame_id=f"static-camera:{index}",
            image=initial.image,
            timestamp_seconds=index / 5.0,
        )
        for index in range(1, 6)
    ]
    static_values = [analyzer.update(frame, params) for frame in static_frames]
    assert all(value["opposition_count"] == 0 for value in static_values)

    short_values = _analyze_sequence(
        road_frames("wrong_way", count=2, seed=17),
        params,
    )
    assert all(value["opposition_count"] == 0 for value in short_values)


def test_occlusion_discards_old_tracks_and_reacquisition_uses_new_frame_ids():
    DirectionAnalyzer, _direction_handler = _load_direction_types()
    params = default_lane_params()
    source_frames = road_frames("wrong_way", count=24, seed=17)
    analyzer = DirectionAnalyzer()

    before_values = [analyzer.update(frame, params) for frame in source_frames[:8]]
    old_track_ids = {track["track_id"] for value in before_values for track in value["tracks"]}
    assert old_track_ids

    black = np.zeros_like(source_frames[0].image)
    analyzer.update(Frame("road-frames-17:8", black, 1.6), params)
    analyzer.update(Frame("road-frames-17:9", black, 1.8), params)

    after_values = []
    for index, original in enumerate(source_frames[8:], start=10):
        frame = Frame(
            frame_id=f"road-frames-17:{index}",
            image=original.image,
            timestamp_seconds=index / 5.0,
        )
        after_values.append(analyzer.update(frame, params))

    new_track_ids = {
        track["track_id"]
        for value in after_values
        for track in value["tracks"]
    }
    assert new_track_ids
    assert old_track_ids.isdisjoint(new_track_ids)
    assert all(
        int(frame_id.rsplit(":", 1)[1]) >= 8
        for value in after_values
        for track in value["tracks"]
        for frame_id in track["frame_ids"]
    )


@pytest.mark.parametrize("gray_value", [0, 127])
def test_uniform_current_pixels_do_not_create_direction_observations(gray_value):
    DirectionAnalyzer, _direction_handler = _load_direction_types()
    params = default_lane_params()
    source_frames = road_frames("wrong_way", count=8, seed=17)
    analyzer = DirectionAnalyzer()
    for frame in source_frames:
        analyzer.update(frame, params)

    uniform = np.full_like(source_frames[0].image, gray_value)
    value = analyzer.update(Frame("uniform-occlusion:8", uniform, 1.6), params)

    assert value["tracks"] == []


def test_partial_frame_occlusion_rejects_only_the_textureless_lane():
    DirectionAnalyzer, _direction_handler = _load_direction_types()
    params = default_lane_params()
    source_frames = road_frames("wrong_way", count=9, seed=17)
    analyzer = DirectionAnalyzer()
    for frame in source_frames[:8]:
        analyzer.update(frame, params)

    partially_occluded = source_frames[8].image.copy()
    x, y, width, height = params["lanes"][0]["region"]
    partially_occluded[y : y + height, x : x + width] = 127
    value = analyzer.update(
        Frame("partial-occlusion:8", partially_occluded, 1.6),
        params,
    )

    assert not any(track["lane_id"] == "northbound" for track in value["tracks"])
    assert any(track["lane_id"] == "southbound" for track in value["tracks"])


@pytest.mark.parametrize(
    "change",
    [
        lambda params: params["lanes"][0].update(region=[-1, 90, 480, 450]),
        lambda params: params["lanes"][0].update(expected=[float("nan"), -1]),
        lambda params: params.update(min_frames=True),
        lambda params: params.update(min_displacement_px=True),
        lambda params: params.update(opposition_cosine=float("nan")),
        lambda params: params["lanes"][0].update(expected=[0, 0]),
    ],
)
def test_invalid_lane_geometry_and_nonfinite_or_boolean_parameters_are_rejected(change):
    DirectionAnalyzer, _direction_handler = _load_direction_types()
    params = default_lane_params()
    change(params)
    frame = road_frames(None, count=1, seed=31)[0]

    with pytest.raises(ValueError):
        DirectionAnalyzer().update(frame, params)


def test_immediate_duplicate_frame_ids_and_timestamp_rollback_are_rejected():
    DirectionAnalyzer, _direction_handler = _load_direction_types()
    params = default_lane_params()
    frames = road_frames(None, count=3, seed=37)

    analyzer = DirectionAnalyzer()
    analyzer.update(frames[0], params)
    duplicate = Frame(frames[0].frame_id, frames[1].image, frames[1].timestamp_seconds)
    with pytest.raises(ValueError):
        analyzer.update(duplicate, params)

    analyzer = DirectionAnalyzer()
    analyzer.update(frames[0], params)
    earlier_time = Frame(frames[1].frame_id, frames[1].image, frames[0].timestamp_seconds)
    with pytest.raises(ValueError):
        analyzer.update(earlier_time, params)


def test_handler_accepts_nonmonotonic_numeric_frame_ids_for_same_context_source():
    _DirectionAnalyzer, direction_handler = _load_direction_types()
    params = default_lane_params()
    rendered_frames = road_frames(None, count=2, seed=39)
    frames = [
        Frame(
            "opaque-road:731",
            rendered_frames[0].image,
            rendered_frames[0].timestamp_seconds,
        ),
        Frame(
            "opaque-road:122",
            rendered_frames[1].image,
            rendered_frames[1].timestamp_seconds,
        ),
    ]
    context = {"source_id": "camera-source", "state": {}}

    first = direction_handler(frames[0], params, context)
    second = direction_handler(frames[1], params, context)

    assert first["warmup"] is True
    assert second["warmup"] is False
    assert second["coordinate_system"] == "crop_pixels"


def test_handler_resets_on_context_source_change_and_keeps_run_state_isolated():
    _DirectionAnalyzer, direction_handler = _load_direction_types()
    params = default_lane_params()
    params_with_orchestration = {**params, "initial_frames": 4}
    first_frame = road_frames(None, count=1, seed=41)[0]
    second_frame = Frame("camera-two:0", first_frame.image, 0.0)

    first_context = {"source_id": "camera-one", "state": {}}
    first_result = direction_handler(first_frame, params_with_orchestration, first_context)
    first_analyzer = first_context["state"]["direction_analyzer"]
    assert first_result["warmup"] is True

    first_context["source_id"] = "camera-two"
    switched_result = direction_handler(second_frame, params_with_orchestration, first_context)
    switched_analyzer = first_context["state"]["direction_analyzer"]
    assert switched_result["warmup"] is True
    assert switched_analyzer is not first_analyzer

    second_context = {"source_id": "camera-two", "state": {}}
    direction_handler(second_frame, params_with_orchestration, second_context)
    assert second_context["state"]["direction_analyzer"] is not switched_analyzer


def test_handler_maps_source_lanes_into_crop_pixels_and_returns_empty_when_none_intersect():
    _DirectionAnalyzer, direction_handler = _load_direction_types()
    params = default_lane_params()
    frames = road_frames("wrong_way", count=24, seed=17)
    right_roi = [480, 90, 480, 450]
    context = {
        "source_id": "road-source",
        "region": right_roi,
        "state": {},
    }

    values = []
    for frame in frames:
        x, y, width, height = right_roi
        crop = frame.image[y : y + height, x : x + width]
        cropped_frame = Frame(frame.frame_id, crop, frame.timestamp_seconds)
        values.append(direction_handler(cropped_frame, params, context))

    assert any(
        track["lane_id"] == "southbound"
        for track in _opposing_tracks(values)
    )
    assert all(value["coordinate_system"] == "crop_pixels" for value in values)
    assert all(
        0 <= track["region"][0] < 480
        and 0 <= track["region"][1] < 450
        for value in values
        for track in value["tracks"]
    )

    no_lane_roi = [0, 0, 480, 90]
    final_source_frame = frames[-1]
    x, y, width, height = no_lane_roi
    empty_crop = final_source_frame.image[y : y + height, x : x + width]
    context["region"] = no_lane_roi
    empty = direction_handler(
        Frame("road-source:24", empty_crop, 4.8),
        params,
        context,
    )

    assert empty["opposition_count"] == 0
    assert empty["tracks"] == []
    assert empty["coordinate_system"] == "crop_pixels"


def test_handler_returns_only_the_public_direction_metrics():
    _DirectionAnalyzer, direction_handler = _load_direction_types()
    frame = road_frames(None, count=1, seed=43)[0]
    metrics = direction_handler(
        frame,
        default_lane_params(),
        {"source_id": "camera-source", "state": {}},
    )

    assert set(metrics) == {
        "opposition_count",
        "warmup",
        "tracks",
        "coordinate_system",
    }
    assert metrics["warmup"] is True


def test_recorded_grayscale_motion_is_confirmed_across_three_observed_frames():
    import hashlib
    import json
    from pathlib import Path

    import cv2

    fixture_dir = (
        Path(__file__).parent
        / "fixtures"
        / "direction_flow_recording"
    )
    manifest = json.loads(
        (fixture_dir / "manifest.json").read_text(encoding="utf-8")
    )
    records = manifest["frames"]
    assert len(records) == 3

    frames = []
    images = []
    for record in records:
        image_bytes = (fixture_dir / record["path"]).read_bytes()
        assert hashlib.sha256(image_bytes).hexdigest() == record["sha256"]
        image = cv2.imdecode(
            np.frombuffer(image_bytes, dtype=np.uint8),
            cv2.IMREAD_COLOR,
        )
        assert image is not None
        images.append(image)
        frames.append(
            Frame(
                record["frame_id"],
                image,
                record["timestamp_seconds"],
            )
        )

    observed_frame_ids = [frame.frame_id for frame in frames]
    assert len(set(observed_frame_ids)) == 3
    assert all(
        0.0 < right.timestamp_seconds - left.timestamp_seconds <= 0.75
        for left, right in zip(frames, frames[1:])
    )

    gray_images = [
        cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        for image in images
    ]
    for motion in manifest["grayscale_motion_reference"]:
        source_index = observed_frame_ids.index(motion["from_frame_id"])
        target_index = observed_frame_ids.index(motion["to_frame_id"])
        x, y, width, height = motion["template_roi_xywh"]
        source_gray = gray_images[source_index]
        target_gray = gray_images[target_index]
        template = source_gray[y : y + height, x : x + width]
        search_left = max(0, x - 15)
        search_top = max(0, y - 10)
        search_right = min(target_gray.shape[1], x + 15 + width)
        search_bottom = min(target_gray.shape[0], y + 35 + height)
        search_area = target_gray[
            search_top:search_bottom,
            search_left:search_right,
        ]
        correlation = cv2.matchTemplate(
            search_area,
            template,
            cv2.TM_CCOEFF_NORMED,
        )
        _, score, _, location = cv2.minMaxLoc(correlation)
        observed_shift = (
            search_left + location[0] - x,
            search_top + location[1] - y,
        )
        expected_shift = tuple(motion["translation_xy_px"])
        assert score >= 0.80
        assert all(
            abs(actual - expected) <= 1
            for actual, expected in zip(observed_shift, expected_shift)
        )

    values = _analyze_sequence(frames, default_lane_params())
    last_motion = manifest["grayscale_motion_reference"][-1]
    x, y, width, height = last_motion["template_roi_xywh"]
    dx, dy = last_motion["translation_xy_px"]
    expected_center = (x + dx + width / 2, y + dy + height / 2)
    confirmed = [
        track
        for track in _opposing_tracks(values[-1:])
        if track["lane_id"] == "northbound"
        and track["frame_ids"] == observed_frame_ids
        and abs(track["region"][0] + track["region"][2] / 2 - expected_center[0]) <= 18
        and abs(track["region"][1] + track["region"][3] / 2 - expected_center[1]) <= 18
        and track["dy_px"] >= 4
    ]
    assert values[-1]["opposition_count"] >= 1
    assert confirmed


def test_native_patch_match_reports_bounded_xy_motion():
    from visionflow.tools.direction import _match_feature_patch

    rng = np.random.default_rng(9182)
    source = rng.integers(0, 256, size=(220, 240), dtype=np.uint8)
    target = np.zeros_like(source)
    target[:, 80:] = source[:, :-80]
    point = np.asarray([100.25, 110.75], dtype=np.float32)

    match = _match_feature_patch(source, target, point)

    assert match is not None
    center, score, peak_gap = match
    assert center == (180.0, 111.0)
    assert score == pytest.approx(1.0)
    assert peak_gap >= 0.05


def test_native_patch_match_rejects_ambiguous_constant_and_out_of_bounds_inputs():
    from visionflow.tools.direction import _match_feature_patch

    rng = np.random.default_rng(712)
    patch = rng.integers(0, 256, size=(21, 21), dtype=np.uint8)
    source = np.zeros((160, 160), dtype=np.uint8)
    source[40:61, 40:61] = patch
    repeated = np.zeros_like(source)
    repeated[45:66, 50:71] = patch
    repeated[95:116, 80:101] = patch
    assert _match_feature_patch(source, repeated, np.asarray([50.0, 50.0])) is None

    constant = np.full((80, 80), 127, dtype=np.uint8)
    assert _match_feature_patch(constant, constant, np.asarray([40.0, 40.0])) is None
    textured = rng.integers(0, 256, size=(80, 80), dtype=np.uint8)
    assert _match_feature_patch(textured, textured, np.asarray([40.0, 40.0])) is None
    assert _match_feature_patch(textured, textured, np.asarray([5.0, 30.0])) is None
    assert _match_feature_patch(
        textured[:18, :24],
        textured[:18, :24],
        np.asarray([12.0, 9.0]),
    ) is None
    assert _match_feature_patch(
        textured,
        textured,
        np.asarray([float("nan"), 30.0]),
    ) is None


def test_native_patch_match_does_not_search_beyond_eighty_pixels():
    from visionflow.tools.direction import _match_feature_patch

    rng = np.random.default_rng(821)
    source = rng.integers(0, 256, size=(220, 240), dtype=np.uint8)
    target = np.zeros_like(source)
    target[:, 81:] = source[:, :-81]

    match = _match_feature_patch(
        source,
        target,
        np.asarray([100.0, 110.0], dtype=np.float32),
    )

    assert match is None


def test_recorded_case1_confirms_only_at_the_visually_tracked_object_region():
    import hashlib
    import json
    import math
    from pathlib import Path

    import cv2

    fixture_dir = Path(__file__).parent / "fixtures" / "direction_case1_recording"
    manifest = json.loads(
        (fixture_dir / "manifest.json").read_text(encoding="utf-8")
    )
    records = manifest["frames"]
    assert [
        (record["source_frame_id"], record["timestamp_seconds"])
        for record in records
    ] == [
        ("road-sim-ce09f51461954dae80ec8104e0d3350b:20", 1.7030000000959262),
        ("road-sim-ce09f51461954dae80ec8104e0d3350b:24", 2.047000000020489),
        ("road-sim-ce09f51461954dae80ec8104e0d3350b:28", 2.359000000054948),
        ("road-sim-ce09f51461954dae80ec8104e0d3350b:32", 2.75),
        ("road-sim-ce09f51461954dae80ec8104e0d3350b:37", 3.219000000040978),
        ("road-sim-ce09f51461954dae80ec8104e0d3350b:41", 3.5),
        ("road-sim-ce09f51461954dae80ec8104e0d3350b:46", 3.859000000054948),
    ]

    frames = []
    for record in records:
        image_bytes = (fixture_dir / record["path"]).read_bytes()
        assert hashlib.sha256(image_bytes).hexdigest() == record["sha256"]
        image = cv2.imdecode(
            np.frombuffer(image_bytes, dtype=np.uint8),
            cv2.IMREAD_COLOR,
        )
        assert image is not None
        frames.append(
            Frame(
                record["source_frame_id"],
                image,
                record["timestamp_seconds"],
            )
        )

    observed_ids = [frame.frame_id for frame in frames]
    assert len(set(observed_ids)) == 7
    intervals = [
        right.timestamp_seconds - left.timestamp_seconds
        for left, right in zip(frames, frames[1:])
    ]
    assert all(
        0.0 < interval
        <= manifest["analysis_contract"]["max_observed_gap_seconds"]
        for interval in intervals
    )

    def matches_annotated_geometry(region, record):
        x, y, width, height = region
        center_x, center_y = record["approx_vehicle_center_xy"]
        center_distance = math.hypot(
            x + width / 2.0 - center_x,
            y + height / 2.0 - center_y,
        )
        left, top, right, bottom = record["approx_vehicle_bbox_xyxy"]
        margin = manifest["vehicle_region_rule"]["bbox_expansion_px"]
        overlaps = (
            x < right + margin
            and x + width > left - margin
            and y < bottom + margin
            and y + height > top - margin
        )
        return (
            center_distance <= manifest["vehicle_region_rule"]["center_tolerance_px"]
            and overlaps
        )

    far_reference = manifest["known_far_false_candidate_reference"]
    far_record = next(
        record for record in records
        if record["source_frame_id"] == far_reference["source_frame_id"]
    )
    source_prefix = records[0]["source_frame_id"].rsplit(":", 1)[0]
    far_source_ids = [
        f"{source_prefix}:{source_number}"
        for source_number in far_reference["observed_source_ids"]
    ]
    timestamp_by_source = {
        record["source_frame_id"]: record["timestamp_seconds"]
        for record in records
    }
    far_timestamps = [timestamp_by_source[source_id] for source_id in far_source_ids]
    assert far_reference["opposition_count_in_that_replay"] >= 1
    assert len(set(far_source_ids)) >= manifest["analysis_contract"]["min_frames"]
    assert all(
        0.0 < right - left
        <= manifest["analysis_contract"]["max_observed_gap_seconds"]
        for left, right in zip(far_timestamps, far_timestamps[1:])
    )
    assert not matches_annotated_geometry(far_reference["region_xywh"], far_record)

    DirectionAnalyzer, _direction_handler = _load_direction_types()
    analyzer = DirectionAnalyzer()
    values = [analyzer.update(frame, default_lane_params()) for frame in frames]
    record_by_id = {record["source_frame_id"]: record for record in records}
    timestamp_by_id = {
        record["source_frame_id"]: record["timestamp_seconds"]
        for record in records
    }

    qualifying = []
    for frame, value in zip(frames, values):
        if value["opposition_count"] < 1:
            continue
        current_record = record_by_id[frame.frame_id]
        for track in value["tracks"]:
            if track["lane_id"] != manifest["analysis_contract"]["lane_id"]:
                continue
            if not track["opposition"] or track["frame_ids"][-1] != frame.frame_id:
                continue
            source_ids = list(dict.fromkeys(track["frame_ids"]))
            if len(source_ids) < manifest["analysis_contract"]["min_frames"]:
                continue
            if not set(source_ids) <= set(observed_ids):
                continue
            ordered_ids = sorted(source_ids, key=timestamp_by_id.__getitem__)
            if any(
                timestamp_by_id[right] - timestamp_by_id[left]
                > manifest["analysis_contract"]["max_observed_gap_seconds"]
                for left, right in zip(ordered_ids, ordered_ids[1:])
            ):
                continue
            if matches_annotated_geometry(track["region"], current_record):
                qualifying.append((frame.frame_id, track["frame_ids"], track["region"]))

    assert qualifying, (
        "no confirmed opposing track is currently localized to the manually reviewed "
        "object geometry across at least three distinct recorded source frames"
    )
