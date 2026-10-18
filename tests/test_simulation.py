from __future__ import annotations

import threading
import time
import gc
import weakref

import cv2
import numpy as np
import pytest

from road_helpers import road_frames, vars_for_frame
from visionflow.contracts import Frame


def _assert_camera_images_released(session, image_refs):
    gc.collect()
    assert image_refs and all(ref() is None for ref in image_refs), "closed camera still owns raw images"
    assert not session._frames and session._latest is None
    preview = session.final_preview_jpeg()
    assert isinstance(preview, bytes) and 0 < len(preview) < 1024 * 1024
    image = cv2.imdecode(np.frombuffer(preview, dtype=np.uint8), cv2.IMREAD_COLOR)
    assert image.shape == (540, 960, 3)


def test_close_releases_raw_frames():
    SimulationSession, _ = _load_simulation_types()
    image_refs = []
    ready = threading.Event()

    def observe(frame):
        image_refs.append(weakref.ref(frame.image))
        ready.set()

    session = SimulationSession("release-test", on_frame=observe)
    reader = session.open_reader()
    try:
        session.start()
        assert ready.wait(3)
        image_refs.append(weakref.ref(session._world._static))
    finally:
        session.close()
    assert not session._producer.is_alive()
    _assert_camera_images_released(session, image_refs)
    assert reader.read() is None
    original_preview = session.final_preview_jpeg()
    session.close()
    assert session.final_preview_jpeg() == original_preview


def test_producer_expiry_releases_raw_frames(monkeypatch):
    import visionflow.simulation as simulation
    monkeypatch.setattr(simulation, "_MAX_SESSION_SECONDS", 0.2)
    image_refs = []
    ready = threading.Event()

    def observe(frame):
        image_refs.append(weakref.ref(frame.image))
        ready.set()

    session = simulation.SimulationSession("expiry-test", on_frame=observe)
    try:
        session.start()
        assert ready.wait(3)
        session._producer.join(3)
        assert not session._producer.is_alive()
        assert session.closed and session.error_code is None
        _assert_camera_images_released(session, image_refs)
    finally:
        session.close()


def test_producer_limit_ends_the_session_without_a_technical_error():
    import visionflow.simulation as simulation
    image_refs = []

    def limited_recording(frame):
        image_refs.append(weakref.ref(frame.image))
        if len(image_refs) >= 3:
            raise simulation.ProducerLimitReached("recording_limit")

    session = simulation.SimulationSession("limited-producer", on_frame=limited_recording)
    try:
        session.start()
        session._producer.join(3)
        assert not session._producer.is_alive()
        assert session.closed
        assert session.error_code is None
        assert session.end_reason == "recording_limit"
        _assert_camera_images_released(session, image_refs)
    finally:
        session.close()


def test_time_cap_and_stop_report_their_end_reason(monkeypatch):
    import visionflow.simulation as simulation
    stopped = simulation.SimulationSession("stop-reason")
    expiring = simulation.SimulationSession("expiry-reason")
    try:
        assert stopped.end_reason is None
        stopped.start()
        stopped.latest_frame()
        stopped.close()
        assert stopped.end_reason == "stopped"
        monkeypatch.setattr(simulation, "_MAX_SESSION_SECONDS", 0.2)
        expiring.start()
        expiring._producer.join(3)
        assert expiring.end_reason == "time_limit"
    finally:
        expiring.close()
        stopped.close()


def test_producer_failure_releases_raw_frames():
    SimulationSession, _ = _load_simulation_types()
    image_refs = []

    def fail_recording(frame):
        image_refs.append(weakref.ref(frame.image))
        raise RuntimeError("recording failure")

    session = SimulationSession("failed-producer", on_frame=fail_recording)
    try:
        session.start()
        session._producer.join(3)
        assert not session._producer.is_alive()
        assert session.error_code == "producer_failed"
        assert session.end_reason == "producer_failed"
        _assert_camera_images_released(session, image_refs)
    finally:
        session.close()


