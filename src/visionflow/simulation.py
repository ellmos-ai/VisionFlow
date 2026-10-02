"""Threaded, bounded-buffer simulation sources for pixel-only road scenes."""

from __future__ import annotations

import threading
import time
import uuid
from collections import deque
from collections.abc import Callable

from visionflow.contracts import Frame
from visionflow.media import FrameSource

from .road_scene import RoadWorld


_FRAME_RATE = 20.0
_FRAME_PERIOD_SECONDS = 1.0 / _FRAME_RATE
_RAW_BUFFER_SIZE = 10
_MAX_SESSION_SECONDS = 120.0


class SimulationSession:
    """One private road world shared by bounded multicast frame readers."""

    def __init__(
        self,
        owner_id: str,
        seed: int = 42,
        on_frame: Callable[[Frame], None] | None = None,
    ) -> None:
        if not isinstance(owner_id, str) or not owner_id.strip():
            raise ValueError("owner_id must be a non-empty string")
        if on_frame is not None and not callable(on_frame):
            raise TypeError("on_frame must be callable or None")

        self.owner_id = owner_id
        self.source_id = f"road-sim-{uuid.uuid4().hex}"
        self._world = RoadWorld(seed=seed)
        self._on_frame = on_frame
        self._condition = threading.Condition()
        self._world_lock = threading.Lock()
        self._frames: deque[tuple[int, Frame]] = deque(maxlen=_RAW_BUFFER_SIZE)
        self._next_frame_number = 0
        self._latest: Frame | None = None
        self._started = False
        self._closed = False
        self._started_at: float | None = None
        self._producer: threading.Thread | None = None
        self._producer_error: Exception | None = None

    @property
    def closed(self) -> bool:
        """Whether the producer has stopped, including the session time cap."""
        with self._condition:
            return self._closed

    @property
    def error_code(self) -> str | None:
        """Return a safe producer status without exposing exception text."""
        with self._condition:
            return "producer_failed" if self._producer_error is not None else None

    def start(self) -> None:
        """Start the producer once; the first frame is produced immediately."""
        with self._condition:
            if self._closed:
                raise RuntimeError("cannot start a closed simulation session")
            if self._started:
                return
            self._started = True
            self._started_at = time.monotonic()
            self._producer = threading.Thread(
                target=self._produce_frames,
                name=f"visionflow-{self.source_id}",
                daemon=True,
            )
            self._producer.start()

    def inject(self, event: str) -> None:
        """Add a visible scene event while keeping world state private."""
        with self._world_lock:
            with self._condition:
                if self._closed:
                    raise RuntimeError("cannot inject into a closed simulation session")
                self._world.inject(event)

    def latest_frame(self) -> Frame:
        """Wait for and return the newest shared frame without copying it."""
        with self._condition:
            while self._latest is None and not self._closed:
                self._condition.wait()
            if self._latest is None:
                raise RuntimeError("simulation session closed before producing a frame")
            return self._latest

    def open_reader(self) -> FrameSource:
        """Open an independent cursor over frames retained in the shared buffer."""
        with self._condition:
            start_sequence = (
                self._frames[0][0] if self._frames else self._next_frame_number
            )
            return _SimulationReader(self, start_sequence)

    def close(self) -> None:
        """Stop production and wake every reader; safe to call more than once."""
        with self._condition:
            self._closed = True
            self._condition.notify_all()
            producer = self._producer

        if producer is not None and producer is not threading.current_thread():
            producer.join()

    def _produce_frames(self) -> None:
        try:
            with self._condition:
                started_at = self._started_at
            if started_at is None:
                return

            next_capture_at = started_at
            while True:
                with self._condition:
                    while not self._closed:
                        delay = next_capture_at - time.monotonic()
                        if delay <= 0:
                            break
                        self._condition.wait(delay)
                    if self._closed:
                        return

                    capture_time = time.monotonic()
                    source_time = max(0.0, capture_time - started_at)
                    if source_time >= _MAX_SESSION_SECONDS:
                        return

                with self._world_lock:
                    image = self._world.render(source_time)

                frame_number = self._next_frame_number
                frame = Frame(
                    frame_id=f"{self.source_id}:{frame_number}",
                    image=image,
                    timestamp_seconds=source_time,
                )
                with self._condition:
                    if self._closed:
                        return
                    self._frames.append((frame_number, frame))
                    self._next_frame_number += 1
                    self._latest = frame
                    self._condition.notify_all()

                if self._on_frame is not None:
                    self._on_frame(frame)

                # Schedule from capture start: slow rendering never causes a catch-up burst.
                next_capture_at = capture_time + _FRAME_PERIOD_SECONDS
        except Exception as exc:
            with self._condition:
                self._producer_error = exc
                self._closed = True
                self._condition.notify_all()
        finally:
            with self._condition:
                self._closed = True
                self._condition.notify_all()


class _SimulationReader:
    """A reader cursor that never owns or stops the shared producer."""

    @property
    def max_observation_fps(self) -> float:
        """Expose the bounded sampling rate for this synthetic camera source."""
        return 5.0


    def __init__(self, session: SimulationSession, next_sequence: int) -> None:
        self.source_id = session.source_id
        self._session = session
        self._next_sequence = next_sequence
        self._closed = False

    def read(self) -> Frame | None:
        session = self._session
        with session._condition:
            while True:
                if self._closed or session._closed:
                    return None

                frames = session._frames
                if frames:
                    oldest_sequence = frames[0][0]
                    if self._next_sequence < oldest_sequence:
                        self._next_sequence = oldest_sequence
                    if self._next_sequence < session._next_frame_number:
                        offset = self._next_sequence - oldest_sequence
                        sequence, frame = frames[offset]
                        if sequence != self._next_sequence:
                            self._next_sequence = oldest_sequence
                            continue
                        self._next_sequence += 1
                        return frame

                session._condition.wait()

    def close(self) -> None:
        """Close this cursor only, leaving other readers and the producer alive."""
        with self._session._condition:
            self._closed = True
            self._session._condition.notify_all()


class SimulationStore:
    """In-memory owner-scoped registry for live simulation sessions."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._sessions: dict[str, SimulationSession] = {}
        self._closed = False

    def create(
        self,
        owner_id: str,
        seed: int = 42,
        *,
        on_frame: Callable[[Frame], None] | None = None,
    ) -> SimulationSession:
        session = SimulationSession(owner_id=owner_id, seed=seed, on_frame=on_frame)
        with self._lock:
            if self._closed:
                session.close()
                raise RuntimeError("simulation store is closed")
            self._sessions[session.source_id] = session
        return session

    def get(self, owner_id: str, source_id: str) -> SimulationSession:
        with self._lock:
            session = self._sessions.get(source_id)
            if session is None or session.owner_id != owner_id:
                raise KeyError(source_id)
            return session

    def reset(
        self,
        owner_id: str,
        source_id: str,
        *,
        on_frame: Callable[[Frame], None] | None = None,
    ) -> SimulationSession:
        with self._lock:
            old_session = self._sessions.get(source_id)
            if old_session is None or old_session.owner_id != owner_id:
                raise KeyError(source_id)
            del self._sessions[source_id]

        old_session.close()
        replacement = SimulationSession(owner_id=owner_id, on_frame=on_frame)
        with self._lock:
            if self._closed:
                replacement.close()
                raise RuntimeError("simulation store is closed")
            self._sessions[replacement.source_id] = replacement
        return replacement

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            sessions = tuple(self._sessions.values())
            self._sessions.clear()

        for session in sessions:
            session.close()


__all__ = ["SimulationSession", "SimulationStore"]
