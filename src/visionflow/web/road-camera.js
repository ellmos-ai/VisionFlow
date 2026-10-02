"use strict";

let mountSequence = 0;

const CAMERA_STANDBY_SVG = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 640 360" width="640" height="360">
  <rect width="640" height="360" fill="#0b1220"/>
  <defs>
    <pattern id="roadgrid" width="40" height="40" patternUnits="userSpaceOnUse">
      <path d="M 40 0 L 0 0 0 40" fill="none" stroke="#1e293b" stroke-width="1"/>
    </pattern>
    <linearGradient id="roadgrade" x1="0%" y1="0%" x2="0%" y2="100%">
      <stop offset="0%" stop-color="#131b2e" />
      <stop offset="100%" stop-color="#0b1220" />
    </linearGradient>
  </defs>
  <rect width="640" height="360" fill="url(#roadgrid)" />
  <polygon points="260,130 380,130 540,360 100,360" fill="url(#roadgrade)" opacity="0.7"/>
  <line x1="320" y1="130" x2="320" y2="360" stroke="#f59e0b" stroke-width="3" stroke-dasharray="16,12" opacity="0.6"/>
  <circle cx="320" cy="115" r="30" fill="#131b2e" stroke="#38bdf8" stroke-width="2" />
  <path d="M310,107 L328,107 L328,123 L310,123 Z M328,111 L336,107 L336,123 L328,119 Z" fill="#38bdf8"/>
  <text x="320" y="195" fill="#f8fafc" font-family="system-ui, -apple-system, sans-serif" font-size="15" font-weight="700" text-anchor="middle">Kamera im Bereitschaftsmodus</text>
  <text x="320" y="222" fill="#94a3b8" font-family="system-ui, -apple-system, sans-serif" font-size="12" text-anchor="middle">Sitzung starten oder ⚡ 1-Klick Demo aktivieren</text>