def _load_road_world():
    try:
        from visionflow.road_scene import RoadWorld
    except ModuleNotFoundError as exc:
        if exc.name == "visionflow.road_scene":
            pytest.fail("Task 1 RED: RoadWorld is missing from visionflow.road_scene")
        raise
    return RoadWorld


def _load_simulation_types():
    try:
        from visionflow.simulation import SimulationSession, SimulationStore
    except ModuleNotFoundError as exc:
        if exc.name == "visionflow.simulation":
            pytest.fail("Task 1 RED: SimulationSession and SimulationStore are missing")
        raise
    return SimulationSession, SimulationStore


def _event_motion(event: str, times: tuple[float, ...], seed: int = 17):
    RoadWorld = _load_road_world()
    baseline = RoadWorld(seed=seed)
    candidate = RoadWorld(seed=seed)
    candidate.inject(event)
    centers: list[float] = []
    areas: list[int] = []
    for source_time in times:
        plain = baseline.render(source_time).astype(np.int16)
        with_event = candidate.render(source_time).astype(np.int16)
        mask = np.max(np.abs(with_event - plain), axis=2) > 30
        ys, _xs = np.where(mask)
        assert ys.size > 20, f"{event} should add a visible object"
        centers.append(float(ys.mean()))
        areas.append(int(ys.size))
    return centers, areas


def test_road_world_renders_a_plausible_animated_road():
    RoadWorld = _load_road_world()
    world = RoadWorld(seed=7)

    first = world.render(0.0)
    later = world.render(0.2)

    assert first.shape == (540, 960, 3)
    assert first.dtype == np.uint8
    assert np.unique(first.reshape(-1, 3), axis=0).shape[0] > 30
    assert np.count_nonzero(np.any(first != later, axis=2)) > 500
    assert not np.array_equal(first[300, 300], first[20, 20])


def test_wrong_way_event_moves_against_both_normal_lane_directions():
    sample_times = (0.2, 0.4, 0.6, 0.8, 1.0)
    down_lane_centers, _down_areas = _event_motion(
        "wrong_way", sample_times, seed=17
    )
    up_lane_centers, _up_areas = _event_motion(
        "wrong_way", sample_times, seed=18
    )

    # The odd seed selects a car normally travelling down; even selects up.
    assert down_lane_centers[-1] < down_lane_centers[0] - 25
    assert up_lane_centers[-1] > up_lane_centers[0] + 25


def _covered_pixels(first, second) -> int:
    """Pixels where two drawn cars are both at least half opaque (exact sprite alpha)."""
    ax, ay, _premultiplied, alpha_a = first["tile"]
    bx, by, _premultiplied, alpha_b = second["tile"]
    size_a, size_b = alpha_a.shape[0], alpha_b.shape[0]
    x0, y0 = max(ax, bx), max(ay, by)
    x1, y1 = min(ax + size_a, bx + size_b), min(ay + size_a, by + size_b)
    if x0 >= x1 or y0 >= y1:
        return 0
    a = alpha_a[y0 - ay:y1 - ay, x0 - ax:x1 - ax, 0] > 0.5
    b = alpha_b[y0 - by:y1 - by, x0 - bx:x1 - bx, 0] > 0.5
    return int(np.count_nonzero(a & b))


@pytest.mark.parametrize("seed", (17, 18, 42, 101))
def test_traffic_in_a_lane_shares_one_speed_and_never_overlaps(seed):
    RoadWorld = _load_road_world()
    world = RoadWorld(seed=seed)
    speeds: dict[int, set[float]] = {}
    for car in world._traffic:
        speeds.setdefault(car.lane, set()).add(car.speed)
    assert all(len(values) == 1 for values in speeds.values()), speeds
    assert sum(1 for _ in world._traffic) >= 6
    # Pixel-exact over two traffic cycles: same lane and neighbouring lanes.
    for step in range(0, 600, 2):
        tiles = world._vehicle_tiles(step / 20.0)
        for index, first in enumerate(tiles):
            for second in tiles[index + 1:]:
                assert _covered_pixels(first, second) == 0, (step / 20.0, first["lane"], second["lane"])


