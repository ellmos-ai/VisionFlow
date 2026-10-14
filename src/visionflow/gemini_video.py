"""Bounded Google Gemini Interactions transport for inline whole-video requests."""

from __future__ import annotations

import base64
import hashlib
import json
import math
import os
import queue
import re
import socket
import threading
import time
import urllib.request
from collections.abc import Callable
from typing import Any

from .video_model import (
    VideoModelError,
    VideoRequest,
    VideoTransportResult,
)


GEMINI_MODEL_ID = "gemini-3.8-flash"
INTERACTIONS_URL = "https://generativelanguage.googleapis.com/v1beta/interactions"
MAX_REQUEST_BYTES = 20 * 1024 * 1024
MAX_RESPONSE_TEXT_BYTES = 1024 * 1024
DEFAULT_TIMEOUT_SECONDS = 30.0
_SAFE_REF_SEGMENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*\Z", re.ASCII)


class VideoTransportError(VideoModelError):
    """A safe native transport failure identified by a stable error code."""


class GeminiVideoTransport:
    """Send bounded inline media through Gemini Interactions without hidden defaults."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        cost_approved: bool = False,
        evidence_reader: Callable[[str], bytes] | None = None,
        http_open: Callable[..., Any] | None = None,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        secret = api_key if api_key is not None else os.environ.get("GEMINI_API_KEY")
        if type(secret) is not str or not secret.strip():
            raise ValueError("Gemini transport requires an external API credential")
        if cost_approved is not True:
            raise ValueError("Gemini transport requires explicit cost approval")
        if not callable(evidence_reader):
            raise ValueError("Gemini transport requires a configured evidence reader")
        if (
            isinstance(timeout_seconds, bool)
            or type(timeout_seconds) not in (int, float)
            or not math.isfinite(float(timeout_seconds))
            or not 0 < float(timeout_seconds) <= DEFAULT_TIMEOUT_SECONDS
        ):
            raise ValueError("timeout_seconds must be greater than 0 and at most 30")
        if not callable(clock):
            raise TypeError("clock must be callable")

        self._api_key = secret
        self._evidence_reader = evidence_reader
        self._http_open = http_open or _urlopen
        if not callable(self._http_open):
            raise TypeError("http_open must be callable")
        self._timeout_seconds = float(timeout_seconds)
        self._clock = clock

    def generate(
        self,
        request: VideoRequest,
        on_delta: Callable[[str], None],
        cancelled: Callable[[], bool],
    ) -> VideoTransportResult:
        if not isinstance(request, VideoRequest):
            raise TypeError("request must be a VideoRequest")
        if not callable(on_delta) or not callable(cancelled):
            raise TypeError("on_delta and cancelled must be callable")
        if request.model_id != GEMINI_MODEL_ID:
            raise VideoTransportError("unsupported_model")

        deadline = self._clock() + self._timeout_seconds
        self._check(deadline, cancelled)
        clip_bytes = self._read_clip(request, deadline, cancelled)
        media: list[tuple[str, str, bytes]] = []
        for evidence_ref in request.evidence_refs:
            self._check(deadline, cancelled)
            _validate_evidence_ref(evidence_ref, request.run_id)
            try:
                image_bytes = self._evidence_reader(evidence_ref)
            except Exception:
                raise VideoTransportError("evidence_unavailable") from None
            self._check(deadline, cancelled)
            if type(image_bytes) is not bytes:
                raise VideoTransportError("invalid_evidence")
            image_mime = _image_mime(image_bytes)
            if image_mime is None:
                raise VideoTransportError("invalid_evidence")
            media.append(("image", image_mime, image_bytes))
            _check_estimated_size(clip_bytes, media, request.prompt)

        self._check(deadline, cancelled)
        body = _build_request_body(request, clip_bytes, media)
        try:
            serialized = json.dumps(
                body,
                ensure_ascii=False,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
        except (TypeError, ValueError, UnicodeError):
            raise VideoTransportError("invalid_request") from None
        if len(serialized) >= MAX_REQUEST_BYTES:
            raise VideoTransportError("media_limit_exceeded")
        self._check(deadline, cancelled)

        http_request = urllib.request.Request(
            INTERACTIONS_URL,
            data=serialized,
            headers={
                "Accept": "text/event-stream",
                "Content-Type": "application/json",
                "X-Goog-Api-Key": self._api_key,
            },
            method="POST",
        )
        response = self._open_with_deadline(http_request, deadline, cancelled)
        try:
            self._validate_response(response)
            return self._read_stream(response, on_delta, deadline, cancelled)
        finally:
            _close_quietly(response)

    def _read_clip(
        self,
        request: VideoRequest,
        deadline: float,
        cancelled: Callable[[], bool],
    ) -> bytes:
        self._check(deadline, cancelled)
        try:
            clip_size = request.clip.path.stat().st_size
        except Exception:
            raise VideoTransportError("clip_unavailable") from None
        if clip_size <= 0 or _base64_size(clip_size) + len(request.prompt.encode("utf-8")) + 2048 >= MAX_REQUEST_BYTES:
            raise VideoTransportError("media_limit_exceeded")
        self._check(deadline, cancelled)
        try:
            clip_bytes = request.clip.path.read_bytes()
        except Exception:
            raise VideoTransportError("clip_unavailable") from None
        self._check(deadline, cancelled)
        if hashlib.sha256(clip_bytes).hexdigest() != request.clip.sha256:
            raise VideoTransportError("clip_integrity_failed")
        if len(clip_bytes) < 12 or clip_bytes[4:8] != b"ftyp":
            raise VideoTransportError("invalid_clip")
        return clip_bytes

    def _open_with_deadline(
        self,
        request: urllib.request.Request,
        deadline: float,
        cancelled: Callable[[], bool],
    ) -> Any:
        """Poll connect/open so cancellation and the same deadline cover connection."""
        self._check(deadline, cancelled)
        result_queue: queue.Queue[tuple[str, Any]] = queue.Queue(maxsize=1)
        abandoned = threading.Event()
        handoff_lock = threading.Lock()
        timeout = max(0.001, deadline - self._clock())

        def open_worker() -> None:
            with handoff_lock:
                if abandoned.is_set() or _cancelled(cancelled) or self._clock() >= deadline:
                    return
            try:
                response = self._http_open(request, timeout=timeout)
            except Exception:
                _publish_result(
                    "error",
                    VideoTransportError("provider_failed"),
                )
                return

            with handoff_lock:
                if abandoned.is_set() or _cancelled(cancelled) or self._clock() >= deadline:
                    close_response = True
                else:
                    try:
                        result_queue.put_nowait(("response", response))
                        close_response = False
                    except queue.Full:
                        close_response = True
            if close_response:
                _close_quietly(response)

        def _publish_result(kind: str, value: Any) -> None:
            with handoff_lock:
                if abandoned.is_set():
                    return
                try:
                    result_queue.put_nowait((kind, value))
                except queue.Full:
                    return

        worker = threading.Thread(target=open_worker, name="gemini-video-connect", daemon=True)
        worker.start()
        received_response = None
        accepted = False
        try:
            while True:
                self._check(deadline, cancelled)
                remaining = deadline - self._clock()
                try:
                    kind, value = result_queue.get(timeout=min(0.05, max(0.001, remaining)))
                except queue.Empty:
                    continue
                if kind == "error":
                    raise value
                received_response = value
                self._check(deadline, cancelled)
                accepted = True
                return received_response
        finally:
            if not accepted:
                with handoff_lock:
                    abandoned.set()
                    if received_response is not None:
                        _close_quietly(received_response)
                    while True:
                        try:
                            kind, value = result_queue.get_nowait()
                        except queue.Empty:
                            break
                        if kind == "response":
                            _close_quietly(value)

    def _validate_response(self, response: Any) -> None:
        status = getattr(response, "status", None)
        if status is None and callable(getattr(response, "getcode", None)):
            try:
                status = response.getcode()
            except Exception:
                raise VideoTransportError("provider_failed") from None
        if status is not None and status != 200:
            raise VideoTransportError("provider_failed")
        headers = getattr(response, "headers", None)
        if headers is not None:
            content_type = headers.get("Content-Type", headers.get("content-type", ""))
            if content_type and not str(content_type).lower().startswith("text/event-stream"):
                raise VideoTransportError("invalid_response")
        if not callable(getattr(response, "readline", None)):
            raise VideoTransportError("invalid_response")

    def _read_stream(
        self,
        response: Any,
        on_delta: Callable[[str], None],
        deadline: float,
        cancelled: Callable[[], bool],
    ) -> VideoTransportResult:
        active_output_steps: set[int] = set()
        response_text: list[str] = []
        response_size = 0
        usage: dict[str, Any] | None = None
        request_id: str | None = None
        completed = False
        event_data: list[str] = []
        event_size = 0

        def process_event(payload: str) -> None:
            nonlocal response_size, usage, request_id, completed
            self._check(deadline, cancelled)
            try:
                event = json.loads(payload, object_pairs_hook=_unique_object)
            except (json.JSONDecodeError, ValueError):
                raise VideoTransportError("invalid_response") from None
            if type(event) is not dict:
                raise VideoTransportError("invalid_response")
            event_type = event.get("event_type", event.get("type"))
            if event_type == "interaction.created":
                interaction = event.get("interaction")
                if type(interaction) is dict:
                    request_id = _safe_request_id(interaction.get("id")) or request_id
            elif event_type == "step.start":
                index = event.get("index")
                step = event.get("step")
                if type(index) is int and not isinstance(index, bool) and type(step) is dict:
                    if step.get("type") == "model_output":
                        active_output_steps.add(index)
                    else:
                        active_output_steps.discard(index)
            elif event_type == "step.delta":
                index = event.get("index")
                delta = event.get("delta")
                if (
                    type(index) is int
                    and not isinstance(index, bool)
                    and index in active_output_steps
                    and type(delta) is dict
                    and delta.get("type") == "text"
                    and type(delta.get("text")) is str
                ):
                    text = delta["text"]
                    response_size += len(text.encode("utf-8"))
                    if response_size > MAX_RESPONSE_TEXT_BYTES:
                        raise VideoTransportError("response_too_large")
                    if text:
                        response_text.append(text)
                        try:
                            on_delta(text)
                        except VideoTransportError:
                            raise
                        except Exception:
                            raise VideoTransportError("callback_failed") from None
                    if isinstance(event.get("metadata"), dict):
                        metadata_usage = event["metadata"].get("total_usage")
                        if type(metadata_usage) is dict:
                            usage = _safe_usage(metadata_usage)
            elif event_type == "step.stop":
                index = event.get("index")
                if type(index) is int and not isinstance(index, bool):
                    active_output_steps.discard(index)
                step_usage = event.get("step_usage")
                if type(step_usage) is dict:
                    usage = _safe_usage(step_usage) or usage
            elif event_type == "interaction.completed":
                interaction = event.get("interaction")
                if type(interaction) is not dict or interaction.get("status") != "completed":
                    raise VideoTransportError("incomplete_response")
                request_id = _safe_request_id(interaction.get("id")) or request_id
                interaction_usage = interaction.get("usage")
                if type(interaction_usage) is dict:
                    usage = _safe_usage(interaction_usage) or usage
                completed = True
            elif event_type in {"error", "interaction.failed"}:
                raise VideoTransportError("provider_failed")
            elif event_type == "interaction.status_update":
                interaction_status = event.get("status")
                if interaction_status in {"failed", "cancelled", "incomplete"}:
                    raise VideoTransportError(
                        "cancelled" if interaction_status == "cancelled" else "incomplete_response"
                    )

        # A socket file cannot be read again after a timeout, so each read may
        # block until the remaining deadline; a watchdog aborts the stream as
        # soon as the run is cancelled or the deadline passes.
        stop_watchdog = threading.Event()

        def watchdog() -> None:
            while not stop_watchdog.wait(0.05):
                if _cancelled(cancelled) or self._clock() >= deadline:
                    _abort_response(response)
                    return

        threading.Thread(target=watchdog, name="gemini-video-stream-watchdog", daemon=True).start()
        try:
            while not completed:
                self._check(deadline, cancelled)
                _set_socket_timeout(response, deadline - self._clock())
                try:
                    line = response.readline()
                except Exception as exc:
                    self._check(deadline, cancelled)
                    code = "timeout" if isinstance(exc, (socket.timeout, TimeoutError)) else "provider_failed"
                    raise VideoTransportError(code) from None
                self._check(deadline, cancelled)
                if not line:
                    if event_data:
                        process_event("\n".join(event_data))
                    break
                if type(line) is str:
                    line_text = line.rstrip("\r\n")
                elif type(line) is bytes:
                    try:
                        line_text = line.decode("utf-8").rstrip("\r\n")
                    except UnicodeError:
                        raise VideoTransportError("invalid_response") from None
                else:
                    raise VideoTransportError("invalid_response")
                if not line_text:
                    if event_data:
                        process_event("\n".join(event_data))
                        event_data.clear()
                        event_size = 0
                    continue
                if line_text.startswith(":"):
                    continue
                if line_text.startswith("data:"):
                    value = line_text[5:]
                    if value.startswith(" "):
                        value = value[1:]
                    event_size += len(value.encode("utf-8"))
                    if event_size > MAX_RESPONSE_TEXT_BYTES:
                        raise VideoTransportError("response_too_large")
                    event_data.append(value)

            self._check(deadline, cancelled)
            result_text = "".join(response_text)
            if not completed or not result_text:
                raise VideoTransportError("incomplete_response")
            return VideoTransportResult(
                raw_text=result_text,
                usage=usage,
                request_id=request_id,
            )
        finally:
            stop_watchdog.set()

    def _check(self, deadline: float, cancelled: Callable[[], bool]) -> None:
        if _cancelled(cancelled):
            raise VideoTransportError("cancelled")
        if self._clock() >= deadline:
            raise VideoTransportError("timeout")


def _build_request_body(
    request: VideoRequest,
    clip_bytes: bytes,
    evidence: list[tuple[str, str, bytes]],
) -> dict[str, Any]:
    content: list[dict[str, Any]] = [
        {
            "type": "video",
            "data": base64.b64encode(clip_bytes).decode("ascii"),
            "mime_type": "video/mp4",
            "processing": {"type": "static", "fps": 5.0},
        }
    ]
    for _kind, mime_type, image_bytes in evidence:
        content.append(
            {
                "type": "image",
                "data": base64.b64encode(image_bytes).decode("ascii"),
                "mime_type": mime_type,
            }
        )
    content.append({"type": "text", "text": request.prompt})
    return {
        "model": GEMINI_MODEL_ID,
        "input": content,
        "stream": True,
    }


def _check_estimated_size(
    clip_bytes: bytes,
    evidence: list[tuple[str, str, bytes]],
    prompt: str,
) -> None:
    estimated = (
        _base64_size(len(clip_bytes))
        + sum(_base64_size(len(image_bytes)) for _kind, _mime, image_bytes in evidence)
        + len(prompt.encode("utf-8"))
        + 4096
    )
    if estimated >= MAX_REQUEST_BYTES:
        raise VideoTransportError("media_limit_exceeded")


def _base64_size(size: int) -> int:
    return ((size + 2) // 3) * 4


def _image_mime(data: bytes) -> str | None:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if len(data) >= 3 and data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    return None


def _validate_evidence_ref(reference: str, run_id: str) -> None:
    if (
        type(reference) is not str
        or not reference
        or "\\" in reference
        or ":" in reference
    ):
        raise VideoTransportError("invalid_evidence_ref")
    parts = reference.split("/")
    if len(parts) < 2 or parts[0] != run_id or any(
        _SAFE_REF_SEGMENT.fullmatch(part) is None
        or part in {".", ".."}
        or part.endswith(".")
        for part in parts
    ):
        raise VideoTransportError("invalid_evidence_ref")


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate field")
        result[key] = value
    return result


def _safe_request_id(value: Any) -> str | None:
    if type(value) is str and 0 < len(value) <= 200:
        return value
    return None


def _safe_usage(usage: dict[str, Any]) -> dict[str, int | float] | None:
    fields = {
        "total_input_tokens",
        "total_output_tokens",
        "total_tokens",
        "total_thought_tokens",
        "total_cached_tokens",
        "input_tokens",
        "output_tokens",
        "cached_tokens",
    }
    result: dict[str, int | float] = {}
    for key in fields:
        value = usage.get(key)
        if (
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and math.isfinite(float(value))
            and value >= 0
        ):
            result[key] = value
    return result or None


def _cancelled(cancelled: Callable[[], bool]) -> bool:
    try:
        return bool(cancelled())
    except Exception:
        return True


def _set_socket_timeout(response: Any, timeout: float) -> None:
    if timeout <= 0:
        return
    try:
        raw = response.fp.raw
        sock = raw._sock
        sock.settimeout(timeout)
    except Exception:
        return


def _abort_response(response: Any) -> None:
    """Unblock a pending read from another thread, then close the response."""
    try:
        sock = response.fp.raw._sock
    except Exception:
        sock = None
    if sock is not None:
        try:
            sock.shutdown(socket.SHUT_RDWR)
        except Exception:
            pass
        # On Windows a timed recv waits in select(), which a local shutdown
        # does not wake; closing the descriptor does. close() alone is
        # deferred while the response's SocketIO still holds a reference.
        try:
            socket.close(sock.detach())
        except Exception:
            pass
    _close_quietly(response)


def _close_quietly(response: Any) -> None:
    try:
        response.close()
    except Exception:
        return


class _RejectRedirects(urllib.request.HTTPRedirectHandler):
    """Fail on 3xx instead of re-sending the API key header to another location."""

    def redirect_request(self, *_args: Any, **_kwargs: Any) -> None:
        return None


_OPENER = urllib.request.build_opener(_RejectRedirects())


def _urlopen(request: urllib.request.Request, *, timeout: float) -> Any:
    return _OPENER.open(request, timeout=timeout)


__all__ = [
    "DEFAULT_TIMEOUT_SECONDS",
    "GEMINI_MODEL_ID",
    "INTERACTIONS_URL",
    "MAX_REQUEST_BYTES",
    "GeminiVideoTransport",
    "VideoTransportError",
]
