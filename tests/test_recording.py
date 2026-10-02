from __future__ import annotations

import hashlib
import json
import shutil
import threading
import subprocess
from pathlib import Path

import cv2
import numpy as np
import pytest

from visionflow.contracts import Frame
from visionflow.recording import ClipNotReady, RecordingError, RecordingStore


def ffmpeg_path() -> Path:
    path = shutil.which("ffmpeg")
    if path is None:
        pytest.skip("FFmpeg is required for the native recording probe")
    result = subprocess.run(
        [path, "-hide_banner", "-encoders"],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    if result.returncode != 0 or "libx264" not in result.stdout:
        pytest.skip("FFmpeg libx264 is required for the native recording probe")
    return Path(path)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def decode_all_frames(path: Path) -> list[np.ndarray]:
    capture = cv2.VideoCapture(str(path))
    assert capture.isOpened(), f"OpenCV could not open exported clip {path}"
    frames: list[np.ndarray] = []
    try:
        while True:
            ok, frame = capture.read()
            if not ok:
                break
            frames.append(frame)
    finally:
        capture.release()
    return frames


def recording_store_with_known_frames(
    tmp_path: Path,
    *,
    start: float = 0.0,
    end: float = 25.0,
    fps: float = 20.0,
    max_seconds: float = 120.0,
    max_bytes: int = 128 * 1024 * 1024,
    owner_id: str = "alice",
    source_id: str = "camera-1",
    size: tuple[int, int] = (540, 960),
) -> RecordingStore:
    store = RecordingStore(
        root=tmp_path / "recording",
        ffmpeg_path=ffmpeg_path(),
        max_seconds=max_seconds,
        max_bytes=max_bytes,
    )
    store.open(owner_id, source_id)
    height, width = size
    count = round((end - start) * fps)
    for index in range(count + 1):
        image = np.empty((height, width, 3), dtype=np.uint8)
        image[:] = (index % 251, (index * 13) % 251, (index * 29) % 251)
        timestamp = round(start + index / fps, 8)
        store.append(
            source_id,
            Frame(
                frame_id=f"frame-{index:06d}",
                image=image,
                timestamp_seconds=timestamp,
            ),
        )
    return store


def test_native_export_is_full_frame_h264_and_extensions_are_immutable(tmp_path: Path) -> None:
    store = recording_store_with_known_frames(tmp_path, start=0, end=25, fps=20)

    clip = store.event_clip("alice", "camera-1", event_time=8)

    assert (clip.start_seconds, clip.end_seconds) == (3.0, 18.0)
    assert clip.available_preroll == 5.0
    assert clip.source_fps == pytest.approx(20.0)
    assert clip.frame_ids[0] == "frame-000060"
    assert clip.frame_ids[-1] == "frame-000360"
    assert len(clip.frame_ids) == 301
    assert clip.path.is_relative_to(store.root)
    assert clip.path.is_file()
    assert clip.sha256 == sha256_file(clip.path)
    encoded_mp4 = clip.path.read_bytes()
    assert encoded_mp4[4:8] == b"ftyp"
    assert encoded_mp4.find(b"moov") < encoded_mp4.find(b"mdat")

    decoded = decode_all_frames(clip.path)
    assert len(decoded) == len(clip.frame_ids)
    assert decoded[0].shape == (540, 960, 3)
    assert decoded[-1].shape == (540, 960, 3)
    original_bytes = clip.path.read_bytes()

    retrieved = store.clip("alice", clip.clip_id)
    assert retrieved == clip
    extended = store.event_clip(
        "alice",
        "camera-1",
        event_time=8,
        end_override=23,
    )
    assert extended.clip_id != clip.clip_id
    assert extended.version > clip.version
    assert extended.end_seconds == 23.0
    assert len(decode_all_frames(extended.path)) == len(extended.frame_ids)
    assert clip.path.read_bytes() == original_bytes
    assert store.clip("alice", clip.clip_id) == clip
    assert store.clip("alice", extended.clip_id) == extended
    assert extended.sha256 == sha256_file(extended.path)
    assert {entry["version"] for entry in store.manifest("alice", "camera-1")["clips"]} == {1, 2}


def test_event_clip_waits_for_real_postroll_and_reports_short_preroll(tmp_path: Path) -> None:
    store = recording_store_with_known_frames(
        tmp_path,
        start=6,
        end=10,
        fps=2,
    )

    with pytest.raises(ClipNotReady) as not_ready:
        store.event_clip("alice", "camera-1", event_time=8, before=5, after=4)
    assert not_ready.value.code == "clip_not_ready"

    store.append(
        "camera-1",
        Frame(
            frame_id="frame-after-postroll",
            image=np.zeros((540, 960, 3), dtype=np.uint8),
            timestamp_seconds=12.5,
        ),
    )
    clip = store.event_clip("alice", "camera-1", event_time=8, before=5, after=4)

    assert clip.start_seconds == 6
    assert clip.end_seconds == 12.5
    assert clip.available_preroll == 2
    assert clip.frame_ids[0] == "frame-000000"
    assert len(decode_all_frames(clip.path)) == len(clip.frame_ids)


def test_duration_cap_rejects_frames_past_limit_without_storing_them(tmp_path: Path) -> None:
    store = recording_store_with_known_frames(
        tmp_path,
        start=0,
        end=1,
        fps=2,
        max_seconds=1,
    )

    with pytest.raises(RecordingError) as over_limit:
        store.append(
            "camera-1",
            Frame(
                frame_id="over-limit",
                image=np.zeros((540, 960, 3), dtype=np.uint8),
                timestamp_seconds=1.5,
            ),
        )
    assert over_limit.value.code == "duration_limit"
    assert len(store.manifest("alice", "camera-1")["frames"]) == 3


def test_compressed_source_bytes_are_bounded_before_frame_write(tmp_path: Path) -> None:
    store = RecordingStore(
        root=tmp_path / "recording",
        ffmpeg_path=ffmpeg_path(),
        max_bytes=1024,
    )
    store.open("alice", "camera-1")
    image = np.zeros((540, 960, 3), dtype=np.uint8)

    with pytest.raises(RecordingError) as over_limit:
        store.append(
            "camera-1",
            Frame(frame_id="too-large", image=image, timestamp_seconds=0),
        )

    assert over_limit.value.code == "byte_limit"
    assert not list(store.root.rglob("*.jpg"))


def test_export_bytes_are_included_in_source_budget(tmp_path: Path) -> None:
    image = np.random.default_rng(42).integers(
        0, 256, size=(540, 960, 3), dtype=np.uint8
    )
    encoded_ok, encoded = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 95])
    assert encoded_ok
    store = RecordingStore(
        root=tmp_path / "recording",
        ffmpeg_path=ffmpeg_path(),
        max_bytes=8 * 1024 * 1024,
    )
    store.open("alice", "camera-1")
    for index in range(2):
        store.append(
            "camera-1",
            Frame(
                frame_id=f"frame-{index}",
                image=image,
                timestamp_seconds=float(index),
            ),
        )

    source_directory = next(store.root.rglob("manifest.json")).parent
    store.max_bytes = _persisted_source_bytes(source_directory) + 1

    with pytest.raises(RecordingError) as over_limit:
        store.event_clip(
            "alice",
            "camera-1",
            event_time=0.5,
            before=0.5,
            after=0.5,
        )

    assert over_limit.value.code == "byte_limit"
    assert not list(store.root.rglob("*.mp4"))