@pytest.mark.parametrize("seed", (17, 18, 42, 101))
def test_wrong_way_driver_uses_a_free_lane_and_never_covers_traffic(seed):
    RoadWorld = _load_road_world()
    world = RoadWorld(seed=seed)
    world.render(3.0)
    world.inject("wrong_way")
    seen = 0
    for step in range(0, 200):
        t = 3.0 + step / 20.0
        tiles = world._vehicle_tiles(t)
        driver = [row for row in tiles if row["kind"] == "wrong_way"]
        traffic = [row for row in tiles if row["kind"] == "traffic"]
        if not driver:
            continue
        seen += 1
        assert all(row["lane"] != driver[0]["lane"] for row in traffic)
        assert not any(_covered_pixels(driver[0], row) for row in traffic), t
    assert seen >= 100  # at least five seconds in view


def _visible_track(event: str, seed: int, seconds: float):
    RoadWorld = _load_road_world()
    baseline = RoadWorld(seed=seed)
    candidate = RoadWorld(seed=seed)
    candidate.inject(event)
    track = []
    for step in range(int(seconds * 10)):
        t = step / 10.0
        mask = np.max(np.abs(candidate.render(t).astype(np.int16) - baseline.render(t).astype(np.int16)), axis=2) > 30
        ys, _xs = np.where(mask)
        if ys.size > 20:
            track.append((t, float(ys.mean())))
    return track


@pytest.mark.parametrize(("event", "seed", "starts", "min_seconds"), [
    ("wrong_way", 18, "top", 6.0),     # even seed: drives down from the horizon
    ("wrong_way", 17, "bottom", 6.0),  # odd seed: drives up from the bottom edge
    ("bird", 17, "bottom", 5.0),
])
def test_events_enter_at_the_start_of_their_direction_and_stay_in_view(event, seed, starts, min_seconds):
    track = _visible_track(event, seed, 10.0)
    assert track, f"{event} never became visible"
    first_y = track[0][1]
    if starts == "top":
        assert first_y < 540 * 0.25, track[:3]
    else:
        assert first_y > 540 * 0.85, track[:3]
    assert track[-1][0] - track[0][0] >= min_seconds, (track[0], track[-1])


@pytest.mark.parametrize("seed", (17, 42))
def test_bird_and_leaf_are_distinct_moving_direction_candidates(seed):
    sample_times = (0.2, 0.4, 0.6, 0.8, 1.0)
    bird_centers, bird_areas = _event_motion("bird", sample_times, seed=seed)
    leaf_centers, leaf_areas = _event_motion("leaf", sample_times, seed=seed)

    assert bird_centers[-1] < bird_centers[0] - 25
    assert leaf_centers[-1] > leaf_centers[0] + 25
    assert min(bird_areas) > 20
    assert min(leaf_areas) > 20


def test_unknown_event_is_rejected_before_the_scene_changes():
    RoadWorld = _load_road_world()
    expected = RoadWorld(seed=23)
    actual = RoadWorld(seed=23)
    expected.render(0.0)
    actual.render(0.0)

    with pytest.raises(ValueError):
        actual.inject("meteor")

    assert np.array_equal(actual.render(0.2), expected.render(0.2))


def test_road_helpers_return_only_unmodified_frame_fields():
    frames = road_frames("bird", count=4, seed=7)

    assert len(frames) == 4
    assert all(isinstance(frame, Frame) for frame in frames)
    assert all(set(vars_for_frame(frame)) == {"frame_id", "image", "timestamp_seconds"} for frame in frames)
    assert vars_for_frame(frames[0])["image"] is frames[0].image
    assert [frame.timestamp_seconds for frame in frames] == pytest.approx([0.0, 0.2, 0.4, 0.6])
    assert len({frame.frame_id.split(":")[0] for frame in frames}) == 1
    assert all(frame.image.shape == (540, 960, 3) for frame in frames)


