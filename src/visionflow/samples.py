"""Bundled sample inputs for the reusable image and video workflows.

The public demo runs the document and motion templates only on these server-side
samples (no uploads, no paths). The media are generated with OpenCV at start-up
from the same synthetic generators as the offline CLI demo.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import cv2

from .demo import _document_images, _write_motion_video

# Public source reference -> (input kind, file name in the samples directory).
SAMPLE_SOURCES: dict[str, tuple[str, str]] = {
    "samples/document-blurred.png": ("image", "document-blurred.png"),
    "samples/document-sharp.png": ("image", "document-sharp.png"),
    "samples/motion.avi": ("video", "motion.avi"),
}
# Bundled template -> sample references it may run on in the public demo.
SAMPLE_WORKFLOWS: dict[str, tuple[str, ...]] = {
    "document-quality.json": ("samples/document-blurred.png", "samples/document-sharp.png"),
    "motion-observation.json": ("samples/motion.avi",),
}


def sample_templates() -> list[dict]:
    """The bundled templates the public demo accepts, each with its allowed samples."""
    from importlib.resources import files

    rows = []
    for name, sources in SAMPLE_WORKFLOWS.items():
        config = json.loads(files("visionflow").joinpath("workflows", name).read_text(encoding="utf-8"))
        rows.append({"id": name.removesuffix(".json"), "kind": config["input"]["kind"],
                     "samples": list(sources), "config": config})
    return rows


def matching_sample_template(body: object) -> dict | None:
    """Return the template that *body* equals exactly, apart from an allowed sample source."""
    if not isinstance(body, dict) or not isinstance(body.get("input"), dict):
        return None
    source = body["input"].get("source")
    for template in sample_templates():
        expected = json.loads(json.dumps(template["config"]))
        expected["input"]["source"] = source
        if source in template["samples"] and _canonical(expected) == _canonical(body):
            return template
    return None


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def prepare_samples(directory: Path | str) -> dict[str, Path]:
    """Write the sample media (replacing older copies) and return reference -> path."""
    target = Path(directory).expanduser().resolve()
    target.mkdir(parents=True, exist_ok=True)
    blurred, sharp = _document_images()
    for name, image in (("document-blurred.png", blurred), ("document-sharp.png", sharp)):
        ok, encoded = cv2.imencode(".png", image)
        if not ok:
            raise RuntimeError("OpenCV could not encode a sample image")
        temporary = target / (name + ".tmp")
        temporary.write_bytes(encoded.tobytes())
        os.replace(temporary, target / name)
    video = target / "motion.avi"
    temporary = target / "motion.tmp.avi"
    temporary.unlink(missing_ok=True)
    _write_motion_video(temporary)
    os.replace(temporary, video)
    return {reference: target / file_name for reference, (_kind, file_name) in SAMPLE_SOURCES.items()}


__all__ = ["SAMPLE_SOURCES", "SAMPLE_WORKFLOWS", "matching_sample_template", "prepare_samples", "sample_templates"]
