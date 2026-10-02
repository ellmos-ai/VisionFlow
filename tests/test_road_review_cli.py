"""CLI contract for starting, reviewing, and resolving owner-scoped road cases."""

from __future__ import annotations

import json

from visionflow.cli import main


def test_road_case_cli_maps_review_commands_to_owner_session_api(monkeypatch, capsys):
    from visionflow import cli

    calls = []

    def record(args, method, path, payload=None):
        calls.append((method, path, payload))
        print(json.dumps({"ok": True}, ensure_ascii=False))
        return 0

    monkeypatch.setattr(cli, "_call_api", record)
    common = ["--server", "http://127.0.0.1:8765", "--session-file", "session.json"]

    commands = [
        ["road", "case", "start", "road-sim-1", "--mode", "fixture", "--fixture-profile", "pending", *common],
        ["road", "case", "status", "case-1", *common],
        ["road", "case", "decide", "case-1", "--version", "4", "--content-hash", "sha256:" + "a" * 64,
         "--decision", "confirm", "--idempotency-key", "confirm-1", *common],
        ["road", "case", "cancel", "case-1", *common],
        ["road", "inbox", *common],
    ]
    for command in commands:
        assert main(command) == 0
        capsys.readouterr()

    assert calls == [
        ("POST", "/road/cases", {
            "source_id": "road-sim-1",
            "mode": "fixture",
            "fixture_profile": "pending",
        }),
        ("GET", "/road/cases/case-1", None),
        ("POST", "/road/cases/case-1/decision", {
            "version": 4,
            "content_hash": "sha256:" + "a" * 64,
            "decision": "confirm",
            "idempotency_key": "confirm-1",
        }),
        ("POST", "/road/cases/case-1/cancel", {}),
        ("GET", "/road/inbox", None),
    ]


def test_road_case_cli_rejects_fixture_profile_for_native_mode_before_http(monkeypatch, capsys):
    from visionflow import cli

    called = False

    def record(*_args, **_kwargs):
        nonlocal called
        called = True
        return 0

    monkeypatch.setattr(cli, "_call_api", record)
    result = main([
        "road", "case", "start", "road-sim-1", "--mode", "gemini_video",
        "--fixture-profile", "pending", "--server", "http://127.0.0.1:8765",
    ])
    output = json.loads(capsys.readouterr().err)

    assert result != 0
    assert called is False
    assert output["error"]["code"] == "fixture_profile_not_allowed"
