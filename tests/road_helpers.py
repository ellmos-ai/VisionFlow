"""Helpers that expose road simulations only as ordinary image frames."""

from __future__ import annotations

from dataclasses import fields

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
