"""Pixel-only traffic direction analysis using bounded feature-sampled optical flow."""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

import cv2
import numpy as np

from visionflow.contracts import Frame, MediaError
from visionflow.media import _validate_image_array


_DEFAULT_MIN_FRAMES = 3
_DEFAULT_MIN_DISPLACEMENT = 4.0
_DEFAULT_OPPOSITION_COSINE = -0.75
_FEATURE_QUALITY_LEVEL = 0.01
_FEATURE_BLOCK_SIZE = 5
_MAX_CORNERS = 240
_MAX_FLOW_DIMENSION = 192
_FLOW_PYR_SCALE = 0.5
_FLOW_LEVELS = 3
_FLOW_WINDOW_SIZE = 9
_FLOW_ITERATIONS = 3
_FLOW_POLY_N = 5
_FLOW_POLY_SIGMA = 1.2
_MAX_TRACK_HISTORY = 12
_MAX_TRACK_MISSED_FRAMES = 2
_MAX_TRACK_GAP_SECONDS = 0.75
_MAX_TRACK_GAP_DISTANCE_PX = 80.0
_TRACK_GAP_ALIGNMENT_COSINE = 0.5
_TRACK_GAP_ALIGNMENT_MIN_DISTANCE_PX = 8.0


class DirectionAnalyzer:
    """Measure coherent feature flow against caller-configured lane vectors."""

    def __init__(self) -> None:
        self._previous_gray: np.ndarray | None = None
        self._previous_frame_id: str | None = None
        self._previous_timestamp: float | None = None
        self._active: list[dict[str, Any]] = []
        self._next_track_number = 1

    def reset(self) -> None:
        """Forget temporal pixels and tracks before a new source or crop."""
        self._previous_gray = None
        self._previous_frame_id = None
        self._previous_timestamp = None
        self._active = []

    def update(
        self,
        frame: Frame,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        """Return current pixel-flow candidates and confirmed opposing tracks."""
        if not isinstance(frame, Frame):
            raise TypeError("direction analysis requires a Frame")
        if not isinstance(params, dict):
            raise ValueError("direction parameters must be a dictionary")
        if not isinstance(frame.frame_id, str) or not frame.frame_id:
            raise ValueError("frame_id must be a non-empty string")
        if (
            isinstance(frame.timestamp_seconds, bool)
            or not isinstance(frame.timestamp_seconds, (int, float))
            or not math.isfinite(frame.timestamp_seconds)
            or frame.timestamp_seconds < 0
        ):
            raise ValueError("frame timestamp must be a finite non-negative number")

        try:
            width, height = _validate_image_array(frame.image, label="direction frame")
        except MediaError as exc:
            raise ValueError(str(exc)) from exc
        try:
            if frame.image.ndim == 2:
                gray = frame.image.copy()
            elif frame.image.shape[2] == 4:
                gray = cv2.cvtColor(frame.image, cv2.COLOR_BGRA2GRAY)
            else:
                gray = cv2.cvtColor(frame.image, cv2.COLOR_BGR2GRAY)
        except cv2.error as exc:
            raise ValueError("cannot convert direction frame to grayscale") from exc

        lanes, min_frames, min_displacement, opposition_cosine = _validate_params(
            params, width=width, height=height
        )
        self._validate_timeline(frame)
        if self._previous_gray is not None and self._previous_gray.shape != gray.shape:
            raise ValueError("frame shape changed during direction analysis")

        warmup = self._previous_gray is None
        candidates: list[dict[str, Any]] = []
        if not warmup:
            assert self._previous_gray is not None
            for lane in lanes:
                candidates.extend(
                    self._lane_candidates(
                        self._previous_gray,
                        gray,
                        lane,
                        min_displacement=min_displacement,
                        opposition_cosine=opposition_cosine,
                    )
                )

        self._active = self._advance_tracks(
            candidates,
            frame.frame_id,
            min_frames=min_frames,
            timestamp_seconds=float(frame.timestamp_seconds),
        )
        self._previous_gray = gray
        self._previous_frame_id = frame.frame_id
        self._previous_timestamp = float(frame.timestamp_seconds)

        observed_tracks = [
            track for track in self._active if track.get("_missed_frames", 0) == 0
        ]
        tracks = [
            {
                "track_id": track["track_id"],
                "lane_id": track["lane_id"],
                "region": list(track["region"]),
                "dx_px": float(track["dx_px"]),
                "dy_px": float(track["dy_px"]),
                "opposition": bool(track["opposition"]),
                "frame_ids": list(track["frame_ids"]),
            }
            for track in observed_tracks
        ]
        opposition_count = sum(
            track["opposition"] and len(track["frame_ids"]) >= min_frames
            for track in observed_tracks
        )
        return {
            "opposition_count": int(opposition_count),
            "warmup": bool(warmup),
            "tracks": tracks,
            "coordinate_system": "crop_pixels",
        }

    def _validate_timeline(self, frame: Frame) -> None:
        if self._previous_frame_id is None:
            return
        if frame.timestamp_seconds <= self._previous_timestamp:
            raise ValueError("frame timestamps must advance monotonically")
        if frame.frame_id == self._previous_frame_id:
            raise ValueError("consecutive frame IDs must be distinct")

    def _lane_candidates(
        self,
        previous_gray: np.ndarray,
        current_gray: np.ndarray,
        lane: dict[str, Any],
        *,
        min_displacement: float,
        opposition_cosine: float,
    ) -> list[dict[str, Any]]:
        x, y, width, height = lane["region"]
        previous = np.ascontiguousarray(previous_gray[y : y + height, x : x + width])
        current = np.ascontiguousarray(current_gray[y : y + height, x : x + width])
        try:
            corners = cv2.goodFeaturesToTrack(
                previous,
                maxCorners=_MAX_CORNERS,
                qualityLevel=_FEATURE_QUALITY_LEVEL,
                minDistance=4,
                blockSize=_FEATURE_BLOCK_SIZE,
                useHarrisDetector=False,
            )
            if corners is None or len(corners) < 2:
                return []
            flow_width, flow_height, scale_x, scale_y = _flow_resolution(width, height)
            if scale_x == 1.0 and scale_y == 1.0:
                previous_flow_input = previous
                current_flow_input = current
            else:
                previous_flow_input = cv2.resize(
                    previous,
                    (flow_width, flow_height),
                    interpolation=cv2.INTER_AREA,
                )
                current_flow_input = cv2.resize(
                    current,
                    (flow_width, flow_height),
                    interpolation=cv2.INTER_AREA,
                )
            forward_flow = cv2.calcOpticalFlowFarneback(
                previous_flow_input,
                current_flow_input,
                None,
                _FLOW_PYR_SCALE,
                _FLOW_LEVELS,
                _FLOW_WINDOW_SIZE,
                _FLOW_ITERATIONS,
                _FLOW_POLY_N,
                _FLOW_POLY_SIGMA,
                0,
            )
            start = corners.reshape(-1, 2)
            start_flow = _source_to_flow_coordinates(start, scale_x, scale_y)
            start_in_bounds = (
                _points_inside_flow_field(start, previous)
                & _points_inside_flow_field(start_flow, forward_flow)
            )
            displacement = np.full_like(start, np.nan)
            displacement_flow = np.full_like(start, np.nan)
            displacement_flow[start_in_bounds] = _sample_flow(
                forward_flow,
                start_flow[start_in_bounds],
            )
            displacement[start_in_bounds] = _flow_vectors_to_source(
                displacement_flow[start_in_bounds],
                scale_x,
                scale_y,
            )
            finish = start + displacement
            del forward_flow

            backward_flow = cv2.calcOpticalFlowFarneback(
                current_flow_input,
                previous_flow_input,
                None,
                _FLOW_PYR_SCALE,
                _FLOW_LEVELS,
                _FLOW_WINDOW_SIZE,
                _FLOW_ITERATIONS,
                _FLOW_POLY_N,
                _FLOW_POLY_SIGMA,
                0,
            )
            finish_flow = _source_to_flow_coordinates(finish, scale_x, scale_y)
            finish_in_bounds = (
                _points_inside_flow_field(finish, current)
                & _points_inside_flow_field(finish_flow, backward_flow)
            )
            valid_endpoints = start_in_bounds & finish_in_bounds
            backward_displacement_flow = np.full_like(start, np.nan)
            backward_displacement_flow[valid_endpoints] = _sample_flow(
                backward_flow,
                finish_flow[valid_endpoints],
            )
            backward_displacement = np.full_like(start, np.nan)
            backward_displacement[valid_endpoints] = _flow_vectors_to_source(
                backward_displacement_flow[valid_endpoints],
                scale_x,
                scale_y,
            )
            returned = finish + backward_displacement
            del backward_flow
        except cv2.error as exc:
            raise ValueError("OpenCV could not track direction features") from exc

        forward_backward_error = np.linalg.norm(start - returned, axis=1)
        magnitude = np.linalg.norm(displacement, axis=1)
        valid = (
            valid_endpoints
            & (forward_backward_error <= 1.5)
            & (magnitude >= 1.25)
            & np.isfinite(displacement).all(axis=1)
            & np.isfinite(returned).all(axis=1)
        )
        if not bool(valid.any()):
            return []

        finishes = finish[valid]
        vectors = displacement[valid]
        corner_strength = cv2.cornerMinEigenVal(
            current,
            blockSize=_FEATURE_BLOCK_SIZE,
            ksize=3,
        )
        strongest_current_corner = float(corner_strength.max())
        if strongest_current_corner <= 0.0:
            return []
        endpoint_strength = _sample_scalar_field(corner_strength, finishes)
        supported = (
            (endpoint_strength > 0.0)
            & (
                endpoint_strength
                >= strongest_current_corner * _FEATURE_QUALITY_LEVEL
            )
        )
        finishes = finishes[supported]
        vectors = vectors[supported]
        if len(finishes) < 2:
            return []

        clusters = _cluster_features(finishes, vectors)
        expected = np.asarray(lane["expected"], dtype=np.float64)
        expected /= np.linalg.norm(expected)
        output: list[dict[str, Any]] = []
        for members in clusters:
            if len(members) < 2:
                continue
            points = finishes[members]
            flow = np.median(vectors[members], axis=0)
            distance = float(np.linalg.norm(flow))
            if not math.isfinite(distance) or distance < min_displacement:
                continue
            cosine = float(np.dot(flow / distance, expected))
            opposition = cosine <= opposition_cosine
            left = max(0, int(math.floor(float(points[:, 0].min()))) + x)
            top = max(0, int(math.floor(float(points[:, 1].min()))) + y)
            right = min(x + width, int(math.ceil(float(points[:, 0].max()))) + x + 1)
            bottom = min(y + height, int(math.ceil(float(points[:, 1].max()))) + y + 1)
            if right <= left:
                right = min(x + width, left + 1)
            if bottom <= top:
                bottom = min(y + height, top + 1)
            output.append(
                {
                    "lane_id": lane["id"],
                    "center": (
                        float(np.median(points[:, 0])) + x,
                        float(np.median(points[:, 1])) + y,
                    ),
                    "region": (left, top, right - left, bottom - top),
                    "dx_px": float(flow[0]),
                    "dy_px": float(flow[1]),
                    "opposition": opposition,
                    "motion_norm": distance,
                }
            )
        return output

    def _advance_tracks(
        self,
        candidates: list[dict[str, Any]],
        frame_id: str,
        *,
        min_frames: int,
        timestamp_seconds: float,
    ) -> list[dict[str, Any]]:
        unmatched = set(range(len(self._active)))
        next_active: list[dict[str, Any]] = []
        for candidate in candidates:
            cx, cy = candidate["center"]
            possible: list[tuple[float, int]] = []
            for index in unmatched:
                old = self._active[index]
                if (
                    old["lane_id"] != candidate["lane_id"]
                    or old["opposition"] != candidate["opposition"]
                ):
                    continue
                ox, oy = old["center"]
                distance = math.hypot(cx - ox, cy - oy)
                missed_frames = old.get("_missed_frames", 0)
                elapsed = timestamp_seconds - old["_last_seen_timestamp"]
                if (
                    elapsed > _MAX_TRACK_GAP_SECONDS
                    or distance > _MAX_TRACK_GAP_DISTANCE_PX
                    or missed_frames > _MAX_TRACK_MISSED_FRAMES
                ):
                    continue
                threshold = max(38.0, candidate["motion_norm"] * 2.8)
                if missed_frames:
                    threshold = min(
                        threshold * (missed_frames + 1),
                        _MAX_TRACK_GAP_DISTANCE_PX,
                    )
                    if distance >= _TRACK_GAP_ALIGNMENT_MIN_DISTANCE_PX:
                        old_motion = math.hypot(old["dx_px"], old["dy_px"])
                        alignment = (
                            ((cx - ox) * old["dx_px"] + (cy - oy) * old["dy_px"])
                            / (distance * old_motion)
                            if old_motion > 0
                            else -1.0
                        )
                        if alignment < _TRACK_GAP_ALIGNMENT_COSINE:
                            continue
                if distance <= threshold:
                    possible.append((distance, index))
            if possible:
                _, matched_index = min(possible)
                unmatched.remove(matched_index)
                previous = self._active[matched_index]
                frame_ids = list(previous["frame_ids"])
                previous_frame_id = self._previous_frame_id
                if previous_frame_id is not None and previous_frame_id not in frame_ids:
                    frame_ids.append(previous_frame_id)
                if frame_id not in frame_ids:
                    frame_ids.append(frame_id)
                if len(frame_ids) > max(min_frames, _MAX_TRACK_HISTORY):
                    frame_ids = frame_ids[-max(min_frames, _MAX_TRACK_HISTORY) :]
                next_active.append(
                    {
                        **candidate,
                        "track_id": previous["track_id"],
                        "frame_ids": frame_ids,
                        "_missed_frames": 0,
                        "_last_seen_timestamp": timestamp_seconds,
                    }
                )
            else:
                track_id = f"direction-track-{self._next_track_number}"
                self._next_track_number += 1
                previous_frame_id = self._previous_frame_id
                frame_ids = (
                    [previous_frame_id, frame_id]
                    if previous_frame_id is not None
                    else [frame_id]
                )
                next_active.append(
                    {
                        **candidate,
                        "track_id": track_id,
                        "frame_ids": frame_ids,
                        "_missed_frames": 0,
                        "_last_seen_timestamp": timestamp_seconds,
                    }
                )
        for index in sorted(unmatched):
            previous = self._active[index]
            missed_frames = previous.get("_missed_frames", 0) + 1
            elapsed = timestamp_seconds - previous["_last_seen_timestamp"]
            if (
                missed_frames <= _MAX_TRACK_MISSED_FRAMES
                and elapsed <= _MAX_TRACK_GAP_SECONDS
            ):
                next_active.append(
                    {
                        **previous,
                        "_missed_frames": missed_frames,
                    }
                )
        return next_active


def direction_handler(
    frame: Frame,
    params: dict[str, Any],
    context: dict[str, Any],
) -> dict[str, Any]:
    """Analyze one frame using state isolated to the owning workflow run."""
    if not isinstance(frame, Frame):
        raise TypeError("direction handler requires a Frame")
    if not isinstance(params, dict):
        raise ValueError("direction parameters must be a dictionary")
    if not isinstance(context, dict):
        raise TypeError("tool context must be a dictionary")
    source_id = context.get("source_id")
    state = context.get("state")
    if not isinstance(source_id, str) or not source_id:
        raise ValueError("direction analysis requires context['source_id']")
    if not isinstance(state, dict):
        raise ValueError("direction analysis requires per-run context['state']")
    if "initial_frames" in params:
        initial_frames = params["initial_frames"]
        if (
            isinstance(initial_frames, bool)
            or not isinstance(initial_frames, int)
            or initial_frames <= 0
        ):
            raise ValueError("initial_frames must be a positive integer")

    roi = context.get("region")
    if roi is not None:
        roi = _validate_roi(roi, frame.image)
    roi_key = tuple(roi) if roi is not None else None
    analysis_params = {key: value for key, value in params.items() if key != "initial_frames"}
    if roi is not None:
        analysis_params = _map_lanes_to_roi(analysis_params, roi)

    analyzer = state.get("direction_analyzer")
    if (
        not isinstance(analyzer, DirectionAnalyzer)
        or state.get("direction_source_id") != source_id
        or state.get("direction_roi") != roi_key
    ):
        analyzer = DirectionAnalyzer()
        state["direction_analyzer"] = analyzer
        state["direction_source_id"] = source_id
        state["direction_roi"] = roi_key

    if roi is not None and not analysis_params.get("lanes"):
        analyzer.reset()
        return {
            "opposition_count": 0,
            "warmup": True,
            "tracks": [],
            "coordinate_system": "crop_pixels",
        }
    return analyzer.update(frame, analysis_params)


def _validate_params(
    params: dict[str, Any],
    *,
    width: int,
    height: int,
) -> tuple[list[dict[str, Any]], int, float, float]:
    allowed = {
        "lanes",
        "min_frames",
        "min_displacement_px",
        "opposition_cosine",
    }
    unknown = set(params) - allowed
    if unknown:
        raise ValueError(f"unknown direction parameters: {', '.join(sorted(unknown))}")
    raw_lanes = params.get("lanes")
    if not isinstance(raw_lanes, list) or not raw_lanes:
        raise ValueError("lanes must be a non-empty list")
    min_frames = params.get("min_frames", _DEFAULT_MIN_FRAMES)
    if isinstance(min_frames, bool) or not isinstance(min_frames, int) or min_frames < 2:
        raise ValueError("min_frames must be an integer of at least 2")
    min_displacement = _finite_number(
        params.get("min_displacement_px", _DEFAULT_MIN_DISPLACEMENT),
        "min_displacement_px",
    )
    if min_displacement <= 0:
        raise ValueError("min_displacement_px must be positive")
    opposition_cosine = _finite_number(
        params.get("opposition_cosine", _DEFAULT_OPPOSITION_COSINE),
        "opposition_cosine",
    )
    if not -1.0 <= opposition_cosine <= 1.0:
        raise ValueError("opposition_cosine must be between -1 and 1")

    lanes: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for raw_lane in raw_lanes:
        if not isinstance(raw_lane, Mapping):
            raise ValueError("each lane must be a mapping")
        lane_id = raw_lane.get("id")
        if not isinstance(lane_id, str) or not lane_id or lane_id in seen_ids:
            raise ValueError("lane IDs must be unique non-empty strings")
        region = _integer_vector(raw_lane.get("region"), 4, "lane region")
        x, y, lane_width, lane_height = region
        if (
            x < 0
            or y < 0
            or lane_width <= 0
            or lane_height <= 0
            or x + lane_width > width
            or y + lane_height > height
        ):
            raise ValueError("lane region must be a positive box inside the frame")
        expected = _numeric_vector(raw_lane.get("expected"), 2, "lane expected vector")
        if math.hypot(*expected) <= 0:
            raise ValueError("lane expected vector must not be zero")
        seen_ids.add(lane_id)
        lanes.append(
            {
                "id": lane_id,
                "region": region,
                "expected": expected,
            }
        )
    return lanes, min_frames, min_displacement, opposition_cosine


def _finite_number(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a finite number")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{name} must be a finite number")
    return number


def _numeric_vector(value: Any, length: int, name: str) -> tuple[float, ...]:
    if not isinstance(value, (list, tuple)) or len(value) != length:
        raise ValueError(f"{name} must contain {length} numbers")
    return tuple(_finite_number(item, name) for item in value)


def _integer_vector(value: Any, length: int, name: str) -> tuple[int, ...]:
    if not isinstance(value, (list, tuple)) or len(value) != length:
        raise ValueError(f"{name} must contain {length} integers")
    values: list[int] = []
    for item in value:
        if isinstance(item, bool) or not isinstance(item, int):
            raise ValueError(f"{name} must contain {length} integers")
        values.append(item)
    return tuple(values)


def _sample_flow(flow: np.ndarray, points: np.ndarray) -> np.ndarray:
    """Bilinearly sample the flow field at bounded (x, y) feature locations."""
    if points.size == 0:
        return np.empty((0, 2), dtype=flow.dtype)
    if not bool(_points_inside_flow_field(points, flow).all()):
        raise ValueError("dense flow coordinates must remain inside the lane crop")
    map_x = points[:, 0].astype(np.float32).reshape(-1, 1)
    map_y = points[:, 1].astype(np.float32).reshape(-1, 1)
    sampled_x = cv2.remap(
        flow[:, :, 0],
        map_x,
        map_y,
        cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=0,
    )
    sampled_y = cv2.remap(
        flow[:, :, 1],
        map_x,
        map_y,
        cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=0,
    )
    return np.column_stack((sampled_x.reshape(-1), sampled_y.reshape(-1)))


def _sample_scalar_field(field: np.ndarray, points: np.ndarray) -> np.ndarray:
    """Bilinearly sample a scalar pixel field at bounded (x, y) locations."""
    if points.size == 0:
        return np.empty((0,), dtype=field.dtype)
    if not bool(_points_inside_flow_field(points, field).all()):
        raise ValueError("pixel coordinates must remain inside the lane crop")
    map_x = points[:, 0].astype(np.float32).reshape(-1, 1)
    map_y = points[:, 1].astype(np.float32).reshape(-1, 1)
    sampled = cv2.remap(
        field,
        map_x,
        map_y,
        cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=0,
    )
    return sampled.reshape(-1)


def _flow_resolution(
    width: int,
    height: int,
) -> tuple[int, int, float, float]:
    scale = min(1.0, _MAX_FLOW_DIMENSION / max(width, height))
    flow_width = max(1, int(round(width * scale)))
    flow_height = max(1, int(round(height * scale)))
    return (
        flow_width,
        flow_height,
        flow_width / width,
        flow_height / height,
    )


def _source_to_flow_coordinates(
    points: np.ndarray,
    scale_x: float,
    scale_y: float,
) -> np.ndarray:
    """Map source pixel centers through the exact rounded resize scales."""
    scales = np.asarray([scale_x, scale_y], dtype=np.float32)
    return (points.astype(np.float32, copy=False) + 0.5) * scales - 0.5


def _flow_vectors_to_source(
    vectors: np.ndarray,
    scale_x: float,
    scale_y: float,
) -> np.ndarray:
    """Convert dense-flow displacement units back to source-crop pixels."""
    inverse_scales = np.asarray([1.0 / scale_x, 1.0 / scale_y], dtype=np.float32)
    return vectors * inverse_scales


def _points_inside_flow_field(points: np.ndarray, flow: np.ndarray) -> np.ndarray:
    height, width = flow.shape[:2]
    return (
        np.isfinite(points).all(axis=1)
        & (points[:, 0] >= 0.0)
        & (points[:, 0] <= width - 1)
        & (points[:, 1] >= 0.0)
        & (points[:, 1] <= height - 1)
    )


def _cluster_features(
    points: np.ndarray,
    vectors: np.ndarray,
) -> list[list[int]]:
    """Group nearby feature samples whose local flows move together."""
    remaining = set(range(len(points)))
    clusters: list[list[int]] = []
    while remaining:
        seed = remaining.pop()
        cluster = [seed]
        frontier = [seed]
        while frontier:
            current = frontier.pop()
            neighbors = [
                index
                for index in remaining
                if float(np.linalg.norm(points[index] - points[current])) <= 27.0
                and float(np.linalg.norm(vectors[index] - vectors[current])) <= 8.0
            ]
            for index in neighbors:
                remaining.remove(index)
                cluster.append(index)
                frontier.append(index)
        clusters.append(cluster)
    return clusters


def _validate_roi(roi: Any, image: np.ndarray) -> tuple[int, int, int, int]:
    values = _integer_vector(roi, 4, "context region")
    x, y, width, height = values
    if x < 0 or y < 0 or width <= 0 or height <= 0:
        raise ValueError("context region must be a positive source-pixel box")
    if image.shape[1] != width or image.shape[0] != height:
        raise ValueError("cropped frame dimensions must match context region")
    return values


def _map_lanes_to_roi(
    params: dict[str, Any],
    roi: tuple[int, int, int, int],
) -> dict[str, Any]:
    if "lanes" not in params or not isinstance(params["lanes"], list):
        return params
    roi_x, roi_y, roi_width, roi_height = roi
    roi_right = roi_x + roi_width
    roi_bottom = roi_y + roi_height
    mapped_lanes = []
    for lane in params["lanes"]:
        if not isinstance(lane, Mapping) or "region" not in lane:
            mapped_lanes.append(lane)
            continue
        x, y, width, height = _integer_vector(lane["region"], 4, "lane region")
        left = max(x, roi_x)
        top = max(y, roi_y)
        right = min(x + width, roi_right)
        bottom = min(y + height, roi_bottom)
        if right <= left or bottom <= top:
            continue
        mapped = dict(lane)
        mapped["region"] = [left - roi_x, top - roi_y, right - left, bottom - top]
        mapped_lanes.append(mapped)
    return {**params, "lanes": mapped_lanes}


__all__ = ["DirectionAnalyzer", "direction_handler"]
