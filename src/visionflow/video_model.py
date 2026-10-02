"""Validated whole-video requests and the run-scoped model adapter."""

from __future__ import annotations

import copy
import json
import math
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any, Literal, Protocol

from .contracts import Finding, ModelDecision, Region, WorkflowConfig
from .model import ModelAdapter, ModelRunContext, validate_model_decision
from .recording import ClipRef


_OBJECT_CLASSES = frozenset({"vehicle", "bird", "leaf", "unknown"})
_ASSESSMENT_FIELDS = frozenset(
    {
        "object_class",
        "hazard",
        "confidence",
        "summary",
        "evidence_frame_ids",
        "decision",
    }
)
_SAFE_REF_SEGMENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*\Z", re.ASCII)
MAX_ASSESSMENT_BYTES = 1024 * 1024


class VideoModelError(RuntimeError):
    """A safe model failure represented only by its stable machine code."""

    def __init__(self, code: str) -> None:
        if type(code) is not str or not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", code):
            code = "model_error"
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class VideoRequest:
    """One immutable request for a whole recorded clip and its trigger evidence."""

    exchange_id: str
    case_id: str
    run_id: str
    clip: ClipRef
    prompt: str
    evidence_refs: tuple[str, ...]
    model_id: str
    processing_fps: float = 5.0

    def __post_init__(self) -> None:
        for field_name in ("exchange_id", "case_id", "run_id", "model_id"):
            value = getattr(self, field_name)
            if type(value) is not str or not value.strip():
                raise ValueError(f"{field_name} must be a non-empty string")
        if not isinstance(self.clip, ClipRef):
            raise TypeError("clip must be a ClipRef")
        if type(self.prompt) is not str or not self.prompt.strip():
            raise ValueError("prompt must be a non-empty string")
        if type(self.evidence_refs) is not tuple or not self.evidence_refs:
            raise ValueError("evidence_refs must be a non-empty tuple")
        for reference in self.evidence_refs:
            if not _valid_evidence_ref(reference, self.run_id):
                raise ValueError("evidence refs must be safe references from this run")
        if (
            isinstance(self.processing_fps, bool)
            or type(self.processing_fps) not in (int, float)
            or not math.isfinite(float(self.processing_fps))
            or float(self.processing_fps) != 5.0
        ):
            raise ValueError("processing_fps must be exactly 5.0")
        if not self.clip.source_id:
            raise ValueError("clip must have a source_id")
        if not self.clip.frame_ids or len(set(self.clip.frame_ids)) != len(self.clip.frame_ids):
            raise ValueError("clip frame_ids must be non-empty and unique")


@dataclass(frozen=True, slots=True)
class VideoAssessment:
    """Provider-independent, validated result for one whole-video request."""

    object_class: Literal["vehicle", "bird", "leaf", "unknown"]
    hazard: bool | None
    confidence: float | None
    summary: str
    evidence_frame_ids: tuple[str, ...]
    decision: ModelDecision

    def __post_init__(self) -> None:
        if self.object_class not in _OBJECT_CLASSES:
            raise ValueError("object_class is unknown")
        if self.hazard is not None and type(self.hazard) is not bool:
            raise ValueError("hazard must be a boolean or null")
        if self.confidence is None:
            if self.object_class != "unknown":
                raise ValueError("null confidence is allowed only for an unknown classification")
        elif (
            isinstance(self.confidence, bool)
            or type(self.confidence) not in (int, float)
            or not math.isfinite(float(self.confidence))
            or not 0 <= float(self.confidence) <= 1
        ):
            raise ValueError("confidence must be a finite number from 0 through 1")
        if type(self.summary) is not str or not self.summary.strip() or len(self.summary) > 2000:
            raise ValueError("summary must be non-empty bounded text")
        if (
            type(self.evidence_frame_ids) is not tuple
            or not self.evidence_frame_ids
            or any(type(frame_id) is not str or not frame_id for frame_id in self.evidence_frame_ids)
            or len(set(self.evidence_frame_ids)) != len(self.evidence_frame_ids)
        ):
            raise ValueError("evidence_frame_ids must be non-empty and unique")
        if not isinstance(self.decision, ModelDecision):
            raise ValueError("decision must be a validated ModelDecision")
        decision = validate_model_decision(self.decision)
        if decision.action not in {"report", "observe"}:
            raise ValueError("video assessment supports only report or observe")
        object.__setattr__(self, "decision", decision)

    def to_dict(self) -> dict[str, Any]:
        """Return only the stable public assessment fields as detached JSON data."""
        return {
            "object_class": self.object_class,
            "hazard": self.hazard,
            "confidence": self.confidence,
            "summary": self.summary,
            "evidence_frame_ids": list(self.evidence_frame_ids),
            "decision": {
                "action": self.decision.action,
                "payload": copy.deepcopy(self.decision.payload),
            },
        }


