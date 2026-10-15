"""Bounded hosted-demo admission, accounting and source-scoped retention."""
from __future__ import annotations

import math
import shutil
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterator


class RetentionCapacityError(RuntimeError):
    code = "demo_capacity_full"


class RetentionExpiredError(KeyError):
    """An unknown or currently collecting source cannot acquire new authority."""


@dataclass(frozen=True, slots=True)
class RetentionPolicy:
    max_sources: int = 32
    max_bytes: int = 536870912
    idle_seconds: float = 1800.0

    def __post_init__(self) -> None:
        for value in (self.max_sources, self.max_bytes):
            if type(value) is not int or value <= 0:
                raise ValueError("retention counts and budgets must be positive integers")
        if type(self.idle_seconds) not in (int, float) or not math.isfinite(self.idle_seconds) or self.idle_seconds <= 0:
            raise ValueError("retention duration must be finite and positive")


@dataclass(slots=True)
class _Source:
    owner: str
    source: str
    floor: int
    last_activity: float
    pins: int = 0
    collecting: bool = False
    settle_requested: bool = False
    usage: dict[str, int] = field(default_factory=dict)
    pending: dict[str, int] = field(default_factory=dict)
    writers: dict[str, threading.RLock] = field(default_factory=dict)

    def held(self) -> int:
        return max(self.floor, sum(max(self.usage.get(key, 0), self.pending.get(key, 0))
                                   for key in self.usage.keys() | self.pending.keys()))


