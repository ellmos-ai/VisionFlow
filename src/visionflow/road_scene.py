"""Deterministic pixel-only road scenes for the isolated road camera demo."""

from __future__ import annotations

import functools
import math
import random
from dataclasses import dataclass

import cv2
import numpy as np


_EVENT_NAMES = frozenset({"wrong_way", "bird", "leaf"})
# Perspective: every road line runs from its bottom position towards one vanishing
# point; at the horizon a line sits at 0.5 + (bottom - 0.5) * _VANISH (road edges
# 0.05/0.95 at the bottom, 0.38/0.62 at the horizon).
_VANISH = 0.12 / 0.45
# Three equal lanes per carriageway (fractions of the width at the bottom edge):
# lanes 0-2 move away from the camera, lanes 3-5 come towards it.
_CARRIAGEWAY_LEFT = (0.07, 0.46)
_CARRIAGEWAY_RIGHT = (0.54, 0.93)
_LANE_WIDTH = (_CARRIAGEWAY_LEFT[1] - _CARRIAGEWAY_LEFT[0]) / 3
_LANE_CENTERS = tuple(
    [_CARRIAGEWAY_LEFT[0] + _LANE_WIDTH * (i + 0.5) for i in range(3)]
    + [_CARRIAGEWAY_RIGHT[0] + _LANE_WIDTH * (i + 0.5) for i in range(3)]
)
_AWAY_LANES = 3
# Regular traffic uses the two outer lanes of each carriageway; every lane moves at
# one shared speed, so cars keep their spacing and never drive into each other.
# The inner lanes next to the median stay free: the wrong-way driver uses one of
# them (lane 2 for even seeds, lane 3 for odd seeds) and is never drawn over a car.
_TRAFFIC_LANES = (0, 1, 4, 5)
_CARS_PER_LANE = 2
_WRONG_WAY_LANES = (2, 3)
# Event speeds in pixels per second at 540 px height (scaled with the scene). Slow
# enough that bird and leaf stay trackable even when a loaded host analyses only
# about two frames per second (measured, see docs/SYNTHETIC_EVALUATION.md).
_WRONG_WAY_SPEED = 70.0
_BIRD_SPEED = 75.0
_LEAF_SPEED = 65.0
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
        lane = _WRONG_WAY_LANES[self._seed % 2] if event == "wrong_way" else None
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
        for lane in _TRAFFIC_LANES:
            speed = self._rng.uniform(0.09, 0.13)
            offset = self._rng.uniform(0.0, 1.0)
            for index in range(_CARS_PER_LANE):
                cars.append(
                    _TrafficCar(
                        lane=lane,
                        phase=(offset + index / _CARS_PER_LANE) % 1.0,
                        speed=speed,
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
        for bottom_x in (0.03, 0.97):
            self._perspective_line(image, *_persp(bottom_x), (70, 76, 74), shoulder + 2)
            self._perspective_line(image, *_persp(bottom_x), (196, 200, 198), shoulder)
        for bottom_x in (0.05, 0.95):
            self._perspective_line(image, *_persp(bottom_x), (175, 177, 171), shoulder)

        # A narrow grassy median and low barrier separate the opposing flows.
        median = np.asarray(
            (
                (int(width * _persp(0.46)[0]), horizon),
                (int(width * _persp(0.54)[0]), horizon),
                (int(width * 0.54), height - 1),
                (int(width * 0.46), height - 1),
            ),
            dtype=np.int32,
        )
        cv2.fillPoly(image, [median], (72, 104, 63), lineType=cv2.LINE_AA)
        self._perspective_line(image, 0.50, 0.50, (150, 155, 151), lane_width + 1)

        # Dashed lines between the three lanes of each carriageway.
        for left, _right in (_CARRIAGEWAY_LEFT, _CARRIAGEWAY_RIGHT):
            for boundary in (1, 2):
                self._draw_dashed_line(image, *_persp(left + _LANE_WIDTH * boundary))

        # Solid edge lines frame both carriageways (shoulder and median side).
        for bottom_x in (*_CARRIAGEWAY_LEFT, *_CARRIAGEWAY_RIGHT):
            self._perspective_line(image, *_persp(bottom_x), (221, 220, 205), lane_width)

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
        top_x, bottom_x = _persp(_LANE_CENTERS[lane])
        fraction = self._depth(y)
        return (top_x + (bottom_x - top_x) * fraction) * self.width

    def _lane_width_px(self, depth: float) -> float:
        return _LANE_WIDTH * self.width * (_VANISH + (1.0 - _VANISH) * depth)

    def _traffic_positions(self, source_time: float):
        """Yield (car, x, y, depth, direction) for every regular car at *source_time*."""
        horizon = self._horizon
        travel = max(1, self.height - 1 - horizon)
        for car in self._traffic:
            distance = (car.phase + source_time * car.speed) % 1.0
            if car.lane < _AWAY_LANES:
                depth = 1.0 - distance
                direction = -1
            else:
                depth = distance
                direction = 1
            y = horizon + depth * travel
            yield car, self._lane_center(car.lane, y), y, depth, direction

    def _wrong_way_position(self, event: _SceneEvent, source_time: float):
        """Return (lane, x, y, depth, direction) while the driver is on the road, else None.

        The driver enters where its lane's traffic leaves: at the horizon when it
        drives down, at the bottom edge when it drives up, so it crosses the whole road.
        """
        elapsed = source_time - event.start_time
        if elapsed < 0:
            return None
        lane = _WRONG_WAY_LANES[1] if event.lane is None else event.lane
        travelled = elapsed * _WRONG_WAY_SPEED * self.height / 540.0
        if lane < _AWAY_LANES:
            direction = 1
            y = self._horizon + travelled
        else:
            direction = -1
            y = self.height * 1.02 - travelled
        if not self._horizon <= y <= self.height * 1.1:
            return None
        return lane, self._lane_center(lane, y), y, self._depth(y), direction

    def _vehicle_boxes(self, source_time: float) -> list[dict[str, object]]:
        """Axis-aligned image boxes of every drawn car (scene geometry for tests)."""
        rows: list[dict[str, object]] = []
        for car, x, y, depth, _direction in self._traffic_positions(source_time):
            rows.append({"kind": "traffic", "lane": car.lane, "box": self._car_box(x, y, depth, car.lane)})
        for event in self._events:
            if event.kind != "wrong_way":
                continue
            position = self._wrong_way_position(event, source_time)
            if position is not None:
                lane, x, y, depth, _direction = position
                rows.append({"kind": "wrong_way", "lane": lane, "box": self._car_box(x, y, depth, lane)})
        return rows

    def _car_size(self, depth: float) -> tuple[int, int]:
        # Cars fill a bit less than half a lane and grow with it towards the camera.
        car_width = max(5, round(self._lane_width_px(depth) * 0.44))
        return car_width, max(9, round(car_width * 1.8))

    def _lane_angle(self, lane: int) -> float:
        top_x, bottom_x = _persp(_LANE_CENTERS[lane])
        travel = max(1, self.height - 1 - self._horizon)
        return math.degrees(math.atan2((bottom_x - top_x) * self.width, travel))

    def _car_box(self, x: float, y: float, depth: float, lane: int) -> tuple[float, float, float, float]:
        car_width, car_height = self._car_size(depth)
        angle = math.radians(abs(self._lane_angle(lane)))
        half_w = (car_width * math.cos(angle) + car_height * math.sin(angle)) / 2
        half_h = (car_height * math.cos(angle) + car_width * math.sin(angle)) / 2
        return x - half_w, y - half_h, x + half_w, y + half_h

    def _draw_traffic(self, image: np.ndarray, source_time: float) -> None:
        for car, x, y, depth, direction in self._traffic_positions(source_time):
            self._draw_car(image, x, y, depth, car.color, direction, car.lane)

    def _draw_events(self, image: np.ndarray, source_time: float) -> None:
        # Event speeds were tuned at 540 px height; keep them resolution-independent.
        speed = self.height / 540.0
        for event in self._events:
            elapsed = source_time - event.start_time
            if elapsed < 0:
                continue
            if event.kind == "wrong_way":
                position = self._wrong_way_position(event, source_time)
                if position is not None:
                    lane, x, y, depth, direction = position
                    self._draw_car(image, x, y, depth, event.color, direction, lane)
            elif event.kind == "bird":
                # The bird enters at the bottom edge and flies up the whole frame.
                y = self.height * 1.02 - elapsed * _BIRD_SPEED * speed
                if -self.height * 0.1 <= y <= self.height * 1.1:
                    depth = self._depth(y)
                    x = self._lane_center(4, y)
                    self._draw_bird(image, x, y, depth, elapsed, event.color)
            else:
                y = self._horizon + self.height * 0.025 + elapsed * _LEAF_SPEED * speed
                if -self.height * 0.1 <= y <= self.height * 1.1:
                    depth = self._depth(y)
                    wind = math.sin(elapsed * 2.7) * self.width * 0.01
                    x = self._lane_center(2, y) + wind
                    self._draw_leaf(image, x, y, depth, elapsed, event.color)

    def _draw_car(
        self,
        image: np.ndarray,
        center_x: float,
        center_y: float,
        depth: float,
        color: tuple[int, int, int],
        direction: int,
        lane: int,
    ) -> None:
        """Draw an upright car on a small tile, rotate it onto the lane and blend it in.

        The tile is drawn twice (black and white background); their difference gives
        an exact anti-aliased alpha without a key colour.
        """
        scale = min(self.width / 960.0, self.height / 540.0)
        car_width, car_height = self._car_size(depth)
        angle = self._lane_angle(lane)
        premultiplied, alpha = _car_sprite(
            tuple(color), direction, car_width, car_height, round(angle, 2), scale
        )
        side = premultiplied.shape[0]
        half = side // 2
        x0, y0 = round(center_x) - half, round(center_y) - half
        ix0, iy0 = max(0, x0), max(0, y0)
        ix1, iy1 = min(image.shape[1], x0 + side), min(image.shape[0], y0 + side)
        if ix0 >= ix1 or iy0 >= iy1:
            return
        tx0, ty0 = ix0 - x0, iy0 - y0
        tx1, ty1 = tx0 + (ix1 - ix0), ty0 + (iy1 - iy0)
        region = image[iy0:iy1, ix0:ix1].astype(np.float32)
        a = alpha[ty0:ty1, tx0:tx1]
        blended = region * (1.0 - a) + premultiplied[ty0:ty1, tx0:tx1]
        image[iy0:iy1, ix0:ix1] = np.clip(blended, 0, 255).astype(np.uint8)

    @staticmethod
    def _draw_car_upright(
        image: np.ndarray,
        center_x: float,
        center_y: float,
        car_width: int,
        car_height: int,
        color: tuple[int, int, int],
        direction: int,
        scale: float,
    ) -> None:
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


@functools.lru_cache(maxsize=1024)
def _car_sprite(
    color: tuple[int, int, int],
    direction: int,
    car_width: int,
    car_height: int,
    angle: float,
    scale: float,
) -> tuple[np.ndarray, np.ndarray]:
    """Rotated, premultiplied car tile and its alpha; cached because a car keeps its
    lane, colour and direction and its size changes in whole pixels."""
    side = int(car_height * 1.5) + 8
    half = side // 2
    tiles = []
    for background in (0, 255):
        tile = np.full((side, side, 3), background, dtype=np.uint8)
        RoadWorld._draw_car_upright(tile, half, half, car_width, car_height, color, direction, scale)
        tiles.append(tile.astype(np.float32))
    black, white = tiles
    alpha = np.clip(1.0 - (white - black).mean(axis=2, keepdims=True) / 255.0, 0.0, 1.0)
    rotation = cv2.getRotationMatrix2D((half, half), angle, 1.0)
    # Drawn over black, so the black tile is already colour times alpha.
    premultiplied = cv2.warpAffine(black, rotation, (side, side), flags=cv2.INTER_LINEAR)
    alpha = cv2.warpAffine(alpha, rotation, (side, side), flags=cv2.INTER_LINEAR)[:, :, None]
    premultiplied.flags.writeable = False
    alpha.flags.writeable = False
    return premultiplied, alpha


def _persp(bottom_x: float) -> tuple[float, float]:
    """Return (horizon_x, bottom_x) of a road line aimed at the vanishing point."""
    return 0.5 + (bottom_x - 0.5) * _VANISH, bottom_x


__all__ = ["RoadWorld"]