@dataclass(frozen=True, slots=True)
class VideoTransportResult:
    """Visible provider text and safe usage identifiers returned by a transport."""

    raw_text: str
    usage: dict[str, Any] | None = None
    request_id: str | None = None

    def __post_init__(self) -> None:
        if type(self.raw_text) is not str:
            raise TypeError("raw_text must be a string")
        if self.usage is not None and type(self.usage) is not dict:
            raise TypeError("usage must be a dictionary or null")
        if self.request_id is not None and (
            type(self.request_id) is not str or not self.request_id
        ):
            raise ValueError("request_id must be non-empty text or null")
        if self.usage is not None:
            object.__setattr__(self, "usage", copy.deepcopy(self.usage))


class VideoTransport(Protocol):
    """Transport boundary for fixture and native whole-video exchanges."""

    def generate(
        self,
        request: VideoRequest,
        on_delta: Callable[[str], None],
        cancelled: Callable[[], bool],
    ) -> VideoTransportResult:
        """Send one bounded request and return the complete visible text."""


class FixtureVideoTransport:
    """Explicit offline transport; it never selects a response from scene metadata."""

    def __init__(self, *, response: VideoTransportResult) -> None:
        if not isinstance(response, VideoTransportResult):
            raise TypeError("fixture transport requires an explicit VideoTransportResult")
        self._response = response

    def generate(
        self,
        request: VideoRequest,
        on_delta: Callable[[str], None],
        cancelled: Callable[[], bool],
    ) -> VideoTransportResult:
        if not isinstance(request, VideoRequest):
            raise TypeError("request must be a VideoRequest")
        if cancelled():
            raise VideoModelError("cancelled")
        if self._response.raw_text:
            on_delta(self._response.raw_text)
        if cancelled():
            raise VideoModelError("cancelled")
        return self._response


def clip_public_metadata(clip: ClipRef) -> dict[str, Any]:
    """Return stable clip metadata without the local filesystem path."""
    if not isinstance(clip, ClipRef):
        raise TypeError("clip must be a ClipRef")
    return {
        "clip_id": clip.clip_id,
        "source_id": clip.source_id,
        "version": clip.version,
        "sha256": clip.sha256,
        "start_seconds": clip.start_seconds,
        "end_seconds": clip.end_seconds,
        "frame_ids": list(clip.frame_ids),
        "source_fps": clip.source_fps,
        "available_preroll": clip.available_preroll,
    }


def validate_video_assessment(
    raw: str,
    request: VideoRequest,
    permitted_region: Region,
) -> VideoAssessment:
    """Parse a complete untrusted JSON assessment and constrain its decision."""
    if not isinstance(request, VideoRequest):
        raise TypeError("request must be a VideoRequest")
    if type(raw) is not str or len(raw.encode("utf-8")) > MAX_ASSESSMENT_BYTES:
        raise ValueError("assessment must be bounded JSON text")
    try:
        parsed = json.loads(
            raw,
            parse_constant=lambda _value: (_ for _ in ()).throw(
                ValueError("non-finite JSON number")
            ),
            object_pairs_hook=_unique_object,
        )
    except (json.JSONDecodeError, UnicodeError, ValueError) as exc:
        raise ValueError("assessment is not valid JSON") from exc
    if type(parsed) is not dict or set(parsed) != _ASSESSMENT_FIELDS:
        raise ValueError("assessment fields do not match the required schema")

    object_class = parsed["object_class"]
    if type(object_class) is not str or object_class not in _OBJECT_CLASSES:
        raise ValueError("object_class is unknown")
    hazard = parsed["hazard"]
    if hazard is not None and type(hazard) is not bool:
        raise ValueError("hazard must be a boolean or null")
    confidence = parsed["confidence"]
    if confidence is None:
        if object_class != "unknown":
            raise ValueError("null confidence is allowed only for unknown")
    elif (
        isinstance(confidence, bool)
        or type(confidence) not in (int, float)
        or not math.isfinite(float(confidence))
        or not 0 <= float(confidence) <= 1
    ):
        raise ValueError("confidence must be a finite number from 0 through 1")
    summary = parsed["summary"]
    if type(summary) is not str or not summary.strip() or len(summary) > 2000:
        raise ValueError("summary must be non-empty bounded text")

    raw_evidence_ids = parsed["evidence_frame_ids"]
    if (
        type(raw_evidence_ids) is not list
        or not raw_evidence_ids
        or any(type(frame_id) is not str or not frame_id for frame_id in raw_evidence_ids)
        or len(set(raw_evidence_ids)) != len(raw_evidence_ids)
    ):
        raise ValueError("evidence_frame_ids must be a non-empty array of unique IDs")
    allowed_frame_ids = set(request.clip.frame_ids)
    if any(frame_id not in allowed_frame_ids for frame_id in raw_evidence_ids):
        raise ValueError("assessment cites a frame outside the requested clip")

    decision = validate_model_decision(parsed["decision"])
    if decision.action not in {"report", "observe"}:
        raise ValueError("video assessment supports only report or observe")
    if decision.action == "observe":
        if decision.payload["source_id"] != request.clip.source_id:
            raise ValueError("observation source must match the requested clip")
        if not _region_contains(permitted_region, decision.payload["region"]):
            raise ValueError("observation region is outside the permitted region")

    return VideoAssessment(
        object_class=object_class,
        hazard=hazard,
        confidence=None if confidence is None else float(confidence),
        summary=summary,
        evidence_frame_ids=tuple(raw_evidence_ids),
        decision=decision,
    )


