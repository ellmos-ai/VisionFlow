"""Stateful frame-difference motion measurements."""

from __future__ import annotations

import cv2
import numpy as np

from visionflow.contracts import MediaError
from visionflow.media import _validate_image_array


DEFAULT_DIFFERENCE_THRESHOLD = 25


def _grayscale(frame: np.ndarray) -> np.ndarray:
    _validate_image_array(frame, label="frame")
    try:
        if frame.ndim == 2:
            return frame
        if frame.shape[2] == 3:
            return cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        return cv2.cvtColor(frame, cv2.COLOR_BGRA2GRAY)
    except cv2.error as exc:
        raise MediaError("cannot convert frame to grayscale") from exc


class MotionAnalyzer:
    """Compare consecutive frames; each instance owns an isolated warm-up state."""

    def __init__(self) -> None:
        self._previous_gray: np.ndarray | None = None
        self._previous_shape: tuple[int, ...] | None = None

    def update(self, frame: np.ndarray) -> dict[str, object]:
        gray = _grayscale(frame)
        shape = tuple(frame.shape)
        if self._previous_gray is None:
            self._previous_gray = gray.copy()
            self._previous_shape = shape
            return {"motion_pixels": 0, "regions": [], "warmup": True}
        if shape != self._previous_shape:
            raise MediaError("frame shape changed during motion analysis")

        try:
            difference = cv2.absdiff(self._previous_gray, gray)
            _, mask = cv2.threshold(
                difference,
                DEFAULT_DIFFERENCE_THRESHOLD,
                255,
                cv2.THRESH_BINARY,
            )
            motion_pixels = int(cv2.countNonZero(mask))
            contours, _ = cv2.findContours(
                mask.copy(),
                cv2.RETR_EXTERNAL,
                cv2.CHAIN_APPROX_SIMPLE,
            )
            regions = []
            for contour in contours:
                x, y, width, height = cv2.boundingRect(contour)
                regions.append(
                    {"x": int(x), "y": int(y), "width": int(width), "height": int(height)}
                )
            regions.sort(key=lambda region: (region["y"], region["x"]))
        except cv2.error as exc:
            raise MediaError("cannot calculate frame motion") from exc

        self._previous_gray = gray.copy()
        self._previous_shape = shape
        return {
            "motion_pixels": motion_pixels,
            "regions": regions,
            "warmup": False,
        }


__all__ = ["MotionAnalyzer"]
