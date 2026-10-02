from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
import pytest

from visionflow.contracts import Frame, InputConfig, MediaError


def _write_image(path: Path, image: np.ndarray) -> None:
    success, encoded = cv2.imencode(".png", image)
    assert success
    path.write_bytes(encoded.tobytes())


def _write_video(path: Path, values: tuple[int, ...] = (20, 80, 150, 230)) -> None:
    fourcc = cv2.VideoWriter_fourcc(*"MJPG")
    writer = cv2.VideoWriter(str(path), fourcc, 5.0, (32, 24))
    assert writer.isOpened(), "OpenCV test wheel must provide a working video writer"
    try:
        for value in values:
            image = np.full((24, 32, 3), value, dtype=np.uint8)
            writer.write(image)
    finally:
        writer.release()


def test_read_image_decodes_unicode_path(tmp_path):
    from visionflow.media import read_image

    path = tmp_path / "Prüfung.png"
    expected = np.zeros((12, 16, 3), dtype=np.uint8)
    expected[:, :8] = (30, 90, 180)
    _write_image(path, expected)

    actual = read_image(path)

    assert actual.shape == (12, 16, 3)
    assert np.array_equal(actual, expected)


@pytest.mark.parametrize("payload", [b"not an image", b"\x89PNG\r\n\x1a\n\x00"])
def test_read_image_rejects_corrupt_or_truncated_bytes(tmp_path, payload):
    from visionflow.media import read_image

    path = tmp_path / "broken.png"
    path.write_bytes(payload)

    with pytest.raises(MediaError):
        read_image(path)


def test_read_image_rejects_input_over_byte_limit(tmp_path, monkeypatch):
    from visionflow import media
    from visionflow.media import read_image

    monkeypatch.setattr(media, "MAX_IMAGE_BYTES", 8)
    path = tmp_path / "oversized.png"
    path.write_bytes(b"x" * 9)

    with pytest.raises(MediaError, match="size"):
        read_image(path)


def test_read_image_rejects_resolution_over_pixel_limit(tmp_path, monkeypatch):
    from visionflow import media
    from visionflow.media import read_image

    monkeypatch.setattr(media, "MAX_IMAGE_PIXELS", 100)
    path = tmp_path / "too-many-pixels.png"
    _write_image(path, np.zeros((11, 10), dtype=np.uint8))

    with pytest.raises(MediaError, match="resolution|pixels"):
        read_image(path)


@pytest.mark.parametrize("max_frames", [0, -1, True, 1.5, "2"])
def test_iter_video_requires_a_strictly_positive_integer_limit(tmp_path, max_frames):
    from visionflow.media import iter_video

    with pytest.raises(MediaError, match="max_frames"):
        list(iter_video(tmp_path / "never-opened.avi", max_frames))


def test_iter_video_reads_at_most_max_frames_from_unicode_path(tmp_path):
    from visionflow.media import iter_video

    path = tmp_path / "Bewegung Prüfung.avi"
    _write_video(path)

    frames = list(iter_video(path, max_frames=2))

    assert len(frames) == 2
    assert all(isinstance(frame, Frame) for frame in frames)
    assert len({frame.frame_id for frame in frames}) == 2
    assert all(frame.image.shape == (24, 32, 3) for frame in frames)
    assert all(frame.timestamp_seconds >= 0 for frame in frames)


def test_iter_video_rejects_corrupt_and_truncated_files(tmp_path):
    from visionflow.media import iter_video

    corrupt = tmp_path / "corrupt.avi"
    corrupt.write_bytes(b"not a video")
    valid = tmp_path / "valid.avi"
    _write_video(valid)
    truncated = tmp_path / "truncated.avi"
    truncated.write_bytes(valid.read_bytes()[:512])

    for path in (corrupt, truncated):
        with pytest.raises(MediaError):
            list(iter_video(path, max_frames=10))


@pytest.mark.parametrize("payload", [b"", b"x" * 65], ids=["empty", "oversized"])
def test_video_source_rejects_empty_or_oversized_file_before_capture(
    tmp_path, monkeypatch, payload
):
    from visionflow import media

    path = tmp_path / "bounded-video.avi"
    path.write_bytes(payload)
    monkeypatch.setattr(media, "MAX_VIDEO_BYTES", 64, raising=False)
    capture_calls = []

    def unexpected_capture(source):
        capture_calls.append(source)
        raise AssertionError("invalid video must be rejected before VideoCapture")

    monkeypatch.setattr(media.cv2, "VideoCapture", unexpected_capture)

    with pytest.raises(MediaError, match="empty|size"):
        media.VideoSource("bounded-video", path, max_frames=1)

    assert capture_calls == []


