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
        self._static: np.ndarray | None = None

    def inject(self, event: str) -> None:
        """Start one visible scenario event, rejecting unknown values first."""
        if event not in _EVENT_NAMES:
            raise ValueError(f"unknown road event: {event!r}")

        color = self._rng.choice(_CAR_COLORS)
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
        # The static scenery is rendered once; every frame starts from a copy.
        if self._static is None:
            image = np.empty((self.height, self.width, 3), dtype=np.uint8)
            self._draw_scenery(image)
            self._draw_road_static(image)
            self._apply_vignette(image)
            self._static = image
        return self._static.copy()

    def _draw_scenery(self, image: np.ndarray) -> None:
        """Sky, hazy hills, grass and soft trees.

        Scenery is deliberately smooth and blurred: the direction detector picks
        the strongest corners per lane, so hard static corners would compete
        with the moving vehicles for its corner budget.
        """
        width, height, horizon = self.width, self.height, self._horizon
        sky_top = np.array((214, 168, 118), dtype=np.float32)
        sky_low = np.array((236, 228, 214), dtype=np.float32)
        ramp = np.linspace(0.0, 1.0, horizon, dtype=np.float32)[:, None]
        image[:horizon] = (sky_top + (sky_low - sky_top) * ramp)[:, None, :].astype(np.uint8)
        grass_far = np.array((104, 146, 108), dtype=np.float32)
        grass_near = np.array((58, 118, 66), dtype=np.float32)
        ramp = np.linspace(0.0, 1.0, height - horizon, dtype=np.float32)[:, None]
        image[horizon:] = (grass_far + (grass_near - grass_far) * ramp)[:, None, :].astype(np.uint8)

        layer = image.copy()
        hill_height = max(2, round(height * 0.05))
        xs = np.arange(0, width + 8, 8, dtype=np.float32)
        for amplitude, phase, shade in ((1.0, 0.0, (166, 168, 146)), (0.6, 1.9, (148, 156, 128))):
            ys = horizon - hill_height * amplitude * (0.55 + 0.45 * np.sin(xs / width * 7.0 + phase))
            hill = np.vstack(
                ([0, horizon + 1], np.column_stack((xs, ys)), [width, horizon + 1])
            ).astype(np.int32)
            cv2.fillPoly(layer, [hill], shade, lineType=cv2.LINE_AA)

        rng = random.Random(1009)
        for side in (-1, 1):
            for row in range(9):
                depth = (row + rng.uniform(0.1, 0.9)) / 9.0
                y = horizon + max(2, round(depth**1.6 * (height - horizon) * 0.95))
                road_edge = 0.5 + side * (0.12 + 0.33 * self._depth(y))
                x = (road_edge + side * (0.04 + 0.10 * depth + rng.uniform(0.0, 0.05))) * width
                radius = max(2, round((5 + 30 * depth) * min(width / 960.0, height / 540.0)))
                trunk = (44, 58, 72)
                cv2.rectangle(
                    layer,
                    (round(x - radius * 0.15), y - radius // 3),
                    (round(x + radius * 0.15), y + radius // 2),
                    trunk,
                    -1,
                )
                crown = (52 + rng.randint(0, 14), 98 + rng.randint(0, 22), 50 + rng.randint(0, 10))
                cv2.ellipse(layer, (round(x), y - radius), (radius, round(radius * 1.25)), 0, 0, 360, crown, -1, cv2.LINE_AA)
        blur = max(3, (round(min(width, height) / 90) // 2) * 2 + 1)
        image[:] = cv2.GaussianBlur(layer, (blur, blur), 0)

    def _apply_vignette(self, image: np.ndarray) -> None:
        # A gentle vignette gives the frame a camera-lens feel without edges.
        width, height = self.width, self.height
        yy, xx = np.mgrid[0:height, 0:width].astype(np.float32)
        dist = ((xx / width - 0.5) ** 2 + (yy / height - 0.5) ** 2) ** 0.5
        shade = 1.0 - 0.22 * np.clip((dist - 0.35) / 0.35, 0.0, 1.0)
        image[:] = (image.astype(np.float32) * shade[:, :, None]).astype(np.uint8)

    def _draw_road_static(self, image: np.ndarray) -> None:
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
        # Asphalt fades into haze towards the horizon (smooth, corner-free).
        mask = np.zeros((height, width), dtype=np.uint8)
        cv2.fillPoly(mask, [road], 255, lineType=cv2.LINE_AA)
        far = np.array((104, 106, 108), dtype=np.float32)
        near = np.array((58, 61, 65), dtype=np.float32)
        ramp = np.zeros((height, 1, 1), dtype=np.float32)
        ramp[horizon:, 0, 0] = np.linspace(0.0, 1.0, height - horizon, dtype=np.float32) ** 0.6
        asphalt = far + (near - far) * ramp
        alpha = (mask.astype(np.float32) / 255.0)[:, :, None]
        image[:] = (image * (1.0 - alpha) + asphalt * alpha).astype(np.uint8)

        shoulder = max(1, round(min(width, height) / 240))
        lane_width = max(1, round(min(width, height) / 300))
        # Guardrails just outside both shoulders: a lit rail over a soft shadow.
        for top_x, bottom_x in ((0.372, 0.03), (0.628, 0.97)):
            self._perspective_line(image, top_x, bottom_x, (70, 76, 74), shoulder + 2)
            self._perspective_line(image, top_x, bottom_x, (196, 200, 198), shoulder)
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
                    y = self.height * 0.43 + elapsed * 82.0
                else:
                    direction = -1
                    y = self.height * 0.73 - elapsed * 82.0
                if -self.height * 0.15 <= y <= self.height * 1.1:
                    depth = self._depth(y)
                    x = self._lane_center(lane, y)
                    self._draw_car(image, x, y, depth, event.color, direction)
            elif event.kind == "bird":
                y = self.height * 0.65 - elapsed * 105.0
                if -self.height * 0.1 <= y <= self.height * 1.1:
                    depth = self._depth(y)
                    x = self._lane_center(3, y)
                    self._draw_bird(image, x, y, depth, elapsed, event.color)
            else:
                y = self._horizon + self.height * 0.025 + elapsed * 90.0
                if -self.height * 0.1 <= y <= self.height * 1.1:
                    depth = self._depth(y)
                    wind = math.sin(elapsed * 2.7) * self.width * 0.01
                    x = self._lane_center(1, y) + wind
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
        # Darker flanks with a lighter centre panel read as a curved body.
        flank = tuple(round(channel * 0.68) for channel in color)
        cv2.fillPoly(image, [body], flank, lineType=cv2.LINE_AA)
        inset = max(1, round(car_width * 0.16))
        cv2.rectangle(
            image,
            (left + inset, top + corner // 2),
            (right - inset, bottom - corner // 2),
            color,
            -1,
            cv2.LINE_AA,
        )
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

        # Roof panel between the windows, a little brighter than the paint.
        roof = tuple(min(255, round(channel * 1.18 + 12)) for channel in color)
        roof_half = max(1, round(car_height * 0.11))
        roof_center = cy + direction * round(car_height * 0.02)
        cv2.rectangle(
            image,
            (cx - glass_width // 2 + 1, roof_center - roof_half),
            (cx + glass_width // 2 - 1, roof_center + roof_half),
            roof,
            -1,
            cv2.LINE_AA,
        )
        # Side mirrors at the front pillars.
        mirror_y = front_center - direction * max(1, windshield_height // 2)
        mirror = max(1, round(car_width * 0.10))
        for side in (-1, 1):
            mx = cx + side * (half_width + mirror // 2)
            cv2.rectangle(image, (mx - mirror // 2, mirror_y - 1), (mx + mirror // 2, mirror_y + 1), flank, -1)

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
        factor = 0.7 + 0.9 * depth
        cx = round(center_x)
        cy = round(center_y)
        body_x = max(4, round(8 * scale * factor))
        body_y = max(5, round(12 * scale * factor))
        wing_span = max(body_x + 4, round(19 * scale * factor))
        wing_lift = round(math.sin(elapsed * 11.0) * body_y * 0.35)

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
        factor = 0.8 + 0.8 * depth
        cx = round(center_x)
        cy = round(center_y)
        axis_x = max(6, round(12 * scale * factor))
        axis_y = max(5, round(9 * scale * factor))
        angle = math.sin(elapsed * 2.7) * 18.0

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
