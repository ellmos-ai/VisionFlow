from __future__ import annotations

import hashlib
import json
import math
import os
import queue
import shutil
import statistics
import subprocess
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from functools import wraps
from threading import Event, RLock, Thread
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from .contracts import Frame


MAX_RECORDING_SECONDS = 120.0
MAX_RECORDING_BYTES = 128 * 1024 * 1024
MAX_FRAME_WIDTH = 960
MAX_FRAME_HEIGHT = 540
MAX_SOURCE_FPS = 20.0
RATE_WINDOW_SECONDS = 1.0
RATE_WINDOW_EXTRA_FRAMES = 1
FFMPEG_TIMEOUT_SECONDS = 30
FFMPEG_VERSION_TIMEOUT_SECONDS = 5
JPEG_QUALITY = 95
MIN_MP4_BYTES = 1024
MAX_CONCURRENT_EXPORTS = 2
MAX_CLIP_OUTPUT_BYTES = MAX_RECORDING_BYTES
FFMPEG_PIPE_CHUNK_BYTES = 64 * 1024
FFMPEG_PIPE_QUEUE_CHUNKS = 4


def _synchronized(method):
    @wraps(method)
    def wrapped(self, *args, **kwargs):
        with self._lock:
            return method(self, *args, **kwargs)
    return wrapped


@dataclass(frozen=True, slots=True)
class ClipRef:
    clip_id: str
    source_id: str
    version: int
    path: Path
    sha256: str
    start_seconds: float
    end_seconds: float
    frame_ids: tuple[str, ...]
    source_fps: float
    available_preroll: float