def test_iter_video_releases_capture_when_consumer_stops_early(tmp_path, monkeypatch):
    from visionflow import media

    path = tmp_path / "early-stop.avi"
    _write_video(path)
    real_video_capture = cv2.VideoCapture
    captures = []

    def track_capture(source):
        capture = real_video_capture(source)
        captures.append(capture)
        return capture

    monkeypatch.setattr(media.cv2, "VideoCapture", track_capture)
    frames = media.iter_video(path, max_frames=4)

    assert next(frames).image.shape == (24, 32, 3)
    frames.close()

    assert len(captures) == 1
    assert not captures[0].isOpened()


def test_open_source_image_returns_one_frame_with_configured_source_id(tmp_path):
    from visionflow.media import open_source

    path = tmp_path / "single.png"
    _write_image(path, np.zeros((8, 10, 3), dtype=np.uint8))
    config = InputConfig(kind="image", source=str(path))

    source = open_source(config, max_frames=4)
    try:
        frame = source.read()
        assert isinstance(frame, Frame)
        assert frame.frame_id.startswith(config.source)
        assert source.source_id == config.source
        assert source.read() is None
    finally:
        source.close()


def test_video_source_cursor_advances_and_close_is_terminal(tmp_path, monkeypatch):
    from visionflow import media

    path = tmp_path / "cursor.avi"
    _write_video(path)
    real_video_capture = cv2.VideoCapture
    captures = []

    def track_capture(source):
        capture = real_video_capture(source)
        captures.append(capture)
        return capture

    monkeypatch.setattr(media.cv2, "VideoCapture", track_capture)
    config = InputConfig(kind="video", source=str(path))
    source = media.open_source(config, max_frames=4)

    first = source.read()
    second = source.read()
    assert isinstance(first, Frame)
    assert isinstance(second, Frame)
    assert first.frame_id != second.frame_id

    source.close()

    assert not captures[0].isOpened()
    assert source.read() is None


def test_video_source_can_seek_forward_on_the_native_media_timeline(tmp_path):
    from visionflow.media import VideoSource

    path = tmp_path / "timeline-30fps.avi"
    fourcc = cv2.VideoWriter_fourcc(*"MJPG")
    writer = cv2.VideoWriter(str(path), fourcc, 30.0, (32, 24))
    assert writer.isOpened()
    try:
        for index in range(90):
            writer.write(np.full((24, 32, 3), index % 255, dtype=np.uint8))
    finally:
        writer.release()

    source = VideoSource("clip", path, max_frames=4)
    try:
        assert source.read().frame_id == "clip:0"
        assert source.read().frame_id == "clip:1"
        start = source.timeline_position_seconds
        assert start == pytest.approx(2 / 30, abs=0.01)
        assert source.observation_timeline_position_seconds is None

        first_sample = source.read_at(start)
        second_sample = source.read_at(start + 1.0)

        assert first_sample.frame_id == "clip:2"
        assert second_sample.frame_id == "clip:32"
        assert first_sample.timestamp_seconds == pytest.approx(2 / 30, abs=0.01)
        assert second_sample.timestamp_seconds - first_sample.timestamp_seconds == pytest.approx(1.0, abs=0.04)
        assert source.timeline_position_seconds == pytest.approx(33 / 30, abs=0.04)
        assert source.read() is None  # The limit counts four delivered frames, not skipped native frames.
    finally:
        source.close()


def test_video_read_at_reports_unsupported_frame_rate_as_media_error(tmp_path, monkeypatch):
    from visionflow import media

    path = tmp_path / "unsupported-fps.avi"
    path.write_bytes(b"placeholder")

    class CaptureWithoutFps:
        def isOpened(self):
            return True

        def get(self, property_id):
            if property_id == cv2.CAP_PROP_FPS:
                return 0.0
            if property_id == cv2.CAP_PROP_FRAME_COUNT:
                return 90.0
            return 0.0

        def set(self, property_id, value):
            return False

        def read(self):
            return False, None

        def release(self):
            pass

    monkeypatch.setattr(media.cv2, "VideoCapture", lambda _: CaptureWithoutFps())
    source = media.VideoSource("clip", path, max_frames=10)
    try:
        with pytest.raises(MediaError, match="frame rate|FPS|fps"):
            source.read_at(0.0)
    finally:
        source.close()


def test_video_read_at_rejects_backend_that_does_not_reach_requested_frame(tmp_path, monkeypatch):
    from visionflow import media

    path = tmp_path / "lying-seek.avi"
    path.write_bytes(b"placeholder")

    class CaptureThatIgnoresSeek:
        def isOpened(self):
            return True

        def get(self, property_id):
            if property_id == cv2.CAP_PROP_FPS:
                return 30.0
            if property_id == cv2.CAP_PROP_FRAME_COUNT:
                return 90.0
            if property_id == cv2.CAP_PROP_POS_FRAMES:
                return 0.0
            return 0.0

        def set(self, property_id, value):
            return True

        def read(self):
            return True, np.zeros((24, 32, 3), dtype=np.uint8)

        def release(self):
            pass

    monkeypatch.setattr(media.cv2, "VideoCapture", lambda _: CaptureThatIgnoresSeek())
    source = media.VideoSource("clip", path, max_frames=10)
    try:
        with pytest.raises(MediaError, match="seek|position|reach"):
            source.read_at(1.0)
    finally:
        source.close()