def build_video_prompt(
    question: str,
    findings: Sequence[Finding],
    clip: ClipRef,
    *,
    application_prompt: str = "",
    frame_source_times: Mapping[str, float] | None = None,
) -> str:
    """Compose visible pixel-only context, lane-flow evidence, times, and schema."""
    if type(question) is not str or not question.strip():
        raise ValueError("question must be a non-empty string")
    if not isinstance(clip, ClipRef):
        raise TypeError("clip must be a ClipRef")
    lines = [
        "Review the entire supplied road-camera video and the trigger evidence images.",
        "Use visible pixels and measured motion only. Ignore simulator flags, seeds, "
        "event names, color labels, and hidden scenario metadata.",
        "Clip metadata from the recorder manifest:",
        f"- Source ID: {clip.source_id}",
        f"- Clip ID and version: {clip.clip_id} / {clip.version}",
        f"- Source time range: {clip.start_seconds:.3f} to {clip.end_seconds:.3f} seconds",
        f"- Source capture FPS: {clip.source_fps:.3f}",
        "The encoded MP4 starts at clip-relative time zero. The source FPS above "
        "describes the captured frames; Gemini's configured sampling FPS is 5.0.",
    ]
    if application_prompt.strip():
        lines.extend(["", application_prompt.strip()])
    lines.extend(["", "Application question:", question.strip()])

    clip_frame_positions = {frame_id: index for index, frame_id in enumerate(clip.frame_ids)}
    if frame_source_times is not None and not isinstance(frame_source_times, Mapping):
        raise TypeError("frame_source_times must be a recorder manifest mapping")
    timestamp_lines: list[str] = []
    for finding in findings:
        frame_ids = getattr(finding, "frame_ids", ())
        for frame_id in frame_ids:
            if frame_id not in clip_frame_positions:
                continue
            seconds = clip_frame_positions[frame_id] / clip.source_fps
            line = f"- {frame_id}: encoded clip time {seconds:.3f} seconds from MP4 start"
            if frame_source_times is not None and frame_id in frame_source_times:
                source_seconds = frame_source_times[frame_id]
                if (
                    isinstance(source_seconds, bool)
                    or type(source_seconds) not in (int, float)
                    or not math.isfinite(float(source_seconds))
                    or source_seconds < 0
                ):
                    raise ValueError("manifest source times must be finite non-negative seconds")
                line += f"; recorder manifest source time {float(source_seconds):.3f} seconds"
            timestamp_lines.append(line)
    if timestamp_lines:
        lines.extend(["", "Evidence frame time references:", *dict.fromkeys(timestamp_lines)])

    lane_lines: list[str] = []
    for finding in findings:
        metrics = getattr(finding, "metrics", {})
        if not isinstance(metrics, dict):
            continue
        tracks = metrics.get("tracks")
        if type(tracks) is not list:
            continue
        for track in tracks[:24]:
            if type(track) is not dict:
                continue
            lane_id = track.get("lane_id")
            dx = track.get("dx_px")
            dy = track.get("dy_px")
            opposition = track.get("opposition")
            if (
                type(lane_id) is not str
                or not lane_id
                or isinstance(dx, bool)
                or type(dx) not in (int, float)
                or isinstance(dy, bool)
                or type(dy) not in (int, float)
                or type(opposition) is not bool
                or not math.isfinite(float(dx))
                or not math.isfinite(float(dy))
            ):
                continue
            lane_lines.append(
                f"- lane {lane_id}: measured motion ({float(dx):.2f}, {float(dy):.2f}) "
                f"pixels/sample; against configured lane direction={str(opposition).lower()}"
            )
    if lane_lines:
        lines.extend(
            [
                "",
                "Configured lane direction and pixel-flow findings "
                "(true means motion opposes the configured lane direction):",
                *dict.fromkeys(lane_lines),
            ]
        )

    lines.extend(
        [
            "",
            "Return one JSON object with exactly these fields: object_class "
            "(vehicle|bird|leaf|unknown), hazard (boolean|null), confidence "
            "(number from 0 to 1|null; null only for an unclear classification, "
            "which must use object_class unknown), summary (short text), "
            "evidence_frame_ids (IDs from this clip), and decision.",
            "decision must be either {\"action\":\"report\",\"payload\":{\"summary\":\"...\"}} "
            "or {\"action\":\"observe\",\"payload\":{\"source_id\":\""
            f"{clip.source_id}"
            "\",\"region\":[x,y,width,height],\"duration_seconds\":number,"
            "\"sample_fps\":number,\"question\":\"...\"}}. For observe, use this exact "
            "source_id and source-pixel coordinates; keep the region inside the authorized "
            "region and honor any duration and sampling limits stated in the application instructions.",
            "Do not return markdown, extra keys, or a partial answer.",
        ]
    )
    return "\n".join(lines)


