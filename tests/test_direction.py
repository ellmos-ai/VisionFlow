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