class RecordingError(RuntimeError):
    """A safe, stable recording failure identified by a machine-readable code."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


class ClipNotReady(RecordingError):
    """Raised when the requested real post-roll has not been recorded yet."""

    def __init__(self, message: str = "Der angeforderte Nachlauf liegt noch nicht vor.") -> None:
        super().__init__("clip_not_ready", message)


@dataclass(frozen=True, slots=True)
class _FrameRecord:
    frame_id: str
    timestamp_seconds: float
    file_name: str
    sha256: str
    size_bytes: int


@dataclass(slots=True)
class _SourceSession:
    owner_id: str
    source_id: str
    directory: Path
    frames_directory: Path
    clips_directory: Path
    frames: list[_FrameRecord] = field(default_factory=list)
    frame_ids: set[str] = field(default_factory=set)
    compressed_bytes: int = 0
    journal_bytes: int = 0
    manifest_reserved_bytes: int = 0
    frame_manifest_item_bytes: int = 0
    clip_manifest_item_bytes: int = 0
    clip_sizes: dict[str, int] = field(default_factory=dict)
    clip_ids_by_event: dict[str, str] = field(default_factory=dict)
    clip_versions_by_event: dict[str, int] = field(default_factory=dict)
    latest_clip_by_event: dict[str, ClipRef] = field(default_factory=dict)
    clip_history: list[ClipRef] = field(default_factory=list)
    pending_export_bytes: int = 0
    finish_ref: ClipRef | None = None
    dimensions: tuple[int, int] | None = None
    finished: bool = False


class RecordingStore:
    """Store bounded source frames and immutable, whole-frame MP4 event clips.

    Source identifiers are unique within a store. A single active owner may
    record a source at a time because append receives only source_id; a new
    simulation run should use a fresh source identifier.
    """

    def __init__(
        self,
        root: Path,
        ffmpeg_path: Path,
        max_seconds: float = MAX_RECORDING_SECONDS,
        max_bytes: int = MAX_RECORDING_BYTES,
    ) -> None:
        if isinstance(max_seconds, bool) or not isinstance(max_seconds, (int, float)):
            raise ValueError("max_seconds muss eine endliche positive Zahl sein.")
        if not math.isfinite(float(max_seconds)) or float(max_seconds) <= 0:
            raise ValueError("max_seconds muss eine endliche positive Zahl sein.")
        if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes <= 0:
            raise ValueError("max_bytes muss eine positive Ganzzahl sein.")
        if not isinstance(root, (str, Path)):
            raise TypeError("root muss ein Pfad sein.")
        if not isinstance(ffmpeg_path, (str, Path)):
            raise TypeError("ffmpeg_path muss ein Pfad sein.")

        self.root = Path(root).expanduser().resolve()
        self.ffmpeg_path = Path(ffmpeg_path).expanduser()
        self.max_seconds = float(max_seconds)
        self.max_bytes = max_bytes
        self._lock = RLock()
        self._sessions: dict[tuple[str, str], _SourceSession] = {}
        self._active_by_source: dict[str, tuple[str, str]] = {}
        self._clips: dict[str, tuple[tuple[str, str], ClipRef]] = {}
        self._ffmpeg_executable: str | None = None
        self._active_exports = 0

    @_synchronized
    def open(self, owner_id: str, source_id: str) -> None:
        owner = self._identifier(owner_id, "owner_id")
        source = self._identifier(source_id, "source_id")
        if source in self._active_by_source:
            raise RecordingError("source_in_use", "Die Quelle wird bereits aufgezeichnet.")
        key = (owner, source)
        if key in self._sessions:
            raise RecordingError("source_exists", "Diese Quelle wurde bereits in diesem Lauf geöffnet.")
        if any(saved_source == source for _, saved_source in self._sessions):
            raise RecordingError("source_exists", "Diese Quellenkennung darf nicht wiederverwendet werden.")

        try:
            self._safe_mkdir(self.root)
            owners_directory = self.root / "owners"
            self._safe_mkdir(owners_directory)
            owner_directory = owners_directory / self._opaque_id(owner)
            self._safe_mkdir(owner_directory)
            source_directory = owner_directory / self._opaque_id(source)
            self._safe_mkdir(source_directory, exist_ok=False)
            frames_directory = source_directory / "frames"
            clips_directory = source_directory / "clips"
            self._safe_mkdir(frames_directory)
            self._safe_mkdir(clips_directory)
        except RecordingError:
            raise
        except FileExistsError as exc:
            raise RecordingError("source_exists", "Diese Quelle besitzt bereits Aufnahmedaten.") from exc
        except OSError as exc:
            raise RecordingError("storage_unavailable", "Der Aufnahmespeicher ist nicht verfügbar.") from exc

        session = _SourceSession(
            owner_id=owner,
            source_id=source,
            directory=source_directory,
            frames_directory=frames_directory,
            clips_directory=clips_directory,
        )
        try:
            self._write_manifest(session)
        except RecordingError:
            try:
                shutil.rmtree(self._safe_path(source_directory))
            except OSError:
                pass
            raise
        self._sessions[key] = session
        self._active_by_source[source] = key

    @_synchronized
    def append(self, source_id: str, frame: Frame) -> None:
        source = self._identifier(source_id, "source_id")
        key = self._active_by_source.get(source)
        if key is None:
            raise RecordingError("source_not_open", "Für diese Quelle läuft keine Aufzeichnung.")
        session = self._sessions[key]
        if session.finished:
            raise RecordingError("source_not_open", "Die Aufzeichnung dieser Quelle ist beendet.")

        frame_id = getattr(frame, "frame_id", None)
        image = getattr(frame, "image", None)
        timestamp = getattr(frame, "timestamp_seconds", None)
        if not self._valid_identifier(frame_id, allow_long=True):
            raise RecordingError("invalid_frame", "Der Frame besitzt keine gültige Kennung.")
        if frame_id in session.frame_ids:
            raise RecordingError("duplicate_frame_id", "Die Frame-Kennung wurde bereits aufgezeichnet.")
        if isinstance(timestamp, bool) or not isinstance(timestamp, (int, float)):
            raise RecordingError("invalid_timestamp", "Die Frame-Zeit ist ungültig.")
        timestamp_value = float(timestamp)
        if not math.isfinite(timestamp_value) or timestamp_value < 0:
            raise RecordingError("invalid_timestamp", "Die Frame-Zeit ist ungültig.")
        if session.frames:
            previous = session.frames[-1].timestamp_seconds
            delta = timestamp_value - previous
            if delta <= 0:
                raise RecordingError("invalid_timestamp", "Frame-Zeiten müssen streng ansteigen.")
            elapsed = timestamp_value - session.frames[0].timestamp_seconds
            if elapsed > self.max_seconds:
                raise RecordingError("duration_limit", "Das Zeitlimit dieser Aufzeichnung ist erreicht.")
            window_start = timestamp_value - RATE_WINDOW_SECONDS
            frames_in_window = 1
            for previous_frame in reversed(session.frames):
                if previous_frame.timestamp_seconds < window_start:
                    break
                frames_in_window += 1
            allowed_frames = int(MAX_SOURCE_FPS * RATE_WINDOW_SECONDS) + RATE_WINDOW_EXTRA_FRAMES
            if frames_in_window > allowed_frames:
                raise RecordingError(
                    "frame_rate_limit",
                    "Innerhalb eines Sekundenfensters sind höchstens 21 Bilder zulässig.",
                )

        if not isinstance(image, np.ndarray) or image.dtype != np.uint8:
            raise RecordingError("invalid_frame", "Erwartet wird ein uint8-Bild.")
        if image.ndim != 3 or image.shape[2] != 3 or image.size == 0:
            raise RecordingError("invalid_frame", "Erwartet wird ein nichtleeres BGR-Vollbild.")
        height, width = image.shape[:2]
        if width > MAX_FRAME_WIDTH or height > MAX_FRAME_HEIGHT:
            raise RecordingError("frame_too_large", "Das Bild überschreitet 960 × 540 Pixel.")
        dimensions = (width, height)
        if session.dimensions is not None and session.dimensions != dimensions:
            raise RecordingError("frame_shape_changed", "Die Bildauflösung hat sich während der Quelle geändert.")

        try:
            ok, encoded = cv2.imencode(
                ".jpg",
                np.ascontiguousarray(image),
                [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY],
            )
        except cv2.error as exc:
            raise RecordingError("invalid_frame", "Das Bild konnte nicht komprimiert werden.") from exc
        if not ok or encoded.size == 0:
            raise RecordingError("invalid_frame", "Das Bild konnte nicht komprimiert werden.")
        image_bytes = encoded.tobytes()
        filename = f"frame-{len(session.frames):06d}.jpg"
        record = _FrameRecord(
            frame_id=frame_id,
            timestamp_seconds=timestamp_value,
            file_name=filename,
            sha256=hashlib.sha256(image_bytes).hexdigest(),
            size_bytes=len(image_bytes),
        )
        index_path = self._safe_path(session.directory / "frames.jsonl")
        manifest_path = self._safe_path(session.directory / "manifest.json")
        index_size_before = self._optional_file_size(index_path)
        manifest_size_before = self._optional_file_size(manifest_path)
        manifest_reservation_before = max(
            manifest_size_before,
            session.manifest_reserved_bytes,
        )
        index_line = self._frame_index_line(record)
        projected_manifest_size = self._manifest_size(session, next_frame=record)
        projected_usage = (
            self._usage(session)
            + len(image_bytes)
            + len(index_line)
            + projected_manifest_size
            - manifest_reservation_before
        )
        if projected_usage > self.max_bytes:
            raise RecordingError("byte_limit", "Das Speicherlimit dieser Aufzeichnung ist erreicht.")

        frame_path = self._safe_path(session.frames_directory / filename)
        try:
            with frame_path.open("xb") as output:
                output.write(image_bytes)
        except FileExistsError as exc:
            raise RecordingError("source_exists", "Ein Quellenbild kann nicht überschrieben werden.") from exc
        except OSError as exc:
            raise RecordingError("storage_unavailable", "Das Quellenbild konnte nicht gespeichert werden.") from exc

        session.frames.append(record)
        session.frame_ids.add(frame_id)
        session.compressed_bytes += len(image_bytes)
        if session.dimensions is None:
            session.dimensions = dimensions
        try:
            self._append_frame_index(session, record, index_line)
        except RecordingError:
            session.frames.pop()
            session.frame_ids.remove(frame_id)
            session.compressed_bytes -= len(image_bytes)
            if not session.frames:
                session.dimensions = None
            self._truncate_file(index_path, index_size_before)
            self._unlink_if_exists(frame_path)
            raise
        session.journal_bytes += len(index_line)
        session.frame_manifest_item_bytes += len(self._frame_manifest_item_bytes(record))
        session.manifest_reserved_bytes = projected_manifest_size

    @_synchronized
    def manifest(self, owner_id: str, source_id: str) -> dict[str, Any]:
        session = self._get_session(owner_id, source_id)
        self._write_manifest(session)
        return self._manifest_data(session)

    def event_clip(
        self,
        owner_id: str,
        source_id: str,
        event_time: float,
        before: float = 5.0,
        after: float = 10.0,
        end_override: float | None = None,
    ) -> ClipRef:
        event = self._finite_seconds(event_time, "event_time")
        pre = self._finite_seconds(before, "before")
        post = self._finite_seconds(after, "after")
        if event < 0 or pre < 0 or post < 0:
            raise RecordingError("invalid_time_range", "Ereigniszeit sowie Vor- und Nachlauf müssen positiv sein.")
        requested_end = (
            self._finite_seconds(end_override, "end_override")
            if end_override is not None
            else event + post
        )
        if requested_end < event:
            raise RecordingError("invalid_time_range", "Das Clipende darf nicht vor dem Ereignis liegen.")
        requested_start = event - pre
        if requested_end - requested_start > self.max_seconds:
            raise RecordingError("duration_limit", "Der angeforderte Clip überschreitet das Zeitlimit.")

        with self._lock:
            session = self._get_session(owner_id, source_id)
            if not session.frames:
                raise ClipNotReady()
            if session.frames[-1].timestamp_seconds < requested_end:
                raise ClipNotReady()

            first_index = next(
                (
                    index for index, record in enumerate(session.frames)
                    if record.timestamp_seconds >= requested_start
                ),
                None,
            )
            if first_index is None:
                raise ClipNotReady()
            selected = session.frames[first_index:]
            last_index = next(
                (
                    index for index, record in enumerate(selected)
                    if record.timestamp_seconds >= requested_end
                ),
                None,
            )
            if last_index is None:
                raise ClipNotReady()
            selected = list(selected[: last_index + 1])
            event_key = self._event_key(event, pre)
            latest = session.latest_clip_by_event.get(event_key)
            if latest is not None and latest.end_seconds >= requested_end:
                return latest
            actual_start = selected[0].timestamp_seconds
            actual_end = selected[-1].timestamp_seconds
            preroll = max(0.0, event - actual_start)

        return self._export(
            session,
            event_key=event_key,
            frames=selected,
            start_seconds=actual_start,
            end_seconds=actual_end,
            available_preroll=preroll,
        )

    @_synchronized
    def clip(self, owner_id: str, clip_id: str) -> ClipRef:
        owner = self._identifier(owner_id, "owner_id")
        clip_key = self._identifier(clip_id, "clip_id")
        record = self._clips.get(clip_key)
        if record is None:
            raise RecordingError("clip_not_found", "Der Clip wurde nicht gefunden.")
        session_key, clip_ref = record
        session = self._sessions[session_key]
        if session.owner_id != owner:
            raise RecordingError("owner_mismatch", "Dieser Clip gehört nicht zu diesem Besitzer.")
        self._verify_clip_file(clip_ref)
        return clip_ref

    def finish(self, owner_id: str, source_id: str) -> ClipRef:
        with self._lock:
            session = self._get_session(owner_id, source_id)
            if session.finish_ref is not None:
                self._verify_clip_file(session.finish_ref)
                return session.finish_ref
            if not session.frames:
                raise ClipNotReady("Es wurden noch keine Bilder für einen vollständigen Clip aufgezeichnet.")
            records = list(session.frames)
            first = records[0].timestamp_seconds
            last = records[-1].timestamp_seconds
            session.finished = True
            self._active_by_source.pop(session.source_id, None)

        clip_ref = self._export(
            session,
            event_key="finish",
            frames=records,
            start_seconds=first,
            end_seconds=last,
            available_preroll=last - first,
        )
        with self._lock:
            session.finish_ref = clip_ref
            self._write_manifest(session)
        return clip_ref

    def _export(
        self,
        session: _SourceSession,
        *,
        event_key: str,
        frames: list[_FrameRecord],
        start_seconds: float,
        end_seconds: float,
        available_preroll: float,
    ) -> ClipRef:
        with self._lock:
            if self._active_exports >= MAX_CONCURRENT_EXPORTS:
                raise RecordingError("export_busy", "Zu viele Videoexporte laufen gleichzeitig.")
            self._active_exports += 1
        try:
            return self._export_with_slot(
                session,
                event_key=event_key,
                frames=frames,
                start_seconds=start_seconds,
                end_seconds=end_seconds,
                available_preroll=available_preroll,
            )
        finally:
            with self._lock:
                self._active_exports -= 1

    def _export_with_slot(
        self,
        session: _SourceSession,
        *,
        event_key: str,
        frames: list[_FrameRecord],
        start_seconds: float,
        end_seconds: float,
        available_preroll: float,
    ) -> ClipRef:
        self._require_encoder()
        fps = self._source_fps(frames)
        clip_id = uuid.uuid4().hex
        with self._lock:
            latest = session.latest_clip_by_event.get(event_key)
            if latest is not None and latest.end_seconds >= end_seconds:
                return latest
            planned_version = session.clip_versions_by_event.get(event_key, 0) + 1
            planned_path = self._safe_path(
                session.clips_directory / f"{clip_id}.v{planned_version}.mp4"
            )
            planned_ref = ClipRef(
                clip_id=clip_id,
                source_id=session.source_id,
                version=planned_version,
                path=planned_path,
                sha256="0" * 64,
                start_seconds=start_seconds,
                end_seconds=end_seconds,
                frame_ids=tuple(record.frame_id for record in frames),
                source_fps=fps,
                available_preroll=available_preroll,
            )
            manifest_path = self._safe_path(session.directory / "manifest.json")
            manifest_size_before = self._optional_file_size(manifest_path)
            manifest_reservation_before = max(
                manifest_size_before,
                session.manifest_reserved_bytes,
            )
            manifest_size_after = self._manifest_size(
                session,
                next_clip=planned_ref,
                export_bytes=self.max_bytes,
            )
            remaining = (
                self.max_bytes
                - self._usage(session)
                - manifest_size_after
                + manifest_reservation_before
            )
        if remaining < MIN_MP4_BYTES:
            raise RecordingError("byte_limit", "Für den Clip ist das Speicherlimit erreicht.")

        payload_parts: list[bytes] = []
        input_size = 0
        for record in frames:
            frame_path = self._safe_path(session.frames_directory / record.file_name)
            try:
                frame_bytes = frame_path.read_bytes()
            except OSError as exc:
                raise RecordingError("source_corrupt", "Ein gespeichertes Quellenbild fehlt.") from exc
            if (
                len(frame_bytes) != record.size_bytes
                or hashlib.sha256(frame_bytes).hexdigest() != record.sha256
            ):
                raise RecordingError("source_corrupt", "Ein gespeichertes Quellenbild wurde verändert.")
            payload_parts.append(frame_bytes)
            input_size += len(frame_bytes)
        if input_size > min(self.max_bytes, MAX_RECORDING_BYTES):
            raise RecordingError("byte_limit", "Der Quellclip überschreitet die Speichergrenze für den Export.")

        executable = self._ffmpeg_executable
        assert executable is not None
        output_limit = min(remaining, MAX_CLIP_OUTPUT_BYTES)
        if output_limit < MIN_MP4_BYTES:
            raise RecordingError("byte_limit", "Für den Clip ist das Speicherlimit erreicht.")
        args = [
            executable,
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "image2pipe",
            "-vcodec",
            "mjpeg",
            "-framerate",
            f"{fps:.8f}",
            "-i",
            "pipe:0",
            "-frames:v",
            str(len(frames)),
            "-an",
            "-vf",
            "pad=ceil(iw/2)*2:ceil(ih/2)*2",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "23",
            "-bf",
            "0",
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+empty_moov+frag_keyframe+default_base_moof",
            "-fs",
            str(output_limit),
            "-f",
            "mp4",
            "pipe:1",
        ]
        output_bytes = self._encode_mp4_to_memory(args, payload_parts, output_limit)
        output_size = len(output_bytes)
        if output_size == 0:
            raise RecordingError("encoder_failed", "FFmpeg konnte keinen gültigen Videoexport erstellen.")
        if output_size >= output_limit:
            raise RecordingError("byte_limit", "Das Speicherlimit erlaubt keinen vollständigen Clip.")

        temp_path = self._safe_path(
            session.clips_directory / f".{clip_id}.{uuid.uuid4().hex}.tmp.mp4"
        )
        pending_reserved = False
        try:
            with self._lock:
                latest = session.latest_clip_by_event.get(event_key)
                if latest is not None and latest.end_seconds >= end_seconds:
                    return latest
                if self._usage(session) + output_size > self.max_bytes:
                    raise RecordingError("byte_limit", "Der Clip würde das Speicherlimit überschreiten.")
                session.pending_export_bytes += output_size
                pending_reserved = True

            try:
                with temp_path.open("xb") as output:
                    output.write(output_bytes)
                    output.flush()
            except OSError as exc:
                raise RecordingError("storage_unavailable", "Der Clip konnte nicht zwischengespeichert werden.") from exc

            if not self._decode_count_matches(temp_path, len(frames)):
                if output_size >= output_limit:
                    raise RecordingError("byte_limit", "Das Speicherlimit erlaubt keinen vollständigen Clip.")
                raise RecordingError("encoder_failed", "Der Videoexport ist unvollständig oder beschädigt.")

            with self._lock:
                latest = session.latest_clip_by_event.get(event_key)
                if latest is not None and latest.end_seconds >= end_seconds:
                    self._unlink_if_exists(temp_path)
                    session.pending_export_bytes -= output_size
                    pending_reserved = False
                    return latest

                version = session.clip_versions_by_event.get(event_key, 0) + 1
                final_path = self._safe_path(
                    session.clips_directory / f"{clip_id}.v{version}.mp4"
                )
                if final_path.exists():
                    raise RecordingError("clip_exists", "Der Clip-Zielpfad ist bereits belegt.")
                linked_final = False
                try:
                    os.link(temp_path, final_path)
                    linked_final = True
                    temp_path.unlink()
                except FileExistsError as exc:
                    raise RecordingError("clip_exists", "Der Clip-Zielpfad ist bereits belegt.") from exc
                except OSError as exc:
                    if linked_final:
                        self._unlink_if_exists(final_path)
                    raise RecordingError("export_commit_failed", "Der Clip konnte nicht unveränderlich gespeichert werden.") from exc

                try:
                    clip_hash = self._sha256_file(final_path)
                except RecordingError:
                    self._unlink_if_exists(final_path)
                    raise
                clip_ref = ClipRef(
                    clip_id=clip_id,
                    source_id=session.source_id,
                    version=version,
                    path=final_path,
                    sha256=clip_hash,
                    start_seconds=start_seconds,
                    end_seconds=end_seconds,
                    frame_ids=tuple(record.frame_id for record in frames),
                    source_fps=fps,
                    available_preroll=available_preroll,
                )
                previous_latest = session.latest_clip_by_event.get(event_key)
                previous_clip_id = session.clip_ids_by_event.get(event_key)
                previous_version = session.clip_versions_by_event.get(event_key)
                session.pending_export_bytes -= output_size
                pending_reserved = False
                session.clip_sizes[clip_id] = output_size
                session.clip_manifest_item_bytes += len(self._clip_manifest_item_bytes(clip_ref))
                session.clip_ids_by_event[event_key] = clip_id
                session.clip_versions_by_event[event_key] = version
                session.latest_clip_by_event[event_key] = clip_ref
                session.clip_history.append(clip_ref)
                session_key = (session.owner_id, session.source_id)
                self._clips[clip_id] = (session_key, clip_ref)
                try:
                    self._write_manifest(session)
                except RecordingError:
                    session.clip_sizes.pop(clip_id, None)
                    session.clip_manifest_item_bytes -= len(self._clip_manifest_item_bytes(clip_ref))
                    session.clip_ids_by_event.pop(event_key, None)
                    session.clip_versions_by_event.pop(event_key, None)
                    session.latest_clip_by_event.pop(event_key, None)
                    if previous_latest is not None:
                        session.latest_clip_by_event[event_key] = previous_latest
                    if previous_clip_id is not None:
                        session.clip_ids_by_event[event_key] = previous_clip_id
                    if previous_version is not None:
                        session.clip_versions_by_event[event_key] = previous_version
                    if session.clip_history and session.clip_history[-1].clip_id == clip_id:
                        session.clip_history.pop()
                    self._clips.pop(clip_id, None)
                    self._unlink_if_exists(final_path)
                    raise
                return clip_ref
        finally:
            self._unlink_if_exists(temp_path)
            if pending_reserved:
                with self._lock:
                    session.pending_export_bytes -= output_size

    def _require_encoder(self) -> None:
        if self._ffmpeg_executable is not None:
            return
        candidate = str(self.ffmpeg_path)
        executable = candidate if self.ffmpeg_path.is_file() else shutil.which(candidate)
        if not executable:
            raise RecordingError("ffmpeg_unavailable", "FFmpeg ist nicht verfügbar.")
        try:
            result = subprocess.run(
                [executable, "-hide_banner", "-encoders"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=FFMPEG_VERSION_TIMEOUT_SECONDS,
                check=False,
                text=True,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise RecordingError("ffmpeg_unavailable", "FFmpeg ist nicht verfügbar.") from exc
        if result.returncode != 0:
            raise RecordingError("ffmpeg_unavailable", "FFmpeg ist nicht verfügbar.")
        if "libx264" not in result.stdout and "libx264" not in result.stderr:
            raise RecordingError("encoder_unavailable", "Der benötigte H.264-Encoder ist nicht verfügbar.")
        self._ffmpeg_executable = executable

    def _encode_mp4_to_memory(
        self,
        args: list[str],
        input_parts: list[bytes],
        max_output_bytes: int,
    ) -> bytearray:
        """Run FFmpeg with bounded in-memory output and no disk destination."""
        deadline = time.monotonic() + FFMPEG_TIMEOUT_SECONDS
        try:
            process = subprocess.Popen(
                args,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                bufsize=0,
            )
        except OSError as exc:
            raise RecordingError("encoder_failed", "Der Videoexport konnte nicht gestartet werden.") from exc

        output_queue: queue.Queue[bytes | None] = queue.Queue(maxsize=FFMPEG_PIPE_QUEUE_CHUNKS)
        stop_reader = Event()
        reader_errors: list[OSError] = []
        writer_errors: list[OSError] = []

        def read_output() -> None:
            try:
                assert process.stdout is not None
                while not stop_reader.is_set():
                    chunk = process.stdout.read(FFMPEG_PIPE_CHUNK_BYTES)
                    if not chunk:
                        break
                    while not stop_reader.is_set():
                        try:
                            output_queue.put(chunk, timeout=0.05)
                            break
                        except queue.Full:
                            continue
            except OSError as exc:
                if not stop_reader.is_set():
                    reader_errors.append(exc)
            finally:
                while not stop_reader.is_set():
                    try:
                        output_queue.put(None, timeout=0.05)
                        break
                    except queue.Full:
                        continue

        def write_input() -> None:
            try:
                assert process.stdin is not None
                for part in input_parts:
                    offset = 0
                    while offset < len(part):
                        if stop_reader.is_set():
                            return
                        chunk = part[offset : offset + FFMPEG_PIPE_CHUNK_BYTES]
                        written = process.stdin.write(chunk)
                        if written is None or written <= 0:
                            raise OSError("FFmpeg closed its input pipe")
                        offset += written
            except (BrokenPipeError, OSError) as exc:
                if not stop_reader.is_set():
                    writer_errors.append(exc)
            finally:
                if process.stdin is not None:
                    try:
                        process.stdin.close()
                    except OSError:
                        pass

        reader = Thread(target=read_output, name="visionflow-ffmpeg-stdout", daemon=True)
        writer = Thread(target=write_input, name="visionflow-ffmpeg-stdin", daemon=True)
        reader.start()
        writer.start()

        captured = bytearray()
        timed_out = False
        overflow = False
        unexpected_error: BaseException | None = None
        try:
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    timed_out = True
                    break
                try:
                    chunk = output_queue.get(timeout=remaining)
                except queue.Empty:
                    timed_out = True
                    break
                if chunk is None:
                    break
                if len(captured) + len(chunk) > max_output_bytes:
                    overflow = True
                    break
                captured.extend(chunk)

            if not timed_out and not overflow:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    timed_out = True
                else:
                    try:
                        process.wait(timeout=remaining)
                    except subprocess.TimeoutExpired:
                        timed_out = True
        except BaseException as exc:
            unexpected_error = exc
        finally:
            must_kill = timed_out or overflow or unexpected_error is not None
            if process.poll() is None and must_kill:
                try:
                    process.kill()
                except OSError:
                    pass
            if process.poll() is None:
                try:
                    process.wait(timeout=1.0)
                except subprocess.TimeoutExpired:
                    try:
                        process.kill()
                    except OSError:
                        pass
                    try:
                        process.wait(timeout=1.0)
                    except subprocess.TimeoutExpired:
                        pass
            stop_reader.set()
            for pipe in (process.stdin, process.stdout):
                if pipe is not None:
                    try:
                        pipe.close()
                    except OSError:
                        pass
            writer.join(timeout=1.0)
            reader.join(timeout=1.0)

        if timed_out:
            raise RecordingError("encoder_timeout", "Der Videoexport hat das Zeitlimit überschritten.")
        if overflow:
            raise RecordingError("byte_limit", "Der Videoexport überschreitet die Speichergrenze.")
        if unexpected_error is not None:
            raise RecordingError("encoder_failed", "Der Videoexport konnte nicht abgeschlossen werden.") from unexpected_error
        if reader_errors or writer_errors or process.returncode != 0:
            raise RecordingError("encoder_failed", "FFmpeg konnte keinen gültigen Videoexport erstellen.")
        return captured

    def _decode_count_matches(self, path: Path, expected: int) -> bool:
        capture = cv2.VideoCapture(str(path))
        if not capture.isOpened():
            capture.release()
            return False
        count = 0
        try:
            while True:
                ok, _frame = capture.read()
                if not ok:
                    break
                count += 1
                if count > expected:
                    return False
        finally:
            capture.release()
        return count == expected

    def _get_session(self, owner_id: str, source_id: str) -> _SourceSession:
        owner = self._identifier(owner_id, "owner_id")
        source = self._identifier(source_id, "source_id")
        key = (owner, source)
        session = self._sessions.get(key)
        if session is not None:
            return session
        if any(saved_source == source for _, saved_source in self._sessions):
            raise RecordingError("owner_mismatch", "Diese Quelle gehört nicht zu diesem Besitzer.")
        raise RecordingError("source_not_found", "Die Quelle wurde nicht gefunden.")

    def _manifest_summary(
        self,
        session: _SourceSession,
        frames: list[_FrameRecord],
        *,
        compressed_bytes: int,
        export_bytes: int | None = None,
    ) -> dict[str, Any]:
        return {
            "source_id": session.source_id,
            "status": "finished" if session.finished else "recording",
            "frame_count": len(frames),
            "source_fps": self._source_fps(frames) if frames else MAX_SOURCE_FPS,
            "start_seconds": frames[0].timestamp_seconds if frames else None,
            "end_seconds": frames[-1].timestamp_seconds if frames else None,
            "max_seconds": self.max_seconds,
            "max_bytes": self.max_bytes,
            "compressed_bytes": compressed_bytes,
            "export_bytes": (
                sum(session.clip_sizes.values())
                if export_bytes is None
                else export_bytes
            ),
            "frames": [],
            "clips": [],
        }

    def _frame_manifest_item_bytes(self, record: _FrameRecord) -> bytes:
        return json.dumps(
            {
                "frame_id": record.frame_id,
                "timestamp_seconds": record.timestamp_seconds,
                "sha256": record.sha256,
                "size_bytes": record.size_bytes,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")

    def _clip_manifest_item_bytes(self, ref: ClipRef) -> bytes:
        return json.dumps(
            {
                "clip_id": ref.clip_id,
                "version": ref.version,
                "sha256": ref.sha256,
                "start_seconds": ref.start_seconds,
                "end_seconds": ref.end_seconds,
                "frame_ids": list(ref.frame_ids),
            },
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")

    def _manifest_size(
        self,
        session: _SourceSession,
        *,
        next_frame: _FrameRecord | None = None,
        next_clip: ClipRef | None = None,
        export_bytes: int | None = None,
    ) -> int:
        frames = session.frames
        compressed_bytes = session.compressed_bytes
        frame_items = session.frame_manifest_item_bytes
        clip_items = session.clip_manifest_item_bytes
        clip_count = len(session.clip_history)
        if next_frame is not None:
            frames = [*frames, next_frame]
            compressed_bytes += next_frame.size_bytes
            frame_items += len(self._frame_manifest_item_bytes(next_frame))
        if next_clip is not None:
            clip_items += len(self._clip_manifest_item_bytes(next_clip))
            clip_count += 1

        summary = self._manifest_summary(
            session,
            frames,
            compressed_bytes=compressed_bytes,
            export_bytes=export_bytes,
        )
        base_size = len(
            json.dumps(summary, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        )
        frame_separators = max(0, len(frames) - 1)
        clip_separators = max(0, clip_count - 1)
        return base_size + frame_items + frame_separators + clip_items + clip_separators

    def _manifest_data(
        self,
        session: _SourceSession,
        *,
        frame_records: list[_FrameRecord] | None = None,
        clip_history: list[ClipRef] | None = None,
    ) -> dict[str, Any]:
        frames = session.frames if frame_records is None else frame_records
        clips_history = session.clip_history if clip_history is None else clip_history
        data = self._manifest_summary(
            session,
            frames,
            compressed_bytes=sum(record.size_bytes for record in frames),
        )
        data["frames"] = [
            {
                "frame_id": record.frame_id,
                "timestamp_seconds": record.timestamp_seconds,
                "sha256": record.sha256,
                "size_bytes": record.size_bytes,
            }
            for record in frames
        ]
        data["clips"] = [
            {
                "clip_id": ref.clip_id,
                "version": ref.version,
                "sha256": ref.sha256,
                "start_seconds": ref.start_seconds,
                "end_seconds": ref.end_seconds,
                "frame_ids": list(ref.frame_ids),
            }
            for ref in clips_history
        ]
        return data

    def _manifest_bytes(
        self,
        session: _SourceSession,
        *,
        frame_records: list[_FrameRecord] | None = None,
        clip_history: list[ClipRef] | None = None,
    ) -> bytes:
        return json.dumps(
            self._manifest_data(
                session,
                frame_records=frame_records,
                clip_history=clip_history,
            ),
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")

    def _frame_index_line(self, record: _FrameRecord) -> bytes:
        return json.dumps(
            {
                "frame_id": record.frame_id,
                "timestamp_seconds": record.timestamp_seconds,
                "file": record.file_name,
                "sha256": record.sha256,
                "size_bytes": record.size_bytes,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8") + b"\n"

    def _append_frame_index(
        self,
        session: _SourceSession,
        record: _FrameRecord,
        line: bytes | None = None,
    ) -> None:
        index_path = self._safe_path(session.directory / "frames.jsonl")
        line = self._frame_index_line(record) if line is None else line
        try:
            mode = "r+b" if index_path.exists() else "w+b"
            with index_path.open(mode) as index:
                index.seek(0, os.SEEK_END)
                start = index.tell()
                try:
                    index.write(line)
                    index.flush()
                except OSError:
                    index.seek(start)
                    index.truncate()
                    raise
        except OSError as exc:
            raise RecordingError("manifest_write_failed", "Das Quellenjournal konnte nicht gespeichert werden.") from exc

    def _write_manifest(self, session: _SourceSession) -> None:
        payload = self._manifest_bytes(session)
        manifest_path = self._safe_path(session.directory / "manifest.json")
        old_manifest_size = self._optional_file_size(manifest_path)
        # os.replace is atomic to readers, but both files occupy disk until it
        # completes. Reserve the old and new manifest together while accounting
        # for any staged clip files that are being validated concurrently.
        new_manifest_reservation = max(len(payload), session.manifest_reserved_bytes)
        projected_usage = (
            self._usage_without_manifest(session)
            + old_manifest_size
            + new_manifest_reservation
        )
        if projected_usage > self.max_bytes:
            raise RecordingError("byte_limit", "Das Speicherlimit dieser Aufzeichnung ist erreicht.")

        temp_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb",
                dir=session.directory,
                prefix=".manifest-",
                suffix=".tmp",
                delete=False,
            ) as output:
                temp_path = Path(output.name)
                output.write(payload)
                output.flush()
                os.fsync(output.fileno())
            self._safe_path(temp_path)
            os.replace(temp_path, manifest_path)
            session.manifest_reserved_bytes = len(payload)
        except (OSError, TypeError, ValueError) as exc:
            if temp_path is not None:
                self._unlink_if_exists(temp_path)
            raise RecordingError("manifest_write_failed", "Das Aufnahmemanifest konnte nicht gespeichert werden.") from exc

    def _source_fps(self, frames: list[_FrameRecord]) -> float:
        if len(frames) < 2:
            return MAX_SOURCE_FPS
        intervals = [
            right.timestamp_seconds - left.timestamp_seconds
            for left, right in zip(frames, frames[1:])
            if right.timestamp_seconds > left.timestamp_seconds
        ]
        if not intervals:
            return MAX_SOURCE_FPS
        return 1.0 / statistics.median(intervals)

    def _usage(self, session: _SourceSession) -> int:
        manifest_size = self._optional_file_size(session.directory / "manifest.json")
        return self._usage_without_manifest(session) + max(
            manifest_size,
            session.manifest_reserved_bytes,
        )

    def _usage_without_manifest(self, session: _SourceSession) -> int:
        return (
            session.compressed_bytes
            + session.journal_bytes
            + sum(session.clip_sizes.values())
            + session.pending_export_bytes
        )

    def _optional_file_size(self, path: Path) -> int:
        safe = self._safe_path(path)
        try:
            info = safe.stat()
        except FileNotFoundError:
            return 0
        except OSError as exc:
            raise RecordingError("storage_unavailable", "Die Größe einer Aufnahmedatei konnte nicht gelesen werden.") from exc
        if not safe.is_file():
            raise RecordingError("unsafe_path", "Ein erwarteter Aufnahmepfad ist keine Datei.")
        return info.st_size

    def _truncate_file(self, path: Path, size: int) -> None:
        safe = self._safe_path(path)
        if not safe.exists():
            return
        try:
            with safe.open("r+b") as output:
                output.truncate(size)
        except OSError:
            pass

    def _event_key(self, event_time: float, before: float) -> str:
        return json.dumps([round(event_time, 9), round(before, 9)], separators=(",", ":"))

    def _finite_seconds(self, value: Any, name: str) -> float:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise RecordingError("invalid_time_range", f"{name} muss eine endliche Zahl sein.")
        result = float(value)
        if not math.isfinite(result):
            raise RecordingError("invalid_time_range", f"{name} muss eine endliche Zahl sein.")
        return result

    def _identifier(self, value: Any, name: str) -> str:
        if not self._valid_identifier(value):
            raise RecordingError("invalid_identifier", f"{name} ist ungültig.")
        return value

    def _valid_identifier(self, value: Any, *, allow_long: bool = False) -> bool:
        limit = 512
        return (
            isinstance(value, str)
            and bool(value.strip())
            and len(value) <= limit
            and "\x00" not in value
            and not any(ord(character) < 32 for character in value)
        )

    def _opaque_id(self, value: str) -> str:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()

    def _safe_mkdir(self, path: Path, *, exist_ok: bool = True) -> None:
        safe = self._safe_path(path)
        safe.mkdir(parents=True, exist_ok=exist_ok)
        self._safe_path(safe)

    def _safe_path(self, path: Path) -> Path:
        candidate = Path(path)
        resolved = candidate.resolve(strict=False)
        if not resolved.is_relative_to(self.root):
            raise RecordingError("unsafe_path", "Ein Aufnahmepfad liegt außerhalb des Datenordners.")
        return resolved

    def _verify_clip_file(self, clip_ref: ClipRef) -> None:
        path = self._safe_path(clip_ref.path)
        if not path.is_file():
            raise RecordingError("clip_not_found", "Der Clip wurde nicht gefunden.")
        if self._sha256_file(path) != clip_ref.sha256:
            raise RecordingError("clip_corrupt", "Der gespeicherte Clip wurde verändert.")

    def _sha256_file(self, path: Path) -> str:
        digest = hashlib.sha256()
        try:
            with path.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)
        except OSError as exc:
            raise RecordingError("clip_not_found", "Der Clip wurde nicht gefunden.") from exc
        return digest.hexdigest()

    def _unlink_if_exists(self, path: Path) -> None:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
