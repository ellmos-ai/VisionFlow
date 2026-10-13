"""Command-line tools and client for the local VisionFlow server."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Sequence, TextIO

from visionflow.config import load_workflow
from visionflow.contracts import ConfigError
from visionflow.runtime import create_registry
from visionflow.api import create_server


DEFAULT_SERVER = "http://127.0.0.1:8765"


class _ServerUnavailable(Exception):
    pass


def _default_session_file(server_url: str) -> Path:
    digest = hashlib.sha256(server_url.encode("utf-8")).hexdigest()[:24]
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home()))
    elif os.environ.get("XDG_STATE_HOME"):
        base = Path(os.environ["XDG_STATE_HOME"])
    else:
        base = Path.home() / ".local" / "state"
    return base / "VisionFlow" / "sessions" / f"{digest}.json"


class _ApiClient:
    def __init__(self, server_url: str, session_file: Path | None = None) -> None:
        self.server_url, self.origin = _normalize_server_url(server_url)
        self.session_file = session_file or _default_session_file(self.server_url)
        self.cookie: str | None = None
        self.csrf_token: str | None = None
        self._load_session()

    def _load_session(self) -> None:
        try:
            data = json.loads(self.session_file.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return
        if (
            isinstance(data, dict)
            and data.get("server") == self.server_url
            and isinstance(data.get("cookie"), str)
            and data["cookie"].startswith("vf_session=")
            and isinstance(data.get("csrf_token"), str)
            and data["csrf_token"]
        ):
            self.cookie = data["cookie"]
            self.csrf_token = data["csrf_token"]

    def _save_session(self) -> None:
        path = self.session_file
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f"{path.name}.{os.getpid()}.tmp")
        payload = {
            "server": self.server_url,
            "cookie": self.cookie,
            "csrf_token": self.csrf_token,
        }
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
            0o600,
        )
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(payload, stream, ensure_ascii=False, separators=(",", ":"))
            stream.write("\n")
        try:
            os.chmod(temporary, 0o600)
        except OSError:
            pass
        os.replace(temporary, path)

    def _request_once(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None,
    ) -> tuple[int, Any, dict[str, str]]:
        encoded = None if payload is None else json.dumps(
            payload,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        ).encode("utf-8")
        headers = {
            "Accept": "application/json",
            "Origin": self.origin,
        }
        if encoded is not None:
            headers["Content-Type"] = "application/json"
        if self.cookie:
            headers["Cookie"] = self.cookie
        if self.csrf_token and method in {"POST", "PUT", "PATCH", "DELETE"}:
            headers["X-CSRF-Token"] = self.csrf_token
        request = urllib.request.Request(
            self.server_url + path,
            data=encoded,
            headers=headers,
            method=method,
        )
        try:
            response = urllib.request.urlopen(request, timeout=5)
        except urllib.error.HTTPError as exc:
            response = exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise _ServerUnavailable from exc
        raw = response.read()
        content_type = response.headers.get("Content-Type", "")
        if content_type.startswith("application/json"):
            try:
                value: Any = json.loads(raw.decode("utf-8"))
            except (UnicodeError, json.JSONDecodeError) as exc:
                raise _ServerUnavailable from exc
        else:
            value = raw.decode("utf-8", errors="replace")
        return response.status, value, {key.lower(): val for key, val in response.headers.items()}

    def _bootstrap(self) -> None:
        status, payload, headers = self._request_once("GET", "/session", None)
        if status != 200 or not isinstance(payload, dict):
            raise _ServerUnavailable
        cookie = headers.get("set-cookie", "").split(";", 1)[0]
        csrf = payload.get("csrf_token")
        if not cookie.startswith("vf_session=") or not isinstance(csrf, str) or not csrf:
            raise _ServerUnavailable
        self.cookie = cookie
        self.csrf_token = csrf
        self._save_session()

    def request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
    ) -> tuple[int, Any]:
        if self.cookie is None or self.csrf_token is None:
            self._bootstrap()
        status, value, _headers = self._request_once(method, path, payload)
        if (
            status == 403
            and isinstance(value, dict)
            and isinstance(value.get("error"), dict)
            and value["error"].get("code") == "session_required"
        ):
            self.cookie = None
            self.csrf_token = None
            self._bootstrap()
            status, value, _headers = self._request_once(method, path, payload)
        return status, value

    def request_bytes(self, path: str) -> tuple[int, bytes | Any, dict[str, str]]:
        if self.cookie is None or self.csrf_token is None:
            self._bootstrap()
        status, raw, headers = self._request_bytes_once(path)
        value: bytes | Any = raw
        if headers.get("content-type", "").startswith("application/json"):
            try:
                value = json.loads(raw.decode("utf-8"))
            except (UnicodeError, json.JSONDecodeError) as exc:
                raise _ServerUnavailable from exc
        if (
            status == 403
            and isinstance(value, dict)
            and isinstance(value.get("error"), dict)
            and value["error"].get("code") == "session_required"
        ):
            self.cookie = None
            self.csrf_token = None
            self._bootstrap()
            status, raw, headers = self._request_bytes_once(path)
            value = raw
            if headers.get("content-type", "").startswith("application/json"):
                try:
                    value = json.loads(raw.decode("utf-8"))
                except (UnicodeError, json.JSONDecodeError) as exc:
                    raise _ServerUnavailable from exc
        return status, value, headers

    def _request_bytes_once(self, path: str) -> tuple[int, bytes, dict[str, str]]:
        headers = {"Accept": "video/mp4, application/json", "Origin": self.origin}
        if self.cookie:
            headers["Cookie"] = self.cookie
        request = urllib.request.Request(self.server_url + path, headers=headers, method="GET")
        try:
            response = urllib.request.urlopen(request, timeout=10)
        except urllib.error.HTTPError as exc:
            response = exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise _ServerUnavailable from exc
        return response.status, response.read(), {key.lower(): val for key, val in response.headers.items()}


def _normalize_server_url(value: str) -> tuple[str, str]:
    try:
        parsed = urllib.parse.urlsplit(value)
        host = (parsed.hostname or "").lower()
        port = parsed.port or (80 if parsed.scheme == "http" else None)
    except ValueError as exc:
        raise ValueError("server URL is invalid") from exc
    if (
        parsed.scheme != "http"
        or host not in {"127.0.0.1", "localhost", "::1"}
        or parsed.username is not None
        or parsed.password is not None
        or port is None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("server URL must target a local loopback HTTP server")
    display_host = f"[{host}]" if ":" in host else host
    origin = f"http://{display_host}:{port}"
    return origin, origin


def _write_json(value: Any, stream: TextIO | None = None) -> None:
    if stream is None:
        stream = sys.stdout
    stream.write(json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")))
    stream.write("\n")


def _write_error(code: str, message: str) -> int:
    _write_json({"error": {"code": code, "message": message}}, sys.stderr)
    return 2


def _configure_console_encoding() -> None:
    """Write UTF-8 JSON from real console streams without disturbing test streams."""
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name, None)
        # Capture wrappers and StringIO instances belong to their caller. The
        # interpreter's original streams are the command-line console/pipe.
        if stream is None or stream is not getattr(sys, f"__{name}__", None):
            continue
        reconfigure = getattr(stream, "reconfigure", None)
        if not callable(reconfigure):
            continue
        try:
            reconfigure(encoding="utf-8")
        except (OSError, TypeError, ValueError):
            # Some embedded or closed streams do not permit reconfiguration.
            continue


def _client_options(
    parser: argparse.ArgumentParser,
    *,
    server_default: str | None = DEFAULT_SERVER,
) -> None:
    server_help = (
        "Loopback-Server für eine optionale Remote-Abfrage"
        if server_default is None
        else "Loopback-Server (Standard: 127.0.0.1:8765)"
    )
    parser.add_argument(
        "--server",
        default=server_default,
        help=server_help,
    )
    parser.add_argument("--session-file", type=Path, help="Lokale Datei für die wiederverwendete CLI-Sitzung")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="visionflow", description="Lokale VisionFlow-Workflows verwalten.")
    commands = parser.add_subparsers(dest="command", required=True)

    validate = commands.add_parser("validate", help="Workflow lokal validieren")
    validate.add_argument("workflow", type=Path)

    serve = commands.add_parser("serve", help="Den lokalen VisionFlow-Server starten")
    serve.add_argument("--host", choices=("127.0.0.1", "localhost", "::1"), default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8765)
    serve.add_argument("--data-dir", type=Path, default=Path(".visionflow-data"))
    serve.add_argument(
        "--camera",
        action="append",
        default=[],
        metavar="NAME=INDEX",
        help="Eine einzelne ausdrücklich freigegebene Kameraquelle; wiederholbar",
    )
    serve.add_argument(
        "--public-demo",
        action="store_true",
        help=(
            "Hosted jury demo behind a reverse proxy: only road simulations and media "
            "uploaded to this server are readable, uploads are capped and live road "
            "sessions are limited. The bind stays loopback-only."
        ),
    )
    serve.add_argument(
        "--max-live-sessions",
        type=int,
        default=4,
        help="Live road sessions allowed at once with --public-demo (default 4)",
    )
    serve.add_argument(
        "--allow-gemini-costs",
        action="store_true",
        help=(
            "Native Gemini-Videoanfragen ausdrücklich erlauben (höchstens zwei Anfragen, "
            "jeweils bis 30 Sekunden und 20 MiB; Providerkosten können entstehen; "
            "erfordert GEMINI_API_KEY)"
        ),
    )

    run = commands.add_parser("run", help="Workflow an einen laufenden lokalen Server senden")
    run.add_argument("workflow", type=Path)
    _client_options(run)

    status = commands.add_parser("status", help="Laufstatus abfragen")
    status.add_argument("run_id")
    _client_options(status)

    cancel = commands.add_parser("cancel", help="Lauf abbrechen")
    cancel.add_argument("run_id")
    _client_options(cancel)

    resume = commands.add_parser("input", help="Auf eine Eingabeanforderung antworten (JSON-Objekt)")
    resume.add_argument("run_id")
    resume.add_argument("payload", help="JSON-Objekt oder @Pfad zu einer JSON-Datei")
    _client_options(resume)

    confirm = commands.add_parser("confirm", help="Einen lokalen Entwurf bestätigen; es wird nichts versendet")
    confirm.add_argument("run_id")
    confirm.add_argument("action_id")
    confirm.add_argument("content_hash")
    _client_options(confirm)

    reject = commands.add_parser("reject", help="Einen lokalen Entwurf ablehnen")
    reject.add_argument("run_id")
    reject.add_argument("action_id")
    reject.add_argument("content_hash")
    _client_options(reject)

    tools = commands.add_parser("tools", help="Verfügbare Werkzeugbeschreibungen abrufen")
    _client_options(tools, server_default=None)

    demo = commands.add_parser("demo", help="Lokale Offline-Demonstration")
    demo_commands = demo.add_subparsers(dest="demo_command", required=True)
    demo_run = demo_commands.add_parser("run", help="Die zwei echten Fixture-Szenarien ausführen")
    demo_run.add_argument("--output-dir", type=Path, required=True)

    road = commands.add_parser("road", help="Lokale Straßenkamera und Aufzeichnungen verwalten")
    road_commands = road.add_subparsers(dest="road_command", required=True)
    road_create = road_commands.add_parser("create", help="Eine private Simulationsquelle starten")
    road_create.add_argument("--seed", type=int, default=42)
    _client_options(road_create)
    road_inject = road_commands.add_parser("inject", help="Ein sichtbares Szenarioereignis auslösen")
    road_inject.add_argument("source_id")
    road_inject.add_argument("event", choices=("wrong_way", "bird", "leaf"))
    _client_options(road_inject)
    for name, help_text in (
        ("reset", "Eine neue Quelle starten und die alte Aufnahme beenden"),
        ("stop", "Quelle beenden und den vollständigen Clip sichern"),
        ("recording", "Aufnahmestatus einer Quelle anzeigen"),
    ):
        command = road_commands.add_parser(name, help=help_text)
        command.add_argument("source_id")
        _client_options(command)
    road_clip = road_commands.add_parser("clip", help="Einen gespeicherten Clip herunterladen")
    road_clip.add_argument("clip_id")
    road_clip.add_argument("--output", type=Path, required=True)
    _client_options(road_clip)

    road_case = road_commands.add_parser("case", help="Einen Straßenfall prüfen und entscheiden")
    case_commands = road_case.add_subparsers(dest="case_command", required=True)
    case_start = case_commands.add_parser("start", help="Eine Analyse für eine Quelle starten")
    case_start.add_argument("source_id")
    case_start.add_argument("--mode", choices=("fixture", "gemini_video"), default="fixture")
    case_start.add_argument("--fixture-profile")
    _client_options(case_start)
    case_status = case_commands.add_parser("status", help="Fallstatus und Modellbewertung abrufen")
    case_status.add_argument("case_id")
    _client_options(case_status)
    case_decide = case_commands.add_parser("decide", help="Eine Fallentscheidung bestätigen oder ablehnen")
    case_decide.add_argument("case_id")
    case_decide.add_argument("--version", type=int, required=True)
    case_decide.add_argument("--content-hash", required=True)
    case_decide.add_argument("--decision", choices=("confirm", "reject"), required=True)
    case_decide.add_argument("--idempotency-key", required=True)
    _client_options(case_decide)
    case_cancel = case_commands.add_parser("cancel", help="Eine laufende Fallprüfung abbrechen")
    case_cancel.add_argument("case_id")
    _client_options(case_cancel)
    road_inbox = road_commands.add_parser("inbox", help="Owner-eigenen Demo-Posteingang anzeigen")
    _client_options(road_inbox)

    return parser


def _camera_mapping(values: Sequence[str]) -> dict[str, int]:
    mapping: dict[str, int] = {}
    for value in values:
        name, separator, raw_index = value.partition("=")
        if not separator or not name or not name.replace("-", "").replace("_", "").isalnum():
            raise ValueError("--camera muss die Form NAME=INDEX haben")
        try:
            index = int(raw_index, 10)
        except ValueError as exc:
            raise ValueError("--camera INDEX muss eine nicht-negative Ganzzahl sein") from exc
        if index < 0 or name in mapping:
            raise ValueError("--camera INDEX muss eindeutig und nicht-negativ sein")
        mapping[name] = index
    return mapping


def _serve(args: argparse.Namespace) -> int:
    runtime = None
    if args.public_demo and (args.camera or args.allow_gemini_costs):
        return _write_error(
            "public_demo_conflict",
            "--public-demo cannot be combined with --camera or --allow-gemini-costs.",
        )
    if args.public_demo and args.max_live_sessions <= 0:
        return _write_error("invalid_max_live_sessions", "--max-live-sessions must be positive.")
    try:
        from visionflow.road_runtime import build_road_runtime
        cameras = _camera_mapping(args.camera)
        public = {"max_live_sessions": args.max_live_sessions} if args.public_demo else {}
        runtime = build_road_runtime(
            args.data_dir,
            allowed_camera_sources=cameras,
            allow_native_costs=args.allow_gemini_costs,
            **public,
        )
        server = create_server(
            runtime.controller,
            host=args.host,
            port=args.port,
            data_dir=args.data_dir,
            evidence_root=getattr(runtime.controller, "evidence_root", args.data_dir / "evidence"),
            registry=getattr(runtime.controller, "registry", None),
            approval_store=getattr(runtime.controller, "approval_store", None),
            road_sessions=runtime.road_sessions,
            road_cases=runtime.road_cases,
            road_unavailable_reason=runtime.road_unavailable_reason,
            **({"public_demo": True} if args.public_demo else {}),
        )
    except (ConfigError, OSError, ValueError) as exc:
        if runtime is not None:
            runtime.close()
        return _write_error("server_start_failed", "Der lokale VisionFlow-Server konnte nicht starten.")

    host, port = server.server_address[:2]
    display_host = f"[{host}]" if ":" in host else host
    _write_json({"status": "serving", "url": f"http://{display_host}:{port}"})
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0

def _load_json_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise ConfigError(f"could not read workflow configuration: {exc}") from exc
    if not isinstance(value, dict):
        raise ConfigError("workflow configuration must be a JSON object")
    return value


def _validate(path: Path) -> int:
    try:
        config = load_workflow(path, create_registry())
    except (ConfigError, OSError, ValueError) as exc:
        return _write_error("configuration_invalid", f"configuration invalid: {exc}")
    _write_json({"valid": True, "name": config.name})
    return 0


def _client(args: argparse.Namespace) -> _ApiClient:
    return _ApiClient(args.server, getattr(args, "session_file", None))


def _call_api(args: argparse.Namespace, method: str, path: str, payload: dict[str, Any] | None = None) -> int:
    try:
        status, result = _client(args).request(method, path, payload)
    except _ServerUnavailable:
        return _write_error("server_unavailable", "No local VisionFlow server answered at the configured address.")
    except (OSError, ValueError, TypeError) as exc:
        return _write_error("client_error", str(exc))
    _write_json(result, sys.stdout if status < 400 else sys.stderr)
    return 0 if status < 400 else 2


def _input_payload(value: str) -> dict[str, Any]:
    if value.startswith("@"):
        try:
            text = Path(value[1:]).read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            raise ValueError(f"input JSON file could not be read: {exc}") from exc
    else:
        text = value
    try:
        payload = json.loads(text)
    except (json.JSONDecodeError, ValueError) as exc:
        raise ValueError(f"input must be a JSON object: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError("input must be a JSON object")
    return payload


def _demo_run(output_dir: Path) -> int:
    try:
        from visionflow.demo import run_demo

        result = run_demo(output_dir)
        if not isinstance(result, dict):
            return _write_error("demo_failed", "The demo returned no structured result.")
    except Exception as exc:
        return _write_error("demo_failed", str(exc) or "The local demo failed.")
    _write_json(result)
    return 0



def _road_command(args: argparse.Namespace) -> int:
    source_id = getattr(args, "source_id", None)
    if args.road_command == "inbox":
        return _call_api(args, "GET", "/road/inbox")
    if args.road_command == "case":
        case_id = getattr(args, "case_id", None)
        if args.case_command == "start":
            if args.mode == "gemini_video" and args.fixture_profile is not None:
                return _write_error(
                    "fixture_profile_not_allowed",
                    "Native Fälle verwenden kein Fixture-Profil.",
                )
            payload = {"source_id": args.source_id, "mode": args.mode}
            if args.fixture_profile is not None:
                payload["fixture_profile"] = args.fixture_profile
            return _call_api(args, "POST", "/road/cases", payload)
        encoded_case = urllib.parse.quote(case_id, safe="")
        if args.case_command == "status":
            return _call_api(args, "GET", f"/road/cases/{encoded_case}")
        if args.case_command == "cancel":
            return _call_api(args, "POST", f"/road/cases/{encoded_case}/cancel", {})
        if args.case_command == "decide":
            return _call_api(
                args,
                "POST",
                f"/road/cases/{encoded_case}/decision",
                {
                    "version": args.version,
                    "content_hash": args.content_hash,
                    "decision": args.decision,
                    "idempotency_key": args.idempotency_key,
                },
            )
        return _write_error("invalid_arguments", "Der Fallbefehl ist nicht verfügbar.")
    if args.road_command == "create":
        return _call_api(args, "POST", "/road/sessions", {"seed": args.seed})
    if args.road_command == "inject":
        path = f"/road/sessions/{urllib.parse.quote(source_id, safe='')}/events"
        return _call_api(args, "POST", path, {"event": args.event})
    if args.road_command in {"reset", "stop"}:
        action = args.road_command
        path = f"/road/sessions/{urllib.parse.quote(source_id, safe='')}/{action}"
        return _call_api(args, "POST", path, {})
    if args.road_command == "recording":
        path = f"/road/sessions/{urllib.parse.quote(source_id, safe='')}/recording"
        return _call_api(args, "GET", path)
    if args.road_command == "clip":
        path = f"/road/clips/{urllib.parse.quote(args.clip_id, safe='')}"
        try:
            status, payload, headers = _client(args).request_bytes(path)
        except _ServerUnavailable:
            return _write_error("server_unavailable", "Am lokalen VisionFlow-Server kam keine Antwort an.")
        except (OSError, ValueError, TypeError) as exc:
            return _write_error("client_error", str(exc))
        if status >= 400:
            if isinstance(payload, dict):
                _write_json(payload, sys.stderr)
            else:
                return _write_error("clip_download_failed", "Der Clip konnte nicht heruntergeladen werden.")
            return 2
        if not isinstance(payload, bytes) or not payload:
            return _write_error("clip_download_failed", "Der Server lieferte keine Clipdaten.")
        expected_hash = headers.get("x-clip-sha256")
        actual_hash = hashlib.sha256(payload).hexdigest()
        if expected_hash and expected_hash != actual_hash:
            return _write_error("clip_integrity", "Die heruntergeladene Clip-Prüfsumme stimmt nicht.")
        output = args.output.expanduser()
        created = False
        try:
            output.parent.mkdir(parents=True, exist_ok=True)
            stream = output.open("xb")
            created = True
            with stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
        except FileExistsError:
            return _write_error("output_exists", "Die Zieldatei ist bereits vorhanden und wurde nicht überschrieben.")
        except OSError as exc:
            if created:
                try:
                    output.unlink(missing_ok=True)
                except OSError:
                    pass
            return _write_error("output_write_failed", str(exc))
        _write_json({
            "clip_id": args.clip_id,
            "path": str(output),
            "bytes": len(payload),
            "sha256": actual_hash,
        })
        return 0
    return _write_error("invalid_arguments", "Der Straßenkamera-Befehl ist nicht verfügbar.")

def main(argv: Sequence[str] | None = None) -> int:
    _configure_console_encoding()
    args = _parser().parse_args(argv)
    if args.command == "validate":
        return _validate(args.workflow)
    if args.command == "serve":
        return _serve(args)
    if args.command == "road":
        return _road_command(args)

    if args.command == "demo":
        return _demo_run(args.output_dir)
    if args.command == "run":
        try:
            payload = _load_json_object(args.workflow)
        except (ConfigError, ValueError) as exc:
            return _write_error("configuration_invalid", f"configuration invalid: {exc}")
        return _call_api(args, "POST", "/runs", payload)
    if args.command == "status":
        return _call_api(args, "GET", f"/runs/{urllib.parse.quote(args.run_id, safe='')}")
    if args.command == "cancel":
        return _call_api(args, "POST", f"/runs/{urllib.parse.quote(args.run_id, safe='')}/cancel", {})
    if args.command == "input":
        try:
            payload = _input_payload(args.payload)
        except ValueError as exc:
            return _write_error("invalid_input", str(exc))
        return _call_api(
            args,
            "POST",
            f"/runs/{urllib.parse.quote(args.run_id, safe='')}/input",
            payload,
        )
    if args.command in {"confirm", "reject"}:
        payload = {"action_id": args.action_id, "content_hash": args.content_hash}
        action = "confirm" if args.command == "confirm" else "reject"
        return _call_api(
            args,
            "POST",
            f"/runs/{urllib.parse.quote(args.run_id, safe='')}/{action}",
            payload,
        )
    if args.command == "tools":
        if args.server is None:
            if args.session_file is not None:
                return _write_error(
                    "invalid_arguments",
                    "--session-file requires an explicit --server for remote tool lookup.",
                )
            descriptors = create_registry().descriptors
            _write_json(
                {
                    "tools": [
                        {
                            "name": descriptor.name,
                            "description": descriptor.description,
                            "input_kinds": list(descriptor.input_kinds),
                        }
                        for descriptor in descriptors
                    ]
                }
            )
            return 0
        return _call_api(args, "GET", "/tools")
    return _write_error("unknown_command", "The requested CLI command is unsupported.")


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["DEFAULT_SERVER", "main"]