def test_other_owner_cannot_read_manifest_or_clip(tmp_path: Path) -> None:
    store = recording_store_with_known_frames(
        tmp_path,
        start=0,
        end=2,
        fps=2,
    )
    clip = store.event_clip(
        "alice",
        "camera-1",
        event_time=1,
        before=1,
        after=1,
    )

    with pytest.raises(RecordingError) as manifest_error:
        store.manifest("mallory", "camera-1")
    assert manifest_error.value.code == "owner_mismatch"

    with pytest.raises(RecordingError) as clip_error:
        store.clip("mallory", clip.clip_id)
    assert clip_error.value.code == "owner_mismatch"


def test_source_and_frame_validation_fail_with_stable_error_codes(tmp_path: Path) -> None:
    store = RecordingStore(root=tmp_path / "recording", ffmpeg_path=ffmpeg_path())
    store.open("alice", "camera-1")

    with pytest.raises(RecordingError) as missing_source:
        store.append("camera-2", Frame("foreign", np.zeros((2, 2, 3), dtype=np.uint8)))
    assert missing_source.value.code == "source_not_open"

    with pytest.raises(RecordingError) as invalid_frame:
        store.append(
            "camera-1",
            Frame("bad-frame", np.zeros((10, 10), dtype=np.float32)),
        )
    assert invalid_frame.value.code == "invalid_frame"

    store.append(
        "camera-1",
        Frame("frame-1", np.zeros((540, 960, 3), dtype=np.uint8), 1.0),
    )
    with pytest.raises(RecordingError) as nonmonotonic:
        store.append(
            "camera-1",
            Frame("frame-2", np.zeros((540, 960, 3), dtype=np.uint8), 0.5),
        )
    assert nonmonotonic.value.code == "invalid_timestamp"


