import importlib

import pytest


def _production_symbol(module_name, symbol_name):
    try:
        module = importlib.import_module(module_name)
    except ModuleNotFoundError as exc:
        if exc.name == module_name:
            pytest.fail(f"{module_name}.{symbol_name} is not implemented", pytrace=False)
        raise

    symbol = getattr(module, symbol_name, None)
    if symbol is None:
        pytest.fail(f"{module_name}.{symbol_name} is not implemented", pytrace=False)
    return symbol


def _store_evidence(*args, **kwargs):
    return _production_symbol("visionflow.evidence", "store_evidence")(*args, **kwargs)


def _trace_recorder():
    return _production_symbol("visionflow.trace", "TraceRecorder")()


def test_evidence_never_overwrites_original(tmp_path):
    original = tmp_path / "original.png"
    original.write_bytes(b"original")

    evidence_root = tmp_path / "evidence"
    saved = _store_evidence(evidence_root, "run-1", "frame-1", b"copy")

    assert original.read_bytes() == b"original"
    assert saved.read_bytes() == b"copy"
    assert saved.parent == evidence_root / "run-1"


@pytest.mark.parametrize(
    ("run_id", "frame_id"),
    [
        ("../outside", "frame-1"),
        ("run-1", "../outside"),
        ("run-1", "nested/frame-1"),
        ("run-1", r"nested\frame-1"),
        (r"C:\outside", "frame-1"),
        ("", "frame-1"),
        (".", "frame-1"),
    ],
)
def test_evidence_rejects_unsafe_path_segments(tmp_path, run_id, frame_id):
    with pytest.raises(ValueError):
        _store_evidence(tmp_path, run_id, frame_id, b"copy")


def test_evidence_never_overwrites_existing_frame_id(tmp_path):
    evidence_root = tmp_path / "evidence"
    saved = _store_evidence(evidence_root, "run-1", "frame-1", b"first")

    with pytest.raises(FileExistsError):
        _store_evidence(evidence_root, "run-1", "frame-1", b"second")

    assert saved.read_bytes() == b"first"


def test_evidence_rejects_symlink_escape_when_supported(tmp_path):
    evidence_root = tmp_path / "evidence"
    evidence_root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    try:
        (evidence_root / "run-1").symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"directory symlinks are unavailable: {exc}")

    with pytest.raises(ValueError):
        _store_evidence(evidence_root, "run-1", "frame-1", b"copy")

    assert list(outside.iterdir()) == []


def test_trace_append_and_export_are_independent_deep_copies():
    trace = _trace_recorder()
    event = {"type": "started", "details": {"frame_ids": ["frame-1"]}}
    expected = {"type": "started", "details": {"frame_ids": ["frame-1"]}}

    trace.append(event)
    event["details"]["frame_ids"].append("mutated")

    exported = trace.export()
    assert exported == [expected]
    exported[0]["details"]["frame_ids"].append("also-mutated")
    assert trace.export() == [expected]


@pytest.mark.parametrize("bad_value", [object(), float("nan")])
def test_trace_rejects_values_outside_json(bad_value):
    trace = _trace_recorder()

    with pytest.raises(ValueError):
        trace.append({"type": "invalid", "value": bad_value})

    assert trace.export() == []


def test_trace_rejects_credentials_and_private_prompts_recursively():
    trace = _trace_recorder()
    unsafe_events = [
        {"type": "model_request", "prompt": "private request"},
        {"type": "tool_failed", "metadata": {"API_Key": "secret"}},
        {"type": "model_request", "details": {"private_prompt": "private request"}},
    ]

    for event in unsafe_events:
        with pytest.raises(ValueError):
            trace.append(event)

    assert trace.export() == []
