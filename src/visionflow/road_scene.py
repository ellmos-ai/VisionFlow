"""Deterministic pixel-only road scenes for the isolated road camera demo."""

from __future__ import annotations

import math
import random
from dataclasses import dataclass

import cv2
import numpy as np


_EVENT_NAMES = frozenset({"wrong_way", "bird", "leaf"})
_CAR_COLORS = (
    (72, 95, 186),
    (42, 140, 212),
    (185, 180, 145),
    (88, 132, 76),
    (222, 222, 216),
    (60, 61, 64),
    (148, 92, 61),
    (116, 110, 177),
)
_BIRD_COLORS = ((32, 35, 38), (48, 47, 43), (58, 59, 61))
_LEAF_COLORS = ((49, 113, 65), (42, 91, 48), (54, 112, 116))


@dataclass(frozen=True, slots=True)
class _TrafficCar:
    lane: int
    phase: float
    speed: float
    color: tuple[int, int, int]


@dataclass(frozen=True, slots=True)
class _SceneEvent:
    kind: str
    start_time: float
    color: tuple[int, int, int]
    lane: int | None = None


class RoadWorld:
    """Render an animated two-way road; scene state stays inside this object."""

    def __init__(self, seed: int = 42, width: int = 960, height: int = 540) -> None:
        if isinstance(seed, bool) or not isinstance(seed, int):
            raise ValueError("seed must be an integer")
        if isinstance(width, bool) or not isinstance(width, int) or width <= 0:
            raise ValueError("width must be a positive integer")
        if isinstance(height, bool) or not isinstance(height, int) or height <= 0:
            raise ValueError("height must be a positive integer")

        self.width = width
        self.height = height
        self._seed = seed
        self._rng = random.Random(seed)
        self._events: list[_SceneEvent] = []
        self._last_render_time = 0.0
        self._traffic = self._make_traffic()

    def inject(self, event: str) -> None:
        """Start one visible scenario event, rejecting unknown values first."""
        if event not in _EVENT_NAMES:
            raise ValueError(f"unknown road event: {event!r}")

        if event == "wrong_way":
            color = self._rng.choice(_CAR_COLORS)
        elif event == "bird":
            color = self._rng.choice(_BIRD_COLORS)
        else:
            color = self._rng.choice(_LEAF_COLORS)
        lane = (2 if self._seed % 2 else 0) if event == "wrong_way" else None
        self._events.append(
            _SceneEvent(
                kind=event,
                start_time=self._last_render_time,
                color=color,
                lane=lane,
            )
        )

    def render(self, source_time: float) -> np.ndarray:
        """Return one BGR image for the requested source time."""
        if (
            isinstance(source_time, bool)
            or not isinstance(source_time, (int, float))
            or not math.isfinite(source_time)
            or source_time < 0
        ):
            raise ValueError("source_time must be a finite non-negative number")

        timestamp = float(source_time)
        self._last_render_time = timestamp
        image = self._draw_background()
        self._draw_road(image)
        self._draw_traffic(image, timestamp)
        self._draw_events(image, timestamp)
        return image

    def _make_traffic(self) -> tuple[_TrafficCar, ...]:
        cars: list[_TrafficCar] = []
        phases = ((0, 0.24), (1, 0.68), (2, 0.31), (3, 0.73), (0, 0.77), (2, 0.82))
        for lane, phase in phases:
            cars.append(
                _TrafficCar(
                    lane=lane,
                    phase=phase,
                    speed=self._rng.uniform(0.09, 0.13),
                    color=self._rng.choice(_CAR_COLORS),
                )
            )
        return tuple(cars)

    def _draw_background(self) -> np.ndarray:
        image = np.empty((self.height, self.width, 3), dtype=np.uint8)
        horizon = self._horizon
        image[:horizon] = (206, 222, 232)
        image[horizon:] = (74, 113, 67)

        # Low distant tree lines make the road read as an outdoor scene.
        tree_y = min(self.height - 1, horizon + max(3, self.height // 70))
        tree_radius = max(2, min(self.width, self.height) // 55)
        for fraction, shade in (
            (0.10, (59, 96, 55)),
            (0.18, (66, 105, 59)),
            (0.82, (62, 101, 57)),
            (0.90, (55, 92, 53)),
        ):
            center = (int(self.width * fraction), tree_y)
            cv2.circle(image, center, tree_radius, shade, -1, cv2.LINE_AA)
        return image

    def _draw_road(self, image: np.ndarray) -> None:
        width = self.width
        height = self.height
        horizon = self._horizon
        road = np.asarray(
            (
                (int(width * 0.38), horizon),
                (int(width * 0.62), horizon),
                (int(width * 0.95), height - 1),
                (int(width * 0.05), height - 1),
            ),
            dtype=np.int32,
        )
        cv2.fillPoly(image, [road], (60, 64, 68), lineType=cv2.LINE_AA)

        shoulder = max(1, round(min(width, height) / 240))
        lane_width = max(1, round(min(width, height) / 300))
        self._perspective_line(image, 0.38, 0.05, (175, 177, 171), shoulder)
        self._perspective_line(image, 0.62, 0.95, (175, 177, 171), shoulder)

        # A narrow grassy median and low barrier separate the opposing flows.
        median = np.asarray(
            (
                (int(width * 0.49), horizon),
                (int(width * 0.51), horizon),
                (int(width * 0.54), height - 1),
                (int(width * 0.46), height - 1),
            ),
            dtype=np.int32,
        )
        cv2.fillPoly(image, [median], (72, 104, 63), lineType=cv2.LINE_AA)
        self._perspective_line(image, 0.50, 0.50, (150, 155, 151), lane_width + 1)

        for top_x, bottom_x in (
            (0.46, 0.3675),
            (0.54, 0.6325),
        ):
            self._draw_dashed_line(image, top_x, bottom_x)

        # Fine shoulder lines frame both carriageways without adding text cues.
        self._perspective_line(image, 0.39, 0.07, (221, 220, 205), lane_width)
        self._perspective_line(image, 0.61, 0.93, (221, 220, 205), lane_width)

    def _draw_dashed_line(self, image: np.ndarray, top_x: float, bottom_x: float) -> None:
        height = self.height
        width = self.width
        horizon = self._horizon
        dash = max(4, int(height * 0.105))
        gap = max(3, int(height * 0.075))
        thickness = max(1, round(min(width, height) / 360))
        y = horizon + int(height * 0.035)
        while y < height:
            end_y = min(height - 1, y + dash)
            start = self._point_at_depth(top_x, bottom_x, y)
            end = self._point_at_depth(top_x, bottom_x, end_y)
            cv2.line(image, start, end, (218, 218, 211), thickness, cv2.LINE_AA)
            y += dash + gap

    def _perspective_line(
        self,
        image: np.ndarray,
        top_x: float,
        bottom_x: float,
        color: tuple[int, int, int],
        thickness: int,
    ) -> None:
        start = (round(self.width * top_x), self._horizon)
        end = (round(self.width * bottom_x), self.height - 1)
        cv2.line(image, start, end, color, thickness, cv2.LINE_AA)

    def _point_at_depth(self, top_x: float, bottom_x: float, y: int) -> tuple[int, int]:
        fraction = self._depth(y)
        x = (top_x + (bottom_x - top_x) * fraction) * self.width
        return round(x), y

    @property
    def _horizon(self) -> int:
        return max(1, min(self.height - 1, round(self.height * 0.16)))

    def _depth(self, y: float) -> float:
        return min(
            1.0,
            max(0.0, (y - self._horizon) / max(1, self.height - 1 - self._horizon)),
        )

    def _lane_center(self, lane: int, y: float) -> float:
        lane_edges = (
            (0.445, 0.32),
            (0.475, 0.415),
            (0.525, 0.585),
            (0.555, 0.68),
        )
        top_x, bottom_x = lane_edges[lane]
        fraction = self._depth(y)
        return (top_x + (bottom_x - top_x) * fraction) * self.width

    def _draw_traffic(self, image: np.ndarray, source_time: float) -> None:
        horizon = self._horizon
        travel = max(1, self.height - 1 - horizon)
        for car in self._traffic:
            distance = (car.phase + source_time * car.speed) % 1.0
            if car.lane < 2:
                depth = 1.0 - distance
                direction = -1
            else:
                depth = distance
                direction = 1
            y = horizon + depth * travel
            x = self._lane_center(car.lane, y)
            self._draw_car(image, x, y, depth, car.color, direction)

    def _draw_events(self, image: np.ndarray, source_time: float) -> None:
        for event in self._events:
            elapsed = source_time - event.start_time
            if elapsed < 0:
                continue
            if event.kind == "wrong_way":
                lane = 2 if event.lane is None else event.lane
                if lane < 2:
                    direction = 1
                    y = self.height * 0.24 + elapsed * 82.0
                else:
                    direction = -1
                    y = self.height * 0.88 - elapsed * 82.0
                if -self.height * 0.15 <= y <= self.height * 1.1:
                    depth = self._depth(y)
                    x = self._lane_center(lane, y)
                    self._draw_car(image, x, y, depth, event.color, direction)
            elif event.kind == "bird":
                y = self.height * 0.79 - elapsed * 105.0
                if -self.height * 0.1 <= y <= self.height * 1.1:
                    depth = self._depth(y)
                    x = self._lane_center(3, y)
                    self._draw_bird(image, x, y, depth, elapsed, event.color)
            else:
                y = self._horizon + self.height * 0.065 + elapsed * 90.0
                if -self.height * 0.1 <= y <= self.height * 1.1:
                    depth = self._depth(y)
                    wind = math.sin(elapsed * 2.7) * self.width * 0.025
                    x = self._lane_center(0, y) + wind
                    self._draw_leaf(image, x, y, depth, elapsed, event.color)

    def _draw_car(
        self,
        image: np.ndarray,
        center_x: float,
        center_y: float,
        depth: float,
        color: tuple[int, int, int],
        direction: int,
    ) -> None:
        scale = min(self.width / 960.0, self.height / 540.0)
        size_scale = 0.52 + 0.75 * depth
        car_width = max(5, round((22 + 17 * depth) * scale))
        car_height = max(9, round((36 + 30 * depth) * scale * size_scale))
        cx = round(center_x)
        cy = round(center_y)
        half_width = car_width // 2
        half_height = car_height // 2
        left = cx - half_width
        right = cx + half_width
        top = cy - half_height
        bottom = cy + half_height
        corner = max(2, car_width // 5)
        body = np.asarray(
            (
                (left + corner, top),
                (right - corner, top),
                (right, top + corner),
                (right, bottom - corner),
                (right - corner, bottom),
                (left + corner, bottom),
                (left, bottom - corner),
                (left, top + corner),
            ),
            dtype=np.int32,
        )

        shadow_offset = max(1, round(scale * 2))
        cv2.ellipse(
            image,
            (cx + shadow_offset, cy + shadow_offset),
            (max(3, car_width // 2), max(4, car_height // 2)),
            0,
            0,
            360,
            (38, 39, 40),
            -1,
            cv2.LINE_AA,
        )
        cv2.fillPoly(image, [body], color, lineType=cv2.LINE_AA)
        cv2.polylines(image, [body], True, (31, 33, 36), max(1, round(scale)), cv2.LINE_AA)

        wheel_width = max(1, round(car_width * 0.12))
        wheel_height = max(2, round(car_height * 0.20))
        wheel_color = (25, 27, 29)
        for wheel_y in (
            cy - round(car_height * 0.29),
            cy + round(car_height * 0.29),
        ):
            cv2.rectangle(
                image,
                (left - wheel_width // 2, wheel_y - wheel_height // 2),
                (left + wheel_width // 2, wheel_y + wheel_height // 2),
                wheel_color,
                -1,
            )
            cv2.rectangle(
                image,
                (right - wheel_width // 2, wheel_y - wheel_height // 2),
                (right + wheel_width // 2, wheel_y + wheel_height // 2),
                wheel_color,
                -1,
            )

        glass = (48, 61, 66)
        glass_width = max(3, round(car_width * 0.62))
        windshield_height = max(3, round(car_height * 0.23))
        front_center = cy + direction * round(car_height * 0.23)
        rear_center = cy - direction * round(car_height * 0.24)
        cv2.rectangle(
            image,
            (cx - glass_width // 2, front_center - windshield_height // 2),
            (cx + glass_width // 2, front_center + windshield_height // 2),
            glass,
            -1,
            cv2.LINE_AA,
        )
        cv2.rectangle(
            image,
            (cx - glass_width // 2, rear_center - windshield_height // 2),
            (cx + glass_width // 2, rear_center + windshield_height // 2),
            (61, 71, 76),
            -1,
            cv2.LINE_AA,
        )

        light_width = max(1, round(car_width * 0.14))
        light_y = cy + direction * max(1, half_height - 1)
        tail_y = cy - direction * max(1, half_height - 1)
        for side in (-1, 1):
            light_x = cx + side * max(1, half_width - light_width)
            cv2.rectangle(
                image,
                (light_x, light_y - 1),
                (light_x + light_width, light_y + 1),
                (205, 220, 233),
                -1,
            )
            cv2.rectangle(
                image,
                (light_x, tail_y - 1),
                (light_x + light_width, tail_y + 1),
                (60, 61, 190),
                -1,
            )

    def _draw_bird(
        self,
        image: np.ndarray,
        center_x: float,
        center_y: float,
        depth: float,
        elapsed: float,
        color: tuple[int, int, int],
    ) -> None:
        scale = min(self.width / 960.0, self.height / 540.0)
        factor = 0.45 + 0.75 * depth
        cx = round(center_x)
        cy = round(center_y)
        body_x = max(3, round(6 * scale * factor))
        body_y = max(4, round(10 * scale * factor))
        wing_span = max(body_x + 3, round(15 * scale * factor))
        wing_lift = round(math.sin(elapsed * 11.0) * body_y * 0.8)

        cv2.ellipse(
            image,
            (cx, cy),
            (body_x, body_y),
            0,
            0,
            360,
            color,
            -1,
            cv2.LINE_AA,
        )
        wing_color = tuple(max(0, channel - 8) for channel in color)
        left_wing = np.asarray(
            (
                (cx - body_x // 2, cy),
                (cx - wing_span, cy - body_y // 2 - wing_lift),
                (cx - wing_span // 2, cy + body_y + wing_lift),
                (cx, cy + body_y // 2),
            ),
            dtype=np.int32,
        )
        right_wing = np.asarray(
            (
                (cx + body_x // 2, cy),
                (cx + wing_span, cy - body_y // 2 - wing_lift),
                (cx + wing_span // 2, cy + body_y + wing_lift),
                (cx, cy + body_y // 2),
            ),
            dtype=np.int32,
        )
        cv2.fillPoly(image, [left_wing, right_wing], wing_color, lineType=cv2.LINE_AA)

        beak = np.asarray(
            (
                (cx - max(2, body_x // 2), cy - body_y + 1),
                (cx + max(2, body_x // 2), cy - body_y + 1),
                (cx, cy - body_y - max(2, round(4 * scale))),
            ),
            dtype=np.int32,
        )
        cv2.fillPoly(image, [beak], (62, 111, 160), lineType=cv2.LINE_AA)
        cv2.circle(
            image,
            (cx + max(1, body_x // 2), cy - body_y // 2),
            max(1, round(scale)),
            (220, 224, 226),
            -1,
            cv2.LINE_AA,
        )

    def _draw_leaf(
        self,
        image: np.ndarray,
        center_x: float,
        center_y: float,
        depth: float,
        elapsed: float,
        color: tuple[int, int, int],
    ) -> None:
        scale = min(self.width / 960.0, self.height / 540.0)
        factor = 0.5 + 0.65 * depth
        cx = round(center_x)
        cy = round(center_y)
        axis_x = max(4, round(10 * scale * factor))
        axis_y = max(3, round(7 * scale * factor))
        angle = math.sin(elapsed * 2.7) * 42.0

        cv2.ellipse(
            image,
            (cx, cy),
            (axis_x, axis_y),
            angle,
            0,
            360,
            color,
            -1,
            cv2.LINE_AA,
        )
        cv2.ellipse(
            image,
            (cx, cy),
            (axis_x, axis_y),
            angle,
            0,
            360,
            (32, 69, 45),
            max(1, round(scale)),
            cv2.LINE_AA,
        )
        vein_dx = round(math.sin(math.radians(angle)) * axis_x * 0.8)
        vein_dy = round(math.cos(math.radians(angle)) * axis_y * 0.8)
        cv2.line(
            image,
            (cx - vein_dx, cy - vein_dy),
            (cx + vein_dx, cy + vein_dy),
            (139, 158, 83),
            max(1, round(scale)),
            cv2.LINE_AA,
        )
        cv2.line(
            image,
            (cx + axis_x - 1, cy + axis_y // 2),
            (cx + axis_x + max(2, round(4 * scale)), cy + axis_y + max(2, round(3 * scale))),
            (39, 73, 45),
            max(1, round(scale)),
            cv2.LINE_AA,
        )


__all__ = ["RoadWorld"]