def test_session_frames_share_one_source_identity_and_close_as_end_of_stream():
    SimulationSession, _SimulationStore = _load_simulation_types()
    session = SimulationSession(owner_id="alice", seed=7)
    reader = session.open_reader()
    try:
        session.start()
        before = reader.read()
        assert before is not None

        session.inject("wrong_way")
        after = reader.read()
        latest = session.latest_frame()

        assert after is not None
        assert latest is not None
        assert before.frame_id != after.frame_id
        assert before.image.shape == after.image.shape == latest.image.shape == (540, 960, 3)
        assert after.timestamp_seconds > before.timestamp_seconds
        assert all(frame.frame_id.startswith(f"{session.source_id}:") for frame in (before, after, latest))
        assert set(vars_for_frame(after)) == {"frame_id", "image", "timestamp_seconds"}
        assert not session.closed
    finally:
        session.close()

    assert session.closed
    assert session.error_code is None
    assert reader.read() is None


def test_on_frame_receives_each_generated_frame_once():
    SimulationSession, _SimulationStore = _load_simulation_types()
    delivered: list[Frame] = []
    enough_frames = threading.Event()

    def record(frame: Frame) -> None:
        delivered.append(frame)
        if len(delivered) >= 6:
            enough_frames.set()

    session = SimulationSession(owner_id="alice", seed=11, on_frame=record)
    try:
        session.start()
        assert enough_frames.wait(4), "producer did not deliver six frames"
    finally:
        session.close()

    frame_numbers = [int(frame.frame_id.rsplit(":", 1)[1]) for frame in delivered]
    assert frame_numbers == list(range(len(delivered)))
    assert len(set(frame.frame_id for frame in delivered)) == len(delivered)
    assert all(frame.frame_id.startswith(f"{session.source_id}:") for frame in delivered)


def test_raw_frame_backlog_drops_old_frames_at_ten_frame_limit():
    SimulationSession, _SimulationStore = _load_simulation_types()
    delivered_ids: list[str] = []
    enough_frames = threading.Event()

    def observe(frame: Frame) -> None:
        delivered_ids.append(frame.frame_id)
        if len(delivered_ids) >= 15:
            enough_frames.set()

    session = SimulationSession(owner_id="alice", seed=13, on_frame=observe)
    reader = session.open_reader()
    try:
        session.start()
        assert enough_frames.wait(4), "producer did not deliver fifteen frames"
        first = reader.read()
        assert first is not None
        oldest_kept = len(delivered_ids) - 10
        assert int(first.frame_id.rsplit(":", 1)[1]) >= oldest_kept
    finally:
        session.close()


def test_closing_one_reader_does_not_stop_the_shared_session():
    SimulationSession, _SimulationStore = _load_simulation_types()
    session = SimulationSession(owner_id="alice", seed=19)
    first_reader = session.open_reader()
    second_reader = session.open_reader()
    try:
        session.start()
        first = first_reader.read()
        assert first is not None
        first_reader.close()

        second = second_reader.read()
        latest = session.latest_frame()

        assert second is not None
        assert latest.frame_id.startswith(f"{session.source_id}:")
        assert second.frame_id == first.frame_id
    finally:
        session.close()


def test_session_close_unblocks_a_reader_waiting_for_its_first_frame():
    SimulationSession, _SimulationStore = _load_simulation_types()
    session = SimulationSession(owner_id="alice", seed=29)
    reader = session.open_reader()
    started_reading = threading.Event()
    returned: list[Frame | None] = []

    def wait_for_frame() -> None:
        started_reading.set()
        returned.append(reader.read())

    thread = threading.Thread(target=wait_for_frame)
    thread.start()
    try:
        assert started_reading.wait(1)
        time.sleep(0.05)
        assert thread.is_alive()

        session.close()
        thread.join(1)
        assert not thread.is_alive()
        assert returned == [None]
    finally:
        session.close()
        reader.close()