class RetentionManager:
    """Reserve before writes, pin active work, and collect through one owned callback.

    Cleanup never runs under the accounting lock. A collecting marker rejects
    new leases until all files and store references have actually been removed.
    """

    def __init__(self, policy: RetentionPolicy, *, cleanup: Callable[[str, str], None],
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.policy = policy
        self._cleanup = cleanup
        self._clock = clock
        self._lock = threading.RLock()
        self._sources: dict[str, _Source] = {}
        self._cleanup_failures = 0
        self._stop = threading.Event()
        self._worker: threading.Thread | None = None

    def reserve_source(self, owner_id: str, source_id: str, bytes_required: int) -> None:
        if not isinstance(owner_id, str) or not owner_id or not isinstance(source_id, str) or not source_id:
            raise ValueError("source and owner identifiers are required")
        self._bytes(bytes_required)
        with self._lock:
            if source_id in self._sources:
                raise ValueError("source already reserved")
            if len(self._sources) >= self.policy.max_sources or self._held() + bytes_required > self.policy.max_bytes:
                raise RetentionCapacityError("The hosted demo resource budget is full.")
            self._sources[source_id] = _Source(owner_id, source_id, bytes_required, self._now())

    @contextmanager
    def protect(self, source_id: str, *, activity: bool = False) -> Iterator[None]:
        with self._lock:
            entry = self._entry(source_id)
            entry.pins += 1
        try:
            yield
        finally:
            with self._lock:
                entry.pins -= 1
                if activity:
                    entry.last_activity = self._now()
                if not entry.pins and entry.settle_requested:
                    entry.floor = 0

    @contextmanager
    def change_usage(self, source_id: str, component: str, new_bytes: int) -> Iterator[None]:
        """Account a complete component size before its side effect; rollback on error.

        During replacement, the larger of old/new usage remains reserved. Failed
        shrinking writes therefore cannot reclaim bytes already admitted elsewhere.
        """
        self._bytes(new_bytes)
        with self.protect(source_id):
            with self._lock:
                entry = self._entry(source_id)
                writer = entry.writers.setdefault(component, threading.RLock())
            with writer:
                with self._lock:
                    if component in entry.pending:
                        raise RuntimeError("nested accounting transaction for the same component")
                    entry.pending[component] = new_bytes
                    if self._held() > self.policy.max_bytes:
                        del entry.pending[component]
                        raise RetentionCapacityError("The hosted demo resource budget is full.")
                try:
                    yield
                except BaseException:
                    with self._lock:
                        entry.pending.pop(component, None)
                    raise
                else:
                    with self._lock:
                        entry.usage[component] = new_bytes
                        entry.pending.pop(component, None)

    def component_capacity(self, source_id: str, component: str) -> int:
        with self._lock:
            entry = self._entry(source_id)
            other_sources = sum(row.held() for row in self._sources.values() if row is not entry)
            other_parts = sum(max(entry.usage.get(key, 0), entry.pending.get(key, 0))
                              for key in entry.usage.keys() | entry.pending.keys() if key != component)
            return max(0, self.policy.max_bytes - other_sources - other_parts)

    def ensure_usage(self, source_id: str, component: str, new_bytes: int) -> None:
        """Keep a conservative high-water charge until verified reconciliation."""
        with self.protect(source_id):
            with self._lock:
                entry = self._entry(source_id)
                writer = entry.writers.setdefault(component, threading.RLock())
            with writer:
                with self._lock:
                    amount = max(new_bytes, entry.usage.get(component, 0))
                with self.change_usage(source_id, component, amount):
                    pass

    def settle(self, source_id: str) -> None:
        """Release the unused full-recording reservation after production ends."""
        with self._lock:
            entry = self._entry(source_id)
            entry.settle_requested = True
            if not entry.pins:
                entry.floor = 0

    def touch(self, source_id: str) -> None:
        with self._lock:
            self._entry(source_id).last_activity = self._now()

    def collect(self, now: float) -> list[str]:
        if not math.isfinite(now):
            raise ValueError("collection time must be finite")
        with self._lock:
            candidates = [row for row in self._sources.values() if not row.pins and not row.collecting
                          and now - row.last_activity >= self.policy.idle_seconds]
            for row in candidates:
                row.collecting = True
        removed = []
        for row in candidates:
            if self._remove(row):
                removed.append(row.source)
        return removed

    def discard(self, source_id: str) -> None:
        """Rollback a failed source creation only after its owned cleanup succeeds."""
        with self._lock:
            row = self._entry(source_id)
            if row.pins:
                raise RuntimeError("cannot discard protected source")
            row.collecting = True
        self._remove(row)

    def snapshot(self) -> dict[str, int]:
        with self._lock:
            return {"source_count": len(self._sources), "reserved_bytes": self._held(),
                    "used_bytes": sum(sum(row.usage.values()) for row in self._sources.values()),
                    "cleanup_failures": self._cleanup_failures}

    def start(self, interval: float = 5.0) -> None:
        if not math.isfinite(interval) or interval <= 0:
            raise ValueError("collection interval must be finite and positive")
        with self._lock:
            if self._worker is not None:
                return

            def work():
                while not self._stop.wait(interval):
                    self.collect(self._now())

            self._worker = threading.Thread(target=work, name="road-retention", daemon=True)
            self._worker.start()

    def close(self) -> None:
        self._stop.set()
        worker = self._worker
        if worker is not None and worker is not threading.current_thread():
            worker.join()

    def _remove(self, row: _Source) -> bool:
        try:
            self._cleanup(row.owner, row.source)
        except Exception:
            # Unremoved bytes keep their charge. No partial success is reported.
            with self._lock:
                row.collecting = False
                self._cleanup_failures += 1
            return False
        with self._lock:
            self._sources.pop(row.source, None)
        return True

    def _entry(self, source: str) -> _Source:
        row = self._sources.get(source)
        if row is None or row.collecting:
            raise RetentionExpiredError(source)
        return row

    def _held(self) -> int:
        return sum(row.held() for row in self._sources.values())

    def _now(self) -> float:
        value = self._clock()
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ValueError("retention clock must return finite monotonic seconds")
        return float(value)

    @staticmethod
    def _bytes(value: int) -> None:
        if type(value) is not int or value < 0:
            raise ValueError("byte counts must be nonnegative integers")


def _owned_files(root: Path, target: Path) -> list[Path]:
    root = Path(root).resolve(strict=True)
    target = Path(target).absolute()
    resolved = target.resolve(strict=False)
    if resolved == root or not resolved.is_relative_to(root):
        raise ValueError("cleanup target must be a child of its owned root")
    for parent in (target, *target.parents):
        if parent == root:
            break
        if parent.is_symlink() or parent.is_junction():
            raise ValueError("cleanup refuses symbolic links and junctions")
    if not target.exists():
        return []
    pending = [target]
    files = []
    while pending:
        directory = pending.pop()
        for child in directory.iterdir():
            if child.is_symlink() or child.is_junction():
                raise ValueError("cleanup refuses symbolic links and junctions")
            if child.is_dir():
                pending.append(child)
            else:
                files.append(child)
    return files


def owned_tree_bytes(root: Path, target: Path) -> int:
    return sum(path.stat().st_size for path in _owned_files(root, target))


def remove_owned_tree(root: Path, target: Path) -> None:
    """Remove one verified child tree; links/junctions fail closed before any delete."""
    _owned_files(root, target)
    if target.exists():
        shutil.rmtree(target)