class VideoModelFactory:
    """RunModelFactory that records and validates one whole-video exchange per ask."""

    def __init__(
        self,
        request_factory: Callable[[ModelRunContext, str, list[Finding]], VideoRequest],
        transport: VideoTransport,
        exchanges: Any,
        owner_for_run: Callable[[str], str],
    ) -> None:
        if not callable(request_factory):
            raise TypeError("request_factory must be callable")
        if not callable(getattr(transport, "generate", None)):
            raise TypeError("transport must implement generate")
        if not callable(getattr(exchanges, "record_request", None)):
            raise TypeError("exchanges must implement ModelExchangeStore")
        if not callable(owner_for_run):
            raise TypeError("owner_for_run must be callable")
        self._request_factory = request_factory
        self._transport = transport
        self._exchanges = exchanges
        self._owner_for_run = owner_for_run

    def create_adapter(
        self,
        config: WorkflowConfig,
        context: ModelRunContext,
    ) -> ModelAdapter:
        if not isinstance(context, ModelRunContext):
            raise TypeError("context must be a ModelRunContext")
        return _VideoModelAdapter(
            request_factory=self._request_factory,
            transport=self._transport,
            exchanges=self._exchanges,
            owner_for_run=self._owner_for_run,
            config=config,
            context=context,
        )


class _VideoModelAdapter:
    MODE = "video"

    def __init__(
        self,
        *,
        request_factory: Callable[[ModelRunContext, str, list[Finding]], VideoRequest],
        transport: VideoTransport,
        exchanges: Any,
        owner_for_run: Callable[[str], str],
        config: WorkflowConfig,
        context: ModelRunContext,
    ) -> None:
        self._request_factory = request_factory
        self._transport = transport
        self._exchanges = exchanges
        self._owner_for_run = owner_for_run
        self._config = config
        self._context = context

    @property
    def mode(self) -> str:
        return self.MODE

    def ask(self, question: str, findings: list[Finding]) -> ModelDecision:
        if self._context.cancelled():
            raise VideoModelError("cancelled")
        if type(findings) is not list or any(not isinstance(item, Finding) for item in findings):
            raise VideoModelError("invalid_request")
        try:
            request = self._request_factory(
                self._context,
                question,
                copy.deepcopy(findings),
            )
            if not isinstance(request, VideoRequest):
                raise VideoModelError("invalid_request")
            if request.run_id != self._context.run_id:
                raise VideoModelError("invalid_request")
            if request.clip.source_id != self._context.source_id:
                raise VideoModelError("invalid_request")
            finding_refs = {item.evidence_ref for item in findings if item.evidence_ref}
            if not set(request.evidence_refs).issubset(finding_refs):
                raise VideoModelError("invalid_request")
            prompt = build_video_prompt(
                question,
                findings,
                request.clip,
                application_prompt=request.prompt,
            )
            request = replace(request, prompt=prompt)
            owner_id = self._owner_for_run(self._context.run_id)
            if type(owner_id) is not str or not owner_id:
                raise VideoModelError("invalid_owner")
            self._exchanges.record_request(owner_id, request)
        except VideoModelError:
            raise
        except Exception:
            raise VideoModelError("invalid_request") from None

        def on_delta(text: str) -> None:
            if self._context.cancelled():
                raise VideoModelError("cancelled")
            self._exchanges.append_delta(request.exchange_id, text)

        try:
            result = self._transport.generate(request, on_delta, self._context.cancelled)
            if self._context.cancelled():
                raise VideoModelError("cancelled")
            if not isinstance(result, VideoTransportResult):
                raise VideoModelError("provider_failed")
            assessment = validate_video_assessment(
                result.raw_text,
                request,
                None,
            )
            if not _observation_is_authorized(self._config, assessment.decision):
                raise ValueError("observe decision exceeds configured authority")
            self._exchanges.complete(
                request.exchange_id,
                assessment,
                raw_output=result.raw_text,
                usage=result.usage,
            )
        except VideoModelError as exc:
            self._exchanges.fail(request.exchange_id, exc.code)
            raise
        except ValueError:
            self._exchanges.fail(request.exchange_id, "invalid_response")
            raise VideoModelError("invalid_response") from None
        except Exception:
            code = "cancelled" if self._context.cancelled() else "provider_failed"
            self._exchanges.fail(request.exchange_id, code)
            raise VideoModelError(code) from None
        return assessment.decision


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON field")
        result[key] = value
    return result


