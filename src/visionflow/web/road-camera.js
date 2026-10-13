"use strict";

const L = (de, en) => (window.VisionFlowI18n ? window.VisionFlowI18n.L(de, en) : en);

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
  <text x="320" y="195" fill="#f8fafc" font-family="system-ui, -apple-system, sans-serif" font-size="15" font-weight="700" text-anchor="middle">${L("Kamera im Bereitschaftsmodus", "Camera in standby mode")}</text>
  <text x="320" y="222" fill="#94a3b8" font-family="system-ui, -apple-system, sans-serif" font-size="12" text-anchor="middle">${L("Kamera einschalten, um die Straße zu beobachten", "Turn on the camera to watch the road")}</text>
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
  queued: L("Wartet", "Waiting"),
  running: L("Läuft", "Running"),
  observing: L("Beobachtet", "Observing"),
  waiting_for_input: L("Eingabe erforderlich", "Input required"),
  waiting_for_approval: L("Freigabe erforderlich", "Approval required"),
  completed: L("Abgeschlossen", "Completed"),
  failed: L("Fehlgeschlagen", "Failed"),
  cancelled: L("Abgebrochen", "Cancelled"),
};
const FIXTURE_PROFILE_LABELS = {
  pending: L("Offline-Testantwort · menschliche Prüfung (Sicherheit 0,80)", "Offline test answer · human review (confidence 0.80)"),
  high: L("Offline-Testantwort · hohe Sicherheit (0,99)", "Offline test answer · high confidence (0.99)"),
  bird: L("Offline-Testantwort · Vogel, keine Gefahr (0,99)", "Offline test answer · bird, no hazard (0.99)"),
  leaf: L("Offline-Testantwort · Blatt, keine Gefahr (0,99)", "Offline test answer · leaf, no hazard (0.99)"),
  followup: L("Offline-Testantwort · Folgebeobachtung", "Offline test answer · follow-up observation"),
  unclear: L("Offline-Testantwort · unklar", "Offline test answer · unclear"),
  error: L("Offline-Testantwort · Modellfehler", "Offline test answer · model error"),
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
    ? value.toLocaleString(L("de-DE", "en-US"), { maximumFractionDigits: digits })
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
  const title = element("h2", "", L("Straßenkamera-Demo", "Road camera demo"));
  title.id = `road-camera-title-${instanceId}`;
  heading.append(title);
  heading.append(element(
    "p",
    "road-camera__intro",
    L("Eine lokale synthetische Verkehrsszene liefert echte Bildpixel für die freigegebenen OpenCV-Werkzeuge.", "A local synthetic traffic scene provides real image pixels for the approved OpenCV tools."),
  ));

  const caseOptions = element("div", "road-camera__case-options");
  const modeLabel = element("label", "", L("Modellmodus", "Model mode"));
  modeLabel.htmlFor = "road-model-mode";
  const modeSelect = element("select", "road-camera__mode-select");
  modeSelect.id = "road-model-mode";
  const profileLabel = element("label", "", L("Offline-Testantwort", "Offline test answer"));
  profileLabel.htmlFor = "road-fixture-profile";
  const profileSelect = element("select", "road-camera__profile-select");
  profileSelect.id = "road-fixture-profile";
  caseOptions.append(modeLabel, modeSelect, profileLabel, profileSelect);

  const toolbar = element("div", "road-camera__toolbar");
  const quickDemoButton = button(L("⚡ 1-Klick Demo (Falschfahrer)", "⚡ 1-click demo (wrong-way driver)"), "button road-quick-demo-btn");
  quickDemoButton.id = "road-camera-quick-demo";
  quickDemoButton.title = L("Startet automatisch Quelle & Analyse und blendet nach 3 Startframes den Falschfahrer ein.", "Automatically starts the source and analysis and injects the wrong-way driver after 3 start frames.");
  const createButton = button(L("Neue Sitzung starten", "Start new session"), "button primary");
  createButton.id = "road-camera-create";
  createButton.setAttribute("aria-label", L("Neue Straßenkamera-Sitzung starten", "Start new road camera session"));
  createButton.disabled = true;
  const stopButton = button(L("Aufnahme beenden", "End recording"), "button danger");
  stopButton.id = "road-camera-stop";
  const resetButton = button(L("Zurücksetzen", "Reset"), "button secondary");
  resetButton.id = "road-camera-reset";
  stopButton.disabled = true;
  resetButton.disabled = true;
  toolbar.append(quickDemoButton, createButton, stopButton, resetButton);

  const status = element("p", "road-camera__status", L("Noch keine Sitzung gestartet.", "No session started yet."));
  status.setAttribute("role", "status");
  status.setAttribute("aria-live", "polite");

  const sourceSummary = element("p", "road-camera__source", L("Quelle: –", "Source: –"));

  const runPanel = element("section", "road-camera__run");
  runPanel.append(element("h3", "", L("Analyse und Controller-Trace", "Analysis and controller trace")));
  const runToolbar = element("div", "road-camera__run-toolbar");
  const runSummary = element("p", "road-camera__run-summary", L("Noch kein Analyse-Lauf gestartet.", "No analysis run started yet."));
  runSummary.setAttribute("role", "status");
  runSummary.setAttribute("aria-live", "polite");
  const cancelRunButton = button(L("Analyse abbrechen", "Cancel analysis"), "button danger");
  cancelRunButton.id = "road-camera-cancel";
  cancelRunButton.disabled = true;
  runToolbar.append(runSummary, cancelRunButton);
  const findings = element("div", "road-camera__findings");
  findings.append(element("p", "road-camera__empty", L("Noch keine Analysebefunde.", "No analysis findings yet.")));
  const traceDetails = element("details");
  traceDetails.append(element("summary", "", L("Gespeicherte Controller-Ereignisse", "Stored controller events")));
  const traceContent = element("pre", "road-camera__trace", L("Noch kein Trace vorhanden.", "No trace available yet."));
  traceDetails.append(traceContent);
  runPanel.append(runToolbar, findings, traceDetails);

  const content = element("div", "road-camera__content");

  const livePanel = element("section", "road-camera__live");
  const liveTitle = element("h3", "", L("Livebild", "Live image"));
  const viewport = element("div", "road-camera__viewport");
  const hud = element("div", "road-camera__hud");
  const recIndicator = element("span", "road-camera__rec-indicator");
  const recDot = element("span", "road-camera__rec-dot road-camera__rec-dot--standby");
  const recText = element("span", "", " STANDBY");
  recIndicator.append(recDot, recText);
  const camInfo = element("span", "road-camera__cam-info", L("CAM-01 · B4 NORD", "CAM-01 · B4 NORTH"));
  hud.append(recIndicator, camInfo);
  const frame = element("img", "road-camera__frame");
  frame.alt = L("Aktuelles Bild der synthetischen Straßenkamera", "Current image of the synthetic road camera");
  frame.decoding = "async";
  frame.src = getStandbyUrl();
  viewport.append(frame, hud);
  const frameMeta = element("p", "road-camera__frame-meta", L("Bereit. Starte eine Sitzung oder nutze die ⚡ 1-Klick Demo.", "Ready. Start a session or use the ⚡ 1-click demo."));
  livePanel.append(liveTitle, viewport, frameMeta);

  const eventPanel = element("section", "road-camera__events");
  eventPanel.append(element("h3", "", L("Szenarioereignis", "Scenario event")));
  const eventLabel = element("label", "", L("Ereignis für die Szene", "Event for the scene"));
  eventLabel.htmlFor = "road-camera-event";
  const eventSelect = element("select", "road-camera__event-select");
  eventSelect.id = "road-camera-event";
  for (const [value, label] of [
    ["wrong_way", L("Falschfahrer", "Wrong-way driver")],
    ["bird", L("Vogel", "Bird")],
    ["leaf", L("Blatt", "Leaf")],
  ]) {
    const option = element("option", "", label);
    option.value = value;
    eventSelect.append(option);
  }
  const injectButton = button(L("Ereignis einblenden", "Inject event"), "button secondary");
  injectButton.id = "road-camera-inject";
  const eventHint = element(
    "p",
    "road-camera__hint",
    L("Das Ereignis verändert nur die Szene. Ein positiver Analysebefund wird ausschließlich aus tatsächlich analysierten Frames übernommen.", "The event only changes the scene. A positive analysis finding is taken solely from frames that were actually analysed."),
  );
  eventPanel.append(eventLabel, eventSelect, injectButton, eventHint);

  const recordingPanel = element("section", "road-camera__recording");
  recordingPanel.append(element("h3", "", L("Aufzeichnung", "Recording")));
  const recordingSummary = element("p", "road-camera__recording-summary", L("Warte auf Aufnahmedaten.", "Waiting for recording data."));
  recordingSummary.setAttribute("aria-live", "off");
  const clipList = element("div", "road-camera__clip-list");
  clipList.setAttribute("aria-label", L("Gespeicherte Clips", "Stored clips"));
  recordingPanel.append(recordingSummary, clipList);

  const playerPanel = element("section", "road-camera__player-panel");
  playerPanel.append(element("h3", "", L("Gespeicherter Clip", "Stored clip")));
  const playerEmpty = element("div", "road-camera__player-empty", L("Noch kein Clip geladen. Wähle nach Beenden der Aufnahme einen Clip aus der Liste.", "No clip loaded yet. After ending the recording, choose a clip from the list."));
  const player = element("video", "road-camera__player");
  player.id = "road-camera-player";
  player.controls = true;
  player.playsInline = true;
  player.preload = "metadata";
  player.setAttribute("aria-label", L("Gespeicherte Straßenkamera-Aufzeichnung", "Stored road camera recording"));
  player.hidden = true;
  playerPanel.append(playerEmpty, player);
  const playerCaption = element("p", "road-camera__player-caption", L("Wähle einen gespeicherten Clip aus.", "Choose a stored clip."));
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
      clipList.append(element("p", "road-camera__empty", L("Noch kein gespeicherter Clip.", "No stored clip yet.")));
      return;
    }
    for (const clip of ordered) {
      const duration = Math.max(0, Number(clip.end_seconds) - Number(clip.start_seconds));
      const sourceShort = String(clip.source_id || "").slice(-8);
      const item = button(
        L(`Quelle …${sourceShort} · Version ${formatNumber(clip.version, 0)} · ${formatNumber(duration)} Sek.`, `Source …${sourceShort} · version ${formatNumber(clip.version, 0)} · ${formatNumber(duration)} s`),
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
        const state = payload.closed ? L("Quelle beendet", "Source ended") : L("Aufzeichnung läuft", "Recording in progress");
        recordingSummary.textContent =
          L(`${state} · ${frames} Frames · ${fps} Bilder/Sek. gemessen · ${seconds} Sek. · ${compressed} Byte`, `${state} · ${frames} frames · ${fps} frames/s measured · ${seconds} s · ${compressed} bytes`);
        if (payload.closed) {
          sourceOpen = false;
          clearFrameTimer();
          updateEventControls();
          setStatus(payload.error_code
            ? L("Quelle mit einem technischen Fehler beendet.", "Source ended with a technical error.")
            : L("Quelle beendet; das letzte echte Kamerabild bleibt sichtbar.", "Source ended; the last real camera image stays visible."));
        }
      }
    } catch (error) {
      if (source === sourceId && sourceGeneration === generation && viewGeneration === view) {
        setStatus(error.message || L("Aufnahmedaten konnten nicht geladen werden.", "Recording data could not be loaded."), true);
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
          L(`Frame ${String(payload.frame_id || "–")} · Quellenzeit ${formatNumber(payload.timestamp_seconds, 2)} Sek.`, `Frame ${String(payload.frame_id || "–")} · source time ${formatNumber(payload.timestamp_seconds, 2)} s`);
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
          ? L("Quelle mit einem technischen Fehler beendet.", "Source ended with a technical error.")
          : L("Quelle beendet; das letzte echte Kamerabild bleibt sichtbar.", "Source ended; the last real camera image stays visible."));
        refreshRecording(source, generation, view);
        return;
      }
    } catch (error) {
      if (current(source, generation, view)) {
        setStatus(error.message || L("Das aktuelle Kamerabild konnte nicht geladen werden.", "The current camera image could not be loaded."), true);
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
    quickDemoButton.disabled = !roadAvailable || destroyed;
    createButton.disabled = !roadAvailable || destroyed;
    stopButton.disabled = !sourceId || destroyed;
    stopButton.textContent = hasOpenSource ? L("Aufnahme beenden", "End recording") : L("Aufnahme speichern", "Save recording");
    resetButton.disabled = !sourceId || destroyed;
    modeSelect.disabled = !roadAvailable || hasOpenSource || destroyed;
    profileSelect.disabled =
      modeSelect.value !== "fixture" || hasOpenSource || destroyed;
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
      const fixture = element("option", "", L("Offline-Testantwort", "Offline test answer"));
      fixture.value = "fixture";
      modeSelect.append(fixture);
    }
    const native = element(
      "option",
      "",
      nativeAllowed ? "Gemini Video" : L("Gemini Video (nicht freigeschaltet)", "Gemini Video (not enabled)"),
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
    const canInject = sourceOpen && warmedUp && runActive && !destroyed;
    eventSelect.disabled = !canInject;
    injectButton.disabled = !canInject;
    cancelRunButton.disabled = !runActive || destroyed;
    if (!roadAvailable) {
      eventHint.textContent = L("Die Straßenkamera ist nicht verfügbar. Prüfe die FFmpeg-Voraussetzung.", "The road camera is not available. Check the FFmpeg requirement.");
    } else if (!sourceOpen) {
      eventHint.textContent = L("Starte zuerst eine Quelle.", "Start a source first.");
    } else if (!warmedUp) {
      eventHint.textContent = L("Ereignisse werden erst freigegeben, wenn drei echte Startframes analysiert wurden.", "Events are only enabled once three real start frames have been analysed.");
    } else if (!runActive) {
      eventHint.textContent = L("Der Analyse-Lauf ist beendet. Starte eine neue Sitzung für eine weitere Prüfung.", "The analysis run has ended. Start a new session for another check.");
    } else {
      eventHint.textContent = L("Das Ereignis verändert nur die Szene. Ein positiver Befund entsteht ausschließlich aus analysierten Frames.", "The event only changes the scene. A positive finding arises solely from analysed frames.");
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
            : L("Für die Straßenkamera-Aufzeichnung muss FFmpeg mit libx264 verfügbar sein.", "Road camera recording requires FFmpeg with libx264.");
          setStatus(message, true);
        } else if (!sourceId) {
          setStatus(L("Lokale Straßenkamera und H.264-Aufzeichnung sind bereit.", "Local road camera and H.264 recording are ready."));
        }
      })
      .catch(() => {
        if (destroyed) return;
        roadAvailable = false;
        capabilities = null;
        configureCaseOptions();
        updateEventControls();
        setStatus(L("Die Kamerafunktion konnte nicht geprüft werden. Prüfe den lokalen FFmpeg-Server.", "The camera function could not be checked. Check the local FFmpeg server."), true);
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
      (RUN_LABELS[statusValue] || statusValue) + L(" · Lauf ", " · Run ") +
      String(result.run_id || runId || "–") +
      (warmedUp
        ? L(" · Startanalyse mit drei Frames abgeschlossen", " · Start analysis with three frames completed")
        : L(" · Startframes ", " · Start frames ") + Math.min(initialCount, 3) + "/3");
    runSummary.classList.toggle("road-camera__run-summary--error", statusValue === "failed");
    findings.replaceChildren();
    const allFindings = Array.isArray(result.findings) ? result.findings : [];
    const displayFindings = allFindings.slice(-12);
    if (displayFindings.length === 0) {
      findings.append(element("p", "road-camera__empty", L("Noch keine Analysebefunde.", "No analysis findings yet.")));
    } else {
      for (const finding of displayFindings) {
        const ids = Array.isArray(finding.frame_ids) ? finding.frame_ids.map(String).join(", ") : "";
        const metrics = finding.metrics && typeof finding.metrics === "object"
          ? Object.entries(finding.metrics)
            .map(([key, value]) => key + ": " + JSON.stringify(value))
            .join(" · ")
          : L("keine Messwerte", "no measurements");
        findings.append(element(
          "p",
          "road-camera__finding",
          String(finding.tool || L("Befund", "Finding")) + " · Frames: " + (ids || L("keine", "none")) + "\n" + metrics,
        ));
      }
    }
    traceContent.textContent = JSON.stringify(trace, null, 2);
    updateEventControls();

    if (autoDemoActive && warmedUp && sourceId && sourceOpen && runActive) {
      autoDemoActive = false;
      setStatus(L("⚡ 1-Klick Demo: Startanalyse (3 Frames) beendet. Falschfahrer wird eingespeist …", "⚡ 1-click demo: start analysis (3 frames) finished. Injecting wrong-way driver …"));
      eventSelect.value = "wrong_way";
      injectEvent().then(() => {
        setStatus(L("⚡ 1-Klick Demo: Falschfahrer aktiv! Beobachte Erkennung und Leitwarten-Eingang.", "⚡ 1-click demo: wrong-way driver active! Watch the detection and the dispatch inbox."));
      }).catch((err) => {
        setStatus(L("Fehler bei automatischer Ereigniseinblendung: ", "Error during automatic event injection: ") + err.message, true);
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
        runSummary.textContent = error.message || L("Der Controllerstatus konnte nicht geladen werden.", "The controller status could not be loaded.");
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
    runSummary.textContent = L("Starte den Fall und die tatsächliche Frameanalyse …", "Starting the case and the actual frame analysis …");
    findings.replaceChildren(element("p", "road-camera__empty", L("Noch keine Analysebefunde.", "No analysis findings yet.")));
    traceContent.textContent = L("Noch kein Trace vorhanden.", "No trace available yet.");
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
      throw new Error(L("Der Server lieferte keinen gültigen Fall- und Laufbezug.", "The server returned no valid case and run reference."));
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
      setStatus(L("Analyse abgebrochen; die Kameraquelle zeichnet weiter auf.", "Analysis cancelled; the camera source keeps recording."));
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
    sourceSummary.textContent = L("Quelle: ", "Source: ") + nextSource;
    clearFrameImage();
    frameMeta.textContent = L("Warte auf das erste Kamerabild.", "Waiting for the first camera image.");
    recordingSummary.textContent = L("Warte auf Aufnahmedaten.", "Waiting for recording data.");
    setStatus(message);
    updateEventControls();
    startPolling();
    startCase(nextSource, sourceGeneration)
      .catch((error) => {
        if (sourceId === nextSource && sourceOpen) {
          runSummary.textContent = error.message || L("Der Kameraablauf konnte nicht gestartet werden.", "The camera workflow could not be started.");
          runSummary.classList.add("road-camera__run-summary--error");
          updateEventControls();
          setStatus(L("Die Quelle läuft, aber der Analyse-Lauf ist fehlgeschlagen.", "The source is running, but the analysis run failed."), true);
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
      applySource(created.source_id, L("Neue synthetische Quelle gestartet.", "New synthetic source started."));
    });
  }

  function injectBlocker() {
    if (destroyed) return "destroyed";
    if (!sourceId || !sourceOpen) return "source_closed";
    if (!warmedUp) return "warming_up";
    if (!runActive) return "run_inactive";
    return null;
  }

  // Resolves to { injected, reason }. Events are only accepted while the
  // bound analysis run observes the scene, so every injected event can still
  // become part of the analysed footage.
  async function injectEvent(forcedEvent) {
    const event = forcedEvent || eventSelect.value;
    const blocked = injectBlocker();
    if (blocked) return { injected: false, reason: blocked };
    const source = sourceId;
    const generation = sourceGeneration;
    return enqueue("event", async () => {
      if (source !== sourceId || generation !== sourceGeneration) {
        return { injected: false, reason: "source_changed" };
      }
      const blockedNow = injectBlocker();
      if (blockedNow) return { injected: false, reason: blockedNow };
      await apiClient.post(
        `/road/sessions/${encodeURIComponent(source)}/events`,
        { event },
      );
      if (current(source, generation)) {
        const label = { wrong_way: L("Falschfahrer", "Wrong-way driver"), bird: L("Vogel", "Bird"), leaf: L("Blatt", "Leaf") }[event] || L("Szenarioereignis", "Scenario event");
        setStatus(L(`${label} in die Szene eingeblendet. Das ist noch kein Analysebefund.`, `${label} injected into the scene. This is not an analysis finding yet.`));
      }
      return { injected: true, reason: null, sourceId: source, caseId };
    });
  }

  // Opens the next case on the camera that is already running (no new source),
  // e.g. after the previous case sealed its clip and went to review.
  async function startNextCase() {
    return enqueue("next-case", async () => {
      if (destroyed || !sourceId || !sourceOpen) {
        throw new Error(L("Die Kamera läuft nicht.", "The camera is not running."));
      }
      const source = sourceId;
      const generation = sourceGeneration;
      try {
        await startCase(source, generation);
      } catch (_error) {
        // The previous case frees the camera a moment after its run ends; retry once.
        await new Promise((resolve) => window.setTimeout(resolve, 600));
        if (!current(source, generation) || !sourceOpen) return;
        await startCase(source, generation);
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
          runSummary.textContent = L("Die Quelle ist beendet; der letzte Laufstatus ist nicht verfügbar.", "The source has ended; the last run status is not available.");
        }
      }
      if (stopped.clip) clips.set(stopped.clip.clip_id, stopped.clip);
      setStatus(wasOpen
        ? L("Quelle beendet; die vollständige Aufnahme wird angezeigt.", "Source ended; the complete recording is shown.")
        : L("Die Quelle war bereits beendet; ihre gespeicherte Aufnahme ist nun fertiggestellt.", "The source had already ended; its stored recording is now finalised."));
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
        throw new Error(L("Der Server lieferte keine neue Quellenkennung.", "The server returned no new source identifier."));
      }
      applySource(reset.source_id, L("Neue Quelle gestartet; frühere Clips bleiben erhalten.", "New source started; earlier clips are kept."));
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
      throw new Error(L("Der Server lieferte keinen abspielbaren Clip.", "The server returned no playable clip."));
    }
    if (result.sha256 && result.sha256 !== clip.sha256) {
      throw new Error(L("Die Clip-Prüfsumme passt nicht zu den gespeicherten Metadaten.", "The clip checksum does not match the stored metadata."));
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
      L(`Quelle ${clip.source_id} · Version ${formatNumber(clip.version, 0)} · ${formatNumber(clip.start_seconds)}–${formatNumber(clip.end_seconds)} Sek.`, `Source ${clip.source_id} · version ${formatNumber(clip.version, 0)} · ${formatNumber(clip.start_seconds)}–${formatNumber(clip.end_seconds)} s`);
    if (player.src !== url) {
      await new Promise((resolve, reject) => {
        const loaded = () => {
          cleanup();
          resolve();
        };
        const failed = () => {
          cleanup();
          reject(new Error(L("Der gespeicherte Clip konnte nicht geöffnet werden.", "The stored clip could not be opened.")));
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

  updateEventControls();
  loadRoadCapabilities();
  function getState() {
    return {
      sourceId,
      sourceOpen,
      caseId,
      runActive,
      warmedUp,
      available: roadAvailable,
      capabilities,
      injectBlocker: injectBlocker(),
    };
  }

  function configureCase({ mode, fixtureProfile } = {}) {
    if (mode && [...modeSelect.options].some((option) => option.value === mode)) modeSelect.value = mode;
    if (fixtureProfile && [...profileSelect.options].some((option) => option.value === fixtureProfile)) {
      profileSelect.value = fixtureProfile;
    }
    updateSourceControls();
    return { mode: modeSelect.value, fixtureProfile: profileSelect.value };
  }

  return {
    destroy,
    getCaseId,
    getSourceId,
    getState,
    configureCase,
    startNextCase,
    setCase,
    injectScenarioEvent: (event) => injectEvent(event),
    createSource,
    stopSource,
  };
}
