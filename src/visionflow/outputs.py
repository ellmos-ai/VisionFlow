from __future__ import annotations

import hashlib
import json
import math
import re
import secrets
from dataclasses import asdict, is_dataclass
from datetime import datetime, timedelta, timezone
from numbers import Integral, Real
from typing import Any

from visionflow.contracts import ActionProposal, RunResult
from visionflow.trace import TraceRecorder

DEFAULT_DRAFT_TTL = timedelta(minutes=15)


def _report_value(value: Any) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        return _report_value(asdict(value))
    if isinstance(value, dict):
        converted: dict[str, Any] = {}
        for key, item in value.items():
            if type(key) is not str:
                raise TypeError("report dictionaries must use string keys")
            converted[key] = _report_value(item)
        return converted
    if isinstance(value, (list, tuple)):
        return [_report_value(item) for item in value]
    if value is None or type(value) in (str, bool):
        return value
    if isinstance(value, Integral):
        return int(value)
    if isinstance(value, Real):
        number = float(value)
        if not math.isfinite(number):
            raise ValueError("report values must be finite numbers")
        return number
    if isinstance(value, datetime):
        return value.isoformat()
    raise TypeError(f"report value of type {type(value).__name__} is not serializable")


def _json_block(value: Any) -> str:
    serialized = json.dumps(
        _report_value(value),
        ensure_ascii=False,
        sort_keys=True,
        indent=2,
        allow_nan=False,
    )
    longest_backticks = max(
        (len(run) for run in re.findall(r"`+", serialized)),
        default=0,
    )
    fence = "`" * max(3, longest_backticks + 1)
    return f"{fence}json\n{serialized}\n{fence}"


def _validated_trace(result: RunResult) -> list[dict[str, Any]]:
    recorder = TraceRecorder()
    for event in result.trace:
        recorder.append(event)
    return recorder.export()


def _model_mode(trace: list[dict[str, Any]]) -> str:
    recorded: list[str] = []
    for event in trace:
        event_type = event.get("type", "")
        if event_type not in {"model_mode", "model_started", "model_completed", "model_call"}:
            continue
        for key in ("model_mode", "mode", "adapter", "model"):
            value = event.get(key)
            if isinstance(value, str) and value.strip() and value not in recorded:
                recorded.append(value)

    if not recorded:
        return "nicht protokolliert"
    fixture = next((value for value in recorded if "fixture" in value.casefold()), None)
    if fixture is not None:
        return f"Fixturemodus (laut Trace: {fixture})"
    return "Im Trace protokolliert: " + ", ".join(recorded)


def render_report(result: RunResult) -> str:
    if not isinstance(result, RunResult):
        raise TypeError("result must be a RunResult")

    trace = _validated_trace(result)
    findings = [_report_value(finding) for finding in result.findings]
    outputs = _report_value(result.outputs)
    report_parts = [
        "# VisionFlow-Bericht",
        "",
        "## Lauf",
        _json_block({"run_id": result.run_id, "status": result.status}),
        "",
        "## Befunde und Belege",
    ]

    if findings:
        for index, finding in enumerate(findings, start=1):
            report_parts.extend(["", f"### Befund {index}", _json_block(finding)])
    else:
        report_parts.extend(["", "Keine Befunde gespeichert."])

    report_parts.extend(["", "## Trace"])
    if trace:
        report_parts.extend(["", _json_block(trace)])
    else:
        report_parts.extend(["", "Keine Trace-Ereignisse gespeichert."])

    if outputs:
        report_parts.extend(["", "## Ausgaben", "", _json_block(outputs)])

    report_parts.extend(
        [
            "",
            "## Modellmodus",
            "",
            _model_mode(trace),
            "",
            "## Grenzen",
            "",
            "- Der Bericht übernimmt gespeicherte Befunde, Messwerte und Trace-Ereignisse; fehlende Angaben werden nicht ergänzt.",
            "- Belegverweise bleiben Referenzen; der Bericht öffnet die Belegdateien nicht erneut.",
            "- Ein Fixture ist kein Nachweis eines echten Modelllaufs.",
            "- Dieser Bericht löst keinen externen Versand aus.",
        ]
    )
    return "\n".join(report_parts) + "\n"


def _proposal_hash(
    *,
    proposal_type: str,
    run_id: str,
    action_id: str,
    content: str,
    destination: str | None,
) -> str:
    payload = {
        "action_id": action_id,
        "content": content,
        "destination": destination,
        "run_id": run_id,
        "type": proposal_type,
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _aware_utc(value: datetime | None, *, field: str) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware")
    return value.astimezone(timezone.utc)


def prepare_notification(
    result: RunResult,
    *,
    now: datetime | None = None,
) -> ActionProposal:
    if not isinstance(result, RunResult):
        raise TypeError("result must be a RunResult")
    if not isinstance(result.run_id, str) or not result.run_id:
        raise ValueError("result.run_id must be a non-empty string")

    created_at = _aware_utc(now, field="now")
    action_type = "notification_draft"
    action_id = "act_" + secrets.token_urlsafe(24)
    content = render_report(result)
    # WorkflowConfig currently has no destination field, so the safe draft has no target.
    destination = None
    content_hash = _proposal_hash(
        proposal_type=action_type,
        run_id=result.run_id,
        action_id=action_id,
        content=content,
        destination=destination,
    )
    return ActionProposal(
        type=action_type,
        run_id=result.run_id,
        action_id=action_id,
        content=content,
        content_hash=content_hash,
        expires_at=created_at + DEFAULT_DRAFT_TTL,
        sent=False,
        destination=destination,
    )