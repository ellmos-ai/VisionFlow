from __future__ import annotations

import cv2
import numpy as np
import pytest

from visionflow.contracts import MediaError


def test_opencv_major_version_is_five():
    assert cv2.__version__.split(".")[0] == "5"


def test_sharp_pattern_scores_above_its_blurred_version():
    from visionflow.tools.quality import analyze_quality

    rows, columns = np.indices((64, 64))
    pattern = (((rows // 4 + columns // 4) % 2) * 255).astype(np.uint8)
    blurred = cv2.GaussianBlur(pattern, (9, 9), 2.0)

    sharp_result = analyze_quality(pattern)
    blurred_result = analyze_quality(blurred)

    assert sharp_result["sharpness_variance"] > blurred_result["sharpness_variance"]


def test_quality_reports_sharpness_and_grayscale_brightness():
    from visionflow.tools.quality import analyze_quality

    image = np.full((8, 12, 3), 100, dtype=np.uint8)

    result = analyze_quality(image)

    assert set(result) >= {"sharpness_variance", "brightness_mean"}
    assert result["brightness_mean"] == pytest.approx(100.0)
    assert result["sharpness_variance"] == pytest.approx(0.0)


@pytest.mark.parametrize(
    "image",
    [
        np.empty((0, 4), dtype=np.uint8),
        np.zeros((4, 4, 2), dtype=np.uint8),
        np.zeros((4, 4), dtype=np.float32),
    ],
)
def test_quality_rejects_invalid_image_shapes_or_types(image):
    from visionflow.tools.quality import analyze_quality

    with pytest.raises(MediaError):
        analyze_quality(image)


def test_motion_warmup_and_identical_frames_report_no_motion():
    from visionflow.tools.motion import MotionAnalyzer

    frame = np.zeros((32, 32), dtype=np.uint8)
    analyzer = MotionAnalyzer()

    warmup = analyzer.update(frame)
    repeated = analyzer.update(frame)

    assert warmup["motion_pixels"] == 0
    assert warmup["regions"] == []
    assert repeated["motion_pixels"] == 0
    assert repeated["regions"] == []


def test_motion_reports_changed_pixels_and_bounding_regions():
    from visionflow.tools.motion import MotionAnalyzer

    baseline = np.zeros((32, 32), dtype=np.uint8)
    changed = baseline.copy()
    changed[10:20, 12:25] = 255
    analyzer = MotionAnalyzer()
    analyzer.update(baseline)

    result = analyzer.update(changed)

    assert result["motion_pixels"] == 130
    assert result["regions"] == [{"x": 12, "y": 10, "width": 13, "height": 10}]


def test_motion_analyzers_keep_warmup_state_independent():
    from visionflow.tools.motion import MotionAnalyzer

    baseline = np.zeros((16, 16), dtype=np.uint8)
    changed = baseline.copy()
    changed[4:8, 5:9] = 255
    first = MotionAnalyzer()
    second = MotionAnalyzer()

    first.update(baseline)
    assert first.update(changed)["motion_pixels"] == 16
    assert second.update(changed)["motion_pixels"] == 0


@pytest.mark.parametrize(
    "next_frame",
    [
        np.zeros((8, 9), dtype=np.uint8),
        np.zeros((8, 8), dtype=np.float32),
    ],
)
def test_motion_rejects_frame_shape_or_type_changes(next_frame):
    from visionflow.tools.motion import MotionAnalyzer

    analyzer = MotionAnalyzer()
    analyzer.update(np.zeros((8, 8), dtype=np.uint8))

    with pytest.raises(MediaError):
        analyzer.update(next_frame)
