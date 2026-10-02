"""Image, video, and explicitly permitted camera sources for VisionFlow."""

from __future__ import annotations

import math
import time
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Protocol

import cv2
import numpy as np

from visionflow.contracts import Frame, InputConfig, MediaError


MAX_IMAGE_BYTES = 20 * 1024 * 1024
MAX_IMAGE_PIXELS = 16_000_000
MAX_IMAGE_DIMENSION = 8_192
MAX_VIDEO_BYTES = 256 * 1024 * 1024


class FrameSource(Protocol):
    """A stateful source whose cursor can continue across workflow steps."""

    source_id: str

    def read(self) -> Frame | None:
        """Return the next frame, or None when this source is exhausted."""

    def close(self) -> None:
        """Release any source resources."""


def _validate_positive_frame_limit(max_frames: int) -> None:
    if isinstance(max_frames, bool) or not isinstance(max_frames, int) or max_frames <= 0:
        raise MediaError("max_frames must be a strictly positive integer")


def _validate_image_array(image: np.ndarray, *, label: str = "image") -> tuple[int, int]:
    if not isinstance(image, np.ndarray):
        raise MediaError(f"{label} must be a NumPy array")
    if image.dtype != np.uint8:
        raise MediaError(f"{label} must use uint8 pixels")
    if image.ndim == 2:
        height, width = image.shape
    elif image.ndim == 3 and image.shape[2] in (3, 4):
        height, width = image.shape[:2]
    else:
        raise MediaError(f"{label} must be grayscale, BGR, or BGRA")
    if width <= 0 or height <= 0:
        raise MediaError(f"{label} must not be empty")
    if width > MAX_IMAGE_DIMENSION or height > MAX_IMAGE_DIMENSION:
        raise MediaError(f"{label} resolution exceeds the dimension limit")
    if width * height > MAX_IMAGE_PIXELS:
        raise MediaError(f"{label} resolution exceeds the pixel limit")
    return width, height


def read_image(path: Path) -> np.ndarray:
    """Decode one bounded image, including paths containing non-ASCII characters."""
    try:
        image_path = Path(path)
        size = image_path.stat().st_size
    except (OSError, TypeError, ValueError) as exc:
        raise MediaError(f"cannot access image path: {path}") from exc

    if size <= 0:
        raise MediaError(f"image file is empty: {image_path}")
    if size > MAX_IMAGE_BYTES:
        raise MediaError(f"image file exceeds the size limit: {image_path}")

    try:
        encoded = image_path.read_bytes()
    except OSError as exc:
        raise MediaError(f"cannot read image: {image_path}") from exc
    if len(encoded) > MAX_IMAGE_BYTES:
        raise MediaError(f"image file exceeds the size limit: {image_path}")
    if not encoded:
        raise MediaError(f"image file is empty: {image_path}")

    try:
        decoded = cv2.imdecode(np.frombuffer(encoded, dtype=np.uint8), cv2.IMREAD_COLOR)
    except cv2.error as exc:
        raise MediaError(f"cannot decode image: {image_path}") from exc
    if decoded is None:
        raise MediaError(f"image is corrupt or unsupported: {image_path}")

    _validate_image_array(decoded, label=f"image {image_path}")
    return decoded


class ImageSource:
    """Read one image as one frame, then report end of input."""

    def __init__(self, source_id: str, path: Path) -> None:
        self.source_id = source_id
        self.path = Path(path)
        self._read = False
        self._closed = False

    def read(self) -> Frame | None:
        if self._closed or self._read:
            return None
        self._read = True
        image = read_image(self.path)
        return Frame(
            frame_id=f"{self.source_id}:0",
            image=image,
            timestamp_seconds=0.0,
        )

    def close(self) -> None:
        self._closed = True


class _CaptureSource:
    """Shared bounded cursor and cleanup logic for video and camera captures."""

    def __init__(
        self,
        source_id: str,
        capture: cv2.VideoCapture,
        max_frames: int,
        *,
        expected_frames: int | None,
        fail_on_end: bool,
    ) -> None:
        self.source_id = source_id
        self._capture = capture
        self._max_frames = max_frames
        self._expected_frames = expected_frames
        self._fail_on_end = fail_on_end
        self._frames_read = 0
        self._closed = False
        try:
            fps = float(capture.get(cv2.CAP_PROP_FPS))
        except cv2.error:
            fps = 0.0
        self._fps = fps if math.isfinite(fps) and fps > 0 else 0.0
        self._started_at = time.monotonic()

    def read(self) -> Frame | None:
        if self._closed:
            return None
        if self._frames_read >= self._max_frames:
            self.close()
            return None

        try:
            success, image = self._capture.read()
        except cv2.error as exc:
            self.close()
            raise MediaError(f"failed to decode frame from source {self.source_id}") from exc

        if not success or image is None:
            unexpected_end = (
                self._fail_on_end
                or self._frames_read == 0
                or (
                    self._expected_frames is not None
                    and self._frames_read < self._expected_frames
                )
            )
            self.close()
            if unexpected_end:
                raise MediaError(f"source ended before a usable frame: {self.source_id}")
            return None

        try:
            _validate_image_array(image, label=f"frame from {self.source_id}")
        except MediaError as exc:
            self.close()
            raise MediaError(f"invalid frame from source {self.source_id}: {exc}") from exc

        frame_index = self._frames_read
        self._frames_read += 1
        try:
            position_ms = float(self._capture.get(cv2.CAP_PROP_POS_MSEC))
        except cv2.error:
            position_ms = 0.0
        if math.isfinite(position_ms) and position_ms > 0:
            timestamp = position_ms / 1000.0
        elif self._fps > 0:
            timestamp = frame_index / self._fps
        else:
            timestamp = time.monotonic() - self._started_at

        frame = Frame(
            frame_id=f"{self.source_id}:{frame_index}",
            image=image,
            timestamp_seconds=max(0.0, float(timestamp)),
        )
        if self._frames_read >= self._max_frames:
            self.close()
        return frame

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self._capture.release()
        except cv2.error:
            pass


