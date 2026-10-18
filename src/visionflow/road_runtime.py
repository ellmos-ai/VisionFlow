"""Public composition root for the optional local road-review runtime."""

from __future__ import annotations

import shutil
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any
from collections.abc import Callable

from .decision_policy import DecisionPolicy, DecisionStore
from .model_exchange import ModelExchangeStore
from .road_api import ROAD_SOURCE_PREFIX, RoadSessionService, make_source_override
from .road_cases import RoadCaseService, RoadModelRouter
from .runtime import create_controller
from .simulated_authority import SimulatedAuthorityInbox
from .simulation import SimulationStore
from .retention import RetentionManager, RetentionPolicy


@dataclass(slots=True)
class RoadRuntime:
    """Owned runtime components used by both ``serve`` and integration tests."""

    controller: Any
    road_sessions: RoadSessionService | None
    road_cases: RoadCaseService | None
    road_unavailable_reason: str | None
    retention: RetentionManager | None = None

    def close(self) -> None:
        if self.road_cases is not None:
            self.road_cases.close()
        if self.road_sessions is not None:
            self.road_sessions.close()


class SystemClock:
    """Wall-clock display and monotonic deadlines for local review decisions."""

    @staticmethod
    def monotonic() -> float:
        return time.monotonic()

    @staticmethod
    def utcnow() -> datetime:
        return datetime.now(timezone.utc)


def build_road_runtime(
    data_dir: Path | str,
    *,
    ffmpeg_path: Path | str | None = None,
    allowed_camera_sources: dict[str, int] | None = None,
    gemini_api_key: str | None = None,
    allow_native_costs: bool = False,
    max_live_sessions: int | None = None,
    retention_policy: RetentionPolicy | None = None,
    retention_clock: Callable[[], float] = time.monotonic,
) -> RoadRuntime:
    """Build a controller with explicit road routing and optional recording.

    The ordinary image and motion workflows keep the existing fixture adapter
    through ``RoadModelRouter``'s default fallback. Native video is available
    only when the caller separately enables provider costs and supplies a key.
    """
    if type(allow_native_costs) is not bool:
        raise ValueError("allow_native_costs must be a boolean")
    if retention_policy is not None and allow_native_costs:
        raise ValueError("hosted retention uses the bounded offline road workflow only")
    cameras = {} if allowed_camera_sources is None else dict(allowed_camera_sources)
    ffmpeg = str(ffmpeg_path) if ffmpeg_path is not None else shutil.which("ffmpeg")
    unavailable_reason: str | None
    if ffmpeg is None:
        unavailable_reason = "ffmpeg_required"
    elif not _has_h264_encoder(ffmpeg):
        unavailable_reason = "h264_encoder_required"
    else:
        unavailable_reason = None

    service_holder: dict[str, RoadSessionService] = {}
    controller_ref: dict[str, Any] = {}
    case_holder: dict[str, RoadCaseService] = {}
    recording_holder: dict[str, Any] = {}

    def cleanup(owner: str, source: str) -> None:
        cases = case_holder.get("cases")
        recordings = recording_holder.get("recordings")
        service = service_holder.get("service")
        run_ids = set(cases.source_run_ids(owner, source) if cases is not None else [])
        run_ids.update(controller_ref["controller"].run_ids_for_source(source))
        # First remove every component's files, then release any references.
        # A partial failure retains all maps and the original conservative charge.
        for run_id in run_ids:
            controller_ref["controller"].delete_run_files(run_id)
        if recordings is not None:
            recordings.delete_source_files(owner, source)
        for run_id in run_ids:
            controller_ref["controller"].forget_run(run_id)
        if recordings is not None:
            recordings.forget_source(owner, source)
        if cases is not None:
            cases.forget_source(owner, source)
        if service is not None:
            service.forget_source(owner, source)

    retention = (RetentionManager(retention_policy, cleanup=cleanup, clock=retention_clock)
                 if retention_policy is not None else None)
    native_transport = None
    if unavailable_reason is None and allow_native_costs:
        try:
            from .gemini_video import GeminiVideoTransport

            evidence_reader = _case_evidence_reader(
                lambda: service_holder.get("service"),
                lambda: controller_ref.get("controller"),
                lambda: controller_ref.get("evidence_root"),
            )
            native_transport = GeminiVideoTransport(
                api_key=gemini_api_key,
                cost_approved=True,
                evidence_reader=evidence_reader,
            )
        except ValueError:
            # No credential or unusable native configuration leaves the offline
            # model path available. Never print the configuration or exception.
            native_transport = None

    router = RoadModelRouter(native_transport=native_transport)
    source_override = None
    if unavailable_reason is None:
        source_override = make_source_override(lambda: service_holder.get("service"))

    controller = create_controller(
        data_dir,
        allowed_camera_sources=cameras,
        source_override=source_override,
        model_factory=router,
        samples=True,
        # Hosted retention budgets road sources; sample runs are bounded by the API.
        **({"retention": retention,
            "retention_scope": lambda source: source.startswith(ROAD_SOURCE_PREFIX)} if retention else {}),
    )
    controller_ref["controller"] = controller
    controller_ref["evidence_root"] = Path(
        getattr(controller, "evidence_root", Path(data_dir) / "evidence")
    ).expanduser().resolve()

    if unavailable_reason is not None:
        return RoadRuntime(controller, None, None, unavailable_reason)

    from .recording import RecordingStore

    recordings = RecordingStore(Path(data_dir) / "road-recordings", Path(ffmpeg), retention=retention)
    recording_holder["recordings"] = recordings
    sessions = SimulationStore(max_live=max_live_sessions)
    road_sessions: RoadSessionService | None = None
    road_cases: RoadCaseService | None = None
    try:
        road_sessions = RoadSessionService(sessions, recordings, controller, retention=retention)
        service_holder["service"] = road_sessions
        decisions = DecisionStore(
            DecisionPolicy(confidence_threshold=0.90, countdown_seconds=60.0),
            SimulatedAuthorityInbox(),
            SystemClock(),
            max_idempotency_keys=64 if retention else None,
            max_snapshot_bytes=65536 if retention else None,
        )
        road_cases = RoadCaseService(
            road_sessions,
            controller,
            recordings,
            ModelExchangeStore(max_record_bytes=65536 if retention else None),
            decisions,
            retention=retention,
        )
        case_holder["cases"] = road_cases
        if retention:
            retention.start()
    except Exception:
        if road_cases is not None:
            road_cases.close()
        if road_sessions is not None:
            road_sessions.close()
        else:
            sessions.close()
        raise

    return RoadRuntime(controller, road_sessions, road_cases, None, retention)


