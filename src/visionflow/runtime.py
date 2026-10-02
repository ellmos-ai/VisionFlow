"""Default VisionFlow tools and local controller assembly."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from copy import deepcopy
from pathlib import Path, PurePosixPath
from typing import Any

from visionflow.contracts import ConfigError, Frame, InputConfig, ModelDecision, WorkflowConfig
from visionflow.registry import ToolDescriptor, ToolRegistry
from visionflow.tools.direction import direction_handler

from visionflow.tools.motion import MotionAnalyzer
from visionflow.tools.quality import analyze_quality


def create_registry() -> ToolRegistry:
    """Return the real OpenCV quality and motion handlers used by runs."""
    registry = ToolRegistry()
    registry.register(
        ToolDescriptor(
            "quality",
            description="Misst Schärfe und mittlere Grauwerthelligkeit eines Einzelbilds.",
            input_kinds=("image",),
        ),
        _quality_handler,
    )
    registry.register(
        ToolDescriptor(
            "motion",
            description="Vergleicht aufeinanderfolgende Bilder auf sichtbare Bewegung.",
            input_kinds=("video", "camera"),
        ),
        _motion_handler,
    )
    registry.register(
        ToolDescriptor(
            "direction",
            description="Prüft sichtbare Bewegung gegen konfigurierte Spurvektoren.",
            input_kinds=("video", "camera"),
        ),
        direction_handler,
    )
    return registry


def _quality_handler(
    frame: Frame,
    params: dict[str, Any],
    context: dict[str, Any],
) -> dict[str, float]:
    if not isinstance(frame, Frame):
        raise TypeError("quality handler requires a Frame")
    return analyze_quality(frame.image)


def _motion_handler(
    frame: Frame,
    params: dict[str, Any],
    context: dict[str, Any],
) -> dict[str, Any]:
    if not isinstance(frame, Frame):
        raise TypeError("motion handler requires a Frame")
    if not isinstance(context, dict):
        raise TypeError("tool context must be a dictionary")
    state = context.get("state")
    if not isinstance(state, dict):
        raise ValueError("motion analysis requires per-run context['state']")
    analyzer = state.get("motion_analyzer")
    if analyzer is None:
        analyzer = MotionAnalyzer()
        state["motion_analyzer"] = analyzer
    if not isinstance(analyzer, MotionAnalyzer):
        raise ValueError("context['state']['motion_analyzer'] has an invalid value")
    return analyzer.update(frame.image)


def create_controller(
    data_dir: Path | str,
    *,
    allowed_camera_sources: Mapping[str, int] | None = None,
    fixture_decisions: Sequence[ModelDecision | dict[str, Any]]
    | Callable[[WorkflowConfig], Sequence[ModelDecision | dict[str, Any]]]
    | None = None,
    clock: Any = None,
    source_override: Callable[[InputConfig, int], Any | None] | None = None,
):
    """Build a controller whose media, model, evidence, and approval state is local.

    Controller and fixture-model imports are deferred so registry-only tools can be
    used while optional orchestration modules are being assembled.
    """
    from visionflow.approval import ApprovalStore
    from visionflow.controller import Controller
    from visionflow import media
    from visionflow.model import FixtureModelAdapter

    root = Path(data_dir).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    evidence_root = root / "evidence"
    uploads_root = root / "uploads"
    uploads_root.mkdir(parents=True, exist_ok=True)
    resolved_uploads_root = uploads_root.resolve(strict=True)
    if not resolved_uploads_root.is_relative_to(root):
        raise ConfigError("upload directory escapes the configured data directory")
    permitted_cameras = dict(allowed_camera_sources or {})
    if any(
        not isinstance(name, str)
        or not name
        or isinstance(device_id, bool)
        or not isinstance(device_id, int)
        or device_id < 0
        for name, device_id in permitted_cameras.items()
    ):
        raise ConfigError("allowed camera sources must map names to non-negative device IDs")

    if source_override is not None and not callable(source_override):
        raise ConfigError("source_override must be callable")

    def source_factory(input_config: InputConfig, max_frames: int):
        if not isinstance(input_config, InputConfig):
            raise ConfigError("input_config must be an InputConfig")
        if source_override is not None:
            overridden = source_override(input_config, max_frames)
            if overridden is not None:
                try:
                    valid = (
                        getattr(overridden, "source_id", None) == input_config.source
                        and callable(getattr(overridden, "read", None))
                        and callable(getattr(overridden, "close", None))
                    )
                except Exception:
                    valid = False
                if not valid:
                    closer = getattr(overridden, "close", None)
                    if callable(closer):
                        try:
                            closer()
                        except Exception:
                            pass
                    raise ConfigError(
                        "source_override must return a source for the configured source "
                        "with read() and close()"
                    )
                return overridden

        source = input_config.source
        if source.startswith("uploads/"):
            relative = PurePosixPath(source)
            if (
                relative.is_absolute()
                or len(relative.parts) != 2
                or relative.parts[0] != "uploads"
                or any(part in {"", ".", ".."} for part in relative.parts)
                or "\\" in source
                or ":" in source
            ):
                raise ConfigError("uploaded media reference is invalid")
            candidate = (root / Path(*relative.parts)).resolve(strict=True)
            if not candidate.is_relative_to(resolved_uploads_root):
                raise ConfigError("uploaded media reference escapes the upload directory")
            if not candidate.is_file():
                raise ConfigError("uploaded media reference is not a file")
            inner_config = InputConfig(input_config.kind, str(candidate))
            inner = media.open_source(
                inner_config,
                max_frames,
                allowed_camera_sources=permitted_cameras,
            )
            return _SourceIdView(inner, source)
        return media.open_source(
            input_config,
            max_frames,
            allowed_camera_sources=permitted_cameras,
        )

    def model_adapter_factory(config: WorkflowConfig):
        if not isinstance(config, WorkflowConfig):
            raise ConfigError("model adapter factory requires a WorkflowConfig")
        if callable(fixture_decisions):
            decisions = list(fixture_decisions(deepcopy(config)))
        elif fixture_decisions is not None:
            decisions = list(deepcopy(fixture_decisions))
        else:
            uses_motion = any(
                node.type == "analyze" and node.params.get("tool") == "motion"
                for node in config.nodes
            )
            authority = next(
                (node for node in config.nodes if node.type == "observe"),
                None,
            )
            if uses_motion and authority is not None:
                duration = min(2.0, float(authority.params["duration_seconds"]))
                sample_fps = min(1.0, float(authority.params["sample_fps"]))
                decisions = [
                    ModelDecision(
                        "observe",
                        {
                            "source_id": config.input.source,
                            "region": deepcopy(authority.params.get("region")),
                            "duration_seconds": duration,
                            "sample_fps": sample_fps,
                            "question": "Weitere Bewegung prüfen",
                        },
                    ),
                    ModelDecision("report", {}),
                ]
            else:
                decisions = [ModelDecision("report", {})]
        return FixtureModelAdapter(decisions=decisions)

    controller_options: dict[str, Any] = {
        "model_adapter": model_adapter_factory,
        "source_factory": source_factory,
        "evidence_root": evidence_root,
        "approval_store": ApprovalStore(),
    }
    if clock is not None:
        controller_options["clock"] = clock
    controller = Controller(
        create_registry(),
        **controller_options,
    )
    return controller


class _SourceIdView:
    """Keep uploaded references stable while delegating media IO to OpenCV."""

    def __init__(self, source: Any, public_source_id: str) -> None:
        self._source = source
        self.source_id = public_source_id
        self._next_frame = 0

    def read(self) -> Frame | None:
        return self._wrap_frame(self._source.read())

    def read_at(self, timestamp_seconds: float) -> Frame | None:
        """Delegate absolute video-time reads; cameras retain their wall-clock read."""
        reader = getattr(self._source, "read_at", None)
        if not callable(reader):
            return self.read()
        return self._wrap_frame(reader(timestamp_seconds))

    @property
    def native_fps(self) -> float | None:
        return getattr(self._source, "native_fps", None)

    @property
    def timeline_position_seconds(self) -> float | None:
        return getattr(self._source, "timeline_position_seconds", None)

    @property
    def observation_timeline_position_seconds(self) -> float | None:
        return getattr(self._source, "observation_timeline_position_seconds", None)

    @observation_timeline_position_seconds.setter
    def observation_timeline_position_seconds(self, value: float | None) -> None:
        setattr(self._source, "observation_timeline_position_seconds", value)

    def _wrap_frame(self, frame: Frame | None) -> Frame | None:
        if frame is None:
            return None
        if not isinstance(frame, Frame):
            raise TypeError("media source must return a Frame or None")
        native_index = frame.frame_id.rsplit(":", 1)[-1]
        if not native_index.isdecimal():
            native_index = str(self._next_frame)
        self._next_frame += 1
        return Frame(
            f"{self.source_id}:{native_index}",
            frame.image,
            frame.timestamp_seconds,
        )

    def close(self) -> None:
        self._source.close()


__all__ = ["create_controller", "create_registry"]