class VideoSource(_CaptureSource):
    """Stream a video file with one persistent cursor and a total frame cap."""

    def __init__(self, source_id: str, path: Path, max_frames: int) -> None:
        _validate_positive_frame_limit(max_frames)
        video_path = Path(path)
        try:
            if not video_path.is_file():
                raise MediaError(f"video file does not exist: {video_path}")
            file_size = video_path.stat().st_size
        except OSError as exc:
            raise MediaError(f"cannot access video file: {video_path}") from exc
        if file_size <= 0:
            raise MediaError(f"video file is empty: {video_path}")
        if file_size > MAX_VIDEO_BYTES:
            raise MediaError(
                f"video file size exceeds the {MAX_VIDEO_BYTES}-byte limit: {video_path}"
            )

        try:
            capture = cv2.VideoCapture(str(video_path))
            if not capture.isOpened():
                capture.release()
                raise MediaError(f"cannot open video: {video_path}")
            reported_frames = float(capture.get(cv2.CAP_PROP_FRAME_COUNT))
        except cv2.error as exc:
            raise MediaError(f"cannot open video: {video_path}") from exc

        expected_frames = None
        if math.isfinite(reported_frames) and reported_frames > 0:
            expected_frames = int(round(reported_frames))
        super().__init__(
            source_id,
            capture,
            max_frames,
            expected_frames=expected_frames,
            fail_on_end=False,
        )


class CameraSource(_CaptureSource):
    """Open exactly one approved camera device ID; never enumerate devices."""

    def __init__(self, source_id: str, device_id: int, max_frames: int) -> None:
        _validate_positive_frame_limit(max_frames)
        if isinstance(device_id, bool) or not isinstance(device_id, int) or device_id < 0:
            raise MediaError("camera device ID must be an explicitly allowed non-negative integer")
        try:
            capture = cv2.VideoCapture(device_id)
            if not capture.isOpened():
                capture.release()
                raise MediaError(f"cannot open permitted camera device {device_id}")
        except cv2.error as exc:
            raise MediaError(f"cannot open permitted camera device {device_id}") from exc
        super().__init__(
            source_id,
            capture,
            max_frames,
            expected_frames=None,
            fail_on_end=True,
        )


def open_source(
    input_config: InputConfig,
    max_frames: int,
    *,
    allowed_camera_sources: Mapping[str, int] | None = None,
) -> FrameSource:
    """Create a source from workflow input; camera IDs require an explicit allowlist."""
    _validate_positive_frame_limit(max_frames)
    if not isinstance(input_config, InputConfig):
        raise MediaError("input_config must be an InputConfig")
    source_id = input_config.source
    if not isinstance(source_id, str) or not source_id.strip():
        raise MediaError("input source must be a non-empty string")

    if input_config.kind == "image":
        return ImageSource(source_id, Path(source_id))
    if input_config.kind == "video":
        return VideoSource(source_id, Path(source_id), max_frames)
    if input_config.kind == "camera":
        if (
            allowed_camera_sources is None
            or not isinstance(allowed_camera_sources, Mapping)
            or source_id not in allowed_camera_sources
        ):
            raise MediaError(f"camera source is not explicitly permitted: {source_id}")
        device_id = allowed_camera_sources[source_id]
        return CameraSource(source_id, device_id, max_frames)
    raise MediaError(f"unsupported input kind: {input_config.kind!r}")


def iter_video(path: Path, max_frames: int) -> Iterator[Frame]:
    """Yield at most max_frames; closing the iterator releases the video capture."""
    _validate_positive_frame_limit(max_frames)
    video_path = Path(path)
    source = VideoSource(str(video_path), video_path, max_frames)
    try:
        while True:
            frame = source.read()
            if frame is None:
                return
            yield frame
    finally:
        source.close()


__all__ = [
    "CameraSource",
    "FrameSource",
    "ImageSource",
    "VideoSource",
    "iter_video",
    "open_source",
    "read_image",
]
