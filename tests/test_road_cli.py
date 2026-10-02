from __future__ import annotations

import json
import shutil
import threading
import time
from pathlib import Path

from visionflow.api import create_server
from visionflow.recording import RecordingStore
from visionflow.road_api import RoadSessionService
from visionflow.runtime import create_controller
from visionflow.simulation import SimulationStore
from visionflow.cli import main


def _serve_road_api(tmp_path: Path):
    ffmpeg = shutil.which("ffmpeg")
    assert ffmpeg
    sessions = SimulationStore()
    recordings = RecordingStore(tmp_path / "recordings", Path(ffmpeg))
    holder = {}

    def source_override(config, _max_frames):
        if config.kind == "camera" and config.source.startswith("road-sim-"):
            service = holder.get("service")
            return None if service is None else service.open_reader(config.source)
        return None

    controller = create_controller(tmp_path / "controller", source_override=source_override)
    service = RoadSessionService(sessions, recordings, controller)
    holder["service"] = service
    server = create_server(
        controller, host="127.0.0.1", port=0, data_dir=tmp_path / "api",
        road_sessions=service,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address[:2]
    return f"http://{host}:{port}", service, server, thread


def test_cli_road_commands_reuse_session_export_real_clip_and_never_overwrite(tmp_path, capsys):
    server_url, service, server, thread = _serve_road_api(tmp_path)
    session_file = tmp_path / "cli-session.json"
    common = ["--server", server_url, "--session-file", str(session_file)]
    try:
        result = main(["road", "create", "--seed", "17", *common])
        created = json.loads(capsys.readouterr().out)
        assert result == 0
        source_id = created["source_id"]

        result = main(["road", "inject", source_id, "leaf", *common])
        injected = json.loads(capsys.readouterr().out)
        assert result == 0
        assert injected == {"source_id": source_id, "accepted": True}

        result = main(["road", "recording", source_id, *common])
        recording = json.loads(capsys.readouterr().out)
        assert result == 0
        assert recording["source_id"] == source_id
        assert recording["frame_count"] > 0
        assert recording["source_fps"] > 0

        time.sleep(0.25)
        result = main(["road", "stop", source_id, *common])
        stopped = json.loads(capsys.readouterr().out)
        assert result == 0
        clip_id = stopped["clip"]["clip_id"]
        assert session_file.is_file()

        output = tmp_path / "road-evidence.mp4"
        result = main(["road", "clip", clip_id, "--output", str(output), *common])
        downloaded = json.loads(capsys.readouterr().out)
        assert result == 0
        assert downloaded["clip_id"] == clip_id
        assert output.read_bytes().startswith(b"\x00\x00\x00")
        before = output.read_bytes()

        result = main(["road", "clip", clip_id, "--output", str(output), *common])
        error = json.loads(capsys.readouterr().err)
        assert result != 0
        assert error["error"]["code"] == "output_exists"
        assert output.read_bytes() == before
    finally:
        service.close()
        server.shutdown()
        thread.join(timeout=3)
        server.server_close()



def test_cli_clip_write_error_never_deletes_preexisting_output(tmp_path, monkeypatch, capsys):
    import hashlib
    from types import SimpleNamespace
    from pathlib import Path

    import visionflow.cli as cli

    output = tmp_path / "already-there.mp4"
    original = b"user-owned bytes"
    output.write_bytes(original)
    payload = b"fake but hash-verified test clip"

    class Client:
        def request_bytes(self, path):
            return 200, payload, {"x-clip-sha256": hashlib.sha256(payload).hexdigest()}

    monkeypatch.setattr(cli, "_client", lambda _args: Client())
    real_open = Path.open

    def fail_only_target(path, *args, **kwargs):
        if path == output and args and args[0] == "xb":
            raise OSError("simulated denied create")
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", fail_only_target)
    result = main([
        "road", "clip", "clip-id", "--output", str(output),
        "--server", "http://127.0.0.1:8765",
        "--session-file", str(tmp_path / "unused-session.json"),
    ])

    error = json.loads(capsys.readouterr().err)
    assert result == 2
    assert error["error"]["code"] == "output_write_failed"
    assert output.read_bytes() == original
