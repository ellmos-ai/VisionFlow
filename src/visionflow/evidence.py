from __future__ import annotations

import re
from pathlib import Path

_SAFE_SEGMENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*\Z", re.ASCII)
_DEVICE_NAMES = {"CON", "PRN", "AUX", "NUL"}


def _safe_segment(value: str, label: str) -> str:
    if not isinstance(value, str) or not _SAFE_SEGMENT.fullmatch(value):
        raise ValueError(f"{label} must be a single safe path segment")
    if value.endswith("."):
        raise ValueError(f"{label} must not end with a dot")

    device_stem = value.split(".", 1)[0].upper()
    if device_stem in _DEVICE_NAMES or re.fullmatch(r"(?:COM|LPT)[1-9]", device_stem):
        raise ValueError(f"{label} is a reserved device name")
    return value


def _resolve_beneath(root: Path, candidate: Path) -> Path:
    try:
        resolved = candidate.resolve(strict=False)
        resolved.relative_to(root)
    except (OSError, RuntimeError, ValueError) as exc:
        raise ValueError("evidence path must stay beneath the evidence root") from exc
    return resolved


def store_evidence(root: Path, run_id: str, frame_id: str, image_bytes: bytes) -> Path:
    """Store one frame's original bytes without replacing an existing artifact."""
    safe_run_id = _safe_segment(run_id, "run_id")
    safe_frame_id = _safe_segment(frame_id, "frame_id")
    if not isinstance(image_bytes, bytes):
        raise TypeError("image_bytes must be bytes")

    root_path = Path(root)
    root_path.mkdir(parents=True, exist_ok=True)
    root_real = root_path.resolve(strict=True)

    run_directory = root_path / safe_run_id
    run_directory.mkdir(exist_ok=True)
    if run_directory.is_symlink():
        raise ValueError("run directory must not be a symlink")

    run_real = _resolve_beneath(root_real, run_directory)
    if not run_real.is_dir():
        raise ValueError("run directory must be a directory")

    evidence_path = run_directory / f"{safe_frame_id}.bin"
    _resolve_beneath(root_real, evidence_path)

    # Exclusive creation prevents duplicate frame IDs from replacing evidence.
    with evidence_path.open("xb") as evidence_file:
        evidence_file.write(image_bytes)

    return evidence_path