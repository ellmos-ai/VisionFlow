"""Thread-safe cumulative budgets for one VisionFlow run."""

from __future__ import annotations

import math
from threading import Lock
from typing import Any

from .contracts import BudgetExceeded


class Budget:
    """Atomically track frame, observation-time, and model-call consumption."""

    def __init__(
        self,
        max_observation_seconds: int | float = 300,
        max_frames: int = 300,
        max_llm_calls: int = 4,
    ) -> None:
        self._validate_seconds(max_observation_seconds, "max_observation_seconds", positive=True)
        self._validate_count(max_frames, "max_frames", positive=True)
        self._validate_count(max_llm_calls, "max_llm_calls", positive=True)
        self._limits: dict[str, int | float] = {
            "frames": max_frames,
            "observation_seconds": float(max_observation_seconds),
            "llm_calls": max_llm_calls,
        }
        self._used: dict[str, int | float] = {
            "frames": 0,
            "observation_seconds": 0.0,
            "llm_calls": 0,
        }
        self._lock = Lock()

    @property
    def remaining(self) -> dict[str, int | float]:
        with self._lock:
            return {
                name: limit - self._used[name]
                for name, limit in self._limits.items()
            }

    def consume(
        self,
        frames: int = 0,
        observation_seconds: int | float = 0,
        llm_calls: int = 0,
    ) -> None:
        """Reserve all requested resources, or leave the budget unchanged."""
        self._validate_count(frames, "frames", positive=False)
        self._validate_seconds(observation_seconds, "observation_seconds", positive=False)
        self._validate_count(llm_calls, "llm_calls", positive=False)
        requested: dict[str, int | float] = {
            "frames": frames,
            "observation_seconds": float(observation_seconds),
            "llm_calls": llm_calls,
        }

        with self._lock:
            exceeded = [
                name
                for name, amount in requested.items()
                if self._used[name] + amount > self._limits[name]
            ]
            if exceeded:
                names = ", ".join(exceeded)
                raise BudgetExceeded(f"run budget exceeded for: {names}")
            for name, amount in requested.items():
                self._used[name] += amount

    def snapshot(self) -> dict[str, dict[str, int | float]]:
        """Return a detached snapshot of consumed, maximum, and remaining values."""
        with self._lock:
            used = dict(self._used)
            limits = dict(self._limits)
            remaining = {
                name: limits[name] - used[name]
                for name in self._limits
            }
        return {"used": used, "limits": limits, "remaining": remaining}

    def _refund(
        self,
        *,
        frames: int = 0,
        observation_seconds: int | float = 0,
        llm_calls: int = 0,
    ) -> None:
        """Refund a reservation when no work happened (for example, media EOF)."""
        self._validate_count(frames, "frames", positive=False)
        self._validate_seconds(observation_seconds, "observation_seconds", positive=False)
        self._validate_count(llm_calls, "llm_calls", positive=False)
        refund: dict[str, int | float] = {
            "frames": frames,
            "observation_seconds": float(observation_seconds),
            "llm_calls": llm_calls,
        }
        with self._lock:
            if any(amount > self._used[name] for name, amount in refund.items()):
                raise ValueError("cannot refund more budget than has been consumed")
            for name, amount in refund.items():
                self._used[name] -= amount

    @staticmethod
    def _validate_count(value: Any, name: str, *, positive: bool) -> None:
        if type(value) is not int:
            raise TypeError(f"{name} must be an integer")
        if value < (1 if positive else 0):
            boundary = "positive" if positive else "non-negative"
            raise ValueError(f"{name} must be a {boundary} integer")

    @staticmethod
    def _validate_seconds(value: Any, name: str, *, positive: bool) -> None:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise TypeError(f"{name} must be a finite number")
        try:
            numeric = float(value)
        except OverflowError as exc:
            raise ValueError(f"{name} must be a finite number") from exc
        if not math.isfinite(numeric):
            raise ValueError(f"{name} must be a finite number")
        if numeric < 0 or (positive and numeric == 0):
            boundary = "positive" if positive else "non-negative"
            raise ValueError(f"{name} must be a {boundary} number")
