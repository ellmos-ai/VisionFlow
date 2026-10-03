from __future__ import annotations

"""Standalone real-browser acceptance runner for the VisionFlow road GUI.

The red phase preserves the pre-repair diagnostic. Fast and full use the public
four-room UI and real local HTTP/API flows. No response or provider is mocked.
"""

import argparse
import hashlib
import json
import os
import re
import socket
import subprocess
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlparse


REPO = Path(__file__).resolve().parents[1]
LOOPBACK_HOSTS = {"127.0.0.1", "localhost"}
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
SOURCE_RECORDING_LIMIT_SECONDS = 120.0
DEADLINE_POLL_INTERVAL_SECONDS = 0.4
DEADLINE_POLL_TOLERANCE_SECONDS = DEADLINE_POLL_INTERVAL_SECONDS + 0.1


def fresh_output_directory(argument: str, phase: str) -> Path:
    root = (REPO / ".local" / "acceptance").resolve()
    root.mkdir(parents=True, exist_ok=True)
    output = Path(argument).expanduser().resolve()
    prefix = "gui-controller-" + phase + "-"
    if output.parent != root or not output.name.startswith(prefix):
        raise ValueError("--output must be a fresh " + prefix + "* directory under " + str(root))
    if output.exists():
        raise FileExistsError("Acceptance output path is not fresh: " + str(output))
    output.mkdir()
    return output


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def wait_for_server(port: int, process: subprocess.Popen[bytes], timeout: float = 30.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError("VisionFlow server exited during startup")
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.25):
                return
        except OSError:
            time.sleep(0.1)
    raise TimeoutError("VisionFlow server did not open its loopback port")


def stop_server_tree(process: subprocess.Popen[bytes] | None) -> dict[str, Any]:
    if process is None:
        return {"owned_server_stopped": True, "tree_kill": "not_started"}
    if process.poll() is not None:
        return {
            "owned_server_stopped": True,
            "tree_kill": "server_already_exited",
            "server_exit_code": process.returncode,
        }

    try:
        result = subprocess.run(
            ["taskkill.exe", "/PID", str(process.pid), "/T", "/F"],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=10,
            creationflags=CREATE_NO_WINDOW,
        )
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        return {
            "owned_server_stopped": process.poll() is not None,
            "tree_kill": "taskkill",
            "taskkill_exit_code": result.returncode,
            "server_exit_code": process.returncode,
        }
    except Exception as exc:
        try:
            process.terminate()
            process.wait(timeout=5)
        except Exception:
            try:
                process.kill()
                process.wait(timeout=5)
            except Exception:
                pass
        return {
            "owned_server_stopped": process.poll() is not None,
            "tree_kill": "direct_fallback",
            "cleanup_error": type(exc).__name__ + ": " + str(exc),
            "server_exit_code": process.poll(),
        }


def get_json(page: Any, path: str) -> dict[str, Any]:
    result = page.evaluate(
        """async (path) => {
          const response = await fetch(path, {
            credentials: "same-origin",
            cache: "no-store"
          });
          let payload = null;
          try { payload = await response.json(); } catch (_) {}
          return {status: response.status, payload};
        }""",
        path,
    )
    if not isinstance(result, dict) or result.get("status") != 200:
        raise RuntimeError(
            "Real API GET failed for "
            + path
            + ": "
            + json.dumps(result, ensure_ascii=False)
        )
    payload = result.get("payload")
    if not isinstance(payload, dict):
        raise RuntimeError("Real API GET returned a non-object for " + path)
    return payload


def wait_for_real_case(page: Any, case_id: str, timeout: float = 60.0) -> tuple[dict[str, Any], dict[str, Any]]:
    deadline = time.monotonic() + timeout
    last_case: dict[str, Any] = {}
    last_model: dict[str, Any] = {}
    case_path = "/road/cases/" + case_id
    model_path = case_path + "/model"

    while time.monotonic() < deadline:
        last_case = get_json(page, case_path)
        last_model = get_json(page, model_path)
        exchanges = last_model.get("exchanges")
        if (
            last_case.get("phase") == "human_review"
            and isinstance(last_case.get("clip"), dict)
            and isinstance(last_case.get("exchange_ids"), list)
            and isinstance(exchanges, list)
            and exchanges
        ):
            return last_case, last_model
        time.sleep(0.25)

    raise TimeoutError(
        "Backend did not reach human_review with ClipRef and ModelExchange: "
        + json.dumps(
            {
                "phase": last_case.get("phase"),
                "has_clip": isinstance(last_case.get("clip"), dict),
                "exchange_ids": last_case.get("exchange_ids"),
                "exchange_count": len(last_model.get("exchanges", []))
                if isinstance(last_model.get("exchanges"), list)
                else None,
            },
            ensure_ascii=False,
        )
    )


def visible_state(page: Any, selector: str) -> dict[str, Any]:
    return page.evaluate(
        """(selector) => {
          const element = document.querySelector(selector);
          if (!element) return {present: false, visible: false, text: ""};
          const style = getComputedStyle(element);
          const rects = element.getClientRects();
          const visible = rects.length > 0 &&
            style.display !== "none" &&
            style.visibility !== "hidden" &&
            Number(style.opacity || "1") > 0;
          const text = element.innerText || element.textContent || "";
          return {
            present: true,
            visible,
            text_preview: text.slice(0, 500),
            text_length: text.length,
            text_truncated: text.length > 500,
            display: style.display,
            visibility: style.visibility,
            rect_count: rects.length
          };
        }""",
        selector,
    )


def inspect_native_player(page: Any, expected_clip_id: str, expected_hash: str) -> dict[str, Any]:
    return page.evaluate(
        """({clipId, sha256}) => {
          const root = document.querySelector("#road-workplace-root");
          const candidates = root ? Array.from(root.querySelectorAll("video")) : [];
          const player = candidates.find((video) =>
            video.dataset.clipId === clipId ||
            (video.currentSrc && video.currentSrc.includes(clipId))
          ) || candidates[0] || null;
          if (!player) {
            return {
              root_present: Boolean(root),
              root_visible: Boolean(root && root.getClientRects().length),
              video_count: candidates.length,
              player_present: false,
              player_visible: false,
              clip_id_matches: false,
              hash_matches: false
            };
          }
          const style = getComputedStyle(player);
          const rects = player.getClientRects();
          return {
            root_present: Boolean(root),
            root_visible: Boolean(root && root.getClientRects().length),
            video_count: candidates.length,
            player_present: true,
            player_visible: rects.length > 0 &&
              style.display !== "none" &&
              style.visibility !== "hidden" &&
              Number(style.opacity || "1") > 0,
            clip_id: player.dataset.clipId || null,
            clip_id_matches: player.dataset.clipId === clipId,
            clip_hash: player.dataset.clipHash || null,
            hash_matches: player.dataset.clipHash === sha256,
            controls: Boolean(player.controls),
            ready_state: player.readyState,
            video_width: player.videoWidth,
            video_height: player.videoHeight
          };
        }""",
        {"clipId": expected_clip_id, "sha256": expected_hash},
    )


def api_get_json(context: Any, base_url: str, path: str, report: dict[str, Any], expected_status: int = 200) -> Any:
    response = context.request.get(base_url + path, timeout=15000)
    key = "GET " + path + " -> " + str(response.status)
    counts = report.setdefault("http_get_counts", {})
    counts[key] = counts.get(key, 0) + 1
    try:
        payload = response.json()
    except Exception as exc:
        raise RuntimeError("Real API GET returned invalid JSON for " + path) from exc
    if response.status != expected_status:
        raise RuntimeError(
            "Real API GET returned HTTP "
            + str(response.status)
            + " for "
            + path
            + ": "
            + json.dumps(payload, ensure_ascii=False)[:800]
        )
    report.setdefault("api_reads", {})[path] = report.setdefault("api_reads", {}).get(path, 0) + 1
    return payload