def _valid_evidence_ref(reference: Any, run_id: str) -> bool:
    if type(reference) is not str or not reference or "\\" in reference or ":" in reference:
        return False
    parts = reference.split("/")
    if len(parts) < 2 or parts[0] != run_id:
        return False
    return all(
        _SAFE_REF_SEGMENT.fullmatch(part) is not None
        and not part.endswith(".")
        and part not in {".", ".."}
        for part in parts
    )


def _normalize_region(region: Region) -> tuple[float, float, float, float] | None:
    if region is None:
        return None
    if type(region) not in (tuple, list) or len(region) != 4:
        raise ValueError("permitted_region must be null or a four-number box")
    values: list[float] = []
    for value in region:
        if isinstance(value, bool) or type(value) not in (int, float):
            raise ValueError("permitted_region values must be finite numbers")
        number = float(value)
        if not math.isfinite(number):
            raise ValueError("permitted_region values must be finite numbers")
        values.append(number)
    x, y, width, height = values
    if x < 0 or y < 0 or width <= 0 or height <= 0:
        raise ValueError("permitted_region must have non-negative origin and positive size")
    if not math.isfinite(x + width) or not math.isfinite(y + height):
        raise ValueError("permitted_region bounds must be finite")
    return x, y, width, height


def _region_contains(
    permitted: Region,
    requested: tuple[float, float, float, float] | None,
) -> bool:
    allowed = _normalize_region(permitted)
    if allowed is None:
        return True
    if requested is None:
        return False
    x, y, width, height = requested
    ax, ay, aw, ah = allowed
    epsilon = 1e-9
    return (
        x + epsilon >= ax
        and y + epsilon >= ay
        and x + width <= ax + aw + epsilon
        and y + height <= ay + ah + epsilon
    )


def _observation_is_authorized(config: WorkflowConfig, decision: ModelDecision) -> bool:
    """Require one configured observe node to contain the complete follow-up request."""
    if decision.action != "observe":
        return True
    payload = decision.payload
    requested_duration = payload["duration_seconds"]
    requested_fps = payload["sample_fps"]
    requested_region = payload["region"]

    for node in getattr(config, "nodes", ()):
        if getattr(node, "type", None) != "observe":
            continue
        params = getattr(node, "params", None)
        if type(params) is not dict:
            continue
        duration = params.get("duration_seconds")
        sample_fps = params.get("sample_fps")
        if not _positive_finite_number(duration) or not _positive_finite_number(sample_fps):
            continue
        try:
            permitted_region = _normalize_region(params.get("region"))
        except (TypeError, ValueError, OverflowError):
            continue
        if (
            requested_duration <= duration
            and requested_fps <= sample_fps
            and _region_contains(permitted_region, requested_region)
        ):
            return True
    return False


def _positive_finite_number(value: Any) -> bool:
    if isinstance(value, bool) or type(value) not in (int, float):
        return False
    try:
        return math.isfinite(float(value)) and value > 0
    except OverflowError:
        return False


__all__ = [
    "FixtureVideoTransport",
    "VideoAssessment",
    "VideoModelError",
    "VideoModelFactory",
    "VideoRequest",
    "VideoTransport",
    "VideoTransportResult",
    "build_video_prompt",
    "clip_public_metadata",
    "validate_video_assessment",
]