def test_video_read_at_rejects_missing_decoded_position_instead_of_forging_frame_id(
    tmp_path, monkeypatch
):
    from visionflow import media

    path = tmp_path / "missing-decoded-position.avi"
    path.write_bytes(b"placeholder")

    class CaptureLosingPositionAfterRead:
        position = 0.0

        def isOpened(self):
            return True

        def get(self, property_id):
            if property_id == cv2.CAP_PROP_FPS:
                return 30.0
            if property_id == cv2.CAP_PROP_FRAME_COUNT:
                return 90.0
            if property_id == cv2.CAP_PROP_POS_FRAMES:
                return self.position
            return 0.0

        def set(self, property_id, value):
            self.position = float(value)
            return True

        def read(self):
            self.position = 0.0
            return True, np.zeros((24, 32, 3), dtype=np.uint8)

        def release(self):
            pass

    monkeypatch.setattr(media.cv2, "VideoCapture", lambda _: CaptureLosingPositionAfterRead())
    source = media.VideoSource("clip", path, max_frames=10)
    try:
        with pytest.raises(MediaError, match="position|frame"):
            source.read_at(1.0)
    finally:
        source.close()


def test_video_read_at_decode_failure_inside_reported_duration_is_media_error(
    tmp_path, monkeypatch
):
    from visionflow import media

    path = tmp_path / "failed-decode.avi"
    path.write_bytes(b"placeholder")

    class CaptureWithDecodeFailure:
        position = 0.0

        def isOpened(self):
            return True

        def get(self, property_id):
            if property_id == cv2.CAP_PROP_FPS:
                return 30.0
            if property_id == cv2.CAP_PROP_FRAME_COUNT:
                return 90.0
            if property_id == cv2.CAP_PROP_POS_FRAMES:
                return self.position
            return 0.0

        def set(self, property_id, value):
            self.position = float(value)
            return True

        def read(self):
            return False, None

        def release(self):
            pass

    monkeypatch.setattr(media.cv2, "VideoCapture", lambda _: CaptureWithDecodeFailure())
    source = media.VideoSource("clip", path, max_frames=10)
    try:
        with pytest.raises(MediaError, match="decoder failed|reported frame"):
            source.read_at(1.0)
    finally:
        source.close()


def test_video_read_at_treats_targets_past_reported_duration_as_eof(tmp_path):
    from visionflow.media import VideoSource

    path = tmp_path / "timeline-eof.avi"
    _write_video(path, values=tuple(range(15)))
    source = VideoSource("clip", path, max_frames=10)
    try:
        assert source.read_at(3.1) is None
        assert source.read() is None
    finally:
        source.close()


def test_camera_source_requires_an_explicit_allowed_mapping(tmp_path, monkeypatch):
    from visionflow import media

    calls = []

    def unexpected_capture(source):
        calls.append(source)
        raise AssertionError("camera discovery/opening must not happen without permission")

    monkeypatch.setattr(media.cv2, "VideoCapture", unexpected_capture)

    with pytest.raises(MediaError, match="permitted|allowed|explicit"):
        media.open_source(
            InputConfig(kind="camera", source="camera-7"),
            max_frames=2,
        )

    assert calls == []


def test_camera_source_opens_only_the_explicit_allowed_device(monkeypatch):
    from visionflow import media

    calls = []

    class FakeCapture:
        def isOpened(self):
            return True

        def get(self, property_id):
            return 0.0

        def release(self):
            pass

    def capture_explicit_device(device_id):
        calls.append(device_id)
        return FakeCapture()

    monkeypatch.setattr(media.cv2, "VideoCapture", capture_explicit_device)
    source = media.open_source(
        InputConfig(kind="camera", source="camera-7"),
        max_frames=2,
        allowed_camera_sources={"camera-7": 7},
    )
    try:
        assert calls == [7]
        assert source.source_id == "camera-7"
    finally:
        source.close()


@pytest.mark.parametrize("device_id", [True, -1, "0", 1.0])
def test_camera_source_rejects_invalid_explicit_device_ids(monkeypatch, device_id):
    from visionflow import media

    calls = []

    def unexpected_capture(source):
        calls.append(source)
        raise AssertionError("invalid device ID must be rejected before opening")

    monkeypatch.setattr(media.cv2, "VideoCapture", unexpected_capture)

    with pytest.raises(MediaError):
        media.open_source(
            InputConfig(kind="camera", source="camera-7"),
            max_frames=2,
            allowed_camera_sources={"camera-7": device_id},
        )

    assert calls == []


def test_open_source_rejects_unknown_input_kind():
    from visionflow.media import open_source

    with pytest.raises(MediaError, match="unsupported"):
        open_source(InputConfig(kind="sound", source="input"), max_frames=1)
