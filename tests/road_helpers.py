"""Helpers that expose road simulations only as ordinary image frames."""

from __future__ import annotations

from dataclasses import fields

import cv2
import numpy as np
import pytest

from visionflow.contracts import Frame


def vars_for_frame(frame: Frame) -> dict[str, object]:
    """Return the dataclass fields without copying or decorating the image."""
    return {field.name: getattr(frame, field.name) for field in fields(frame)}


def road_frames(
    event: str | None,
    count: int = 30,
    seed: int = 42,
) -> list[Frame]:
    """Render a short road sequence with no scene metadata in the returned frames."""
    if isinstance(count, bool) or not isinstance(count, int) or count < 0:
        raise ValueError("count must be a non-negative integer")

    try:
        from visionflow.road_scene import RoadWorld
    except ModuleNotFoundError as exc:
        if exc.name == "visionflow.road_scene":
            pytest.fail("Task 1 RED: RoadWorld is missing from visionflow.road_scene")
        raise

    world = RoadWorld(seed=seed)
    if event is not None:
        world.inject(event)

    source_id = f"road-frames-{seed}"
    frames: list[Frame] = []
    for index in range(count):
        timestamp = index / 5.0
        frames.append(
            Frame(
                frame_id=f"{source_id}:{index}",
                image=world.render(timestamp),
                timestamp_seconds=timestamp,
            )
        )
    return frames


def default_lane_params() -> dict[str, object]:
    """Return the two pixel-space road lanes and their ordinary travel vectors."""
    return {
        "lanes": [
            {
                "id": "northbound",
                "region": [0, 90, 480, 450],
                "expected": [0, -1],
            },
            {
                "id": "southbound",
                "region": [480, 90, 480, 450],
                "expected": [0, 1],
            },
        ],
        "min_frames": 3,
        "min_displacement_px": 4.0,
        "opposition_cosine": -0.75,
    }


def recolored_normal_frames(count: int = 30, seed: int = 42) -> list[Frame]:
    """Apply a deterministic color/brightness transform to normal road pixels."""
    frames = road_frames(None, count=count, seed=seed)
    recolored: list[Frame] = []
    for frame in frames:
        gray = cv2.cvtColor(frame.image, cv2.COLOR_BGR2GRAY)
        image = np.empty((*gray.shape, 3), dtype=np.uint8)
        image[:, :, 0] = (gray * 0.45).astype(np.uint8)
        image[:, :, 1] = (gray * 0.75 + 35).astype(np.uint8)
        image[:, :, 2] = (gray * 0.95 + 5).astype(np.uint8)
        recolored.append(
            Frame(
                frame_id=frame.frame_id,
                image=image,
                timestamp_seconds=frame.timestamp_seconds,
            )
        )
    return recolored


def reverse_pixel_frames(
    frames: list[Frame],
    source_id: str = "reversed-road",
) -> list[Frame]:
    """Reverse only image order and assign a fresh monotone frame timeline."""
    reversed_frames: list[Frame] = []
    for index, frame in enumerate(reversed(frames)):
        reversed_frames.append(
            Frame(
                frame_id=f"{source_id}:{index}",
                image=frame.image,
                timestamp_seconds=index / 5.0,
            )
        )
    return reversed_frames