def _has_h264_encoder(executable: str) -> bool:
    try:
        result = subprocess.run(
            [executable, "-hide_banner", "-encoders"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0 and "libx264" in (result.stdout + result.stderr)


def _case_evidence_reader(session_lookup, controller_lookup, root_lookup):
    def read(reference: str) -> bytes:
        if type(reference) is not str or "\\" in reference or ":" in reference or "\x00" in reference:
            raise ValueError("invalid evidence reference")
        relative = PurePosixPath(reference)
        if (
            relative.is_absolute()
            or len(relative.parts) < 2
            or any(part in {"", ".", ".."} for part in relative.parts)
        ):
            raise ValueError("invalid evidence reference")

        sessions = session_lookup()
        controller = controller_lookup()
        root = root_lookup()
        if sessions is None or controller is None or root is None:
            raise FileNotFoundError("evidence runtime is unavailable")
        run_id = relative.parts[0]
        binding = sessions.owner_source_for_run(run_id)
        if (
            not isinstance(binding, tuple)
            or len(binding) != 2
            or not all(type(item) is str and item for item in binding)
        ):
            raise FileNotFoundError("evidence run is unavailable")
        owner_id, source_id = binding
        sessions.get(owner_id, source_id)
        result = controller.status(run_id)
        allowed = {
            finding.evidence_ref
            for finding in getattr(result, "findings", [])
            if isinstance(getattr(finding, "evidence_ref", None), str)
        }
        if reference not in allowed:
            raise FileNotFoundError("evidence reference is unavailable")

        root_path = Path(root).resolve()
        target = root_path.joinpath(*relative.parts).resolve(strict=True)
        if not target.is_relative_to(root_path) or not target.is_file():
            raise FileNotFoundError("evidence reference is unavailable")
        return target.read_bytes()

    return read


__all__ = ["RoadRuntime", "SystemClock", "build_road_runtime"]
