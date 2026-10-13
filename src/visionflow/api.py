"""Loopback-only HTTP API for the local VisionFlow controller."""

from __future__ import annotations

import hmac
import json
import mimetypes
import re
import secrets
import socket
import threading
import time
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from math import isfinite
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import parse_qs, urlsplit

from visionflow.contracts import (
    ApprovalRejected,
    ConfigError,
    InvalidTransition,
    MediaError,
    ObservationRejected,
    UnknownRun,
)


MAX_JSON_BYTES = 1024 * 1024
MAX_UPLOAD_BYTES = 64 * 1024 * 1024
SESSION_COOKIE = "vf_session"
SESSION_TTL_SECONDS = 8 * 60 * 60
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
DEFAULT_PUBLIC_UPLOAD_QUOTA_BYTES = 200 * 1024 * 1024
_PUBLIC_UPLOAD_REF = re.compile(r"uploads/vf_[0-9a-f]{48}\.bin")
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


class _ApiFault(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


class _ApiState:
    def __init__(
        self,
        controller: Any,
        *,
        data_dir: Path | str,
        evidence_root: Path | str | None,
        registry: Any,
        approval_store: Any,
        road_sessions: Any = None,
        road_cases: Any = None,
        road_unavailable_reason: str | None = None,
        public_demo: bool = False,
        public_upload_quota_bytes: int = 0,
    ) -> None:
        if type(public_demo) is not bool:
            raise ValueError("public_demo must be a boolean")
        if type(public_upload_quota_bytes) is not int or public_upload_quota_bytes < 0:
            raise ValueError("public_upload_quota_bytes must be a non-negative integer")
        self.public_demo = public_demo
        self.public_upload_quota_bytes = public_upload_quota_bytes or DEFAULT_PUBLIC_UPLOAD_QUOTA_BYTES
        self.controller = controller
        self.root = Path(data_dir).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.uploads_root = self.root / "uploads"
        self.uploads_root.mkdir(parents=True, exist_ok=True)
        self.evidence_root = Path(evidence_root or self.root / "evidence").expanduser().resolve()
        self.registry = registry or getattr(controller, "registry", None)
        if self.registry is None:
            from visionflow.runtime import create_registry

            self.registry = create_registry()
        self.approval_store = approval_store or getattr(controller, "approval_store", None)
        self.road_sessions = road_sessions
        self.road_cases = road_cases
        allowed_reasons = {None, "ffmpeg_required", "h264_encoder_required"}
        if road_unavailable_reason not in allowed_reasons:
            raise ValueError("unsupported road capability reason")
        self.road_unavailable_reason = road_unavailable_reason or "ffmpeg_required"
        self.sessions: dict[str, tuple[str, float]] = {}
        self.run_sessions: dict[str, str] = {}
        self.lock = threading.RLock()


class VisionFlowHTTPServer(ThreadingHTTPServer):
    """Threaded server with an exact loopback bind and isolated per-server state."""

    daemon_threads = True
    allow_reuse_address = True

    def server_close(self) -> None:
        try:
            super().server_close()
        finally:
            for service in (self.state.road_cases, self.state.road_sessions):
                if service is not None:
                    service.close()

    def __init__(
        self,
        server_address: tuple[str, int],
        controller: Any,
        *,
        data_dir: Path | str,
        evidence_root: Path | str | None = None,
        registry: Any = None,
        approval_store: Any = None,
        road_sessions: Any = None,
        road_cases: Any = None,
        road_unavailable_reason: str | None = None,
        public_demo: bool = False,
        public_upload_quota_bytes: int | None = None,
    ) -> None:
        host = server_address[0]
        if host not in _LOOPBACK_HOSTS:
            raise ValueError("VisionFlow server must bind to a loopback address")
        if ":" in host:
            # Instance attribute so the IPv4 default stays untouched for other servers.
            self.address_family = socket.AF_INET6
        self.state = _ApiState(
            controller,
            data_dir=data_dir,
            evidence_root=evidence_root,
            registry=registry,
            approval_store=approval_store,
            road_sessions=road_sessions,
            road_cases=road_cases,
            road_unavailable_reason=road_unavailable_reason,
            public_demo=public_demo,
            public_upload_quota_bytes=public_upload_quota_bytes or 0,
        )
        super().__init__(server_address, _VisionFlowHandler)


def create_server(
    controller: Any,
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
    data_dir: Path | str = ".visionflow-data",
    evidence_root: Path | str | None = None,
    registry: Any = None,
    approval_store: Any = None,
    road_sessions: Any = None,
    road_cases: Any = None,
    road_unavailable_reason: str | None = None,
    public_demo: bool = False,
    public_upload_quota_bytes: int | None = None,
) -> VisionFlowHTTPServer:
    """Create a local server. It is loopback-only and does not start automatically.

    With public_demo the server is prepared for a hosted demo behind a reverse
    proxy: workflows may only read road simulation sources or media uploaded to
    this server, and stored uploads are capped. The bind stays loopback-only.
    """
    return VisionFlowHTTPServer(
        (host, port),
        controller,
        data_dir=data_dir,
        evidence_root=evidence_root,
        registry=registry,
        approval_store=approval_store,
        road_sessions=road_sessions,
        road_cases=road_cases,
        road_unavailable_reason=road_unavailable_reason,
        public_demo=public_demo,
        public_upload_quota_bytes=public_upload_quota_bytes,
    )


def _json_value(value: Any) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        return _json_value(asdict(value))
    if isinstance(value, dict):
        if any(type(key) is not str for key in value):
            raise TypeError("JSON objects must use string keys")
        return {key: _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("API datetimes must be timezone-aware")
        return value.isoformat()
    if value is None or type(value) in {str, bool, int}:
        return value
    if type(value) is float:
        if not isfinite(value):
            raise ValueError("API numbers must be finite")
        return value
    raise TypeError(f"cannot serialize {type(value).__name__} as JSON")


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON value {value} is not allowed")


class _VisionFlowHandler(BaseHTTPRequestHandler):
    server: VisionFlowHTTPServer

    def log_message(self, format: str, *args: Any) -> None:
        # Request paths, cookies, and CSRF values never go to the standard log.
        return

    def _host_parts(self) -> tuple[str, int]:
        header = self.headers.get("Host", "")
        if not header:
            raise _ApiFault(403, "host_rejected", "A loopback Host header is required.")
        try:
            parsed = urlsplit("//" + header)
            hostname = (parsed.hostname or "").lower()
            port = parsed.port
        except ValueError as exc:
            raise _ApiFault(403, "host_rejected", "The Host header is invalid.") from exc
        if (
            parsed.username is not None
            or parsed.password is not None
            or hostname not in _LOOPBACK_HOSTS
            or port != self.server.server_port
        ):
            raise _ApiFault(403, "host_rejected", "Only the local VisionFlow host is allowed.")
        return hostname, port

    def _check_request(self, *, require_origin: bool = False) -> None:
        host, port = self._host_parts()
        origin = self.headers.get("Origin")
        if not origin:
            if require_origin:
                raise _ApiFault(403, "origin_required", "A same-origin request is required.")
            return
        try:
            parsed = urlsplit(origin)
            origin_host = (parsed.hostname or "").lower()
            origin_port = parsed.port or (80 if parsed.scheme == "http" else None)
        except ValueError as exc:
            raise _ApiFault(403, "origin_rejected", "The request origin is invalid.") from exc
        if (
            parsed.scheme != "http"
            or parsed.username is not None
            or parsed.password is not None
            or origin_host not in _LOOPBACK_HOSTS
            or origin_host != host
            or origin_port != port
        ):
            raise _ApiFault(403, "origin_rejected", "Cross-origin requests are not allowed.")

    def _send_bytes(
        self,
        status: int,
        payload: bytes,
        content_type: str,
        *,
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        if extra_headers:
            for key, value in extra_headers.items():
                self.send_header(key, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(payload)

    def _send_json(
        self,
        status: int,
        payload: Any,
        *,
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        try:
            encoded = json.dumps(
                _json_value(payload),
                ensure_ascii=False,
                allow_nan=False,
                separators=(",", ":"),
            ).encode("utf-8")
        except (TypeError, ValueError):
            self._send_error(500, "serialization_error", "The response could not be serialized.")
            return
        self._send_bytes(
            status,
            encoded,
            "application/json; charset=utf-8",
            extra_headers=extra_headers,
        )

    def _send_error(self, status: int, code: str, message: str) -> None:
        self._send_json(status, {"error": {"code": code, "message": message}})

    def _send_fault(self, fault: _ApiFault) -> None:
        self._send_error(fault.status, fault.code, fault.message)

    def _handle_exception(self, exc: Exception) -> None:
        if isinstance(exc, _ApiFault):
            self._send_fault(exc)
        elif isinstance(exc, UnknownRun):
            self._send_error(404, "unknown_run", "The requested run does not exist.")
        elif isinstance(exc, InvalidTransition):
            self._send_error(409, "invalid_transition", str(exc))
        elif isinstance(exc, ConfigError):
            self._send_error(422, "configuration_invalid", str(exc))
        elif isinstance(exc, (ApprovalRejected, ObservationRejected, MediaError)):
            self._send_error(409 if isinstance(exc, ApprovalRejected) else 422, "request_rejected", str(exc))
        else:
            self._send_error(500, "internal_error", "The local request could not be completed.")

    def _read_length(self, maximum: int) -> int:
        raw = self.headers.get("Content-Length")
        if raw is None:
            raise _ApiFault(411, "content_length_required", "Content-Length is required.")
        try:
            length = int(raw, 10)
        except (TypeError, ValueError) as exc:
            raise _ApiFault(400, "invalid_content_length", "Content-Length must be an integer.") from exc
        if length < 0:
            raise _ApiFault(400, "invalid_content_length", "Content-Length must not be negative.")
        if length > maximum:
            self.close_connection = True
            code = "upload_too_large" if maximum == MAX_UPLOAD_BYTES else "body_too_large"
            raise _ApiFault(413, code, f"The request body exceeds the {maximum}-byte limit.")
        return length

    def _read_json(self, *, optional: bool = False) -> dict[str, Any]:
        content_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
        if content_type != "application/json":
            raise _ApiFault(415, "json_required", "Content-Type must be application/json.")
        length = self._read_length(MAX_JSON_BYTES)
        if optional and length == 0:
            return {}
        if length == 0:
            raise _ApiFault(400, "json_required", "A JSON object is required.")
        raw = self.rfile.read(length)
        if len(raw) != length:
            raise _ApiFault(400, "incomplete_body", "The request body ended early.")
        try:
            value = json.loads(raw.decode("utf-8"), parse_constant=_reject_json_constant)
        except (UnicodeError, json.JSONDecodeError, ValueError) as exc:
            raise _ApiFault(400, "invalid_json", "The request body is not valid UTF-8 JSON.") from exc
        if not isinstance(value, dict):
            raise _ApiFault(422, "json_object_required", "The request body must be a JSON object.")
        return value

    def _session_id(self) -> str | None:
        cookie = SimpleCookie()
        try:
            cookie.load(self.headers.get("Cookie", ""))
        except Exception:
            return None
        morsel = cookie.get(SESSION_COOKIE)
        return morsel.value if morsel is not None else None

    def _require_session(self, *, require_csrf: bool = True) -> str:
        session_id = self._session_id()
        csrf = self.headers.get("X-CSRF-Token", "")
        with self.server.state.lock:
            now = time.monotonic()
            for expired, (_, created_at) in list(self.server.state.sessions.items()):
                if now - created_at > SESSION_TTL_SECONDS:
                    del self.server.state.sessions[expired]
            session = self.server.state.sessions.get(session_id or "")
        if (
            session is None
            or (
                require_csrf
                and (
                    not csrf
                    # http.server decodes headers as Latin-1; compare bytes so a
                    # non-ASCII token is a 403 instead of a TypeError (500).
                    or not hmac.compare_digest(
                        csrf.encode("utf-8", "surrogateescape"),
                        session[0].encode("utf-8"),
                    )
                )
            )
        ):
            raise _ApiFault(403, "session_required", "A valid local session and CSRF token are required.")
        return session_id or ""

    def _require_run_owner(self, run_id: str, session_id: str) -> Any:
        # Look up the run first so unknown identifiers consistently return 404.
        result = self.server.state.controller.status(run_id)
        with self.server.state.lock:
            owner = self.server.state.run_sessions.get(run_id)
        if owner is None or not hmac.compare_digest(owner, session_id):
            raise _ApiFault(
                403,
                "session_mismatch",
                "This action belongs to a different local session.",
            )
        return result

    def _json_route(self, path: str) -> tuple[str, list[str]]:
        parts = path.strip("/").split("/") if path != "/" else []
        if any(not part for part in parts):
            return "", []
        return (parts[0] if parts else ""), parts

    def _road_service(self):
        service = self.server.state.road_sessions
        if service is None:
            raise _ApiFault(404, "road_unavailable", "Die lokale Kamerademo ist nicht verfügbar.")
        return service

    def _authorize_road_source(self, session_id: str, source_id: str) -> Any:
        service = self._road_service()
        owner = service.source_owner(source_id)
        if owner is None:
            raise _ApiFault(404, "road_source_not_found", "Die Kameraquelle wurde nicht gefunden.")
        if not hmac.compare_digest(owner, session_id):
            raise _ApiFault(403, "session_mismatch", "Diese Kameraquelle gehört zu einer anderen lokalen Sitzung.")
        try:
            return service.get(session_id, source_id)
        except KeyError as exc:
            raise _ApiFault(404, "road_source_not_found", "Die Kameraquelle wurde nicht gefunden.") from exc

    def _check_public_source(self, kind: Any, source: Any) -> None:
        """In the public demo only road simulations and own uploads are readable."""
        if not self.server.state.public_demo:
            return
        from visionflow.road_api import ROAD_SOURCE_PREFIX

        if kind == "camera" and isinstance(source, str) and source.startswith(ROAD_SOURCE_PREFIX):
            return
        if kind in {"image", "video"} and isinstance(source, str) and _PUBLIC_UPLOAD_REF.fullmatch(source):
            return
        raise _ApiFault(
            403,
            "public_demo_source_rejected",
            "The public demo only reads the road simulation and media uploaded here.",
        )

    def _check_public_resume(self, body: Any) -> None:
        if not self.server.state.public_demo or not isinstance(body, dict):
            return
        nested = body.get("input")
        for container in (body, nested if isinstance(nested, dict) else {}):
            if "source" not in container:
                continue
            source = container.get("source")
            kind = container.get("kind")
            if kind is None:
                from visionflow.road_api import ROAD_SOURCE_PREFIX

                road = isinstance(source, str) and source.startswith(ROAD_SOURCE_PREFIX)
                kind = "camera" if road else "image"
            self._check_public_source(kind, source)

    def _authorize_workflow_source(self, session_id: str, workflow: Any) -> str | None:
        from visionflow.road_api import ROAD_SOURCE_PREFIX

        source_id = getattr(getattr(workflow, "input", None), "source", None)
        kind = getattr(getattr(workflow, "input", None), "kind", None)
        if kind != "camera" or not isinstance(source_id, str) or not source_id.startswith(ROAD_SOURCE_PREFIX):
            return None
        self._authorize_road_source(session_id, source_id)
        return source_id

    def _resume_source(self, body: Any) -> str | None:
        if not isinstance(body, dict):
            return None
        source_id = body.get("source")
        nested = body.get("input")
        if not isinstance(source_id, str) and isinstance(nested, dict):
            source_id = nested.get("source")
        if not isinstance(source_id, str):
            return None
        from visionflow.road_api import ROAD_SOURCE_PREFIX
        return source_id if source_id.startswith(ROAD_SOURCE_PREFIX) else None

    def _cancel_run(self, session_id: str, run_id: str) -> Any:
        service = self.server.state.road_sessions
        if service is not None and service.owner_source_for_run(run_id) is not None:
            owner_source = service.owner_source_for_run(run_id)
            if owner_source is not None and hmac.compare_digest(owner_source[0], session_id):
                service.cancel_run(session_id, run_id)
                return self.server.state.controller.status(run_id)
        return self.server.state.controller.cancel(run_id)

    def _handle_road_get(self, path: str) -> bool:
        if not path.startswith("/road/"):
            return False
        try:
            from visionflow.road_api import dispatch_road_get

            session_id = self._require_session(require_csrf=False)
            service = self.server.state.road_sessions
            if path.split("?", 1)[0] == "/road/capabilities" and service is None:
                reason = self.server.state.road_unavailable_reason
                messages = {
                    "ffmpeg_required": "Für die Straßenkamera-Aufzeichnung muss FFmpeg mit libx264 verfügbar sein.",
                    "h264_encoder_required": "Die Straßenkamera-Aufzeichnung benötigt den FFmpeg-Encoder libx264.",
                }
                self._send_json(200, {
                    "available": False,
                    "encoder_available": False,
                    "reason_code": reason,
                    "message": messages.get(reason, messages["ffmpeg_required"]),
                    "native_enabled": False,
                    "modes": [],
                    "fixture_profiles": [],
                })
                return True
            response = dispatch_road_get(
                self._road_service(),
                session_id,
                path,
                cases=self.server.state.road_cases,
            )
            if response is None:
                raise _ApiFault(404, "not_found", "Die angeforderte lokale Ressource wurde nicht gefunden.")
            if isinstance(response.payload, bytes):
                self._send_bytes(
                    response.status,
                    response.payload,
                    response.content_type,
                    extra_headers=response.headers,
                )
            else:
                self._send_json(response.status, response.payload, extra_headers=response.headers)
        except _ApiFault as fault:
            self._send_fault(fault)
        except Exception as exc:
            self._send_road_error(exc)
        return True
    def _handle_road_post(self, path: str, body: Any, session_id: str) -> bool:
        if not path.startswith("/road/"):
            return False
        try:
            from visionflow.road_api import dispatch_road_post

            response = dispatch_road_post(
                self._road_service(),
                session_id,
                path,
                body,
                cases=self.server.state.road_cases,
            )
            if response is None:
                raise _ApiFault(404, "not_found", "Die angeforderte lokale Ressource wurde nicht gefunden.")
            if path.split("?", 1)[0] == "/road/cases":
                case = response.payload.get("case") if isinstance(response.payload, dict) else None
                run_id = case.get("run_id") if isinstance(case, dict) else None
                if type(run_id) is not str or not run_id:
                    raise _ApiFault(500, "run_start_failed", "Der Fall konnte keinem Lauf zugeordnet werden.")
                with self.server.state.lock:
                    existing_owner = self.server.state.run_sessions.get(run_id)
                    if existing_owner is None:
                        self.server.state.run_sessions[run_id] = session_id
                if existing_owner is not None:
                    try:
                        case_service = self.server.state.road_cases
                        if case_service is not None and isinstance(case, dict):
                            case_service.cancel(session_id, case["case_id"])
                    except Exception:
                        pass
                    raise _ApiFault(409, "run_id_conflict", "Der Lauf ist bereits einer Sitzung zugeordnet.")
            self._send_json(response.status, response.payload, extra_headers=response.headers)
        except _ApiFault as fault:
            self._send_fault(fault)
        except Exception as exc:
            self._send_road_error(exc)
        return True

    def _send_road_error(self, exc: Exception) -> None:
        from visionflow.decision_policy import DecisionError
        from visionflow.recording import RecordingError
        from visionflow.road_api import RoadSessionError
        from visionflow.road_cases import RoadCaseError

        if isinstance(exc, (RoadSessionError, RoadCaseError)):
            self._send_error(exc.status, exc.code, str(exc))
        elif isinstance(exc, DecisionError):
            status = 404 if exc.code == "case_not_found" else 409
            message = (
                "Der Fall wurde nicht gefunden."
                if status == 404
                else "Die Fallentscheidung ist nicht mehr aktuell oder kann nicht übernommen werden."
            )
            self._send_error(status, exc.code, message)
        elif isinstance(exc, RecordingError):
            status = 403 if exc.code == "owner_mismatch" else 404 if exc.code in {"source_not_found", "clip_not_found"} else 409
            self._send_error(status, exc.code, str(exc))
        elif isinstance(exc, KeyError):
            self._send_error(404, "road_source_not_found", "Die Kameraquelle wurde nicht gefunden.")
        else:
            self._send_error(500, "road_request_failed", "Die Kamerademo konnte die Anfrage nicht ausführen.")

    def do_GET(self) -> None:
        try:
            self._check_request()
            parsed = urlsplit(self.path)
            path = parsed.path
            if path == "/session":
                session_id = self._session_id()
                now = time.monotonic()
                with self.server.state.lock:
                    existing = self.server.state.sessions.get(session_id or "")
                    if existing is not None and now - existing[1] <= SESSION_TTL_SECONDS:
                        csrf = existing[0]
                    else:
                        session_id = secrets.token_urlsafe(32)
                        csrf = secrets.token_urlsafe(32)
                    self.server.state.sessions[session_id] = (csrf, now)
                self._send_json(
                    200,
                    {"csrf_token": csrf, "public_demo": self.server.state.public_demo},
                    extra_headers={
                        "Set-Cookie": (
                            f"{SESSION_COOKIE}={session_id}; Path=/; HttpOnly; SameSite=Strict"
                        )
                    },
                )
                return
            if self._handle_road_get(path):
                return


            if path == "/tools":
                self._send_json(200, {"tools": self.server.state.registry.descriptors})
                return

            if path in {
                "/", "/index.html", "/app.js", "/style.css", "/road-camera.js",
                "/road-model.js", "/road-workplace.js", "/road.css",
                "/road-cartoon.css", "/road-sfx.js", "/road-game.js", "/i18n.js", "/favicon.ico",
            }:
                self._serve_web(path)
                return

            parts = path.strip("/").split("/") if path != "/" else []
            if len(parts) == 2 and parts[0] == "runs":
                self.server.state.controller.status(parts[1])
                session_id = self._require_session(require_csrf=False)
                result = self._require_run_owner(parts[1], session_id)
                self._send_json(200, result)
                return
            if len(parts) == 3 and parts[0] == "runs" and parts[2] == "report":
                self.server.state.controller.status(parts[1])
                session_id = self._require_session(require_csrf=False)
                result = self._require_run_owner(parts[1], session_id)
                from visionflow.outputs import render_report

                report = render_report(result).encode("utf-8")
                self._send_bytes(200, report, "text/markdown; charset=utf-8")
                return
            if len(parts) == 3 and parts[0] == "runs" and parts[2] == "evidence":
                session_id = self._require_session(require_csrf=False)
                result = self._require_run_owner(parts[1], session_id)
                query = parse_qs(parsed.query, keep_blank_values=True)
                references = query.get("ref", [])
                if len(references) != 1 or not references[0]:
                    raise _ApiFault(404, "evidence_not_found", "The evidence reference is not available.")
                payload, mime_type = self._read_evidence(result, references[0])
                self._send_bytes(200, payload, mime_type)
                return
            raise _ApiFault(404, "not_found", "The requested local resource does not exist.")
        except Exception as exc:
            self._handle_exception(exc)

    def _serve_web(self, path: str) -> None:
        if path == "/favicon.ico":
            self._send_bytes(204, b"", "image/x-icon")
            return
        mapping = {
            "/": "index.html",
            "/index.html": "index.html",
            "/app.js": "app.js",
            "/style.css": "style.css",
            "/road-camera.js": "road-camera.js",
            "/road-model.js": "road-model.js",
            "/road-workplace.js": "road-workplace.js",
            "/road.css": "road.css",
            "/road-cartoon.css": "road-cartoon.css",
            "/road-sfx.js": "road-sfx.js",
            "/road-game.js": "road-game.js",
            "/i18n.js": "i18n.js",
        }
        if path not in mapping:
            raise _ApiFault(404, "not_found", "The requested local resource does not exist.")
        filename = mapping[path]
        target = Path(__file__).resolve().parent / "web" / filename
        try:
            payload = target.read_bytes()
        except OSError as exc:
            raise _ApiFault(404, "web_asset_missing", "A VisionFlow interface asset is missing.") from exc
        content_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        if filename.endswith((".html", ".js", ".css")):
            content_type += "; charset=utf-8"
        headers = {}
        if filename == "index.html":
            headers["Content-Security-Policy"] = (
                "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' blob:; "
                "media-src 'self' blob:; connect-src 'self' blob:; object-src 'none'; base-uri 'none'; frame-ancestors 'none'"
            )
        self._send_bytes(200, payload, content_type, extra_headers=headers)

    def _read_evidence(self, result: Any, reference: str) -> tuple[bytes, str]:
        allowed = {
            finding.evidence_ref
            for finding in getattr(result, "findings", [])
            if isinstance(getattr(finding, "evidence_ref", None), str)
        }
        if reference not in allowed:
            raise _ApiFault(404, "evidence_not_found", "The evidence reference is not available.")
        relative = PurePosixPath(reference)
        if (
            relative.is_absolute()
            or not relative.parts
            or any(part in {"", ".", ".."} for part in relative.parts)
            or "\\" in reference
            or ":" in reference
            or "\x00" in reference
        ):
            raise _ApiFault(404, "evidence_not_found", "The evidence reference is not available.")
        root = self.server.state.evidence_root.resolve()
        try:
            target = root.joinpath(*relative.parts).resolve(strict=True)
            if not target.is_relative_to(root) or not target.is_file():
                raise ValueError("evidence path is outside the configured root")
            payload = target.read_bytes()
        except (OSError, RuntimeError, ValueError) as exc:
            raise _ApiFault(404, "evidence_not_found", "The evidence reference is not available.") from exc
        if payload.startswith(_PNG_SIGNATURE):
            return payload, "image/png"
        if payload.startswith(b"\xff\xd8\xff"):
            return payload, "image/jpeg"
        if payload.startswith((b"GIF87a", b"GIF89a")):
            return payload, "image/gif"
        if payload.startswith(b"RIFF") and payload[8:12] == b"WEBP":
            return payload, "image/webp"
        return payload, "application/octet-stream"

    def do_POST(self) -> None:
        try:
            self._check_request(require_origin=True)
            parsed = urlsplit(self.path)
            path = parsed.path
            session_id = self._require_session()
            if path == "/media":
                self._upload_media()
                return
            body = self._read_json(optional=path.endswith("/cancel"))
            if self._handle_road_post(path, body, session_id):
                return
            if path == "/validate":
                workflow = self._validate_workflow(body)
                self._send_json(200, {"valid": True, "name": workflow.name})
                return
            if path == "/runs":
                workflow = self._validate_workflow(body)
                self._check_public_source(
                    getattr(getattr(workflow, "input", None), "kind", None),
                    getattr(getattr(workflow, "input", None), "source", None),
                )
                road_source_id = self._authorize_workflow_source(session_id, workflow)
                result = self.server.state.controller.start(workflow)
                run_id = getattr(result, "run_id", None)
                if not isinstance(run_id, str) or not run_id:
                    raise _ApiFault(500, "invalid_controller_response", "The controller returned no run ID.")
                with self.server.state.lock:
                    if run_id in self.server.state.run_sessions:
                        raise _ApiFault(409, "run_id_conflict", "The controller returned a duplicate run ID.")
                    self.server.state.run_sessions[run_id] = session_id
                if road_source_id is not None:
                    try:
                        self._road_service().bind_run(session_id, road_source_id, run_id)
                    except Exception as exc:
                        try:
                            self.server.state.controller.cancel(run_id)
                        except Exception:
                            pass
                        with self.server.state.lock:
                            self.server.state.run_sessions.pop(run_id, None)
                        from visionflow.road_api import RoadSessionError
                        if isinstance(exc, RoadSessionError):
                            raise _ApiFault(exc.status, exc.code, str(exc)) from exc
                        raise
                self._send_json(202, result)
                return

            parts = path.strip("/").split("/") if path != "/" else []
            if len(parts) == 3 and parts[0] == "runs":
                run_id, action = parts[1], parts[2]
                if action == "cancel":
                    self._require_run_owner(run_id, session_id)
                    result = self._cancel_run(session_id, run_id)
                    self._send_json(200, result)
                    return
                if action == "input":
                    self._require_run_owner(run_id, session_id)
                    self._check_public_resume(body)
                    road_source_id = self._resume_source(body)
                    if road_source_id is not None:
                        self._authorize_road_source(session_id, road_source_id)
                    result = self.server.state.controller.resume(run_id, body)
                    if road_source_id is not None:
                        service = self._road_service()
                        existing = service.owner_source_for_run(run_id)
                        try:
                            if existing is None:
                                service.bind_run(session_id, road_source_id, run_id)
                            elif existing != (session_id, road_source_id):
                                service.rebind_run(session_id, road_source_id, run_id)
                        except Exception as exc:
                            try:
                                self.server.state.controller.cancel(run_id)
                            except Exception:
                                pass
                            from visionflow.road_api import RoadSessionError
                            if isinstance(exc, RoadSessionError):
                                raise _ApiFault(exc.status, exc.code, str(exc)) from exc
                            raise
                    self._send_json(200, result)
                    return
                if action in {"confirm", "reject"}:
                    current = self._require_run_owner(run_id, session_id)
                    store = self.server.state.approval_store
                    if store is None:
                        raise _ApiFault(409, "approval_unavailable", "No local approval store is configured.")
                    action_id = body.get("action_id")
                    if not isinstance(action_id, str) or not action_id:
                        raise _ApiFault(422, "invalid_approval", "action_id is required.")
                    if action == "confirm":
                        content_hash = body.get("content_hash")
                        if not isinstance(content_hash, str) or not content_hash:
                            raise _ApiFault(422, "invalid_approval", "content_hash is required.")
                        if current.status == "waiting_for_approval":
                            resumed = self.server.state.controller.resume(
                                run_id,
                                {
                                    "approved": True,
                                    "action_id": action_id,
                                    "content_hash": content_hash,
                                },
                            )
                            receipt = store.confirm(
                                run_id,
                                action_id,
                                content_hash,
                                datetime.now(timezone.utc),
                            )
                            self._send_json(
                                200,
                                {"receipt": receipt, "run": resumed, "sent": False},
                            )
                        elif current.status in {"completed", "failed", "cancelled"}:
                            already_confirmed = any(
                                isinstance(output, dict)
                                and output.get("type") == "approval_receipt"
                                and output.get("action_id") == action_id
                                and output.get("content_hash") == content_hash
                                for output in getattr(current, "outputs", [])
                            )
                            if not already_confirmed:
                                raise InvalidTransition(
                                    "terminal runs accept only an idempotent confirmed receipt"
                                )
                            receipt = store.confirm(
                                run_id,
                                action_id,
                                content_hash,
                                datetime.now(timezone.utc),
                            )
                            self._send_json(200, {"receipt": receipt, "sent": False})
                        else:
                            raise InvalidTransition("run is not waiting for approval")
                    else:
                        if current.status == "waiting_for_approval":
                            resumed = self.server.state.controller.resume(
                                run_id,
                                {
                                    "approved": False,
                                    "action_id": action_id,
                                    "content_hash": body.get("content_hash"),
                                },
                            )
                            self._send_json(
                                200,
                                {"rejected": True, "run": resumed, "sent": False},
                            )
                        elif current.status in {"completed", "failed", "cancelled"}:
                            store.reject(run_id, action_id, datetime.now(timezone.utc))
                            self._send_json(200, {"rejected": True, "sent": False})
                        else:
                            raise InvalidTransition("run is not waiting for approval")
                    return
            raise _ApiFault(404, "not_found", "The requested local resource does not exist.")
        except Exception as exc:
            self._handle_exception(exc)

    def _validate_workflow(self, data: dict[str, Any]):
        from visionflow.config import validate_workflow

        return validate_workflow(data, self.server.state.registry)

    def _upload_media(self) -> None:
        content_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
        if content_type != "application/octet-stream":
            raise _ApiFault(415, "binary_upload_required", "Media uploads must use application/octet-stream.")
        kind = self.headers.get("X-VisionFlow-Media-Kind", "")
        if kind not in {"image", "video"}:
            raise _ApiFault(422, "invalid_media_kind", "Media kind must be image or video.")
        length = self._read_length(MAX_UPLOAD_BYTES)
        if length == 0:
            raise _ApiFault(422, "empty_upload", "The media upload is empty.")
        root = self.server.state.root.resolve()
        uploads = self.server.state.uploads_root
        try:
            resolved_uploads = uploads.resolve(strict=True)
            if not resolved_uploads.is_relative_to(root):
                raise ValueError("upload directory escapes data directory")
        except (OSError, RuntimeError, ValueError) as exc:
            raise _ApiFault(500, "upload_storage_unavailable", "The local upload directory is unsafe.") from exc

        if self.server.state.public_demo:
            used = sum(item.stat().st_size for item in resolved_uploads.iterdir() if item.is_file())
            if used + length > self.server.state.public_upload_quota_bytes:
                # Drain the bounded body so the client reliably receives the reply.
                remaining = length
                while remaining:
                    chunk = self.rfile.read(min(64 * 1024, remaining))
                    if not chunk:
                        break
                    remaining -= len(chunk)
                raise _ApiFault(507, "demo_storage_full", "The public demo upload storage is full.")
        filename = "vf_" + secrets.token_hex(24) + ".bin"
        target = resolved_uploads / filename
        remaining = length
        try:
            with target.open("xb") as stream:
                while remaining:
                    chunk = self.rfile.read(min(64 * 1024, remaining))
                    if not chunk:
                        raise _ApiFault(400, "incomplete_body", "The media upload ended early.")
                    stream.write(chunk)
                    remaining -= len(chunk)
        except _ApiFault:
            try:
                target.unlink(missing_ok=True)
            except OSError:
                pass
            raise
        except OSError as exc:
            try:
                target.unlink(missing_ok=True)
            except OSError:
                pass
            raise _ApiFault(500, "upload_storage_unavailable", "The media upload could not be stored.") from exc
        reference = f"uploads/{filename}"
        self._send_json(201, {"source": reference, "kind": kind})


__all__ = ["MAX_JSON_BYTES", "MAX_UPLOAD_BYTES", "VisionFlowHTTPServer", "create_server"]
