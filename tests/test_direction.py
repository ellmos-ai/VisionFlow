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


def test_frame_ids_and_timestamps_must_advance_monotonically():
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