def test_store_enforces_owner_scope_and_reset_replaces_source_identity():
    SimulationSession, SimulationStore = _load_simulation_types()
    store = SimulationStore()
    original = store.create(owner_id="alice", seed=31)
    waiting_reader = original.open_reader()

    try:
        assert store.get("alice", original.source_id) is original
        with pytest.raises(KeyError):
            store.get("bob", original.source_id)

        replacement = store.reset("alice", original.source_id)

        assert replacement.source_id != original.source_id
        assert replacement.owner_id == "alice"
        assert waiting_reader.read() is None
        with pytest.raises(KeyError):
            store.get("alice", original.source_id)
        with pytest.raises(KeyError):
            store.get("bob", replacement.source_id)
    finally:
        store.close()
        waiting_reader.close()


def test_store_close_stops_all_session_readers():
    SimulationSession, SimulationStore = _load_simulation_types()
    store = SimulationStore()
    session = store.create(owner_id="alice", seed=37)
    reader = session.open_reader()

    session.start()
    assert session.latest_frame() is not None
    store.close()

    assert reader.read() is None


def test_store_create_and_reset_deliver_to_their_own_callbacks():
    SimulationSession, SimulationStore = _load_simulation_types()
    store = SimulationStore()
    created_frames: list[Frame] = []
    replacement_frames: list[Frame] = []
    created_ready = threading.Event()
    replacement_ready = threading.Event()

    def record_created(frame: Frame) -> None:
        created_frames.append(frame)
        created_ready.set()

    def record_replacement(frame: Frame) -> None:
        replacement_frames.append(frame)
        replacement_ready.set()

    try:
        original = store.create(
            owner_id="alice", seed=41, on_frame=record_created
        )
        original.start()
        assert created_ready.wait(3), "create callback received no generated frame"

        replacement = store.reset(
            "alice", original.source_id, on_frame=record_replacement
        )
        replacement.start()
        assert replacement_ready.wait(3), "reset callback received no generated frame"

        assert created_frames[0].frame_id.startswith(f"{original.source_id}:")
        assert replacement_frames[0].frame_id.startswith(f"{replacement.source_id}:")
    finally:
        store.close()


def test_reader_close_wakeups_preserve_frame_rate_and_render_timestamps(monkeypatch):
    RoadWorld = _load_road_world()
    original_render = RoadWorld.render
    render_times: list[float] = []

    def record_render_time(world, source_time: float):
        render_times.append(source_time)
        return original_render(world, source_time)

    monkeypatch.setattr(RoadWorld, "render", record_render_time)
    delivered: list[Frame] = []
    enough_frames = threading.Event()

    def record(frame: Frame) -> None:
        delivered.append(frame)
        if len(delivered) >= 5:
            enough_frames.set()

    SimulationSession, _SimulationStore = _load_simulation_types()
    session = SimulationSession(owner_id="alice", seed=43, on_frame=record)
    reader = session.open_reader()
    try:
        session.start()
        first = reader.read()
        assert first is not None
        reader.close()

        # Repeated cursor closes wake the producer during its scheduled wait.
        for _ in range(5):
            time.sleep(0.008)
            session.open_reader().close()

        assert enough_frames.wait(3), "producer did not deliver five frames"
    finally:
        session.close()
        reader.close()

    frame_times = [frame.timestamp_seconds for frame in delivered[:5]]
    assert len(render_times) >= len(frame_times)
    assert all(
        later - earlier >= 0.05 - 1e-6
        for earlier, later in zip(frame_times, frame_times[1:])
    )
    assert frame_times == pytest.approx(render_times[: len(frame_times)])