</svg>`;

let standbyBlobUrl = null;
function getStandbyUrl() {
  if (!standbyBlobUrl) {
    standbyBlobUrl = URL.createObjectURL(new Blob([CAMERA_STANDBY_SVG], { type: "image/svg+xml" }));
  }
  return standbyBlobUrl;
}

const ACTIVE_RUN_STATES = new Set([
  "queued", "running", "observing", "waiting_for_input", "waiting_for_approval",
]);
const RUN_LABELS = {
  queued: "Wartet",
  running: "Läuft",
  observing: "Beobachtet",
  waiting_for_input: "Eingabe erforderlich",
  waiting_for_approval: "Freigabe erforderlich",
  completed: "Abgeschlossen",
  failed: "Fehlgeschlagen",
  cancelled: "Abgebrochen",
};
const FIXTURE_PROFILE_LABELS = {
  pending: "Offline-Testantwort · menschliche Prüfung (Sicherheit 0,80)",
  high: "Offline-Testantwort · hohe Sicherheit (0,99)",
  bird: "Offline-Testantwort · Vogel, keine Gefahr (0,99)",
  leaf: "Offline-Testantwort · Blatt, keine Gefahr (0,99)",
  followup: "Offline-Testantwort · Folgebeobachtung",
  unclear: "Offline-Testantwort · unklar",
  error: "Offline-Testantwort · Modellfehler",
};
const LOCKED_CASE_PHASES = new Set(["model_review", "human_review", "no_response"]);

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function button(text, className = "button secondary") {
  const node = element("button", className, text);
  node.type = "button";
  return node;
}

function formatNumber(value, digits = 1) {
  return typeof value === "number" && Number.isFinite(value)
    ? value.toLocaleString("de-DE", { maximumFractionDigits: digits })
    : "–";
}

export function mountRoadCamera(container, apiClient) {
  if (!container || typeof container.replaceChildren !== "function") {
    throw new TypeError("mountRoadCamera benötigt einen DOM-Container.");
  }
  if (!apiClient || typeof apiClient.get !== "function" ||
      typeof apiClient.post !== "function" || typeof apiClient.getClip !== "function") {
    throw new TypeError("mountRoadCamera benötigt lokale get/post/getClip-Aufrufe.");
  }

  const instanceId = ++mountSequence;
  let destroyed = false;
  let roadAvailable = false;
  let capabilities = null;
  let sourceId = null;
  let sourceOpen = false;
  let sourceGeneration = 0;
  let viewGeneration = 0;
  let clipSelectionGeneration = 0;
  let locked = false;
  let operationQueue = Promise.resolve();
  const queuedOperations = new Map();
  const clips = new Map();
  const clipUrls = new Map();
  const clipInflight = new Map();
  let currentClipId = null;
  let frameTimer = null;
  let recordingTimer = null;
  let lastRecordingRequest = 0;
  let clipListSignature = "";
  let caseId = null;
  let caseSnapshot = null;
  let runId = null;
  let runActive = false;
  let warmedUp = false;
  let runTimer = null;
  let frameSequence = 0;
  let frameObjectUrl = null;
  const pendingFrameUrls = new Set();
  let autoDemoActive = false;

  const root = element("section", "road-camera");
  root.setAttribute("aria-labelledby", `road-camera-title-${instanceId}`);

  const heading = element("div", "road-camera__heading");
  const title = element("h2", "", "Straßenkamera-Demo");
  title.id = `road-camera-title-${instanceId}`;
  heading.append(title);
  heading.append(element(
    "p",
    "road-camera__intro",
    "Eine lokale synthetische Verkehrsszene liefert echte Bildpixel für die freigegebenen OpenCV-Werkzeuge.",
  ));

  const caseOptions = element("div", "road-camera__case-options");
  const modeLabel = element("label", "", "Modellmodus");
  modeLabel.htmlFor = "road-model-mode";
  const modeSelect = element("select", "road-camera__mode-select");
  modeSelect.id = "road-model-mode";
  const profileLabel = element("label", "", "Offline-Testantwort");
  profileLabel.htmlFor = "road-fixture-profile";
  const profileSelect = element("select", "road-camera__profile-select");
  profileSelect.id = "road-fixture-profile";
  caseOptions.append(modeLabel, modeSelect, profileLabel, profileSelect);

  const toolbar = element("div", "road-camera__toolbar");
  const quickDemoButton = button("⚡ 1-Klick Demo (Falschfahrer)", "button road-quick-demo-btn");
  quickDemoButton.id = "road-camera-quick-demo";
  quickDemoButton.title = "Startet automatisch Quelle & Analyse und blendet nach 3 Startframes den Falschfahrer ein.";
  const createButton = button("Neue Sitzung starten", "button primary");
  createButton.id = "road-camera-create";
  createButton.setAttribute("aria-label", "Neue Straßenkamera-Sitzung starten");
  createButton.disabled = true;
  const stopButton = button("Aufnahme beenden", "button danger");
  stopButton.id = "road-camera-stop";
  const resetButton = button("Zurücksetzen", "button secondary");
  resetButton.id = "road-camera-reset";
  stopButton.disabled = true;
  resetButton.disabled = true;
  toolbar.append(quickDemoButton, createButton, stopButton, resetButton);

  const status = element("p", "road-camera__status", "Noch keine Sitzung gestartet.");
  status.setAttribute("role", "status");
  status.setAttribute("aria-live", "polite");

  const sourceSummary = element("p", "road-camera__source", "Quelle: –");

  const runPanel = element("section", "road-camera__run");
  runPanel.append(element("h3", "", "Analyse und Controller-Trace"));
  const runToolbar = element("div", "road-camera__run-toolbar");
  const runSummary = element("p", "road-camera__run-summary", "Noch kein Analyse-Lauf gestartet.");
  runSummary.setAttribute("role", "status");
  runSummary.setAttribute("aria-live", "polite");
  const cancelRunButton = button("Analyse abbrechen", "button danger");
  cancelRunButton.id = "road-camera-cancel";
  cancelRunButton.disabled = true;
  runToolbar.append(runSummary, cancelRunButton);
  const findings = element("div", "road-camera__findings");
  findings.append(element("p", "road-camera__empty", "Noch keine Analysebefunde."));
  const traceDetails = element("details");
  traceDetails.append(element("summary", "", "Gespeicherte Controller-Ereignisse"));
  const traceContent = element("pre", "road-camera__trace", "Noch kein Trace vorhanden.");
  traceDetails.append(traceContent);
  runPanel.append(runToolbar, findings, traceDetails);

  const content = element("div", "road-camera__content");

  const livePanel = element("section", "road-camera__live");
  const liveTitle = element("h3", "", "Livebild");
  const viewport = element("div", "road-camera__viewport");
  const hud = element("div", "road-camera__hud");
  const recIndicator = element("span", "road-camera__rec-indicator");
  const recDot = element("span", "road-camera__rec-dot road-camera__rec-dot--standby");
  const recText = element("span", "", " STANDBY");
  recIndicator.append(recDot, recText);
  const camInfo = element("span", "road-camera__cam-info", "CAM-01 · B4 NORD");
  hud.append(recIndicator, camInfo);
  const frame = element("img", "road-camera__frame");
  frame.alt = "Aktuelles Bild der synthetischen Straßenkamera";
  frame.decoding = "async";
  frame.src = getStandbyUrl();
  viewport.append(frame, hud);
  const frameMeta = element("p", "road-camera__frame-meta", "Bereit. Starte eine Sitzung oder nutze die ⚡ 1-Klick Demo.");
  livePanel.append(liveTitle, viewport, frameMeta);

  const eventPanel = element("section", "road-camera__events");
  eventPanel.append(element("h3", "", "Szenarioereignis"));
  const eventLabel = element("label", "", "Ereignis für die Szene");
  eventLabel.htmlFor = "road-camera-event";
  const eventSelect = element("select", "road-camera__event-select");
  eventSelect.id = "road-camera-event";
  for (const [value, label] of [
    ["wrong_way", "Falschfahrer"],
    ["bird", "Vogel"],
    ["leaf", "Blatt"],
  ]) {
    const option = element("option", "", label);
    option.value = value;
    eventSelect.append(option);
  }
  const injectButton = button("Ereignis einblenden", "button secondary");
  injectButton.id = "road-camera-inject";
  const eventHint = element(
    "p",
    "road-camera__hint",
    "Das Ereignis verändert nur die Szene. Ein positiver Analysebefund wird ausschließlich aus tatsächlich analysierten Frames übernommen.",
  );
  eventPanel.append(eventLabel, eventSelect, injectButton, eventHint);

  const recordingPanel = element("section", "road-camera__recording");
  recordingPanel.append(element("h3", "", "Aufzeichnung"));
  const recordingSummary = element("p", "road-camera__recording-summary", "Warte auf Aufnahmedaten.");
  recordingSummary.setAttribute("aria-live", "off");
  const clipList = element("div", "road-camera__clip-list");
  clipList.setAttribute("aria-label", "Gespeicherte Clips");
  recordingPanel.append(recordingSummary, clipList);

  const playerPanel = element("section", "road-camera__player-panel");
  playerPanel.append(element("h3", "", "Gespeicherter Clip"));
  const playerEmpty = element("div", "road-camera__player-empty", "Noch kein Clip geladen. Wähle nach Beenden der Aufnahme einen Clip aus der Liste.");
  const player = element("video", "road-camera__player");
  player.id = "road-camera-player";
  player.controls = true;
  player.playsInline = true;
  player.preload = "metadata";
  player.setAttribute("aria-label", "Gespeicherte Straßenkamera-Aufzeichnung");
  player.hidden = true;
  playerPanel.append(playerEmpty, player);
  const playerCaption = element("p", "road-camera__player-caption", "Wähle einen gespeicherten Clip aus.");
  playerPanel.append(playerCaption);

  const secondaryGrid = element("div", "road-camera__secondary");
  secondaryGrid.append(eventPanel, recordingPanel);
  content.append(livePanel, secondaryGrid, playerPanel);
  root.append(heading, caseOptions, toolbar, status, sourceSummary, runPanel, content);
  container.replaceChildren(root);

  function setStatus(message, isError = false) {
    if (destroyed) return;
    status.textContent = message;
    status.classList.toggle("road-camera__status--error", isError);
  }

  function current(source, generation, view = viewGeneration) {
    return !destroyed && sourceId === source &&
      sourceGeneration === generation && viewGeneration === view;
  }

  function enqueue(key, task) {
    if (queuedOperations.has(key)) return queuedOperations.get(key);
    const result = operationQueue.then(task, task);
    operationQueue = result.catch(() => {});
    const shared = result.finally(() => {
      if (queuedOperations.get(key) === shared) queuedOperations.delete(key);
    });
    queuedOperations.set(key, shared);
    return shared;
  }

  function clearFrameTimer() {
    if (frameTimer !== null) window.clearTimeout(frameTimer);
    frameTimer = null;
  }

  function clearRunTimer() {
    if (runTimer !== null) window.clearTimeout(runTimer);
    runTimer = null;
  }

  function clearFrameImage() {
    frameSequence += 1;
    frame.src = getStandbyUrl();
    for (const url of pendingFrameUrls) URL.revokeObjectURL(url);
    pendingFrameUrls.clear();
    if (frameObjectUrl) URL.revokeObjectURL(frameObjectUrl);
    frameObjectUrl = null;
  }

  function setFrameImage(encoded) {
    const bytes = Uint8Array.from(atob(encoded), (character) => character.charCodeAt(0));
    const url = URL.createObjectURL(new Blob([bytes], { type: "image/jpeg" }));
    const sequence = ++frameSequence;
    pendingFrameUrls.add(url);
    frame.onload = () => {
      if (!pendingFrameUrls.has(url)) return;
      pendingFrameUrls.delete(url);
      if (sequence !== frameSequence) {
        URL.revokeObjectURL(url);
        return;
      }
      if (frameObjectUrl && frameObjectUrl !== url) URL.revokeObjectURL(frameObjectUrl);
      for (const pending of pendingFrameUrls) URL.revokeObjectURL(pending);
      pendingFrameUrls.clear();
      frameObjectUrl = url;
      frame.onload = null;
      frame.onerror = null;
    };
    frame.onerror = () => {
      if (pendingFrameUrls.delete(url)) URL.revokeObjectURL(url);
      if (frameObjectUrl === url) frameObjectUrl = null;
      frame.onload = null;
      frame.onerror = null;
    };
    frame.src = url;
  }

  function clearRecordingTimer() {
    if (recordingTimer !== null) window.clearTimeout(recordingTimer);
    recordingTimer = null;
  }

  function renderClipList() {
    const ordered = [...clips.values()].sort((left, right) =>
      String(left.source_id).localeCompare(String(right.source_id)) ||
      Number(left.version || 0) - Number(right.version || 0),
    );
    const signature = ordered.map((clip) => `${clip.clip_id}:${clip.sha256}`).join("|");
    if (signature === clipListSignature) return;
    const active = document.activeElement;
    const focusedClip = active && active.dataset ? active.dataset.clipId : null;
    clipListSignature = signature;
    clipList.replaceChildren();
    if (ordered.length === 0) {
      clipList.append(element("p", "road-camera__empty", "Noch kein gespeicherter Clip."));
      return;
    }
    for (const clip of ordered) {
      const duration = Math.max(0, Number(clip.end_seconds) - Number(clip.start_seconds));
      const sourceShort = String(clip.source_id || "").slice(-8);
      const item = button(
        `Quelle …${sourceShort} · Version ${formatNumber(clip.version, 0)} · ${formatNumber(duration)} Sek.`,
        "button secondary road-camera__clip-button",
      );
      item.dataset.clipId = clip.clip_id;
      item.setAttribute("aria-pressed", String(currentClipId === clip.clip_id));
      item.addEventListener("click", () => {
        loadClip(clip).catch((error) => setStatus(error.message, true));
      });
      clipList.append(item);
    }
    if (focusedClip) {
      const replacement = [...clipList.querySelectorAll("[data-clip-id]")]
        .find((item) => item.dataset.clipId === focusedClip);
      if (replacement) replacement.focus();
    }
  }

  function rememberClips(payload) {
    if (!payload || !Array.isArray(payload.clips)) return;
    for (const clip of payload.clips) {
      if (clip && typeof clip.clip_id === "string" && typeof clip.sha256 === "string") {
        clips.set(clip.clip_id, clip);
      }
    }
    renderClipList();
  }

  async function refreshRecording(source, generation, view = viewGeneration) {
    if (!source || destroyed) return;
    const elapsed = Date.now() - lastRecordingRequest;
    if (elapsed < 1000) {
      clearRecordingTimer();
      recordingTimer = window.setTimeout(
        () => refreshRecording(source, generation, view),
        1000 - elapsed,
      );
      return;
    }
    lastRecordingRequest = Date.now();
    try {
      const payload = await apiClient.get(
        `/road/sessions/${encodeURIComponent(source)}/recording`,
      );
      if (destroyed || viewGeneration !== view) return;
      rememberClips(payload);
      if (source === sourceId && sourceGeneration === generation) {
        const fps = formatNumber(payload.source_fps, 2);
        const frames = formatNumber(payload.frame_count, 0);
        const seconds = formatNumber(payload.end_seconds, 2);
        const compressed = formatNumber(payload.compressed_bytes, 0);
        const state = payload.closed ? "Quelle beendet" : "Aufzeichnung läuft";
        recordingSummary.textContent =
          `${state} · ${frames} Frames · ${fps} Bilder/Sek. gemessen · ${seconds} Sek. · ${compressed} Byte`;
        if (payload.closed) {
          sourceOpen = false;
          clearFrameTimer();
          updateEventControls();
          setStatus(payload.error_code
            ? "Quelle mit einem technischen Fehler beendet."
            : "Quelle beendet; das letzte echte Kamerabild bleibt sichtbar.");
        }
      }
    } catch (error) {
      if (source === sourceId && sourceGeneration === generation && viewGeneration === view) {
        setStatus(error.message || "Aufnahmedaten konnten nicht geladen werden.", true);
      }
    } finally {
      if (!destroyed && source === sourceId && sourceGeneration === generation &&
          viewGeneration === view && sourceOpen) {
        clearRecordingTimer();
        recordingTimer = window.setTimeout(
          () => refreshRecording(source, generation, view),
          Math.max(0, 1000 - (Date.now() - lastRecordingRequest)),
        );
      }
    }
  }

  async function pollFrame() {
    const source = sourceId;
    const generation = sourceGeneration;
    const view = viewGeneration;
    if (!source || !sourceOpen || destroyed) return;
    try {
      const payload = await apiClient.get(
        `/road/sessions/${encodeURIComponent(source)}/frame`,
      );
      if (!current(source, generation, view)) return;
      if (typeof payload.image_base64 === "string" && payload.image_base64) {
        setFrameImage(payload.image_base64);
        frameMeta.textContent =
          `Frame ${String(payload.frame_id || "–")} · Quellenzeit ${formatNumber(payload.timestamp_seconds, 2)} Sek.`;
        recText.textContent = " LIVE / REC";
        recDot.classList.remove("road-camera__rec-dot--standby");
      }
      if (payload.closed) {
        sourceOpen = false;
        clearFrameTimer();
        updateEventControls();
        recText.textContent = " STANDBY";
        recDot.classList.add("road-camera__rec-dot--standby");
        setStatus(payload.error_code
          ? "Quelle mit einem technischen Fehler beendet."
          : "Quelle beendet; das letzte echte Kamerabild bleibt sichtbar.");
        refreshRecording(source, generation, view);
        return;
      }
    } catch (error) {
      if (current(source, generation, view)) {
        setStatus(error.message || "Das aktuelle Kamerabild konnte nicht geladen werden.", true);
      }
    }
    if (current(source, generation, view) && sourceOpen) {
      frameTimer = window.setTimeout(pollFrame, 250);
    }
  }

  function startPolling() {
    clearFrameTimer();
    clearRecordingTimer();
    if (!sourceId || !sourceOpen) return;
    const source = sourceId;
    const generation = sourceGeneration;
    const view = viewGeneration;
    pollFrame();
    refreshRecording(source, generation, view);
  }

  function updateSourceControls() {
    const hasOpenSource = Boolean(sourceId && sourceOpen);
    quickDemoButton.disabled = !roadAvailable || locked || destroyed;
    createButton.disabled = !roadAvailable || locked || destroyed;
    stopButton.disabled = !sourceId || destroyed;
    stopButton.textContent = hasOpenSource ? "Aufnahme beenden" : "Aufnahme speichern";
    resetButton.disabled = !sourceId || destroyed;
    modeSelect.disabled = !roadAvailable || hasOpenSource || locked || destroyed;
    profileSelect.disabled =
      modeSelect.value !== "fixture" || hasOpenSource || locked || destroyed;
    profileLabel.hidden = modeSelect.value !== "fixture";
    profileSelect.hidden = modeSelect.value !== "fixture";
  }

  function configureCaseOptions() {
    const priorMode = modeSelect.value || "fixture";
    const modes = Array.isArray(capabilities?.modes)
      ? capabilities.modes.filter((value) => typeof value === "string")
      : [];
    const fixtureAllowed = capabilities?.available === true && modes.includes("fixture");
    const nativeAllowed =
      capabilities?.available === true &&
      capabilities?.native_enabled === true &&
      modes.includes("gemini_video");
    modeSelect.replaceChildren();
    if (fixtureAllowed) {
      const fixture = element("option", "", "Offline-Testantwort");
      fixture.value = "fixture";
      modeSelect.append(fixture);
    }
    const native = element(
      "option",
      "",
      nativeAllowed ? "Gemini Video" : "Gemini Video (nicht freigeschaltet)",
    );
    native.value = "gemini_video";
    native.disabled = !nativeAllowed;
    modeSelect.append(native);
    if (priorMode === "gemini_video" && nativeAllowed) modeSelect.value = priorMode;
    else if (fixtureAllowed) modeSelect.value = "fixture";
    else modeSelect.value = "gemini_video";

    const profiles = Array.isArray(capabilities?.fixture_profiles)
      ? capabilities.fixture_profiles.filter(
          (value) => typeof value === "string" && Object.hasOwn(FIXTURE_PROFILE_LABELS, value),
        )
      : [];
    profileSelect.replaceChildren();
    for (const profile of profiles) {
      const option = element("option", "", FIXTURE_PROFILE_LABELS[profile]);
      option.value = profile;
      profileSelect.append(option);
    }
    if (profiles.includes("pending")) profileSelect.value = "pending";
    updateSourceControls();
    updateEventControls();
  }

  function updateEventControls() {
    const canInject = sourceOpen && warmedUp && runActive && !locked && !destroyed;
    eventSelect.disabled = !canInject;
    injectButton.disabled = !canInject;
    cancelRunButton.disabled = !runActive || destroyed;
    if (locked) {
      eventHint.textContent = "Während einer Prüfung sind neue Szenarioereignisse gesperrt. Stoppen und Zurücksetzen bleiben verfügbar.";
    } else if (!roadAvailable) {
      eventHint.textContent = "Die Straßenkamera ist nicht verfügbar. Prüfe die FFmpeg-Voraussetzung.";
    } else if (!sourceOpen) {
      eventHint.textContent = "Starte zuerst eine Quelle.";
    } else if (!warmedUp) {
      eventHint.textContent = "Ereignisse werden erst freigegeben, wenn drei echte Startframes analysiert wurden.";
    } else if (!runActive) {
      eventHint.textContent = "Der Analyse-Lauf ist beendet. Starte eine neue Sitzung für eine weitere Prüfung.";
    } else {
      eventHint.textContent = "Das Ereignis verändert nur die Szene. Ein positiver Befund entsteht ausschließlich aus analysierten Frames.";
    }
    updateSourceControls();
  }

  function loadRoadCapabilities() {
    apiClient.get("/road/capabilities")
      .then((response) => {
        if (destroyed) return;
        capabilities = response && typeof response === "object" ? response : null;
        roadAvailable = Boolean(capabilities && capabilities.available === true);
        configureCaseOptions();
        updateEventControls();
        if (!roadAvailable) {
          const message = capabilities && typeof capabilities.message === "string"
            ? capabilities.message
            : "Für die Straßenkamera-Aufzeichnung muss FFmpeg mit libx264 verfügbar sein.";
          setStatus(message, true);
        } else if (!sourceId) {
          setStatus("Lokale Straßenkamera und H.264-Aufzeichnung sind bereit.");
        }
      })
      .catch(() => {
        if (destroyed) return;
        roadAvailable = false;
        capabilities = null;
        configureCaseOptions();
        updateEventControls();
        setStatus("Die Kamerafunktion konnte nicht geprüft werden. Prüfe den lokalen FFmpeg-Server.", true);
      });
  }
  function renderRun(result) {
    if (!result || typeof result !== "object") return;
    const statusValue = typeof result.status === "string" ? result.status : "unknown";
    const trace = Array.isArray(result.trace) ? result.trace : [];
    runActive = ACTIVE_RUN_STATES.has(statusValue);
    const initialCount = trace.filter((item) =>
      item && (
        item.type === "frame_analyzed" ||
        item.type === "frame_observed" ||
        item.type === "observation_frame"
      ),
    ).length;
    warmedUp = initialCount >= 3 && statusValue !== "failed" && statusValue !== "cancelled";
    runSummary.textContent =
      (RUN_LABELS[statusValue] || statusValue) + " · Lauf " +
      String(result.run_id || runId || "–") +
      (warmedUp
        ? " · Startanalyse mit drei Frames abgeschlossen"
        : " · Startframes " + Math.min(initialCount, 3) + "/3");
    runSummary.classList.toggle("road-camera__run-summary--error", statusValue === "failed");
    findings.replaceChildren();
    const allFindings = Array.isArray(result.findings) ? result.findings : [];
    const displayFindings = allFindings.slice(-12);
    if (displayFindings.length === 0) {
      findings.append(element("p", "road-camera__empty", "Noch keine Analysebefunde."));
    } else {
      for (const finding of displayFindings) {
        const ids = Array.isArray(finding.frame_ids) ? finding.frame_ids.map(String).join(", ") : "";
        const metrics = finding.metrics && typeof finding.metrics === "object"
          ? Object.entries(finding.metrics)
            .map(([key, value]) => key + ": " + JSON.stringify(value))
            .join(" · ")
          : "keine Messwerte";
        findings.append(element(
          "p",
          "road-camera__finding",
          String(finding.tool || "Befund") + " · Frames: " + (ids || "keine") + "\n" + metrics,
        ));
      }
    }
    traceContent.textContent = JSON.stringify(trace, null, 2);
    updateEventControls();

    if (autoDemoActive && warmedUp && sourceId && sourceOpen && runActive && !locked) {
      autoDemoActive = false;
      setStatus("⚡ 1-Klick Demo: Startanalyse (3 Frames) beendet. Falschfahrer wird eingespeist …");
      eventSelect.value = "wrong_way";
      injectEvent().then(() => {
        setStatus("⚡ 1-Klick Demo: Falschfahrer aktiv! Beobachte Erkennung und Leitwarten-Eingang.");
      }).catch((err) => {
        setStatus("Fehler bei automatischer Ereigniseinblendung: " + err.message, true);
      });
    }
  }

  function pollRun() {
    const source = sourceId;
    const generation = sourceGeneration;
    const view = viewGeneration;
    const currentRun = runId;
    if (!source || !currentRun || destroyed) return;
    apiClient.get("/runs/" + encodeURIComponent(currentRun))
      .then((result) => {
        if (!current(source, generation, view) || runId !== currentRun) return;
        renderRun(result);
        if (runActive) runTimer = window.setTimeout(pollRun, 500);
      })
      .catch((error) => {
        if (!current(source, generation, view) || runId !== currentRun) return;
        runSummary.textContent = error.message || "Der Controllerstatus konnte nicht geladen werden.";
        runSummary.classList.add("road-camera__run-summary--error");
        if (runActive) runTimer = window.setTimeout(pollRun, 1000);
      });
  }

  async function startCase(source, generation) {
    clearRunTimer();
    runId = null;
    caseId = null;
    caseSnapshot = null;
    root.dataset.caseId = "";
    runActive = false;
    warmedUp = false;
    runSummary.textContent = "Starte den Fall und die tatsächliche Frameanalyse …";
    findings.replaceChildren(element("p", "road-camera__empty", "Noch keine Analysebefunde."));
    traceContent.textContent = "Noch kein Trace vorhanden.";
    updateEventControls();

    const mode = modeSelect.value || "fixture";
    const request = { source_id: source, mode };
    if (mode === "fixture") request.fixture_profile = profileSelect.value || "pending";
    const response = await apiClient.post("/road/cases", request);
    const result = response && response.case && typeof response.case === "object"
      ? response.case
      : response;
    if (!current(source, generation) || !sourceOpen) {
      if (result && typeof result.case_id === "string") {
        apiClient.post("/road/cases/" + encodeURIComponent(result.case_id) + "/cancel", {}).catch(() => {});
      }
      return;
    }
    if (!result || typeof result.case_id !== "string" || !result.case_id ||
        typeof result.run_id !== "string" || !result.run_id) {
      throw new Error("Der Server lieferte keinen gültigen Fall- und Laufbezug.");
    }
    caseId = result.case_id;
    caseSnapshot = result;
    root.dataset.caseId = caseId;
    runId = result.run_id;
    root.dispatchEvent(new CustomEvent("road-case-started", {
      bubbles: true,
      detail: { caseId, sourceId: source, runId, case: result },
    }));
    renderRun({
      run_id: runId,
      status: result.controller_status || "queued",
      findings: [],
      trace: Array.isArray(result.perception_trace) ? result.perception_trace : [],
    });
    pollRun();
  }

  async function cancelAnalysis() {
    const currentRun = runId;
    const source = sourceId;
    const generation = sourceGeneration;
    if (!currentRun || !runActive) return;
    return enqueue("cancel-run", async () => {
      if (destroyed || source !== sourceId || generation !== sourceGeneration || currentRun !== runId) return;
      const result = caseId
        ? await apiClient.post("/road/cases/" + encodeURIComponent(caseId) + "/cancel", {})
        : await apiClient.post("/runs/" + encodeURIComponent(currentRun) + "/cancel", {});
      if (!current(source, generation) || currentRun !== runId) return;
      if (result && typeof result === "object" && result.case_id) {
        caseSnapshot = result;
        root.dispatchEvent(new CustomEvent("road-case-changed", {
          bubbles: true,
          detail: { caseId: result.case_id, sourceId: source, case: result },
        }));
      }
      renderRun(await apiClient.get("/runs/" + encodeURIComponent(currentRun)));
      setStatus("Analyse abgebrochen; die Kameraquelle zeichnet weiter auf.");
    });
  }

  function applySource(nextSource, message) {
    sourceGeneration += 1;
    viewGeneration += 1;
    clearRunTimer();
    runId = null;
    runActive = false;
    warmedUp = false;
    sourceId = nextSource;
    sourceOpen = true;
    root.dataset.sourceId = nextSource;
    currentClipId = null;
    sourceSummary.textContent = "Quelle: " + nextSource;
    clearFrameImage();
    frameMeta.textContent = "Warte auf das erste Kamerabild.";
    recordingSummary.textContent = "Warte auf Aufnahmedaten.";
    setStatus(message);
    updateEventControls();
    startPolling();
    startCase(nextSource, sourceGeneration)
      .catch((error) => {
        if (sourceId === nextSource && sourceOpen) {
          runSummary.textContent = error.message || "Der Kameraablauf konnte nicht gestartet werden.";
          runSummary.classList.add("road-camera__run-summary--error");
          updateEventControls();
          setStatus("Die Quelle läuft, aber der Analyse-Lauf ist fehlgeschlagen.", true);
        }
      });
  }

  async function createSource() {
    return enqueue("create", async () => {
      if (sourceId && sourceOpen) {
        const previous = sourceId;
        const stopped = await apiClient.post(
          `/road/sessions/${encodeURIComponent(previous)}/stop`,
          {},
        );
        if (stopped.clip) clips.set(stopped.clip.clip_id, stopped.clip);
        const oldGeneration = sourceGeneration;
        await refreshRecording(previous, oldGeneration, viewGeneration);
      }
      const created = await apiClient.post("/road/sessions", { seed: 42 });
      if (destroyed || typeof created.source_id !== "string") return;
      applySource(created.source_id, "Neue synthetische Quelle gestartet.");
    });
  }

  async function injectEvent() {
    if (!sourceId || !sourceOpen || !warmedUp || !runActive || locked) return;
    const source = sourceId;
    const generation = sourceGeneration;
    const event = eventSelect.value;
    return enqueue("event", async () => {
      if (destroyed || source !== sourceId || generation !== sourceGeneration ||
          !sourceOpen || locked) return;
      await apiClient.post(
        `/road/sessions/${encodeURIComponent(source)}/events`,
        { event },
      );
      if (current(source, generation)) {
        const label = { wrong_way: "Falschfahrer", bird: "Vogel", leaf: "Blatt" }[event] || "Szenarioereignis";
        setStatus(`${label} in die Szene eingeblendet. Das ist noch kein Analysebefund.`);
      }
    });
  }

  async function stopSource() {
    if (!sourceId) return;
    const source = sourceId;
    const generation = sourceGeneration;
    return enqueue("stop", async () => {
      if (destroyed || source !== sourceId || generation !== sourceGeneration) return;
      const wasOpen = sourceOpen;
      const stopped = await apiClient.post(
        `/road/sessions/${encodeURIComponent(source)}/stop`,
        {},
      );
      if (!current(source, generation)) return;
      sourceOpen = false;
      clearFrameTimer();
      clearRunTimer();
      updateEventControls();
      if (runId) {
        try {
          renderRun(await apiClient.get("/runs/" + encodeURIComponent(runId)));
        } catch (_error) {
          runSummary.textContent = "Die Quelle ist beendet; der letzte Laufstatus ist nicht verfügbar.";
        }
      }
      if (stopped.clip) clips.set(stopped.clip.clip_id, stopped.clip);
      setStatus(wasOpen
        ? "Quelle beendet; die vollständige Aufnahme wird angezeigt."
        : "Die Quelle war bereits beendet; ihre gespeicherte Aufnahme ist nun fertiggestellt.");
      await refreshRecording(source, generation, viewGeneration);
      renderClipList();
    });
  }

  async function resetSource() {
    if (!sourceId) return;
    const previous = sourceId;
    const generation = sourceGeneration;
    return enqueue("reset", async () => {
      if (destroyed || previous !== sourceId || generation !== sourceGeneration) return;
      clearRunTimer();
      clearFrameTimer();
      clearRecordingTimer();
      viewGeneration += 1;
      runActive = false;
      warmedUp = false;
      autoDemoActive = false;
      playerEmpty.hidden = false;
      player.hidden = true;
      player.removeAttribute("src");
      currentClipId = null;
      recText.textContent = " STANDBY";
      recDot.classList.add("road-camera__rec-dot--standby");
      updateEventControls();
      const reset = await apiClient.post(
        `/road/sessions/${encodeURIComponent(previous)}/reset`,
        {},
      );
      if (destroyed || previous !== sourceId || generation !== sourceGeneration) return;
      await refreshRecording(previous, generation, viewGeneration);
      if (typeof reset.source_id !== "string") {
        throw new Error("Der Server lieferte keine neue Quellenkennung.");
      }
      applySource(reset.source_id, "Neue Quelle gestartet; frühere Clips bleiben erhalten.");
    });
  }

  async function fetchClipBytes(clipId) {
    const existing = clipInflight.get(clipId);
    if (existing) return existing;
    const request = apiClient.getClip(
      `/road/clips/${encodeURIComponent(clipId)}`,
    );
    clipInflight.set(clipId, request);
    try {
      return await request;
    } finally {
      if (clipInflight.get(clipId) === request) clipInflight.delete(clipId);
    }
  }

  async function loadClip(clip) {
    if (destroyed) return;
    const selection = ++clipSelectionGeneration;
    const sourceGenerationAtStart = sourceGeneration;
    const viewAtStart = viewGeneration;
    const known = clipUrls.get(clip.clip_id);
    if (known && known.sha256 === clip.sha256) {
      if (currentClipId === clip.clip_id) return;
      await attachClip(clip, known.url, selection, sourceGenerationAtStart, viewAtStart);
      return;
    }

    const result = await fetchClipBytes(clip.clip_id);
    if (destroyed || selection !== clipSelectionGeneration ||
        sourceGenerationAtStart !== sourceGeneration || viewAtStart !== viewGeneration) return;
    if (!result || !(result.blob instanceof Blob)) {
      throw new Error("Der Server lieferte keinen abspielbaren Clip.");
    }
    if (result.sha256 && result.sha256 !== clip.sha256) {
      throw new Error("Die Clip-Prüfsumme passt nicht zu den gespeicherten Metadaten.");
    }
    const url = URL.createObjectURL(result.blob);
    clipUrls.set(clip.clip_id, { url, sha256: clip.sha256 });
    await attachClip(clip, url, selection, sourceGenerationAtStart, viewAtStart);
  }

  async function attachClip(clip, url, selection, sourceGenerationAtStart, viewAtStart) {
    if (destroyed || selection !== clipSelectionGeneration ||
        sourceGenerationAtStart !== sourceGeneration || viewAtStart !== viewGeneration) return;
    const previousId = currentClipId;
    if (previousId === clip.clip_id && player.src === url) return;
    currentClipId = clip.clip_id;
    playerEmpty.hidden = true;
    player.hidden = false;
    player.dataset.clipId = clip.clip_id;
    player.dataset.clipHash = clip.sha256 || "";
    for (const item of clipList.querySelectorAll("[data-clip-id]")) {
      item.setAttribute("aria-pressed", String(item.dataset.clipId === currentClipId));
    }
    playerCaption.textContent =
      `Quelle ${clip.source_id} · Version ${formatNumber(clip.version, 0)} · ${formatNumber(clip.start_seconds)}–${formatNumber(clip.end_seconds)} Sek.`;
    if (player.src !== url) {
      await new Promise((resolve, reject) => {
        const loaded = () => {
          cleanup();
          resolve();
        };
        const failed = () => {
          cleanup();
          reject(new Error("Der gespeicherte Clip konnte nicht geöffnet werden."));
        };
        const cleanup = () => {
          player.removeEventListener("loadedmetadata", loaded);
          player.removeEventListener("error", failed);
        };
        player.addEventListener("loadedmetadata", loaded, { once: true });
        player.addEventListener("error", failed, { once: true });
        player.src = url;
        player.load();
      });
      if (destroyed || selection !== clipSelectionGeneration ||
          sourceGenerationAtStart !== sourceGeneration || viewAtStart !== viewGeneration) return;
      try {
        player.currentTime = 0;
      } catch (_error) {
        // Some decoders need one animation frame after loadedmetadata.
      }
    }
    if (previousId && previousId !== clip.clip_id) {
      const old = clipUrls.get(previousId);
      if (old) {
        URL.revokeObjectURL(old.url);
        clipUrls.delete(previousId);
      }
    }
  }

  quickDemoButton.addEventListener("click", () => {
    autoDemoActive = true;
    if (modeSelect) modeSelect.value = "fixture";
    if (profileSelect) profileSelect.value = "pending";
    updateSourceControls();
    createSource().catch((error) => {
      autoDemoActive = false;
      setStatus(error.message, true);
    });
  });
  cancelRunButton.addEventListener("click", () => {
    autoDemoActive = false;
    cancelAnalysis().catch((error) => setStatus(error.message, true));
  });
  createButton.addEventListener("click", () => {
    createSource().catch((error) => setStatus(error.message, true));
  });
  stopButton.addEventListener("click", () => {
    stopSource().catch((error) => setStatus(error.message, true));
  });
  resetButton.addEventListener("click", () => {
    resetSource().catch((error) => setStatus(error.message, true));
  });
  injectButton.addEventListener("click", () => {
    injectEvent().catch((error) => setStatus(error.message, true));
  });
  modeSelect.addEventListener("change", updateSourceControls);

  function setLocked(value) {
    locked = Boolean(value);
    updateEventControls();
  }

  function setCase(caseInfo) {
    const nextId = caseInfo && typeof caseInfo.case_id === "string"
      ? caseInfo.case_id
      : caseInfo && typeof caseInfo.caseId === "string"
        ? caseInfo.caseId
        : null;
    const changed = caseId !== nextId;
    caseId = nextId;
    caseSnapshot = caseInfo || null;
    root.dataset.caseId = nextId || "";
    if (caseInfo && typeof caseInfo.source_id === "string") {
      root.dataset.sourceId = caseInfo.source_id;
    }
    if (caseInfo && typeof caseInfo.run_id === "string") runId = caseInfo.run_id;
    if (changed && sourceId && sourceOpen) {
      viewGeneration += 1;
      startPolling();
      if (runId && runActive) {
        clearRunTimer();
        pollRun();
      }
    }
  }

  function getSourceId() {
    return sourceId;
  }

  function getCaseId() {
    return caseId;
  }

  function destroy() {
    if (destroyed) return;
    destroyed = true;
    autoDemoActive = false;
    sourceGeneration += 1;
    viewGeneration += 1;
    clipSelectionGeneration += 1;
    clearFrameTimer();
    clearRecordingTimer();
    clearRunTimer();
    clearFrameImage();
    for (const entry of clipUrls.values()) URL.revokeObjectURL(entry.url);
    clipUrls.clear();
    clipInflight.clear();
    container.replaceChildren();
  }

  setLocked(false);
  loadRoadCapabilities();
  return { destroy, getCaseId, getSourceId, setLocked, setCase };
}