def test_simultaneous_owner_for_same_source_is_rejected(tmp_path: Path) -> None:
    store = RecordingStore(root=tmp_path / "recording", ffmpeg_path=ffmpeg_path())
    store.open("alice", "camera-1")

    with pytest.raises(RecordingError) as duplicate:
        store.open("mallory", "camera-1")

    assert duplicate.value.code == "source_in_use"


def test_missing_ffmpeg_has_a_safe_error_code(tmp_path: Path) -> None:
    store = RecordingStore(
        root=tmp_path / "recording",
        ffmpeg_path=tmp_path / "missing-ffmpeg.exe",
    )
    store.open("alice", "camera-1")
    for index in range(2):
        store.append(
            "camera-1",
            Frame(
                frame_id=f"frame-{index}",
                image=np.zeros((540, 960, 3), dtype=np.uint8),
                timestamp_seconds=float(index),
            ),
        )

    with pytest.raises(RecordingError) as unavailable:
        store.event_clip(
            "alice",
            "camera-1",
            event_time=0.5,
            before=0.5,
            after=0.5,
        )

    assert unavailable.value.code == "ffmpeg_unavailable"



def test_end_override_waits_for_its_actual_source_timestamp(tmp_path: Path) -> None:
    store = recording_store_with_known_frames(
        tmp_path,
        start=0,
        end=10,
        fps=2,
    )

    with pytest.raises(ClipNotReady):
        store.event_clip(
            "alice",
            "camera-1",
            event_time=6,
            before=1,
            end_override=12,
        )

    store.append(
        "camera-1",
        Frame(
            frame_id="frame-after-gap",
            image=np.zeros((540, 960, 3), dtype=np.uint8),
            timestamp_seconds=12,
        ),
    )
    clip = store.event_clip(
        "alice",
        "camera-1",
        event_time=6,
        before=1,
        end_override=12,
    )

    assert clip.start_seconds == 5
    assert clip.end_seconds == 12
    assert clip.frame_ids[-1] == "frame-after-gap"


def test_finish_exports_the_full_recording_and_closes_the_source(tmp_path: Path) -> None:
    store = recording_store_with_known_frames(
        tmp_path,
        start=0,
        end=2,
        fps=1,
    )

    clip = store.finish("alice", "camera-1")

    assert clip.start_seconds == 0
    assert clip.end_seconds == 2
    assert clip.frame_ids == ("frame-000000", "frame-000001", "frame-000002")
    assert store.manifest("alice", "camera-1")["status"] == "finished"
    assert len(decode_all_frames(clip.path)) == 3
    with pytest.raises(RecordingError) as closed:
        store.append(
            "camera-1",
            Frame(
                "after-finish",
                np.zeros((540, 960, 3), dtype=np.uint8),
                timestamp_seconds=3,
            ),
        )
    assert closed.value.code == "source_not_open"



def test_nominal_twenty_fps_with_jitter_is_accepted(tmp_path: Path) -> None:
    store = RecordingStore(root=tmp_path / "recording", ffmpeg_path=ffmpeg_path())
    store.open("alice", "camera-jitter")
    image = np.zeros((540, 960, 3), dtype=np.uint8)

    for index, timestamp in enumerate((0.0, 0.011, 0.052, 0.098, 0.151)):
        store.append(
            "camera-jitter",
            Frame(f"jitter-{index}", image, timestamp_seconds=timestamp),
        )

    assert store.manifest("alice", "camera-jitter")["frame_count"] == 5