def wait_for_job_case(
    context: Any,
    base_url: str,
    source_id: str,
    job_id: str,
    report: dict[str, Any],
    *,
    expected_phases: set[str] | None = None,
    min_exchanges: int = 1,
    expected_exchange_status: str = "complete",
    expected_exchange_statuses: set[str] | None = None,
    expected_job_states: set[str] | None = None,
    timeout: float = 90.0,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    accepted_job_states = expected_job_states or {"completed"}
    accepted_exchange_statuses = expected_exchange_statuses or {expected_exchange_status}
    deadline = time.monotonic() + timeout
    jobs_path = "/road/sessions/" + quote(source_id, safe="") + "/jobs"
    last_job: dict[str, Any] = {}
    last_case: dict[str, Any] = {}
    last_model: dict[str, Any] = {}
    while time.monotonic() < deadline:
        jobs_payload = api_get_json(context, base_url, jobs_path, report)
        jobs = jobs_payload.get("jobs") if isinstance(jobs_payload, dict) else None
        if not isinstance(jobs, list):
            raise RuntimeError("Real jobs endpoint did not return a jobs array")
        last_job = next(
            (row for row in jobs if isinstance(row, dict) and row.get("job_id") == job_id),
            {},
        )
        case_id = last_job.get("case_id")
        if isinstance(case_id, str) and case_id:
            case_path = "/road/cases/" + quote(case_id, safe="")
            last_case = api_get_json(context, base_url, case_path, report)
            last_model = api_get_json(context, base_url, case_path + "/model", report)
            exchanges = last_model.get("exchanges") if isinstance(last_model, dict) else None
            ids = last_case.get("exchange_ids") if isinstance(last_case, dict) else None
            matched = [
                item for item in exchanges
                if isinstance(item, dict) and isinstance(ids, list) and item.get("exchange_id") in ids
            ] if isinstance(exchanges, list) else []
            phase_ok = expected_phases is None or last_case.get("phase") in expected_phases
            status_ok = all(item.get("status") in accepted_exchange_statuses for item in matched)
            clip_ok = isinstance(last_case.get("clip"), dict)
            job_done = last_job.get("state") in accepted_job_states
            if phase_ok and len(matched) >= min_exchanges and status_ok and clip_ok and job_done:
                report.setdefault("job_waits", {})[job_id] = {
                    "polls": report.get("job_poll_counts", {}).get(job_id, 0),
                    "last_state": last_job.get("state"),
                }
                return last_job, last_case, last_model, matched
        report.setdefault("job_poll_counts", {})[job_id] = report.get("job_poll_counts", {}).get(job_id, 0) + 1
        time.sleep(0.25)
    raise TimeoutError(
        "Real job did not reach its expected Case/ModelExchange state: "
        + json.dumps(
            {
                "job_id": job_id,
                "state": last_job.get("state"),
                "case_id": last_job.get("case_id"),
                "phase": last_case.get("phase"),
                "exchange_ids": last_case.get("exchange_ids"),
                "exchange_count": len(last_model.get("exchanges", []))
                if isinstance(last_model.get("exchanges"), list)
                else None,
            },
            ensure_ascii=False,
        )
    )


def submit_scenario_job(page: Any, source_id: str, event: str, profile: str, report: dict[str, Any]) -> dict[str, Any]:
    event_buttons = {
        "wrong_way": "#btn-sim-wrongway",
        "bird": "#btn-sim-bird",
        "leaf": "#btn-sim-leaf",
    }
    if event not in event_buttons:
        raise ValueError("Unsupported browser scenario event: " + event)

    mode_select = page.locator("#road-model-mode")
    profile_select = page.locator("#road-fixture-profile")
    mode_select.wait_for(state="visible", timeout=10000)
    profile_select.wait_for(state="visible", timeout=10000)
    mode_select.select_option("fixture")
    profile_select.select_option(profile)
    selected = {
        "mode": mode_select.input_value(),
        "fixture_profile": profile_select.input_value(),
    }
    if selected != {"mode": "fixture", "fixture_profile": profile}:
        raise AssertionError("Visible fixture controls do not show the explicitly selected job profile")

    expected_path = "/road/sessions/" + quote(source_id, safe="") + "/jobs"
    with page.expect_response(
        lambda response: response.request.method == "POST"
        and urlparse(response.url).hostname in LOOPBACK_HOSTS
        and urlparse(response.url).path == expected_path,
        timeout=15000,
    ) as response_info:
        page.locator(event_buttons[event]).click()
    response = response_info.value
    try:
        payload = response.json()
    except Exception as exc:
        raise AssertionError("Real job POST did not return JSON") from exc
    try:
        body = json.loads(response.request.post_data or "{}")
    except json.JSONDecodeError as exc:
        raise AssertionError("Real job POST body was not JSON") from exc
    if response.status != 202:
        raise AssertionError("Real scenario job POST returned HTTP " + str(response.status))
    expected_body = {"event": event, "mode": "fixture", "fixture_profile": profile}
    if body != expected_body:
        raise AssertionError(
            "Real job POST did not carry the explicitly selected event/mode/profile: "
            + json.dumps(body, ensure_ascii=False)
        )
    job = payload.get("job") if isinstance(payload, dict) else None
    if not isinstance(job, dict):
        raise AssertionError("Real job POST did not return a job object")
    for key, value in {
        "source_id": source_id,
        "event": event,
        "mode": "fixture",
        "fixture_profile": profile,
    }.items():
        if job.get(key) != value:
            raise AssertionError("Job snapshot disagrees with selected " + key)
    if not isinstance(job.get("job_id"), str) or not job["job_id"]:
        raise AssertionError("Real job snapshot has no job_id")
    report.setdefault("job_submissions", []).append(
        {
            "http_status": response.status,
            "path": expected_path,
            "request": body,
            "selected_controls": selected,
            "job": {
                key: job.get(key)
                for key in ("job_id", "source_id", "event", "mode", "fixture_profile", "state", "case_id", "run_id")
            },
        }
    )
    return job


def enter_room(page: Any, number: int) -> None:
    page.locator("#nav-node-room-" + str(number)).click()
    page.locator("#cartoon-room-" + str(number)).wait_for(state="visible", timeout=10000)


def start_real_source(page: Any, report: dict[str, Any], initial_profile: str = "pending") -> str:
    mode_select = page.locator("#road-model-mode")
    profile_select = page.locator("#road-fixture-profile")
    mode_select.wait_for(state="visible", timeout=10000)
    profile_select.wait_for(state="visible", timeout=10000)
    mode_select.select_option("fixture")
    profile_select.select_option(initial_profile)
    selected = {"mode": mode_select.input_value(), "fixture_profile": profile_select.input_value()}
    if selected != {"mode": "fixture", "fixture_profile": initial_profile}:
        raise AssertionError("Source-start controls do not show an explicit fixture selection")

    with page.expect_response(
        lambda response: response.request.method == "POST"
        and urlparse(response.url).hostname in LOOPBACK_HOSTS
        and urlparse(response.url).path == "/road/sessions",
        timeout=15000,
    ) as response_info:
        create_button = page.locator("#road-camera-create")
        create_button.wait_for(state="visible", timeout=10000)
        page.wait_for_function(
            "() => { const button = document.querySelector('#road-camera-create'); return button && !button.disabled; }",
            timeout=10000,
        )
        create_button.click()
    response = response_info.value
    try:
        payload = response.json()
    except Exception as exc:
        raise AssertionError("Real source-start POST did not return JSON") from exc
    if response.status != 201 or not isinstance(payload, dict):
        raise AssertionError("Real source-start POST did not return HTTP 201 with a JSON object")
    source_id = payload.get("source_id")
    if not isinstance(source_id, str) or not source_id:
        raise AssertionError("Real source-start response has no source_id")
    page.wait_for_function(
        """(sourceId) => {
          const root = document.querySelector('#road-camera-root');
          const mounted = root && root.querySelector('.road-camera');
          return (root && root.dataset.sourceId === sourceId) ||
            (mounted && mounted.dataset.sourceId === sourceId);
        }""",
        arg=source_id,
        timeout=10000,
    )
    report.setdefault("sources", []).append(
        {"source_id": source_id, "created_http_status": response.status, "initial_profile": initial_profile}
    )
    return source_id


def summarize_clip(clip: dict[str, Any]) -> dict[str, Any]:
    return {
        key: clip.get(key)
        for key in ("clip_id", "source_id", "version", "sha256", "start_seconds", "end_seconds")
    }


def verify_received_clip(context: Any, base_url: str, clip: dict[str, Any], report: dict[str, Any], case_id: str) -> dict[str, Any]:
    clip_id = clip.get("clip_id")
    expected_hash = clip.get("sha256")
    if not isinstance(clip_id, str) or not clip_id or not isinstance(expected_hash, str) or not expected_hash:
        raise AssertionError("Server ClipRef is missing clip_id or sha256")
    response = context.request.get(
        base_url + "/road/clips/" + quote(clip_id, safe=""),
        timeout=15000,
    )
    data = response.body()
    received = hashlib.sha256(data).hexdigest()
    content_type = response.headers.get("content-type", "")
    if response.status != 200 or "video/mp4" not in content_type.lower():
        raise AssertionError("Real ClipRef GET did not return HTTP 200 video/mp4")
    if received != expected_hash:
        raise AssertionError("SHA-256 of received MP4 bytes differs from the server ClipRef")
    row = {
        "case_id": case_id,
        "clip_id": clip_id,
        "source_id": clip.get("source_id"),
        "status": response.status,
        "content_type": content_type,
        "bytes": len(data),
        "sha256_expected": expected_hash,
        "sha256_received": received,
        "sha256_matches": True,
    }
    report.setdefault("clip_downloads", []).append(row)
    return row


def verify_case_exchange_identity(
    case: dict[str, Any],
    model: dict[str, Any],
    exchanges: list[dict[str, Any]],
    expected_profile: str,
) -> list[dict[str, Any]]:
    case_id = case.get("case_id")
    source_id = case.get("source_id")
    if not isinstance(case_id, str) or not case_id or not isinstance(source_id, str) or not source_id:
        raise AssertionError("Real Case snapshot is missing case/source identity")
    if model.get("case_id") != case_id:
        raise AssertionError("ModelExchange response belongs to another Case")
    if case.get("mode") != "fixture" or case.get("fixture_profile") != expected_profile:
        raise AssertionError("Case snapshot does not retain the explicitly selected fixture profile")
    if not isinstance(case.get("run_id"), str) or not case.get("run_id"):
        raise AssertionError("Real Case snapshot is missing its actual Run ID")
    if not isinstance(case.get("clip"), dict):
        raise AssertionError("Real Case snapshot has no ClipRef")
    for exchange in exchanges:
        if exchange.get("case_id") != case_id or exchange.get("run_id") != case.get("run_id"):
            raise AssertionError("ModelExchange Case/Run identity differs from its real Case")
        if exchange.get("fixture_profile") != expected_profile or exchange.get("mode") != "fixture":
            raise AssertionError("ModelExchange does not preserve the explicitly selected profile")
        clip = exchange.get("clip")
        if not isinstance(clip, dict) or clip.get("source_id") != source_id:
            raise AssertionError("ModelExchange has no same-source ClipRef")
    latest = exchanges[-1]
    if latest.get("clip", {}).get("clip_id") != case["clip"].get("clip_id"):
        raise AssertionError("Latest ModelExchange ClipRef differs from Case ClipRef")
    if latest.get("clip", {}).get("sha256") != case["clip"].get("sha256"):
        raise AssertionError("Latest ModelExchange ClipRef hash differs from Case ClipRef")
    return exchanges


def select_real_case(page: Any, case_id: str) -> dict[str, Any]:
    ticket = page.locator('#datacenter-queue-list button[data-testid="case-ticket"][data-case-id="' + case_id + '"]')
    ticket.wait_for(state="visible", timeout=15000)
    ticket.focus()
    ticket.press("Enter")
    page.wait_for_function(
        """(caseId) => {
          const root = document.querySelector('.road-cartoon-stage');
          return root && root.dataset.selectedCaseId === caseId;
        }""",
        arg=case_id,
        timeout=10000,
    )
    pressed = ticket.get_attribute("aria-pressed")
    if pressed != "true":
        raise AssertionError("Selected case ticket is not marked aria-pressed=true")
    return {
        "case_id": case_id,
        "aria_pressed": pressed,
        "selected_case_id": page.locator(".road-cartoon-stage").get_attribute("data-selected-case-id"),
    }


def visible_media_state(page: Any, case_id: str, clip: dict[str, Any]) -> dict[str, Any]:
    return page.evaluate(
        """({caseId, clipId, sha256}) => {
          const root = document.querySelector('#road-workplace-root');
          const videos = root ? Array.from(root.querySelectorAll('video')) : [];
          const player = videos.find(video =>
            video.dataset.caseId === caseId &&
            video.dataset.clipId === clipId &&
            video.dataset.clipHash === sha256
          );
          if (!player) return {present: false, visible: false, video_count: videos.length};
          const style = getComputedStyle(player);
          const rect = player.getBoundingClientRect();
          return {
            present: true,
            visible: rect.width > 0 && rect.height > 0 &&
              style.display !== 'none' && style.visibility !== 'hidden' &&
              Number(style.opacity || '1') > 0,
            case_id: player.dataset.caseId,
            clip_id: player.dataset.clipId,
            clip_hash: player.dataset.clipHash,
            controls: Boolean(player.controls),
            ready_state: player.readyState,
            video_width: player.videoWidth,
            video_height: player.videoHeight,
            duration: player.duration,
            paused: player.paused,
            error_code: player.error ? player.error.code : null
          };
        }""",
        {
            "caseId": case_id,
            "clipId": clip.get("clip_id"),
            "sha256": clip.get("sha256"),
        },
    )


def verify_native_playback(
    page: Any,
    case_id: str,
    clip: dict[str, Any],
    *,
    report: dict[str, Any],
) -> dict[str, Any]:
    page.wait_for_function(
        """({caseId, clipId, sha256}) => {
          const root = document.querySelector('#road-workplace-root');
          const player = root && Array.from(root.querySelectorAll('video')).find(video =>
            video.dataset.caseId === caseId &&
            video.dataset.clipId === clipId &&
            video.dataset.clipHash === sha256
          );
          return player && player.readyState >= 1 && player.videoWidth > 0 && player.videoHeight > 0;
        }""",
        arg={"caseId": case_id, "clipId": clip.get("clip_id"), "sha256": clip.get("sha256")},
        timeout=20000,
    )
    # Locate by every server-backed identity and bring the exact native player
    # into view before exercising playback.
    selector = (
        '#road-workplace-root video[data-case-id="' + case_id
        + '"][data-clip-id="' + str(clip.get("clip_id"))
        + '"][data-clip-hash="' + str(clip.get("sha256")) + '"]'
    )
    player = page.locator(selector)
    if player.count() != 1:
        raise AssertionError("No workplace video matches the selected Case/ClipRef identities")
    player.scroll_into_view_if_needed(timeout=5000)
    if not player.is_visible():
        raise AssertionError("The exact selected Case/Clip video is not visible after scrolling")
    start_state = player.evaluate("""video => ({
      current_time: video.currentTime,
      paused: video.paused,
      ready_state: video.readyState,
      video_width: video.videoWidth,
      video_height: video.videoHeight,
      duration: video.duration,
      error_code: video.error ? video.error.code : null,
      rect: (() => {
        const r = video.getBoundingClientRect();
        return {left: r.left, top: r.top, right: r.right, bottom: r.bottom};
      })(),
    })""")
    start = start_state["current_time"]
    identity = {"case_id": case_id, "clip_id": clip.get("clip_id"), "sha256": clip.get("sha256")}
    attempt = {
        "identity": identity,
        "scrolled_into_view": True,
        "initial_state": start_state,
    }
    report.setdefault("native_playback_attempts", {})[case_id] = attempt
    play_started = time.monotonic()
    playback_wait_error = None
    try:
        player.evaluate("(video) => video.play()")
        page.wait_for_function(
            """({caseId, clipId, sha256, start}) => {
              const root = document.querySelector('#road-workplace-root');
              const video = root && Array.from(root.querySelectorAll('video')).find(item =>
                item.dataset.caseId === caseId &&
                item.dataset.clipId === clipId &&
                item.dataset.clipHash === sha256
              );
              return video && !video.paused && Number.isFinite(video.currentTime) &&
                video.currentTime > start;
            }""",
            arg={
                "caseId": case_id,
                "clipId": clip.get("clip_id"),
                "sha256": clip.get("sha256"),
                "start": start,
            },
            timeout=5000,
        )
    except Exception as exc:
        playback_wait_error = type(exc).__name__ + ": " + str(exc)
    after_play = player.evaluate("""video => ({
      current_time: video.currentTime,
      paused: video.paused,
      ready_state: video.readyState,
      error_code: video.error ? video.error.code : null,
    })""")
    playback_wait_seconds = round(time.monotonic() - play_started, 3)
    attempt.update({
        "playback_wait_seconds": playback_wait_seconds,
        "playback_wait_error": playback_wait_error,
        "after_play_state": after_play,
    })
    if (
        playback_wait_error is not None
        or after_play["paused"]
        or after_play["current_time"] <= start
    ):
        raise AssertionError(
            "Native workplace video did not resume and advance within five seconds: "
            + json.dumps(attempt)
        )
    page.evaluate(
        """({caseId, clipId, sha256}) => {
          const root = document.querySelector('#road-workplace-root');
          const video = Array.from(root.querySelectorAll('video')).find(item =>
            item.dataset.caseId === caseId && item.dataset.clipId === clipId &&
            item.dataset.clipHash === sha256
          );
          video.pause();
        }""",
        {"caseId": case_id, "clipId": clip.get("clip_id"), "sha256": clip.get("sha256")},
    )
    page.wait_for_function(
        """({caseId, clipId, sha256}) => {
          const root = document.querySelector('#road-workplace-root');
          const video = Array.from(root.querySelectorAll('video')).find(item =>
            item.dataset.caseId === caseId && item.dataset.clipId === clipId &&
            item.dataset.clipHash === sha256
          );
          return video && video.paused;
        }""",
        arg={"caseId": case_id, "clipId": clip.get("clip_id"), "sha256": clip.get("sha256")},
        timeout=5000,
    )
    target = page.evaluate(
        """({caseId, clipId, sha256}) => {
          const root = document.querySelector('#road-workplace-root');
          const video = Array.from(root.querySelectorAll('video')).find(item =>
            item.dataset.caseId === caseId && item.dataset.clipId === clipId &&
            item.dataset.clipHash === sha256
          );
          if (!Number.isFinite(video.duration) || video.duration <= 0.2) return null;
          const target = Math.min(1.0, video.duration / 2);
          video.currentTime = target;
          return target;
        }""",
        {"caseId": case_id, "clipId": clip.get("clip_id"), "sha256": clip.get("sha256")},
    )
    if target is None:
        raise AssertionError("Native workplace video has no seekable duration")
    page.wait_for_function(
        """({caseId, clipId, sha256, target}) => {
          const root = document.querySelector('#road-workplace-root');
          const video = Array.from(root.querySelectorAll('video')).find(item =>
            item.dataset.caseId === caseId && item.dataset.clipId === clipId &&
            item.dataset.clipHash === sha256
          );
          return video && Math.abs(video.currentTime - target) < 0.25;
        }""",
        arg={"caseId": case_id, "clipId": clip.get("clip_id"), "sha256": clip.get("sha256"), "target": target},
        timeout=5000,
    )
    return {
        "play_advanced": True,
        "pause_worked": True,
        "seek_target": target,
        "seek_worked": True,
        "initial_time": start,
        "time_after_play": after_play["current_time"],
        "playback_wait_seconds": playback_wait_seconds,
    }


def wait_for_case_phase(
    context: Any,
    base_url: str,
    case_id: str,
    report: dict[str, Any],
    expected_phases: set[str],
    *,
    timeout: float = 15.0,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    last: dict[str, Any] = {}
    path = "/road/cases/" + quote(case_id, safe="")
    while time.monotonic() < deadline:
        last = api_get_json(context, base_url, path, report)
        if last.get("phase") in expected_phases:
            return last
        time.sleep(0.25)
    raise TimeoutError(
        "Real Case did not reach an expected server phase: "
        + json.dumps({"case_id": case_id, "phase": last.get("phase"), "expected": sorted(expected_phases)})
    )


def inbox_records(context: Any, base_url: str, report: dict[str, Any]) -> list[dict[str, Any]]:
    payload = api_get_json(context, base_url, "/road/inbox", report)
    rows = payload if isinstance(payload, list) else payload.get("records", []) if isinstance(payload, dict) else []
    if not isinstance(rows, list) or any(not isinstance(item, dict) for item in rows):
        raise AssertionError("Real inbox endpoint did not return a list of receipt records")
    return rows


def receipt_ids(rows: list[dict[str, Any]]) -> list[str]:
    return sorted(
        str(row.get("receipt_id") or row.get("message_id") or row.get("id"))
        for row in rows
        if row.get("receipt_id") or row.get("message_id") or row.get("id")
    )


def click_reject_for_selected_case(page: Any, case_id: str, report: dict[str, Any]) -> dict[str, Any]:
    button = page.locator("#btn-operator-deny")
    button.wait_for(state="visible", timeout=10000)
    if button.is_disabled():
        raise AssertionError("The actual server-backed selected case has no enabled reject action")
    path = "/road/cases/" + quote(case_id, safe="") + "/decision"
    with page.expect_response(
        lambda response: response.request.method == "POST"
        and urlparse(response.url).hostname in LOOPBACK_HOSTS
        and urlparse(response.url).path == path,
        timeout=15000,
    ) as response_info:
        button.click()
    response = response_info.value
    payload = response.json()
    try:
        body = json.loads(response.request.post_data or "{}")
    except json.JSONDecodeError as exc:
        raise AssertionError("Decision POST did not contain JSON") from exc
    if response.status != 200 or body.get("decision") != "reject":
        raise AssertionError("Visible reject action did not POST a real reject decision")
    report.setdefault("decisions", []).append(
        {
            "case_id": case_id,
            "decision": body.get("decision"),
            "version": body.get("version"),
            "content_hash": body.get("content_hash"),
            "http_status": response.status,
            "result_phase": payload.get("phase"),
            "first_receipt_id": (payload.get("first_receipt") or {}).get("receipt_id"),
            "correction_receipt_id": (payload.get("correction_receipt") or {}).get("receipt_id"),
        }
    )
    return payload


def click_confirm_for_selected_case(page: Any, case_id: str, report: dict[str, Any]) -> dict[str, Any]:
    button = page.locator("#btn-operator-confirm")
    button.wait_for(state="visible", timeout=10000)
    if button.is_disabled():
        raise AssertionError("The actual server-backed selected case has no enabled human confirm action")
    if "Gefahr bestätigen" not in (button.text_content() or ""):
        raise AssertionError("The visible confirm control is not the human danger-confirmation action")
    path = "/road/cases/" + quote(case_id, safe="") + "/decision"
    with page.expect_response(
        lambda response: response.request.method == "POST"
        and urlparse(response.url).hostname in LOOPBACK_HOSTS
        and urlparse(response.url).path == path,
        timeout=15000,
    ) as response_info:
        button.click()
    response = response_info.value
    payload = response.json()
    try:
        body = json.loads(response.request.post_data or "{}")
    except json.JSONDecodeError as exc:
        raise AssertionError("Human confirm POST did not contain JSON") from exc
    receipt = payload.get("first_receipt")
    if (
        response.status != 200
        or body.get("decision") != "confirm"
        or payload.get("phase") != "reported"
        or not isinstance(receipt, dict)
        or receipt.get("reason") != "human_confirmed"
        or receipt.get("case_id") != case_id
    ):
        raise AssertionError("Visible human confirmation did not create its actual human_confirmed receipt")
    report.setdefault("decisions", []).append(
        {
            "case_id": case_id,
            "decision": body.get("decision"),
            "version": body.get("version"),
            "content_hash": body.get("content_hash"),
            "http_status": response.status,
            "result_phase": payload.get("phase"),
            "first_receipt_id": receipt.get("receipt_id"),
            "first_receipt_reason": receipt.get("reason"),
            "correction_receipt_id": (payload.get("correction_receipt") or {}).get("receipt_id"),
            "visible_control": button.get_attribute("id"),
        }
    )
    return payload


def process_real_job(
    page: Any,
    context: Any,
    base_url: str,
    source_id: str,
    event: str,
    profile: str,
    report: dict[str, Any],
    *,
    expected_phases: set[str],
    min_exchanges: int = 1,
    expected_job_states: set[str] | None = None,
    expected_exchange_statuses: set[str] | None = None,
    timeout: float = 90.0,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    job = submit_scenario_job(page, source_id, event, profile, report)
    if job.get("case_id") is not None:
        raise AssertionError("A newly accepted UI job already claimed a Case before server processing")
    case_id = job["case_id"]
    job, case, model, exchanges = wait_for_job_case(
        context,
        base_url,
        source_id,
        job["job_id"],
        report,
        expected_phases=expected_phases,
        min_exchanges=min_exchanges,
        expected_job_states=expected_job_states,
        expected_exchange_statuses=expected_exchange_statuses,
        timeout=timeout,
    )
    case_id = case.get("case_id")
    if not isinstance(case_id, str) or not case_id:
        raise AssertionError("Completed server job did not identify an actual Case")
    if job.get("case_id") != case_id or job.get("source_id") != source_id:
        raise AssertionError("Server job-to-Case identity does not match the selected source")
    if job.get("run_id") != case.get("run_id"):
        raise AssertionError("Server job Run ID differs from the returned Case Run ID")
    verify_case_exchange_identity(case, model, exchanges, profile)
    expected_ids = case.get("exchange_ids")
    actual_ids = [item.get("exchange_id") for item in exchanges]
    if not isinstance(expected_ids, list) or set(expected_ids) != set(actual_ids):
        raise AssertionError("ModelExchange list does not exactly match the Case exchange_ids")
    clip_reports = []
    for exchange in exchanges:
        clip = exchange.get("clip")
        if not isinstance(clip, dict):
            raise AssertionError("A real ModelExchange is missing its ClipRef")
        clip_reports.append(verify_received_clip(context, base_url, clip, report, case_id))
    # Verify the Case's current ClipRef independently of the exchange ClipRefs.
    case_clip_report = verify_received_clip(context, base_url, case["clip"], report, case_id)
    job_report = {
        "job_id": job.get("job_id"),
        "source_id": source_id,
        "event": event,
        "mode": job.get("mode"),
        "fixture_profile": job.get("fixture_profile"),
        "state": job.get("state"),
        "error": job.get("error"),
        "case_id": case_id,
        "run_id": case.get("run_id"),
        "phase": case.get("phase"),
        "model_error": case.get("model_error"),
        "assessment": {
            key: (case.get("assessment") or {}).get(key)
            for key in ("object_class", "hazard", "confidence", "decision")
        } if isinstance(case.get("assessment"), dict) else None,
        "exchange_ids": actual_ids,
        "clips": clip_reports,
        "case_clip": case_clip_report,
    }
    report.setdefault("cases", []).append(job_report)
    return job, case, model, exchanges


def assert_wcag_text_contrast(page: Any, locator: Any, label: str) -> dict[str, Any]:
    measured = locator.evaluate("""element => {
      const parse = value => {
        const match = value.match(/rgba?[(]([^)]*)[)]/i);
        if (!match) throw new Error('Unsupported computed color: ' + value);
        const channels = match[1].split(',').map(part => Number.parseFloat(part.trim()));
        return [channels[0], channels[1], channels[2], channels.length > 3 ? channels[3] : 1];
      };
      const composite = (front, back) => {
        const alpha = front[3];
        return [
          front[0] * alpha + back[0] * (1 - alpha),
          front[1] * alpha + back[1] * (1 - alpha),
          front[2] * alpha + back[2] * (1 - alpha),
          1,
        ];
      };
      const backgrounds = [];
      for (let node = element; node; node = node.parentElement) {
        const color = parse(getComputedStyle(node).backgroundColor);
        if (color[3] > 0) backgrounds.push(color);
      }
      let background = [255, 255, 255, 1];
      for (let index = backgrounds.length - 1; index >= 0; index--) {
        background = composite(backgrounds[index], background);
      }
      const foreground = composite(parse(getComputedStyle(element).color), background);
      const luminance = color => {
        const linear = color.slice(0, 3).map(channel => {
          const normalized = channel / 255;
          return normalized <= 0.04045 ? normalized / 12.92 : Math.pow((normalized + 0.055) / 1.055, 2.4);
        });
        return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2];
      };
      const foregroundLuminance = luminance(foreground);
      const backgroundLuminance = luminance(background);
      const lighter = Math.max(foregroundLuminance, backgroundLuminance);
      const darker = Math.min(foregroundLuminance, backgroundLuminance);
      const rgb = color => color.slice(0, 3).map(Math.round);
      return {
        foreground: getComputedStyle(element).color,
        effective_foreground_rgb: rgb(foreground),
        effective_background_rgb: rgb(background),
        contrast_ratio: Number(((lighter + 0.05) / (darker + 0.05)).toFixed(2)),
      };
    }""")
    if measured["contrast_ratio"] < 4.5:
        raise AssertionError(
            label + " visible text does not meet WCAG AA 4.5:1 contrast: "
            + json.dumps(measured)
        )
    return measured


def verify_model_ui(page: Any, case: dict[str, Any], exchanges: list[dict[str, Any]], report: dict[str, Any]) -> dict[str, Any]:
    case_id = case["case_id"]
    if page.locator("#cartoon-room-2").is_hidden():
        enter_room(page, 2)
    selection = select_real_case(page, case_id)
    latest = exchanges[-1]
    exchange_id = latest["exchange_id"]
    card = page.locator(
        '#road-model-root .road-model-exchange[data-exchange-id="' + exchange_id + '"]'
    )
    card.wait_for(state="visible", timeout=15000)
    if card.get_attribute("data-case-id") != case_id:
        raise AssertionError("Visible ModelExchange card is not bound to the selected server Case")
    model_error = case.get("model_error")
    prompt = card.locator(".road-model-prompt")
    raw = card.locator(".road-model-raw-output")
    prompt_visible = prompt.is_visible()
    raw_visible = raw.is_visible()
    if not prompt_visible:
        raise AssertionError("Actual model prompt exists in the selected Case but is hidden in the UI")
    prompt_text = prompt.text_content() or ""
    if not prompt_text.strip() or prompt_text != latest.get("prompt"):
        raise AssertionError("Visible model prompt does not exactly match the actual ModelExchange")
    prompt_contrast = assert_wcag_text_contrast(page, prompt, "Visible model prompt")
    raw_text = raw.text_content() or ""
    raw_output_contrast = None
    if model_error is None:
        if not raw_visible or not raw_text.strip() or raw_text != latest.get("raw_output"):
            raise AssertionError("Visible raw output does not exactly match the actual ModelExchange")
        raw_output_contrast = assert_wcag_text_contrast(page, raw, "Visible raw model output")

    verdict = page.locator("#datacenter-verdict-box")
    verdict_text = verdict.text_content() or ""
    if isinstance(case.get("assessment"), dict):
        for key in ("object_class", "confidence"):
            value = case["assessment"].get(key)
            if value is not None and str(value) not in verdict_text:
                raise AssertionError("Server assessment field is missing from the visible assessment panel: " + key)
    else:
        if "confidence" in verdict_text.lower() or re.search(r"(?<!\d)0\.\d+(?!\d)", verdict_text):
            raise AssertionError("The UI fabricated a numeric finding without a server assessment")
        if model_error and model_error not in verdict_text:
            raise AssertionError("The actual server model error is not visible in the assessment panel")

    result = {
        "selection": selection,
        "exchange_id": exchange_id,
        "exchange_case_id": card.get_attribute("data-case-id"),
        "prompt_visible": prompt_visible,
        "prompt_exact": prompt_text == latest.get("prompt"),
        "prompt_contrast": prompt_contrast,
        "raw_output_visible": raw_visible,
        "raw_output_exact": raw_text == latest.get("raw_output"),
        "raw_output_contrast": raw_output_contrast,
        "assessment_visible": bool((case.get("assessment") or {}).get("object_class")) if isinstance(case.get("assessment"), dict) else False,
        "model_error_visible": bool(model_error and model_error in verdict_text),
    }
    report.setdefault("ui_cases", {})[case_id] = result
    return result


def verify_workplace_ui(
    page: Any,
    case: dict[str, Any],
    *,
    playback: bool,
    report: dict[str, Any],
) -> dict[str, Any]:
    case_id = case["case_id"]
    if page.locator("#cartoon-room-3").is_hidden():
        enter_room(page, 3)
    clip = case.get("clip")
    if not isinstance(clip, dict):
        raise AssertionError("Selected real Case does not have a server ClipRef")
    video_selector = (
        '#road-workplace-root video[data-case-id="' + case_id
        + '"][data-clip-id="' + str(clip.get("clip_id"))
        + '"][data-clip-hash="' + str(clip.get("sha256")) + '"]'
    )
    video_locator = page.locator(video_selector)
    if video_locator.count() != 1:
        raise AssertionError("No unique native workplace video matches the selected Case/ClipRef")
    video_locator.scroll_into_view_if_needed(timeout=5000)
    page.wait_for_function(
        """({caseId, clipId, sha256}) => {
          const root = document.querySelector('#road-workplace-root');
          const video = root && Array.from(root.querySelectorAll('video')).find(item =>
            item.dataset.caseId === caseId && item.dataset.clipId === clipId && item.dataset.clipHash === sha256
          );
          if (!video) return false;
          const style = getComputedStyle(video);
          const rect = video.getBoundingClientRect();
          return rect.width > 0 && rect.height > 0 && style.display !== 'none' &&
            style.visibility !== 'hidden' && Number(style.opacity || '1') > 0 &&
            video.readyState >= 1 && video.videoWidth > 0 && video.videoHeight > 0;
        }""",
        arg={"caseId": case_id, "clipId": clip.get("clip_id"), "sha256": clip.get("sha256")},
        timeout=20000,
    )
    state = visible_media_state(page, case_id, clip)
    if not state.get("present") or not state.get("visible") or not state.get("controls"):
        raise AssertionError("Native workplace video is missing, hidden, or has no native controls")
    if state.get("ready_state", 0) < 1 or state.get("video_width", 0) <= 0 or state.get("video_height", 0) <= 0:
        raise AssertionError("Native workplace video has not decoded real clip metadata and pixels")
    if state.get("error_code") is not None:
        raise AssertionError("Native workplace video reports a decode error")
    ui_clip_path = "GET /road/clips/" + quote(str(clip.get("clip_id")), safe="")
    if report.get("ui_http_paths", {}).get(ui_clip_path, 0) < 1:
        raise AssertionError("The native UI did not fetch this selected Case's real MP4 over HTTP")
    playback_result = verify_native_playback(page, case_id, clip, report=report) if playback else None
    state["native_playback"] = playback_result
    report.setdefault("workplace_players", {})[case_id] = state
    return state


def save_screenshot(page: Any, output: Path, report: dict[str, Any], name: str) -> None:
    path = output / name
    page.screenshot(path=str(path), full_page=True)
    report.setdefault("screenshots", []).append(str(path))


def run_fast_flow(
    page: Any,
    context: Any,
    base_url: str,
    output: Path,
    report: dict[str, Any],
) -> dict[str, Any]:
    enter_room(page, 1)
    source_id = start_real_source(page, report, "pending")
    job, case, model, exchanges = process_real_job(
        page, context, base_url, source_id, "wrong_way", "pending", report,
        expected_phases={"human_review"},
    )
    report["checks"]["same_source_case_exchange_clip_ids"] = (
        job.get("case_id") == case.get("case_id") == model.get("case_id")
        and case.get("clip", {}).get("clip_id") == exchanges[-1].get("clip", {}).get("clip_id")
        and case.get("run_id") == job.get("run_id") == exchanges[-1].get("run_id")
    )
    if not report["checks"]["same_source_case_exchange_clip_ids"]:
        raise AssertionError("Actual HTTP Case/Run/Clip/ModelExchange identities do not agree")

    enter_room(page, 2)
    verify_model_ui(page, case, exchanges, report)
    save_screenshot(page, output, report, "fast-room-2-model.png")
    enter_room(page, 3)
    verify_workplace_ui(page, case, playback=True, report=report)
    save_screenshot(page, output, report, "fast-room-3-native-player.png")

    inbox_before = inbox_records(context, base_url, report)
    before_ids = receipt_ids(inbox_before)
    click_reject_for_selected_case(page, case["case_id"], report)
    rejected = wait_for_case_phase(context, base_url, case["case_id"], report, {"rejected"})
    if rejected.get("first_receipt") or rejected.get("correction_receipt"):
        raise AssertionError("Terminal reject unexpectedly produced a report or correction receipt")
    page.locator("#btn-schmidt-coffee").click()
    page.locator("#cartoon-room-4").wait_for(state="visible", timeout=10000)
    page.wait_for_timeout(250)
    after_ids = receipt_ids(inbox_records(context, base_url, report))
    if after_ids != before_ids:
        raise AssertionError("Coffee navigation changed actual receipt records")
    still_rejected = api_get_json(
        context, base_url, "/road/cases/" + quote(case["case_id"], safe=""), report
    )
    if still_rejected.get("phase") != "rejected":
        raise AssertionError("Coffee navigation changed the server-reported rejected Case")
    report["checks"].update({
        "terminal_reject_no_receipt": True,
        "coffee_preserved_rejection": True,
        "coffee_created_no_receipt": True,
        "native_decode_play_pause_seek": report["workplace_players"][case["case_id"]]["native_playback"] is not None,
    })
    save_screenshot(page, output, report, "fast-room-4-coffee-no-escalation.png")
    return {"source_id": source_id, "case_id": case["case_id"]}


def run_full_profiles(
    page: Any,
    context: Any,
    base_url: str,
    output: Path,
    report: dict[str, Any],
) -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []

    def create_source(reason: str) -> str:
        if reason:
            report.setdefault("source_switches", []).append({"reason": reason})
        enter_room(page, 1)
        source_id = start_real_source(page, report, "pending")
        if reason:
            report["source_switches"][-1]["new_source_id"] = source_id
        return source_id

    def job(
        source: str,
        event: str,
        profile: str,
        phase: str,
        *,
        minimum_exchanges: int = 1,
        job_states: set[str] | None = None,
        exchange_states: set[str] | None = None,
        timeout: float = 90.0,
    ) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
        enter_room(page, 1)
        prior_selection = page.locator(".road-cartoon-stage").get_attribute("data-selected-case-id")
        actual_job, case, model, exchanges = process_real_job(
            page, context, base_url, source, event, profile, report,
            expected_phases={phase},
            min_exchanges=minimum_exchanges,
            expected_job_states=job_states,
            expected_exchange_statuses=exchange_states,
            timeout=timeout,
        )
        cases.append({"source_id": source, "case_id": case["case_id"], "profile": profile, "phase": phase})
        if prior_selection and prior_selection != case["case_id"]:
            # New work must not take review focus away from an older selected Case.
            if page.locator(".road-cartoon-stage").get_attribute("data-selected-case-id") != prior_selection:
                raise AssertionError("Arriving job changed the selected historical Case")
        enter_room(page, 2)
        verify_model_ui(page, case, exchanges, report)
        if profile != "error":
            enter_room(page, 3)
            verify_workplace_ui(page, case, playback=False, report=report)
        if profile in {"pending", "high", "followup"} or profile == "error":
            if profile != "high":
                enter_room(page, 3)
                click_reject_for_selected_case(page, case["case_id"], report)
                expected_terminal = "rejected" if phase == "human_review" else "corrected"
                case = wait_for_case_phase(context, base_url, case["case_id"], report, {expected_terminal})
        return actual_job, case, exchanges

    # Start timing before source creation so setup latency makes this an upper
    # bound on the real source age, not an optimistic underestimate.
    source_one_timer_started = time.monotonic()
    source_one = create_source("")
    pending_job, pending_case, _ = job(source_one, "wrong_way", "pending", "human_review")
    # Keep the first ticket selected while later real jobs enter the same source queue.
    if page.locator(".road-cartoon-stage").get_attribute("data-selected-case-id") != pending_case["case_id"]:
        raise AssertionError("First actual Case did not remain selected after its terminal review")
    high_job, high_case, high_exchanges = job(source_one, "wrong_way", "high", "reported")
    high_receipt = high_case.get("first_receipt")
    if not isinstance(high_receipt, dict) or high_receipt.get("reason") != "high_confidence":
        raise AssertionError("Explicit high fixture did not create its actual high-confidence receipt")
    if high_case.get("assessment", {}).get("confidence") is None:
        raise AssertionError("High-confidence report lacks a server assessment confidence")
    # Complete the actual late rejection through the selected-case UI and verify linked correction.
    enter_room(page, 3)
    selected = page.locator(".road-cartoon-stage").get_attribute("data-selected-case-id")
    if selected != high_case["case_id"]:
        select_real_case(page, high_case["case_id"])
    click_reject_for_selected_case(page, high_case["case_id"], report)
    high_corrected = wait_for_case_phase(context, base_url, high_case["case_id"], report, {"corrected"})
    correction = high_corrected.get("correction_receipt")
    if not isinstance(correction, dict) or correction.get("original_receipt_id") != high_receipt.get("receipt_id"):
        raise AssertionError("Late reject correction is not linked to the actual first receipt")

    if len([item for item in report["cases"] if item["source_id"] == source_one]) < 2:
        raise AssertionError("The same first source did not process multiple distinct server Cases")
    bird_job, bird_case, _ = job(source_one, "bird", "bird", "dismissed", timeout=75.0)
    if bird_case.get("assessment", {}).get("object_class") != "bird" or bird_case.get("assessment", {}).get("hazard") is not False:
        raise AssertionError("Bird profile did not produce the explicit server-backed non-hazard assessment")
    source_one_upper_bound = time.monotonic() - source_one_timer_started
    source_runtime_proof = {
        "source_id": source_one,
        "cases_completed": len([item for item in report["cases"] if item["source_id"] == source_one]),
        "measured_upper_bound_seconds": round(source_one_upper_bound, 2),
        "recording_limit_seconds": SOURCE_RECORDING_LIMIT_SECONDS,
        "within_recording_limit": source_one_upper_bound < SOURCE_RECORDING_LIMIT_SECONDS,
    }
    report.setdefault("source_runtime_proofs", []).append(source_runtime_proof)
    if source_one_upper_bound >= SOURCE_RECORDING_LIMIT_SECONDS:
        raise AssertionError(
            "Three real Cases on one source exceeded the server's 120-second source limit: "
            + json.dumps(source_runtime_proof)
        )

    source_two = create_source("fresh independent source after three actual Cases on source one; keep later follow-up/error checks inside the server source-age limit")
    leaf_job, leaf_case, _ = job(source_two, "leaf", "leaf", "dismissed", timeout=75.0)
    if leaf_case.get("assessment", {}).get("object_class") != "leaf" or leaf_case.get("assessment", {}).get("hazard") is not False:
        raise AssertionError("Leaf profile did not produce the explicit server-backed non-hazard assessment")
    followup_job, followup_case, followup_exchanges = job(
        source_two, "wrong_way", "followup", "human_review", minimum_exchanges=2, timeout=90.0
    )
    if len(followup_exchanges) != 2:
        raise AssertionError("Follow-up fixture did not produce two actual ModelExchanges")
    if followup_exchanges[0].get("exchange_id") == followup_exchanges[1].get("exchange_id"):
        raise AssertionError("Follow-up ModelExchange IDs are not distinct")
    error_job, error_case, error_exchanges = job(
        source_two,
        "wrong_way",
        "error",
        "human_review",
        job_states={"failed"},
        exchange_states={"failed"},
        timeout=90.0,
    )
    if error_case.get("assessment") is not None or not error_case.get("model_error"):
        raise AssertionError("Error profile fabricated an assessment or omitted the actual model error")
    error_ui = report.get("ui_cases", {}).get(error_case["case_id"], {})
    if not error_ui.get("model_error_visible"):
        raise AssertionError("The actual model error is not visible and explicitly unresolved in the UI")

    human_source = create_source("fresh independent source for a visible operator confirmation")
    human_job, human_case, _human_model, human_exchanges = process_real_job(
        page, context, base_url, human_source, "wrong_way", "pending", report,
        expected_phases={"human_review"},
    )
    cases.append({
        "source_id": human_source,
        "case_id": human_case["case_id"],
        "profile": "pending",
        "phase": "human_review",
    })
    enter_room(page, 2)
    verify_model_ui(page, human_case, human_exchanges, report)
    enter_room(page, 3)
    verify_workplace_ui(page, human_case, playback=False, report=report)
    inbox_before_confirm = inbox_records(context, base_url, report)
    before_confirm_ids = receipt_ids(inbox_before_confirm)
    click_confirm_for_selected_case(page, human_case["case_id"], report)
    confirmed_case = wait_for_case_phase(
        context, base_url, human_case["case_id"], report, {"reported"}
    )
    inbox_after_confirm = inbox_records(context, base_url, report)
    case_receipts = [
        item for item in inbox_after_confirm if item.get("case_id") == human_case["case_id"]
    ]
    new_case_receipts = [
        item for item in case_receipts
        if str(item.get("receipt_id") or item.get("message_id") or item.get("id")) not in before_confirm_ids
    ]
    human_receipt = confirmed_case.get("first_receipt")
    if (
        len(case_receipts) != 1
        or len(new_case_receipts) != 1
        or new_case_receipts[0].get("reason") != "human_confirmed"
        or not isinstance(human_receipt, dict)
        or new_case_receipts[0].get("receipt_id") != human_receipt.get("receipt_id")
        or human_receipt.get("reason") != "human_confirmed"
        or confirmed_case.get("correction_receipt") is not None
    ):
        raise AssertionError(
            "The visible human confirm must create exactly one matching human_confirmed inbox receipt"
        )
    report["human_confirmed_acceptance"] = {
        "source_id": human_source,
        "job_id": human_job.get("job_id"),
        "case_id": human_case["case_id"],
        "run_id": human_case.get("run_id"),
        "exchange_ids": [item.get("exchange_id") for item in human_exchanges],
        "visible_control": "btn-operator-confirm",
        "post_status": 200,
        "post_decision": "confirm",
        "final_phase": confirmed_case.get("phase"),
        "receipt_id": human_receipt.get("receipt_id"),
        "receipt_reason": human_receipt.get("reason"),
        "case_receipt_count": len(case_receipts),
    }
    report["checks"]["visible_human_confirmed_exactly_one_receipt"] = True
    save_screenshot(page, output, report, "full-human-confirmed-receipt.png")

    report["checks"].update({
        "high_confidence_receipt": True,
        "late_reject_linked_correction": True,
        "bird_profile_dismissed": True,
        "leaf_profile_dismissed": True,
        "followup_two_exchanges_same_case": True,
        "error_no_fabricated_score": True,
        "three_actual_cases_same_source": len([item for item in report["cases"] if item["source_id"] == source_one]) >= 3,
    })
    inspect_responsive_accessibility(
        page, output, report, language_case=high_case, language_exchange=high_exchanges[-1]
    )
    page.emulate_media(reduced_motion="no-preference")
    page.set_viewport_size({"width": 1440, "height": 1000})
    save_screenshot(page, output, report, "full-queue-three-real-cases.png")
    return cases


def run_deadline_reload_and_analysis(
    page: Any,
    context: Any,
    base_url: str,
    output: Path,
    report: dict[str, Any],
) -> Any:
    # The new source isolates the real 20-second server deadline from the profile matrix.
    enter_room(page, 1)
    noresponse_source = start_real_source(page, report, "pending")
    report.setdefault("source_switches", []).append({
        "reason": "fresh source for the unshortened real server deadline and tab-close restore",
        "new_source_id": noresponse_source,
    })
    noresponse_job, noresponse_case, model, exchanges = process_real_job(
        page, context, base_url, noresponse_source, "wrong_way", "pending", report,
        expected_phases={"human_review"},
    )
    enter_room(page, 2)
    verify_model_ui(page, noresponse_case, exchanges, report)
    selected = select_real_case(page, noresponse_case["case_id"])
    storage = page.evaluate("""() => {
      const raw = localStorage.getItem('visionflow.road-controller.v1');
      return {raw, value: raw ? JSON.parse(raw) : null};
    }""")
    stored = storage.get("value")
    if not isinstance(stored, dict) or set(stored) != {"sourceId", "selectedCaseId"}:
        raise AssertionError("Road UI persistence stored unexpected fields or omitted the source/case IDs")
    if stored.get("sourceId") != noresponse_source or stored.get("selectedCaseId") != noresponse_case["case_id"]:
        raise AssertionError("Road UI persistence does not reflect the validated actual source/case selection")
    # Refresh the server-owned countdown immediately before closing the tab.
    # The elapsed clock starts before this GET, so the comparison is conservative.
    deadline_clock_started = time.monotonic()
    deadline_snapshot = api_get_json(
        context, base_url, "/road/cases/" + quote(noresponse_case["case_id"], safe=""), report
    )
    if (
        deadline_snapshot.get("case_id") != noresponse_case["case_id"]
        or deadline_snapshot.get("source_id") != noresponse_source
        or deadline_snapshot.get("phase") != "human_review"
    ):
        raise AssertionError("Fresh deadline snapshot does not identify the same human-review Case")
    remaining = deadline_snapshot.get("remaining_seconds")
    if not isinstance(remaining, (int, float)) or isinstance(remaining, bool) or not (0 <= remaining <= 20.5):
        raise AssertionError("Server human-review deadline snapshot is missing or outside its real 20-second policy")

    # Closing the tab does not stop or accelerate the server-owned deadline.
    page.close()
    page = None
    noresponse = wait_until_noresponse(context, base_url, noresponse_case["case_id"], report, timeout=35.0)
    elapsed = time.monotonic() - deadline_clock_started
    minimum_elapsed = max(0.0, float(remaining) - DEADLINE_POLL_TOLERANCE_SECONDS)
    if elapsed < minimum_elapsed:
        raise AssertionError(
            "Natural no_response arrived before the measured server countdown could expire: "
            + json.dumps({
                "initial_remaining_seconds": remaining,
                "elapsed_seconds": round(elapsed, 2),
                "poll_tolerance_seconds": DEADLINE_POLL_TOLERANCE_SECONDS,
                "minimum_elapsed_seconds": round(minimum_elapsed, 2),
            })
        )
    first_receipt = noresponse.get("first_receipt")
    if not isinstance(first_receipt, dict) or first_receipt.get("reason") != "no_response":
        raise AssertionError("Natural server deadline did not produce the actual no_response receipt")
    report["deadline"] = {
        "case_id": noresponse_case["case_id"],
        "initial_remaining_seconds": remaining,
        "tab_closed": True,
        "wait_seconds": round(elapsed, 2),
        "minimum_elapsed_seconds": round(minimum_elapsed, 2),
        "poll_tolerance_seconds": DEADLINE_POLL_TOLERANCE_SECONDS,
        "final_phase": noresponse.get("phase"),
        "receipt_id": first_receipt.get("receipt_id"),
        "receipt_reason": first_receipt.get("reason"),
    }
    reopened = context.new_page()
    reopened.goto(base_url + "/", wait_until="domcontentloaded", timeout=30000)
    reopened.locator("#road-tab").wait_for(state="visible", timeout=15000)
    reopened.wait_for_function(
        """({sourceId, caseId}) => {
          const camera = document.querySelector('#road-camera-root');
          const game = document.querySelector('.road-cartoon-stage');
          return game && game.dataset.selectedCaseId === caseId && camera &&
            (camera.dataset.sourceId === sourceId || camera.querySelector('.road-camera')?.dataset.sourceId === sourceId);
        }""",
        arg={"sourceId": noresponse_source, "caseId": noresponse_case["case_id"]},
        timeout=20000,
    )
    restored = reopened.evaluate("""() => {
      const raw = localStorage.getItem('visionflow.road-controller.v1');
      return raw ? JSON.parse(raw) : null;
    }""")
    if restored != {"sourceId": noresponse_source, "selectedCaseId": noresponse_case["case_id"]}:
        raise AssertionError("A new tab in the same context did not restore only the validated source and selected Case")
    reopened.locator("#nav-node-room-2").click()
    reopened.locator("#datacenter-queue-list button[data-case-id='" + noresponse_case["case_id"] + "']").wait_for(state="visible", timeout=15000)
    enter_room(reopened, 3)
    click_reject_for_selected_case(reopened, noresponse_case["case_id"], report)
    corrected = wait_for_case_phase(context, base_url, noresponse_case["case_id"], report, {"corrected"})
    correction = corrected.get("correction_receipt")
    if not isinstance(correction, dict) or correction.get("original_receipt_id") != first_receipt.get("receipt_id"):
        raise AssertionError("Late no_response rejection did not create a linked server correction")

    enter_room(reopened, 1)
    source_root = reopened.locator("#road-camera-root")
    stop = reopened.locator("#road-camera-stop")
    if stop.is_disabled():
        raise AssertionError("The restored source stop action is unexpectedly disabled")
    with reopened.expect_response(
        lambda response: response.request.method == "POST"
        and urlparse(response.url).hostname in LOOPBACK_HOSTS
        and urlparse(response.url).path == "/road/sessions/" + quote(noresponse_source, safe="") + "/stop",
        timeout=35000,
    ) as stop_info:
        stop.click()
    if stop_info.value.status != 200:
        raise AssertionError("Visible stop action did not end the server source")
    recording = wait_for_recording_closed(context, base_url, noresponse_source, report, timeout=15.0)
    if recording.get("source_id") != noresponse_source:
        raise AssertionError("Stopped recording snapshot belongs to another source")
    post_count_before = len([item for item in report.get("posts", []) if item.get("path") == "/road/sessions"])
    reopened.reload(wait_until="domcontentloaded", timeout=30000)
    reopened.wait_for_function(
        """(sourceId) => {
          const root = document.querySelector('#road-camera-root');
          const mounted = root && root.querySelector('.road-camera');
          return (root && root.dataset.sourceId === sourceId) || (mounted && mounted.dataset.sourceId === sourceId);
        }""",
        arg=noresponse_source,
        timeout=20000,
    )
    post_count_after = len([item for item in report.get("posts", []) if item.get("path") == "/road/sessions"])
    status_text = reopened.locator("#cartoon-cam-status").text_content() or ""
    restored_source = reopened.locator("#road-camera-root").evaluate(
        "root => ({source: root.dataset.sourceId || root.querySelector('.road-camera')?.dataset.sourceId})"
    )
    if post_count_after != post_count_before or restored_source.get("source") != noresponse_source:
        raise AssertionError("Ended-source reload auto-created a new source or changed source identity")
    if "Quelle beendet" not in status_text:
        raise AssertionError("Ended historical source is not visibly marked as ended after tab reload")
    enter_room(reopened, 2)
    reopened.locator("#datacenter-queue-list button[data-case-id='" + noresponse_case["case_id"] + "']").wait_for(state="visible", timeout=15000)
    if reopened.locator(".road-cartoon-stage").get_attribute("data-selected-case-id") != noresponse_case["case_id"]:
        raise AssertionError("Ended-source reload did not restore the selected historical Case")
    enter_room(reopened, 3)
    verify_workplace_ui(reopened, corrected, playback=False, report=report)
    report["checks"].update({
        "server_deadline_real_20_seconds": elapsed >= minimum_elapsed,
        "deadline_elapsed_at_least_measured_initial_remaining": True,
        "tab_close_did_not_stop_deadline": True,
        "new_tab_restored_source_case": True,
        "no_response_receipt_then_linked_correction": True,
        "ended_source_reload_kept_history_without_new_source": True,
    })

    # Exercise the preserved one-run analysis workspace with the app's actual sample-image controls.
    reopened.locator("#analysis-tab").click()
    reopened.locator("#workspace-analysis").wait_for(state="visible", timeout=10000)
    reopened.locator("#sample-sharp-btn").click()
    reopened.wait_for_function(
        "() => document.querySelector('#source-file')?.files?.length === 1",
        timeout=10000,
    )
    with reopened.expect_response(
        lambda response: response.request.method == "POST"
        and urlparse(response.url).hostname in LOOPBACK_HOSTS
        and urlparse(response.url).path == "/runs",
        timeout=20000,
    ) as run_info:
        reopened.locator("#start-button").click()
    run_response = run_info.value
    run_payload = run_response.json()
    if run_response.status != 202 or not isinstance(run_payload.get("run_id"), str):
        raise AssertionError("Existing single-analysis workspace did not start an actual local Run")
    run_id = run_payload["run_id"]
    wait_for_run_status(context, base_url, run_id, report, {"waiting_for_input"}, timeout=20.0)
    if reopened.locator("#input-panel").is_hidden():
        raise AssertionError("Existing analysis workspace did not show its real follow-up-input request")
    # Feed the app-generated blurred sample through the visible replacement file control.
    reopened.locator("#sample-blurred-btn").click()
    reopened.wait_for_function(
        "() => document.querySelector('#source-file')?.files?.length === 1",
        timeout=10000,
    )
    reopened.evaluate("""() => {
      const source = document.querySelector('#source-file').files[0];
      const transfer = new DataTransfer();
      transfer.items.add(source);
      document.querySelector('#replacement-file').files = transfer.files;
    }""")
    with reopened.expect_response(
        lambda response: response.request.method == "POST"
        and urlparse(response.url).hostname in LOOPBACK_HOSTS
        and urlparse(response.url).path == "/runs/" + quote(run_id, safe="") + "/input",
        timeout=20000,
    ) as input_info:
        reopened.locator("#resume-button").click()
    if input_info.value.status != 200:
        raise AssertionError("Existing single-analysis workflow did not accept its visible replacement image")
    final_run = wait_for_run_status(context, base_url, run_id, report, {"completed"}, timeout=25.0)
    if not final_run.get("findings"):
        raise AssertionError("Existing single-analysis workflow completed without its actual quality finding")
    report["legacy_analysis"] = {
        "run_id": run_id,
        "initial_status": "waiting_for_input",
        "final_status": final_run.get("status"),
        "finding_count": len(final_run.get("findings", [])),
    }
    report["checks"]["legacy_single_analysis_followup_completed"] = True
    save_screenshot(reopened, output, report, "full-analysis-workspace.png")
    return reopened


def wait_until_noresponse(
    context: Any,
    base_url: str,
    case_id: str,
    report: dict[str, Any],
    *,
    timeout: float,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    last: dict[str, Any] = {}
    path = "/road/cases/" + quote(case_id, safe="")
    while time.monotonic() < deadline:
        last = api_get_json(context, base_url, path, report)
        if last.get("phase") == "no_response":
            return last
        if last.get("phase") != "human_review":
            raise AssertionError("Server Case left human_review before its natural deadline: " + str(last.get("phase")))
        time.sleep(DEADLINE_POLL_INTERVAL_SECONDS)
    raise TimeoutError("Actual server 20-second human-review deadline did not expire naturally")


def wait_for_recording_closed(
    context: Any,
    base_url: str,
    source_id: str,
    report: dict[str, Any],
    *,
    timeout: float,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    path = "/road/sessions/" + quote(source_id, safe="") + "/recording"
    last: dict[str, Any] = {}
    while time.monotonic() < deadline:
        payload = api_get_json(context, base_url, path, report)
        last = payload.get("recording", payload) if isinstance(payload, dict) else {}
        if last.get("closed") is True or last.get("status") in {"stopped", "ended", "closed"}:
            return last
        time.sleep(0.25)
    raise TimeoutError("Real source recording did not become closed after visible stop")


def wait_for_run_status(
    context: Any,
    base_url: str,
    run_id: str,
    report: dict[str, Any],
    expected: set[str],
    *,
    timeout: float,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    path = "/runs/" + quote(run_id, safe="")
    last: dict[str, Any] = {}
    while time.monotonic() < deadline:
        last = api_get_json(context, base_url, path, report)
        if last.get("status") in expected:
            return last
        time.sleep(0.25)
    raise TimeoutError(
        "Actual legacy Run did not reach the expected state: "
        + json.dumps({"run_id": run_id, "status": last.get("status"), "expected": sorted(expected)})
    )


def inspect_responsive_accessibility(
    page: Any,
    output: Path,
    report: dict[str, Any],
    *,
    language_case: dict[str, Any],
    language_exchange: dict[str, Any],
) -> None:
    page.emulate_media(reduced_motion="reduce")
    page.set_viewport_size({"width": 390, "height": 844})
    enter_room(page, 3)
    metrics = page.evaluate("""() => {
      const selectors = ['#workspace-road', '#road-workplace-root', '#btn-schmidt-coffee', '#nav-node-room-1'];
      const boxes = Object.fromEntries(selectors.map(selector => {
        const node = document.querySelector(selector);
        if (!node) return [selector, null];
        const rect = node.getBoundingClientRect();
        return [selector, {left: rect.left, right: rect.right, width: rect.width, visible: rect.width > 0 && rect.height > 0}];
      }));
      const animated = ['.patrol-car', '.cartoon-stage', '.cartoon-patrol-car']
        .map(selector => document.querySelector(selector))
        .filter(Boolean)
        .map(node => ({selector: node.className, animation: getComputedStyle(node).animationDuration, transition: getComputedStyle(node).transitionDuration}));
      return {innerWidth, scrollWidth: document.documentElement.scrollWidth, boxes, animated, language: document.documentElement.lang};
    }""")
    if metrics["scrollWidth"] > metrics["innerWidth"] + 1:
        raise AssertionError("390 px controller layout has horizontal document overflow")
    for selector, box in metrics["boxes"].items():
        if not box or not box["visible"] or box["left"] < -1 or box["right"] > 391:
            raise AssertionError("A key road workspace control is outside the 390 px viewport: " + selector)
    def duration_ms(value: str) -> float:
        value = value.strip().split(",", 1)[0]
        if value.endswith("ms"):
            return float(value[:-2])
        if value.endswith("s"):
            return float(value[:-1]) * 1000
        return float("inf")
    if any(duration_ms(item["animation"]) > 0.1 or duration_ms(item["transition"]) > 0.1 for item in metrics["animated"]):
        raise AssertionError("Reduced-motion mode did not reduce animated/transition durations")

    case_id = language_case.get("case_id")
    exchange_id = language_exchange.get("exchange_id")
    if not isinstance(case_id, str) or not case_id or not isinstance(exchange_id, str) or not exchange_id:
        raise AssertionError("Language acceptance requires an actual selected Case and ModelExchange")
    expected_prompt = language_exchange.get("prompt")
    expected_raw_output = language_exchange.get("raw_output")
    if not isinstance(expected_prompt, str) or not expected_prompt or not isinstance(expected_raw_output, str) or not expected_raw_output:
        raise AssertionError("Language acceptance requires the actual non-empty prompt and raw answer")

    def assert_visible_copy(room: int, language: str) -> dict[str, str]:
        if page.locator("html").get_attribute("lang") != language:
            raise AssertionError("Document language does not match the selected UI language")
        per_room = {
            2: {
                "#speech-kira": {
                    "de": "Hier erscheinen tatsächliche Jobs, Modellprüfungen, Prompts, Belege und Antworten.",
                    "en": "Actual jobs, model reviews, prompts, evidence, and responses appear here.",
                },
                "#road-model-root .road-panel-title": {
                    "de": "Modellprüfung und Ereignisclips",
                    "en": "Model reviews and event clips",
                },
            },
            3: {
                "#speech-schmidt": {
                    "de": "Wähle einen tatsächlichen Fall und prüfe dessen gespeicherten Clip und Befund.",
                    "en": "Select an actual case to review its saved clip and assessment.",
                },
                "#road-workplace-root .road-panel-title": {
                    "de": "Menschlicher Arbeitsplatz und Demo-Postfach",
                    "en": "Human review workspace and demo inbox",
                },
            },
            4: {
                "#speech-weber": {
                    "de": "Dieser Raum zeigt ausschließlich Phase, Frist und Belege aus dem ausgewählten Serverfall.",
                    "en": "This room shows only the phase, deadline, and receipts from the selected server case.",
                },
                "#road-timeline-root .road-panel-title": {
                    "de": "Fallchronik",
                    "en": "Case timeline",
                },
            },
        }
        texts: dict[str, str] = {}
        for selector, translations in per_room[room].items():
            locator = page.locator(selector)
            locator.wait_for(state="visible", timeout=10000)
            actual = (locator.text_content() or "").strip()
            expected = translations[language]
            if actual != expected:
                raise AssertionError(
                    f"Room {room} {language} visible copy mismatch at {selector}: "
                    + json.dumps({"expected": expected, "actual": actual}, ensure_ascii=False)
                )
            texts[selector] = actual
        return texts

    def assert_model_data_unchanged(stage: str) -> dict[str, Any]:
        card = page.locator(
            '#road-model-root .road-model-exchange[data-exchange-id="' + exchange_id + '"]'
        )
        if card.get_attribute("data-case-id") != case_id:
            raise AssertionError("Language toggle changed the ModelExchange Case identity at " + stage)
        prompt_text = card.locator(".road-model-prompt").text_content() or ""
        raw_text = card.locator(".road-model-raw-output").text_content() or ""
        if prompt_text != expected_prompt or raw_text != expected_raw_output:
            raise AssertionError("Language toggle modified the actual prompt or raw answer at " + stage)
        if page.locator(".road-cartoon-stage").get_attribute("data-selected-case-id") != case_id:
            raise AssertionError("Language toggle changed the actual selected Case at " + stage)
        return {
            "case_id": case_id,
            "exchange_id": exchange_id,
            "prompt_sha256": hashlib.sha256(prompt_text.encode("utf-8")).hexdigest(),
            "raw_output_sha256": hashlib.sha256(raw_text.encode("utf-8")).hexdigest(),
        }

    def toggle_language(language: str) -> None:
        if page.locator("html").get_attribute("lang") != language:
            page.locator("#cartoon-lang-btn").click()
            page.wait_for_function(
                "(expected) => document.documentElement.lang === expected",
                arg=language,
                timeout=5000,
            )

    enter_room(page, 2)
    verify_model_ui(page, language_case, [language_exchange], report)
    language_report: dict[str, Any] = {
        "case_id": case_id,
        "exchange_id": exchange_id,
        "rooms": {},
        "model_data_unchanged_on_every_toggle": True,
    }
    model_baseline = assert_model_data_unchanged("room 2 German baseline")
    language_report["rooms"]["2"] = {"de": assert_visible_copy(2, "de")}
    toggle_language("en")
    language_report["rooms"]["2"]["en"] = assert_visible_copy(2, "en")
    assert_model_data_unchanged("room 2 English")
    toggle_language("de")
    assert_visible_copy(2, "de")
    assert_model_data_unchanged("room 2 German restore")

    enter_room(page, 3)
    language_report["rooms"]["3"] = {"de": assert_visible_copy(3, "de")}
    toggle_language("en")
    language_report["rooms"]["3"]["en"] = assert_visible_copy(3, "en")
    assert_model_data_unchanged("room 3 English")
    toggle_language("de")
    assert_visible_copy(3, "de")
    assert_model_data_unchanged("room 3 German restore")

    enter_room(page, 4)
    language_report["rooms"]["4"] = {"de": assert_visible_copy(4, "de")}
    toggle_language("en")
    language_report["rooms"]["4"]["en"] = assert_visible_copy(4, "en")
    assert_model_data_unchanged("room 4 English")
    toggle_language("de")
    assert_visible_copy(4, "de")
    assert_model_data_unchanged("room 4 German restore")

    language_report["model_data_baseline"] = model_baseline
    report["language_acceptance"] = language_report
    report["responsive_accessibility"] = {
        **metrics,
        "english_toggle": True,
        "german_restore": True,
        "visible_translations_rooms_2_3_4": True,
        "raw_prompt_answer_unchanged": True,
        "reduced_motion": True,
    }
    enter_room(page, 3)
    save_screenshot(page, output, report, "full-mobile-390px.png")


def run_red_phase(args: argparse.Namespace, sync_playwright: Any) -> int:
    output = fresh_output_directory(args.output, args.phase)
    server_python = Path(args.server_python).expanduser().resolve()
    edge_binary = Path(args.browser_path).expanduser().resolve()
    port = free_port()
    base_url = "http://127.0.0.1:" + str(port)
    server_log_path = output / "server.log"
    report_path = output / "acceptance.json"
    screenshot_paths = [
        output / "room-2-after-backend-ready.png",
        output / "room-3-after-backend-ready.png",
    ]
    server_data = output / "server-data"
    server_log = server_log_path.open("w", encoding="utf-8", newline="\n")
    server: subprocess.Popen[bytes] | None = None
    browser = None
    context = None
    report: dict[str, Any] = {
        "started_at": datetime.now(timezone.utc).isoformat(),
        "worktree": str(REPO),
        "output_directory": str(output),
        "harness_python": sys.executable,
        "phase": args.phase,
        "server_python": str(server_python),
        "edge_binary": str(edge_binary),
        "base_url": base_url,
        "fixture": {"mode": "fixture", "profile": "pending", "scenario": "wrong_way"},
        "status": "incomplete",
        "posts": [],
        "external_hosts": [],
        "browser_errors": [],
        "console_errors": [],
        "checks": {},
    }
    external_hosts: set[str] = set()

    exit_code = 2
    try:
        if not server_python.is_file():
            raise FileNotFoundError("Server Python is missing: " + str(server_python))
        if not edge_binary.is_file():
            raise FileNotFoundError("Microsoft Edge executable is missing")

        server_env = os.environ.copy()
        server_env["PYTHONIOENCODING"] = "utf-8"
        existing_pythonpath = server_env.get("PYTHONPATH", "")
        server_env["PYTHONPATH"] = str(REPO / "src") + (
            os.pathsep + existing_pythonpath if existing_pythonpath else ""
        )
        server = subprocess.Popen(
            [
                str(server_python),
                "-m",
                "visionflow.cli",
                "serve",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
                "--data-dir",
                str(server_data),
            ],
            cwd=REPO,
            env=server_env,
            stdout=server_log,
            stderr=subprocess.STDOUT,
            creationflags=CREATE_NO_WINDOW,
        )
        report["server_pid"] = server.pid
        wait_for_server(port, server)
        report["checks"]["isolated_loopback_server"] = True

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                executable_path=str(edge_binary),
                headless=True,
                args=["--autoplay-policy=no-user-gesture-required"],
            )
            report["edge_version"] = browser.version
            context = browser.new_context(viewport={"width": 1440, "height": 1000})

            def guard_external(route: Any) -> None:
                host = urlparse(route.request.url).hostname or ""
                if host not in LOOPBACK_HOSTS:
                    external_hosts.add(host)
                    try:
                        route.abort()
                    except Exception:
                        return
                    return
                try:
                    route.continue_()
                except Exception:
                    return

            context.route("**/*", guard_external)
            page = context.new_page()
            page.on("pageerror", lambda error: report["browser_errors"].append(str(error)))
            page.on(
                "console",
                lambda message: report["console_errors"].append(message.text)
                if message.type == "error"
                else None,
            )

            def record_request(request: Any) -> None:
                if request.method != "POST":
                    return
                parsed = urlparse(request.url)
                if parsed.hostname not in LOOPBACK_HOSTS:
                    return
                if parsed.path.startswith("/road/") or parsed.path.startswith("/runs"):
                    report["posts"].append(
                        {
                            "path": parsed.path,
                            "body": request.post_data,
                        }
                    )

            page.on("request", record_request)
            page.goto(base_url + "/", wait_until="domcontentloaded", timeout=30000)
            page.locator("#road-tab").wait_for(state="visible", timeout=15000)
            page.wait_for_function(
                "() => document.querySelector('#road-tab') && !document.querySelector('#road-tab').disabled",
                timeout=15000,
            )
            page.locator("#road-tab").click()
            page.locator(".road-cartoon-stage").wait_for(state="visible", timeout=15000)
            page.locator("#btn-toggle-cam").wait_for(state="visible", timeout=15000)
            page.wait_for_function(
                """() => {
                  const mode = document.querySelector('#road-model-mode');
                  const profile = document.querySelector('#road-fixture-profile');
                  return mode && profile &&
                    Array.from(mode.options).some(option => option.value === 'fixture') &&
                    Array.from(profile.options).some(option => option.value === 'pending');
                }""",
                timeout=15000,
            )
            selected_fixture = page.evaluate(
                """() => {
                  const mode = document.querySelector('#road-model-mode');
                  const profile = document.querySelector('#road-fixture-profile');
                  mode.value = 'fixture';
                  mode.dispatchEvent(new Event('change', {bubbles: true}));
                  profile.value = 'pending';
                  profile.dispatchEvent(new Event('change', {bubbles: true}));
                  return {mode: mode.value, fixture_profile: profile.value};
                }"""
            )
            if selected_fixture != {"mode": "fixture", "fixture_profile": "pending"}:
                raise AssertionError("Could not explicitly select fixture/pending in the UI controls")
            report["fixture"]["selected_controls"] = selected_fixture
            page.locator("#btn-toggle-cam").click()
            page.wait_for_function(
                """() => {
                  const camera = document.querySelector(".road-camera");
                  return camera && camera.dataset.caseId && camera.dataset.sourceId;
                }""",
                timeout=15000,
            )
            binding = page.locator(".road-camera").evaluate(
                """element => ({
                  case_id: element.dataset.caseId,
                  source_id: element.dataset.sourceId
                })"""
            )
            case_id = binding["case_id"]
            source_id = binding["source_id"]
            report["selected_binding"] = binding

            page.locator("#btn-sim-wrongway").click()
            page.locator("#nav-node-room-2").click()

            case, model = wait_for_real_case(page, case_id)
            clip = case["clip"]
            exchanges = model["exchanges"]
            exchange_ids = case["exchange_ids"]
            exchange_by_id = {
                item.get("exchange_id"): item
                for item in exchanges
                if isinstance(item, dict) and item.get("exchange_id")
            }
            matched_exchanges = [
                exchange_by_id[item]
                for item in exchange_ids
                if item in exchange_by_id
            ]
            if not matched_exchanges:
                raise AssertionError(
                    "human_review had no ModelExchange matching case.exchange_ids"
                )
            exchange = matched_exchanges[-1]

            if case.get("case_id") != case_id or case.get("source_id") != source_id:
                raise AssertionError("DOM case/source binding disagrees with the real Case API")
            if case.get("mode") != "fixture" or case.get("fixture_profile") != "pending":
                raise AssertionError("The server did not retain the explicitly selected fixture")
            if exchange.get("fixture_profile") != "pending":
                raise AssertionError("The ModelExchange does not carry the selected pending profile")
            exchange_clip = exchange.get("clip")
            if not isinstance(exchange_clip, dict):
                raise AssertionError("The real ModelExchange has no ClipRef metadata")
            if exchange_clip.get("clip_id") != clip.get("clip_id"):
                raise AssertionError("Case ClipRef and ModelExchange ClipRef do not match")
            if exchange_clip.get("source_id") != source_id:
                raise AssertionError("ModelExchange ClipRef belongs to another source")

            clip_id = str(clip.get("clip_id") or "")
            expected_hash = str(clip.get("sha256") or "")
            if not clip_id or not expected_hash:
                raise AssertionError("The real Case ClipRef lacks clip_id or sha256")

            clip_response = context.request.get(
                base_url + "/road/clips/" + clip_id,
                timeout=15000,
            )
            clip_bytes = clip_response.body()
            received_hash = hashlib.sha256(clip_bytes).hexdigest()
            if clip_response.status != 200:
                raise AssertionError("The real clip endpoint returned HTTP " + str(clip_response.status))
            if received_hash != expected_hash:
                raise AssertionError(
                    "SHA-256 of received MP4 bytes does not match the Case ClipRef"
                )
            content_type = clip_response.headers.get("content-type", "")
            if "video/mp4" not in content_type.lower():
                raise AssertionError("The clip endpoint did not return video/mp4")

            report["backend_ready"] = {
                "case_id": case_id,
                "source_id": source_id,
                "phase": case.get("phase"),
                "mode": case.get("mode"),
                "fixture_profile": case.get("fixture_profile"),
                "assessment": {
                    key: case.get("assessment", {}).get(key)
                    for key in ("object_class", "hazard", "confidence", "summary", "decision")
                },
                "exchange_ids": exchange_ids,
                "exchange_count": len(exchanges),
                "matched_exchange_id": exchange.get("exchange_id"),
                "exchange_fixture_profile": exchange.get("fixture_profile"),
                "clip": {
                    key: clip.get(key)
                    for key in ("clip_id", "source_id", "version", "sha256", "start_seconds", "end_seconds")
                },
                "exchange_clip": {
                    key: exchange_clip.get(key)
                    for key in ("clip_id", "source_id", "version", "sha256")
                },
                "download": {
                    "status": clip_response.status,
                    "content_type": content_type,
                    "bytes": len(clip_bytes),
                    "sha256_received": received_hash,
                    "sha256_matches": received_hash == expected_hash,
                },
            }
            report["checks"]["real_human_review"] = case.get("phase") == "human_review"
            report["checks"]["real_clip_and_exchange_ready"] = True
            report["checks"]["real_clip_bytes_hash_matches"] = received_hash == expected_hash
            report["checks"]["real_http_posts"] = {
                "session_start": any(item["path"] == "/road/sessions" for item in report["posts"]),
                "case_start": any(item["path"] == "/road/cases" for item in report["posts"]),
                "wrong_way_event": any(
                    item["path"].endswith("/events")
                    and isinstance(item.get("body"), str)
                    and '"wrong_way"' in item["body"]
                    for item in report["posts"]
                ),
            }
            if not all(report["checks"]["real_http_posts"].values()):
                raise AssertionError("The UI did not send the real session/case/event POSTs")

            page.screenshot(path=str(output / "room-2-after-backend-ready.png"), full_page=True)
            model_visibility = visible_state(page, "#road-model-root")
            model_area_visibility = visible_state(page, ".road-workspace-grid")
            report["ui_room_2"] = {
                "model_root": model_visibility,
                "model_workspace_grid": model_area_visibility,
            }

            page.locator("#nav-node-room-3").click()
            page.screenshot(path=str(output / "room-3-after-backend-ready.png"), full_page=True)
            workplace_visibility = visible_state(page, "#road-workplace-root")
            player_state = inspect_native_player(page, clip_id, expected_hash)
            report["ui_room_3"] = {
                "workplace_root": workplace_visibility,
                "native_player": player_state,
                "cartoon_monitor_video_count": page.locator(
                    "#cartoon-review-video-box video"
                ).count(),
            }

            failures: list[str] = []
            if not model_visibility["visible"]:
                failures.append("actual_model_workspace_invisible")
            if not workplace_visibility["visible"]:
                failures.append("actual_workplace_invisible")
            if not player_state.get("player_present") or not player_state.get("player_visible"):
                failures.append("native_case_video_player_missing_or_invisible")
            if player_state.get("player_present") and (
                not player_state.get("clip_id_matches") or not player_state.get("hash_matches")
            ):
                failures.append("native_video_not_bound_to_selected_case_clip")
            report["product_failures"] = failures
            report["external_hosts"] = sorted(external_hosts)
            report["checks"]["no_external_browser_requests"] = not external_hosts

            if failures:
                report["status"] = "targeted_red"
                report["classification"] = (
                    "Backend reached human_review with matching ClipRef/ModelExchange "
                    "and verified MP4 bytes before the GUI visibility/player assertions failed."
                )
                exit_code = 1
            else:
                report["status"] = "passed"
                report["classification"] = "Model workspace and native case video are visible."
                exit_code = 0

    except Exception as exc:
        report["status"] = "incomplete"
        report["error_type"] = type(exc).__name__
        report["error"] = str(exc)
        report["traceback"] = traceback.format_exc()
        report["external_hosts"] = sorted(external_hosts)
        exit_code = 2
    finally:
        try:
            if context is not None:
                context.close()
        except Exception:
            pass
        try:
            if browser is not None:
                browser.close()
        except Exception:
            pass
        report["cleanup"] = stop_server_tree(server)
        server_log.close()
        report["finished_at"] = datetime.now(timezone.utc).isoformat()
        report["exit_code"] = exit_code
        report_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    print(json.dumps({
        "status": report["status"],
        "phase": report["phase"],
        "exit_code": exit_code,
        "edge_version": report.get("edge_version"),
        "case_id": (report.get("backend_ready") or {}).get("case_id"),
        "source_id": (report.get("backend_ready") or {}).get("source_id"),
        "exchange_id": (report.get("backend_ready") or {}).get("matched_exchange_id"),
        "clip_id": ((report.get("backend_ready") or {}).get("clip") or {}).get("clip_id"),
        "sha256_received": ((report.get("backend_ready") or {}).get("download") or {}).get("sha256_received"),
        "product_failures": report.get("product_failures", []),
        "checks": report.get("checks", {}),
        "artifacts": {
            "report": str(report_path),
            "screenshots": [str(path) for path in screenshot_paths if path.is_file()],
        },
        "cleanup": report.get("cleanup"),
    }, ensure_ascii=False, indent=2))
    return exit_code


def run_fast_or_full_phase(args: argparse.Namespace, sync_playwright: Any) -> int:
    output = fresh_output_directory(args.output, args.phase)
    server_python = Path(args.server_python).expanduser().resolve()
    edge_binary = Path(args.browser_path).expanduser().resolve()
    port = free_port()
    base_url = "http://127.0.0.1:" + str(port)
    server_log_path = output / "server.log"
    report_path = output / "acceptance.json"
    server_data = output / "server-data"
    server_log = server_log_path.open("w", encoding="utf-8", newline="\n")
    server: subprocess.Popen[bytes] | None = None
    browser = None
    context = None
    report: dict[str, Any] = {
        "started_at": datetime.now(timezone.utc).isoformat(),
        "worktree": str(REPO),
        "output_directory": str(output),
        "phase": args.phase,
        "harness_python": sys.executable,
        "server_python": str(server_python),
        "edge_binary": str(edge_binary),
        "base_url": base_url,
        "status": "incomplete",
        "posts": [],
        "external_hosts": [],
        "browser_errors": [],
        "console_errors": [],
        "http_paths": {},
        "checks": {},
    }
    external_hosts: set[str] = set()
    exit_code = 2

    try:
        if not server_python.is_file():
            raise FileNotFoundError("Server Python is missing: " + str(server_python))
        if not edge_binary.is_file():
            raise FileNotFoundError("Microsoft Edge executable is missing: " + str(edge_binary))

        server_env = os.environ.copy()
        server_env["PYTHONIOENCODING"] = "utf-8"
        server_env["PYTHONUNBUFFERED"] = "1"
        server_env["PYTHONFAULTHANDLER"] = "1"
        existing_pythonpath = server_env.get("PYTHONPATH", "")
        server_env["PYTHONPATH"] = str(REPO / "src") + (
            os.pathsep + existing_pythonpath if existing_pythonpath else ""
        )
        server_command = [
            str(server_python), "-u", "-m", "visionflow.cli", "serve",
            "--host", "127.0.0.1", "--port", str(port),
            "--data-dir", str(server_data),
        ]
        report["server_launch_command"] = server_command
        server = subprocess.Popen(
            server_command,
            cwd=REPO,
            env=server_env,
            stdout=server_log,
            stderr=subprocess.STDOUT,
            creationflags=CREATE_NO_WINDOW,
        )
        report["server_pid"] = server.pid
        wait_for_server(port, server)
        report["checks"]["isolated_loopback_server"] = True

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                executable_path=str(edge_binary),
                headless=True,
                args=["--autoplay-policy=no-user-gesture-required"],
            )
            report["edge_version"] = browser.version
            context = browser.new_context(viewport={"width": 1440, "height": 1000})

            def guard_external(route: Any) -> None:
                host = urlparse(route.request.url).hostname or ""
                if host not in LOOPBACK_HOSTS:
                    external_hosts.add(host)
                    route.abort()
                    return
                route.continue_()

            def attach_page(page: Any) -> None:
                page.on("pageerror", lambda error: report["browser_errors"].append(str(error)))
                def record_ui_request(request: Any) -> None:
                    parsed = urlparse(request.url)
                    if parsed.hostname not in LOOPBACK_HOSTS:
                        return
                    key = request.method + " " + parsed.path
                    paths = report.setdefault("ui_http_paths", {})
                    paths[key] = paths.get(key, 0) + 1

                page.on("request", record_ui_request)
                page.on(
                    "console",
                    lambda message: report["console_errors"].append(message.text)
                    if message.type == "error" else None,
                )

            def record_request(request: Any) -> None:
                parsed = urlparse(request.url)
                if parsed.hostname not in LOOPBACK_HOSTS:
                    return
                key = request.method + " " + parsed.path
                paths = report["http_paths"]
                paths[key] = paths.get(key, 0) + 1
                if request.method == "POST" and (
                    parsed.path.startswith("/road/") or parsed.path.startswith("/runs")
                ):
                    report["posts"].append({"path": parsed.path, "body": request.post_data})

            context.route("**/*", guard_external)
            context.on("page", attach_page)
            context.on("request", record_request)
            page = context.new_page()
            response = page.goto(base_url + "/", wait_until="domcontentloaded", timeout=30000)
            headers = response.headers if response is not None else {}
            csp = headers.get("content-security-policy", "")
            report["csp"] = csp
            if not csp or "default-src 'self'" not in csp or "script-src 'self'" not in csp or "object-src 'none'" not in csp:
                raise AssertionError("Local UI response did not supply the expected restrictive self-only CSP")
            if "unsafe-inline" in csp or "unsafe-eval" in csp:
                raise AssertionError("Local UI CSP unexpectedly allows inline scripts or eval")
            report["checks"]["restrictive_self_only_csp"] = True
            page.locator("#road-tab").wait_for(state="visible", timeout=15000)
            page.wait_for_function(
                "() => document.querySelector('#road-tab') && !document.querySelector('#road-tab').disabled",
                timeout=15000,
            )
            page.locator("#road-tab").click()
            page.locator(".road-cartoon-stage").wait_for(state="visible", timeout=15000)
            page.locator("#road-camera-create").wait_for(state="visible", timeout=15000)
            page.wait_for_function(
                "() => document.querySelector('#road-model-mode')?.options?.length > 0 && document.querySelector('#road-fixture-profile')?.options?.length > 0",
                timeout=15000,
            )
            report["checks"]["four_room_controller_booted"] = True

            try:
                if args.phase == "fast":
                    summary = run_fast_flow(page, context, base_url, output, report)
                    report["fast_result"] = summary
                else:
                    report["full_fast_result"] = run_fast_flow(page, context, base_url, output, report)
                    run_full_profiles(page, context, base_url, output, report)
                    page = run_deadline_reload_and_analysis(page, context, base_url, output, report)
            except Exception:
                try:
                    failure_screenshot = output / (args.phase + "-failure-state.png")
                    page.screenshot(path=str(failure_screenshot), full_page=True)
                    report.setdefault("screenshots", []).append(str(failure_screenshot))
                except Exception:
                    pass
                raise

            if external_hosts:
                raise AssertionError("Browser attempted external hosts: " + ", ".join(sorted(external_hosts)))
            if report["browser_errors"]:
                raise AssertionError("Unexpected JavaScript exceptions occurred: " + " | ".join(report["browser_errors"][:5]))
            if report["console_errors"]:
                raise AssertionError("Browser reported JavaScript/CSP errors: " + " | ".join(report["console_errors"][:5]))
            report["checks"]["no_unexpected_javascript_errors"] = True
            report["checks"]["no_external_browser_requests"] = True
            report["status"] = "passed"
            report["classification"] = "Real local HTTP, server, browser, case, and clip acceptance passed."
            exit_code = 0

    except Exception as exc:
        report["status"] = "failed" if report.get("checks", {}).get("isolated_loopback_server") else "incomplete"
        report["error_type"] = type(exc).__name__
        report["error"] = str(exc)
        report["traceback"] = traceback.format_exc()
        report["external_hosts"] = sorted(external_hosts)
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.25):
                report["listener_probe_at_error"] = {"reachable": True}
        except OSError as probe_error:
            report["listener_probe_at_error"] = {
                "reachable": False,
                "error_type": type(probe_error).__name__,
                "error": str(probe_error),
            }
        exit_code = 1 if report["status"] == "failed" else 2
    finally:
        if server is None:
            report["server_process_observation_before_cleanup"] = {
                "started": False,
                "pid": None,
                "poll_returncode": None,
                "running": False,
            }
        else:
            server_returncode = server.poll()
            report["server_process_observation_before_cleanup"] = {
                "started": True,
                "pid": server.pid,
                "poll_returncode": server_returncode,
                "running": server_returncode is None,
                "server_log_bytes": server_log_path.stat().st_size if server_log_path.exists() else None,
                "stdout_unbuffered": True,
                "python_faulthandler": True,
            }
        try:
            if context is not None:
                context.close()
        except Exception:
            pass
        try:
            if browser is not None:
                browser.close()
        except Exception:
            pass
        report["cleanup"] = stop_server_tree(server)
        server_log.close()
        report["finished_at"] = datetime.now(timezone.utc).isoformat()
        report["exit_code"] = exit_code
        report_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    short_cases = [
        {
            "source_id": item.get("source_id"),
            "case_id": item.get("case_id"),
            "profile": item.get("fixture_profile"),
            "phase": item.get("phase"),
        }
        for item in report.get("cases", [])
    ]
    print(json.dumps({
        "status": report["status"],
        "phase": report["phase"],
        "exit_code": exit_code,
        "edge_version": report.get("edge_version"),
        "sources": [item.get("source_id") for item in report.get("sources", [])],
        "cases": short_cases,
        "fast_result": report.get("fast_result"),
        "deadline": report.get("deadline"),
        "legacy_analysis": report.get("legacy_analysis"),
        "error": report.get("error"),
        "checks": report.get("checks", {}),
        "external_hosts": report.get("external_hosts", []),
        "browser_errors": report.get("browser_errors", []),
        "console_errors": report.get("console_errors", []),
        "artifacts": {
            "report": str(report_path),
            "screenshots": report.get("screenshots", []),
        },
        "cleanup": report.get("cleanup"),
    }, ensure_ascii=False, indent=2))
    return exit_code


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", choices=("red", "fast", "full"), required=True)
    parser.add_argument("--server-python", required=True, help="Worktree Python used for the local server")
    parser.add_argument("--browser-path", required=True, help="Native Edge executable")
    parser.add_argument("--output", required=True, help="Fresh gui-controller-{red,fast,full}-* directory under .local/acceptance")
    args = parser.parse_args()

    # Browser automation stays out of pytest collection and is available only in
    # the optional browser-test environment selected explicitly by this CLI.
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        parser.error("Playwright is available only in the optional browser-test environment: " + str(exc))
    if args.phase == "red":
        return run_red_phase(args, sync_playwright)
    return run_fast_or_full_phase(args, sync_playwright)


if __name__ == "__main__":
    raise SystemExit(main())
