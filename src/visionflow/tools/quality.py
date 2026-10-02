"""OpenCV quality measurements for bounded image arrays."""

from __future__ import annotations

import cv2
import numpy as np

from visionflow.contracts import MediaError
from visionflow.media import _validate_image_array


def _grayscale(image: np.ndarray) -> np.ndarray:
    _validate_image_array(image)
    try:
        if image.ndim == 2:
            return image
        if image.shape[2] == 3:
            return cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        return cv2.cvtColor(image, cv2.COLOR_BGRA2GRAY)
    except cv2.error as exc:
        raise MediaError("cannot convert image to grayscale") from exc


def analyze_quality(image: np.ndarray) -> dict[str, float]:
    """Return Laplacian sharpness variance and mean grayscale brightness."""
    gray = _grayscale(image)
    try:
        laplacian = cv2.Laplacian(gray, cv2.CV_64F)
    except cv2.error as exc:
        raise MediaError("cannot calculate image sharpness") from exc
    return {
        "sharpness_variance": float(np.var(laplacian, dtype=np.float64)),
        "brightness_mean": float(np.mean(gray, dtype=np.float64)),
    }


__all__ = ["analyze_quality"]