def test_sustained_rate_above_twenty_fps_is_rejected_by_window(tmp_path: Path) -> None:
    store = RecordingStore(root=tmp_path / "recording", ffmpeg_path=ffmpeg_path())
    store.open("alice", "camera-fast")
    image = np.zeros((540, 960, 3), dtype=np.uint8)

    for index in range(22):
        timestamp = index / 25.0
        if index == 21:
            with pytest.raises(RecordingError) as too_fast:
                store.append(
                    "camera-fast",
                    Frame(f"fast-{index}", image, timestamp_seconds=timestamp),
                )
            assert too_fast.value.code == "frame_rate_limit"
        else:
            store.append(
                "camera-fast",
                Frame(f"fast-{index}", image, timestamp_seconds=timestamp),
            )


def test_real_simulation_session_frames_are_recorded_for_over_one_second(tmp_path: Path) -> None:
    from visionflow.simulation import SimulationSession

    store = RecordingStore(root=tmp_path / "recording", ffmpeg_path=ffmpeg_path())
    delivered: list[Frame] = []
    enough_frames = threading.Event()

    def record(frame: Frame) -> None:
        store.append(session.source_id, frame)
        delivered.append(frame)
        if len(delivered) >= 25:
            enough_frames.set()

    session = SimulationSession(owner_id="alice", seed=73, on_frame=record)
    store.open("alice", session.source_id)
    try:
        session.start()
        assert enough_frames.wait(5), "SimulationSession did not record 25 frames"
    finally:
        session.close()

    manifest = store.manifest("alice", session.source_id)
    assert len(delivered) >= 25
    assert manifest["frame_count"] == len(delivered)
    assert manifest["frames"][-1]["timestamp_seconds"] - manifest["frames"][0]["timestamp_seconds"] >= 1.0
    assert all(frame["frame_id"].startswith(f"{session.source_id}:") for frame in manifest["frames"])



def test_frame_index_journal_has_one_valid_json_record_per_frame(tmp_path: Path) -> None:
    store = RecordingStore(root=tmp_path / "recording", ffmpeg_path=ffmpeg_path())
    store.open("alice", "camera-index")
    image = np.zeros((540, 960, 3), dtype=np.uint8)
    for index in range(3):
        store.append(
            "camera-index",
            Frame(f"index-{index}", image, timestamp_seconds=index / 10),
        )

    journal = next(store.root.rglob("frames.jsonl"))
    lines = journal.read_text(encoding="utf-8").splitlines()
    entries = [json.loads(line) for line in lines]

    assert len(entries) == 3
    assert [entry["frame_id"] for entry in entries] == ["index-0", "index-1", "index-2"]
    assert all(entry["sha256"] for entry in entries)



def test_frame_producer_can_append_while_ffmpeg_encodes_a_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from concurrent.futures import ThreadPoolExecutor

    store = RecordingStore(root=tmp_path / "recording", ffmpeg_path=ffmpeg_path())
    store.open("alice", "camera-concurrent")
    image = np.zeros((540, 960, 3), dtype=np.uint8)
    for index in range(3):
        store.append(
            "camera-concurrent",
            Frame(f"before-{index}", image, timestamp_seconds=float(index)),
        )

    encoding_started = threading.Event()
    finish_encoding = threading.Event()

    def blocked_encode(args, **kwargs):
        if "-encoders" in args:
            return subprocess.CompletedProcess(args, 0, stdout="libx264", stderr="")
        encoding_started.set()
        if not finish_encoding.wait(5):
            raise subprocess.TimeoutExpired(args, 5)
        Path(args[-1]).write_bytes(b"test-container" * 512)
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr("visionflow.recording.subprocess.run", blocked_encode)
    monkeypatch.setattr(store, "_decode_count_matches", lambda _path, _count: True)

    with ThreadPoolExecutor(max_workers=2) as executor:
        export = executor.submit(
            store.event_clip,
            "alice",
            "camera-concurrent",
            1,
            1,
            1,
        )
        assert encoding_started.wait(3)
        append = executor.submit(
            store.append,
            "camera-concurrent",
            Frame("during-encode", image, timestamp_seconds=3.0),
        )
        appended_before_encode_finished = False
        try:
            append.result(timeout=2)
            appended_before_encode_finished = True
        finally:
            finish_encoding.set()
        clip = export.result(timeout=5)

    assert appended_before_encode_finished
    assert clip.frame_ids == ("before-0", "before-1", "before-2")
    assert store.manifest("alice", "camera-concurrent")["frame_count"] == 4



def _persisted_source_bytes(source_directory: Path) -> int:
    return sum(
        item.stat().st_size
        for item in source_directory.rglob("*")
        if item.is_file()
    )


def test_byte_budget_accounts_for_journal_and_manifest_before_frame_write(
    tmp_path: Path,
) -> None:
    store = RecordingStore(
        root=tmp_path / "recording",
        ffmpeg_path=ffmpeg_path(),
        max_bytes=1_000_000,
    )
    store.open("alice", "camera-budget")
    source_directory = next(store.root.rglob("manifest.json")).parent
    baseline_bytes = _persisted_source_bytes(source_directory)
    image = np.zeros((48, 64, 3), dtype=np.uint8)
    encoded_ok, encoded = cv2.imencode(
        ".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 95]
    )
    assert encoded_ok

    # Leave room for the JPEG itself, but not its JSONL entry and manifest growth.
    store.max_bytes = baseline_bytes + int(encoded.nbytes)
    with pytest.raises(RecordingError) as over_limit:
        store.append(
            "camera-budget",
            Frame("frame-ä", image, timestamp_seconds=0),
        )

    assert over_limit.value.code == "byte_limit"
    assert _persisted_source_bytes(source_directory) == baseline_bytes
    assert not list(source_directory.rglob("*.jpg"))


def test_persisted_source_files_stay_within_budget_after_manifest_and_export(
    tmp_path: Path,
) -> None:
    store = RecordingStore(
        root=tmp_path / "recording",
        ffmpeg_path=ffmpeg_path(),
        max_bytes=2 * 1024 * 1024,
    )
    store.open("alice", "camera-persisted-budget")
    image = np.zeros((48, 64, 3), dtype=np.uint8)
    for index in range(6):
        store.append(
            "camera-persisted-budget",
            Frame(f"frame-{index}", image, timestamp_seconds=index * 0.5),
        )

    clip = store.event_clip(
        "alice",
        "camera-persisted-budget",
        event_time=1.0,
        before=1.0,
        after=1.0,
    )
    manifest = store.manifest("alice", "camera-persisted-budget")
    source_directory = next(store.root.rglob("frames.jsonl")).parent
    persisted_files = [
        item for item in source_directory.rglob("*") if item.is_file()
    ]
    persisted_bytes = sum(item.stat().st_size for item in persisted_files)

    assert clip.path.is_file()
    assert any(item.name == "manifest.json" for item in persisted_files)
    assert any(item.name == "frames.jsonl" for item in persisted_files)
    assert persisted_bytes <= store.max_bytes
    assert manifest["compressed_bytes"] == sum(
        item.stat().st_size
        for item in (source_directory / "frames").glob("*.jpg")
    )
    assert manifest["export_bytes"] == clip.path.stat().st_size



def test_manifest_reservation_matches_utf8_manifest_after_append_and_export(
    tmp_path: Path,
) -> None:
    store = RecordingStore(
        root=tmp_path / "recording",
        ffmpeg_path=ffmpeg_path(),
        max_bytes=2 * 1024 * 1024,
    )
    store.open("alice", "camera-manifest-size")
    image = np.zeros((48, 64, 3), dtype=np.uint8)
    for index in range(5):
        store.append(
            "camera-manifest-size",
            Frame(f"bild-ä-{index}", image, timestamp_seconds=float(index)),
        )
        session = store._sessions[("alice", "camera-manifest-size")]
        assert session.manifest_reserved_bytes == len(store._manifest_bytes(session))

    store.event_clip(
        "alice",
        "camera-manifest-size",
        event_time=2.0,
        before=1.0,
        after=1.0,
    )
    session = store._sessions[("alice", "camera-manifest-size")]
    assert session.manifest_reserved_bytes == len(store._manifest_bytes(session))

    store.manifest("alice", "camera-manifest-size")
    manifest_path = next(store.root.rglob("manifest.json"))
    assert manifest_path.stat().st_size == session.manifest_reserved_bytes
