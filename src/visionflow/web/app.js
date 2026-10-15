"use strict";

(() => {
  const $ = (id) => document.getElementById(id);
  const L = (de, en) => window.VisionFlowI18n ? window.VisionFlowI18n.L(de, en) : en;
  const LOCALE = window.VisionFlowI18n && window.VisionFlowI18n.isDe ? "de-DE" : "en-US";
  const activeStates = new Set([
    "queued",
    "running",
    "observing",
    "waiting_for_input",
    "waiting_for_approval",
  ]);
  const stateLabels = {
    idle: L("Noch kein Lauf", "No run yet"),
    queued: L("Warteschlange", "Queued"),
    running: L("Läuft", "Running"),
    observing: L("Beobachtet", "Observing"),
    waiting_for_input: L("Eingabe erforderlich", "Input required"),
    waiting_for_approval: L("Freigabe erforderlich", "Approval required"),
    completed: L("Abgeschlossen", "Completed"),
    failed: L("Fehlgeschlagen", "Failed"),
    cancelled: L("Abgebrochen", "Cancelled"),
  };

  let csrfToken = null;
  let descriptors = [];
  let currentRunId = null;
  let currentInputKind = null;
  let currentResult = null;
  let lastUploadedFile = null;
  let lastUploadedKind = null;
  let lastUploadedSource = null;
  let pollTimer = null;
  let evidenceObjectUrl = null;
  let reportText = "";
  let roadModulePromise = null;
  let roadSessionModule = null;
  let roadMount = null;
  let roadModelMount = null;
  let roadWorkplaceMount = null;
  let roadGameMount = null;
  let roadWorkspaceReady = false;
  let roadRefreshTimer = null;
  let roadRefreshInFlight = false;
  let roadActiveCaseId = null;
  let roadViewedCaseId = null;
  let roadRefreshGeneration = 0;
  let sessionReady = false;

  function setMessage(element, message, isError = false) {
    element.textContent = message;
    element.classList.toggle("error-message", isError);
  }

  // Server messages are German; known codes get a message in the UI language.
  const ERROR_TEXT = {
    demo_busy: L(
      "Alle Demo-Kameras sind belegt. Jede Sitzung endet nach zwei Minuten – bitte gleich erneut versuchen.",
      "All demo cameras are busy. Each session ends after two minutes – please try again shortly.",
    ),
    public_demo_source_rejected: L(
      "Die öffentliche Demo liest nur die synthetische Straßensimulation.",
      "The public demo only reads the synthetic road simulation.",
    ),
    public_demo_upload_disabled: L(
      "Uploads sind in der öffentlichen Straßendemo deaktiviert. Bild- und Videoabläufe kannst du lokal ausprobieren.",
      "Uploads are disabled in the public road demo. Try image and video workflows in the local app.",
    ),
    demo_storage_full: L(
      "Der Upload-Speicher der öffentlichen Demo ist voll. Er wird täglich geleert.",
      "The public demo upload storage is full. It is cleared every day.",
    ),
    service_closed: L("Die Kamerademo ist beendet.", "The camera demo has ended."),
  };

  function apiError(payload, status) {
    const error = payload && payload.error;
    if (error && typeof error.code === "string" && ERROR_TEXT[error.code]) {
      const known = new Error(`${ERROR_TEXT[error.code]} (HTTP ${status})`);
      known.code = error.code;
      return known;
    }
    if (status === 429) {
      return new Error(L(
        "Zu viele Anfragen in kurzer Zeit – bitte einen Moment warten.",
        "Too many requests in a short time – please wait a moment.",
      ));
    }
    if (error && typeof error.message === "string") {
      return new Error(`${error.message} (HTTP ${status})`);
    }
    return new Error(L(`Die lokale Anfrage ist fehlgeschlagen (HTTP ${status}).`, `The local request failed (HTTP ${status}).`));
  }

  async function jsonRequest(path, options = {}) {
    const headers = {
      Accept: "application/json",
      ...(options.headers || {}),
    };
    if (csrfToken) headers["X-CSRF-Token"] = csrfToken;
    const response = await fetch(path, {
      ...options,
      headers,
      credentials: "same-origin",
      cache: "no-store",
    });
    let payload;
    try {
      payload = await response.json();
    } catch (_error) {
      throw new Error(L(`Der lokale Server lieferte keine gültige JSON-Antwort (HTTP ${response.status}).`, `The local server returned no valid JSON response (HTTP ${response.status}).`));
    }
    if (!response.ok) throw apiError(payload, response.status);
    return payload;
  }

  function postJson(path, payload) {
    return jsonRequest(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
  }

  function setSourceControls() {
    const kind = $("input-kind").value;
    $("file-source").hidden = kind === "camera";
    $("camera-source").hidden = kind !== "camera";
    $("source-file").accept = kind === "image"
      ? "image/*"
      : "video/*,.avi,.mp4,.mov,.mkv,.webm";
    $("sample-fps").max = kind === "camera" ? "1" : "30";
    if (Number($("sample-fps").value) > Number($("sample-fps").max)) {
      $("sample-fps").value = $("sample-fps").max;
    }
    renderToolChoices();
  }

  function applyPreset() {
    const preset = $("workflow-preset").value;
    $("model-question-group").hidden = preset === "document-quality";
    if (preset === "document-quality") {
      $("input-kind").value = "image";
      $("approval-draft").checked = false;
      $("preset-description").textContent = L("Der Ablauf misst Schärfe und Helligkeit, fordert dann bewusst eine gezielte Neuaufnahme an und misst das Ersatzbild erneut. Es gibt keinen automatischen Messwert-Grenzwert.", "The workflow measures sharpness and brightness, then deliberately requests a targeted re-capture and measures the replacement image again. There is no automatic measurement threshold.");
    } else {
      if ($("input-kind").value === "image") $("input-kind").value = "video";
      $("approval-draft").checked = true;
      $("preset-description").textContent = L("Der Ablauf misst Videoframes, prüft die begrenzte Folgebeobachtung und legt anschließend einen lokalen Benachrichtigungsentwurf zur Freigabe vor. Es findet kein Versand statt.", "The workflow measures video frames, runs the bounded follow-up observation and then submits a local notification draft for approval. Nothing is sent.");
    }
    setSourceControls();
  }

  function sourceKindChanged() {
    const kind = $("input-kind").value;
    if (kind === "image") {
      $("workflow-preset").value = "document-quality";
      $("model-question-group").hidden = true;
      $("approval-draft").checked = false;
      $("preset-description").textContent = L("Der Ablauf misst Schärfe und Helligkeit, fordert dann bewusst eine gezielte Neuaufnahme an und misst das Ersatzbild erneut. Es gibt keinen automatischen Messwert-Grenzwert.", "The workflow measures sharpness and brightness, then deliberately requests a targeted re-capture and measures the replacement image again. There is no automatic measurement threshold.");
    } else {
      $("workflow-preset").value = "motion-observation";
      $("model-question-group").hidden = false;
      $("preset-description").textContent = L("Der Ablauf misst Videoframes, prüft die begrenzte Folgebeobachtung und legt anschließend einen lokalen Benachrichtigungsentwurf zur Freigabe vor. Es findet kein Versand statt.", "The workflow measures video frames, runs the bounded follow-up observation and then submits a local notification draft for approval. Nothing is sent.");
    }
    setSourceControls();
  }

  function renderToolChoices() {
    const kind = $("input-kind").value;
    const select = $("tool-select");
    const available = descriptors.filter((item) =>
      Array.isArray(item.input_kinds) && item.input_kinds.includes(kind),
    );
    select.replaceChildren();
    for (const descriptor of available) {
      const option = document.createElement("option");
      option.value = descriptor.name;
      option.textContent = descriptor.name;
      option.title = toolText(descriptor);
      select.append(option);
    }
    select.disabled = available.length === 0;
    if (available.length === 0) {
      const option = document.createElement("option");
      option.value = "";
      option.textContent = L("Kein Werkzeug für diese Quelle", "No tool for this source");
      select.append(option);
    } else {
      const preferred = kind === "image" ? "quality" : "motion";
      select.value = available.some((item) => item.name === preferred)
        ? preferred
        : available[0].name;
    }
    updateToolDescription();
  }

  const TOOL_TEXT = {
    quality: L(
      "Misst Schärfe und mittlere Grauwerthelligkeit eines Einzelbilds.",
      "Measures sharpness and mean grayscale brightness of a single image.",
    ),
    motion: L(
      "Vergleicht aufeinanderfolgende Bilder auf sichtbare Bewegung.",
      "Compares consecutive frames for visible motion.",
    ),
    direction: L(
      "Prüft sichtbare Bewegung gegen konfigurierte Spurvektoren.",
      "Checks visible motion against configured lane directions.",
    ),
  };

  function toolText(descriptor) {
    return descriptor ? TOOL_TEXT[descriptor.name] || descriptor.description || "" : "";
  }

  function updateToolDescription() {
    const descriptor = descriptors.find((item) => item.name === $("tool-select").value);
    $("tool-description").textContent = toolText(descriptor);
  }

  async function resolveSource(kind, input = $("source-file")) {
    if (kind === "camera") {
      const name = $("camera-name").value.trim();
      if (!name) throw new Error(L("Bitte gib den Namen einer ausdrücklich freigegebenen Kamera ein.", "Please enter the name of an explicitly approved camera."));
      return name;
    }
    const file = input.files && input.files[0];
    if (!file) throw new Error(L("Bitte wähle zuerst eine lokale Bild- oder Videodatei aus.", "Please choose a local image or video file first."));
    if (file.size <= 0 || file.size > 64 * 1024 * 1024) {
      throw new Error(L("Die ausgewählte Datei muss zwischen 1 Byte und 64 MiB groß sein.", "The selected file must be between 1 byte and 64 MiB."));
    }
    if (lastUploadedFile === file && lastUploadedKind === kind && lastUploadedSource) {
      return lastUploadedSource;
    }
    const payload = await jsonRequest("/media", {
      method: "POST",
      headers: {
        "Content-Type": "application/octet-stream",
        "X-VisionFlow-Media-Kind": kind,
      },
      body: file,
    });
    if (typeof payload.source !== "string" || !payload.source.startsWith("uploads/")) {
      throw new Error(L("Der Server lieferte keine sichere lokale Medienreferenz.", "The server returned no safe local media reference."));
    }
    lastUploadedFile = file;
    lastUploadedKind = kind;
    lastUploadedSource = payload.source;
    return payload.source;
  }

  function positiveNumber(id, label, integer = false) {
    const value = Number($(id).value);
    if (!Number.isFinite(value) || value <= 0 || (integer && !Number.isInteger(value))) {
      throw new Error(L(`${label} muss eine positive${integer ? " ganze" : "e"} Zahl sein.`, `${label} must be a positive${integer ? " whole" : ""} number.`));
    }
    return value;
  }

  async function buildConfig() {
    const kind = $("input-kind").value;
    const preset = $("workflow-preset").value;
    const tool = $("tool-select").value;
    const descriptor = descriptors.find((item) => item.name === tool);
    if (!descriptor || !descriptor.input_kinds.includes(kind)) {
      throw new Error(L("Für diese Quellenart ist kein passendes Analysewerkzeug verfügbar.", "No suitable analysis tool is available for this source type."));
    }
    const source = await resolveSource(kind);
    const observationSeconds = positiveNumber("observation-limit", L("Beobachtungsgrenze", "Observation limit"), true);
    const sampleFps = positiveNumber("sample-fps", L("Abtastrate", "Sampling rate"));
    const maxFrames = positiveNumber("max-frames", L("Frame-Limit", "Frame limit"), true);
    const maxModelCalls = positiveNumber("max-model-calls", L("Modellaufruf-Limit", "Model call limit"), true);
    const name = $("workflow-name").value.trim() || L("VisionFlow-Lauf", "VisionFlow run");
    let nodes = [];
    if (preset === "document-quality") {
      if (kind !== "image" || tool !== "quality") {
        throw new Error(L("Die Vorlage für Dokumentqualität benötigt das Einzelbild-Werkzeug quality.", "The document quality template requires the single-image tool quality."));
      }
      nodes.push(
        {
          id: "initial-quality",
          type: "analyze",
          params: { tool, tool_params: {} },
          next: ["retake-request"],
        },
        {
          id: "retake-request",
          type: "request_input",
          params: { reason: L("Diese Dokumentvorlage fordert eine gezielte Neuaufnahme an. Die gespeicherten Messwerte werden nicht automatisch mit einem Grenzwert bewertet.", "This document template requests a targeted re-capture. The stored measurements are not evaluated automatically against a threshold.") },
          next: ["replacement-quality"],
        },
        {
          id: "replacement-quality",
          type: "analyze",
          params: { tool, tool_params: {} },
          next: ["report"],
        },
        {
          id: "report",
          type: "report",
          params: { format: "markdown" },
          next: [],
        },
      );
    } else {
      if (tool !== "motion" || !["video", "camera"].includes(kind)) {
        throw new Error(L("Die Vorlage für Bewegungsbeobachtung benötigt Video oder eine freigegebene Kamera.", "The motion observation template requires a video or an approved camera."));
      }
      nodes = [
        {
          id: "motion-analysis",
          type: "analyze",
          params: { tool, tool_params: {} },
          next: ["model-decision"],
        },
        {
          id: "model-decision",
          type: "ask_model",
          params: {
            question: $("model-question").value.trim() || L("Welche Auffälligkeit zeigen die Messwerte?", "Which anomaly do the measurements show?"),
          },
          next: [],
        },
        {
          id: "observe-authority",
          type: "observe",
          params: {
            duration_seconds: observationSeconds,
            sample_fps: sampleFps,
            region: null,
          },
          next: [],
        },
      ];
    }
    return {
      schema_version: 1,
      name,
      input: { kind, source },
      limits: {
        max_observation_seconds: observationSeconds,
        max_frames: maxFrames,
        max_llm_calls: maxModelCalls,
      },
      nodes,
      outputs: $("approval-draft").checked
        ? [{ type: "notification_draft", requires_approval: true }]
        : [],
    };
  }

  function setBusy(button, busy, label) {
    if (busy) {
      button.dataset.originalText = button.textContent;
      button.textContent = label;
      button.disabled = true;
    } else {
      button.textContent = button.dataset.originalText || button.textContent;
      delete button.dataset.originalText;
      button.disabled = false;
    }
  }

  async function validateWorkflow() {
    const button = $("validate-button");
    setBusy(button, true, L("Prüfe …", "Validating …"));
    try {
      const config = await buildConfig();
      const result = await postJson("/validate", config);
      setMessage($("setup-message"), L(`Konfiguration geprüft: ${result.name}`, `Configuration validated: ${result.name}`));
      $("start-button").disabled = false;
      return config;
    } catch (error) {
      setMessage($("setup-message"), error.message, true);
      $("start-button").disabled = true;
      return null;
    } finally {
      setBusy(button, false, "");
    }
  }

  async function startRun() {
    const button = $("start-button");
    setBusy(button, true, L("Starte …", "Starting …"));
    try {
      const config = await buildConfig();
      const validation = await postJson("/validate", config);
      if (!validation.valid) throw new Error(L("Der Server konnte die Konfiguration nicht bestätigen.", "The server could not confirm the configuration."));
      const result = await postJson("/runs", config);
      currentRunId = result.run_id;
      currentInputKind = config.input.kind;
      currentResult = result;
      reportText = "";
      $("run-id").textContent = result.run_id;
      $("run-message").textContent = L("Der Lauf wurde vom lokalen Controller angenommen.", "The run was accepted by the local controller.");
      $("report-content").textContent = L("Der gespeicherte Bericht ist noch nicht geladen.", "The stored report has not been loaded yet.");
      clearEvidence();
      renderResult(result);
      await pollStatus();
    } catch (error) {
      setMessage($("setup-message"), error.message, true);
    } finally {
      button.textContent = button.dataset.originalText || L("Lauf starten", "Start run");
      delete button.dataset.originalText;
      button.disabled = Boolean(currentRunId && activeStates.has(currentResult?.status));
    }
  }

  async function pollStatus() {
    if (!currentRunId) return;
    try {
      const result = await jsonRequest(`/runs/${encodeURIComponent(currentRunId)}`);
      currentResult = result;
      renderResult(result);
      if (activeStates.has(result.status)) {
        pollTimer = window.setTimeout(pollStatus, 700);
      } else {
        pollTimer = null;
        $("start-button").disabled = false;
      }
    } catch (error) {
      setMessage($("run-message"), error.message, true);
    }
  }

  function renderResult(result) {
    const status = typeof result.status === "string" ? result.status : "unknown";
    const badge = $("status-badge");
    badge.dataset.state = status;
    badge.textContent = stateLabels[status] || status;
    $("cancel-button").disabled = !activeStates.has(status);
    $("start-button").disabled = activeStates.has(status);
    if (status === "failed") {
      $("run-message").textContent = L("Der Controller hat den Lauf als fehlgeschlagen markiert. Gespeicherte Befunde bleiben sichtbar.", "The controller marked the run as failed. Stored findings remain visible.");
    } else if (status === "completed") {
      $("run-message").textContent = L("Der Controller hat den Lauf abgeschlossen.", "The controller completed the run.");
    } else if (status === "cancelled") {
      $("run-message").textContent = L("Der Controller hat den Lauf abgebrochen.", "The controller cancelled the run.");
    } else if (status !== "idle") {
      $("run-message").textContent = L(`Aktueller Controllerstatus: ${status}`, `Current controller status: ${status}`);
    }
    $("load-report-button").disabled = false;
    $("trace-content").textContent = JSON.stringify(result.trace || [], null, 2);
    renderBudget(result.trace || []);
    renderFindings(result.findings || []);
    renderPending(result);
  }

  function renderBudget(trace) {
    const snapshotEvent = Array.isArray(trace)
      ? trace.slice().reverse().find((event) => event && event.budget && event.budget.used && event.budget.limits && event.budget.remaining)
      : null;
    if (!snapshotEvent) {
      $("budget-summary").textContent = L("Noch kein Budget-Snapshot im Controller-Trace verfügbar.", "No budget snapshot in the controller trace yet.");
      return;
    }
    const { used, limits, remaining } = snapshotEvent.budget;
    const display = (value) => typeof value === "number" && Number.isFinite(value)
      ? value.toLocaleString(LOCALE, { maximumFractionDigits: 2 })
      : L("nicht protokolliert", "not recorded");
    $("budget-summary").textContent = [
      L(`Beobachtungszeit: ${display(used.observation_seconds)} / ${display(limits.observation_seconds)} Sek. · Rest ${display(remaining.observation_seconds)} Sek.`, `Observation time: ${display(used.observation_seconds)} / ${display(limits.observation_seconds)} s · remaining ${display(remaining.observation_seconds)} s`),
      L(`Frames: ${display(used.frames)} / ${display(limits.frames)} · Rest ${display(remaining.frames)}`, `Frames: ${display(used.frames)} / ${display(limits.frames)} · remaining ${display(remaining.frames)}`),
      L(`Modellaufrufe: ${display(used.llm_calls)} / ${display(limits.llm_calls)} · Rest ${display(remaining.llm_calls)}`, `Model calls: ${display(used.llm_calls)} / ${display(limits.llm_calls)} · remaining ${display(remaining.llm_calls)}`),
    ].join("\n");
  }

  function appendText(parent, tag, text, className = "") {
    const node = document.createElement(tag);
    node.textContent = text;
    if (className) node.className = className;
    parent.append(node);
    return node;
  }

  function renderFindings(findings) {
    const container = $("findings-list");
    container.replaceChildren();
    if (!Array.isArray(findings) || findings.length === 0) {
      appendText(container, "p", L("Noch keine Befunde gespeichert.", "No findings stored yet."), "empty-state");
      return;
    }
    findings.forEach((finding, index) => {
      const article = document.createElement("article");
      article.className = "finding-card";
      appendText(article, "h4", L(`Befund ${index + 1}: ${String(finding.tool || "Werkzeug")}`, `Finding ${index + 1}: ${String(finding.tool || "tool")}`));
      appendText(article, "p", L(`Frames: ${(finding.frame_ids || []).map(String).join(", ") || "keine"}`, `Frames: ${(finding.frame_ids || []).map(String).join(", ") || "none"}`), "hint");
      const metrics = document.createElement("dl");
      metrics.className = "metrics-list";
      for (const [key, value] of Object.entries(finding.metrics || {})) {
        appendText(metrics, "dt", key);
        appendText(metrics, "dd", JSON.stringify(value));
      }
      article.append(metrics);
      if (typeof finding.evidence_ref === "string" && finding.evidence_ref) {
        const button = appendText(article, "button", L("Gespeicherten Bildbeleg öffnen", "Open stored image evidence"), "button secondary evidence-button");
        button.type = "button";
        button.addEventListener("click", () => showEvidence(finding.evidence_ref));
      }
      container.append(article);
    });
  }

  function clearEvidence() {
    if (evidenceObjectUrl) URL.revokeObjectURL(evidenceObjectUrl);
    evidenceObjectUrl = null;
    $("evidence-image").removeAttribute("src");
    $("evidence-image").hidden = true;
    $("evidence-caption").textContent = L("Ein Befund mit Belegreferenz zeigt hier sein gespeichertes Bild.", "A finding with an evidence reference shows its stored image here.");
  }

  async function showEvidence(reference) {
    if (!currentRunId) return;
    try {
      const response = await fetch(
        `/runs/${encodeURIComponent(currentRunId)}/evidence?ref=${encodeURIComponent(reference)}`,
        {
          method: "GET",
          headers: csrfToken ? { "X-CSRF-Token": csrfToken } : {},
          credentials: "same-origin",
          cache: "no-store",
        },
      );
      if (!response.ok) {
        let payload = null;
        try { payload = await response.json(); } catch (_error) { payload = null; }
        throw apiError(payload, response.status);
      }
      const blob = await response.blob();
      if (!blob.type.startsWith("image/")) throw new Error(L("Der Beleg ist keine unterstützte Bilddatei.", "The evidence is not a supported image file."));
      const nextUrl = URL.createObjectURL(blob);
      const previousUrl = evidenceObjectUrl;
      evidenceObjectUrl = nextUrl;
      const image = $("evidence-image");
      image.onload = () => {
        if (previousUrl) URL.revokeObjectURL(previousUrl);
        $("evidence-caption").textContent = L(`Gespeicherter Beleg: ${reference}`, `Stored evidence: ${reference}`);
      };
      image.onerror = () => {
        URL.revokeObjectURL(nextUrl);
        if (evidenceObjectUrl === nextUrl) evidenceObjectUrl = null;
        image.hidden = true;
        setMessage($("run-message"), L("Der gespeicherte Bildbeleg konnte nicht angezeigt werden.", "The stored image evidence could not be displayed."), true);
      };
      image.src = nextUrl;
      image.hidden = false;
    } catch (error) {
      setMessage($("run-message"), error.message, true);
    }
  }

  function renderPending(result) {
    const requests = Array.isArray(result.pending_requests) ? result.pending_requests : [];
    const inputRequest = requests.find((request) => request.type === "request_input");
    const approvalRequest = requests.find((request) => request.type === "request_approval");
    const waitingForInput = result.status === "waiting_for_input" && Boolean(inputRequest);
    const waitingForApproval = result.status === "waiting_for_approval" && Boolean(approvalRequest);
    $("input-panel").hidden = !waitingForInput;
    $("approval-panel").hidden = !waitingForApproval;
    if (waitingForInput) {
      $("input-reason").textContent = inputRequest.reason || L("Der Lauf wartet auf eine Eingabe.", "The run is waiting for input.");
      $("replacement-file").hidden = currentInputKind === "camera";
      $("replacement-file").accept = currentInputKind === "image" ? "image/*" : "video/*,.avi,.mp4,.mov,.mkv,.webm";
    }
    if (waitingForApproval) {
      $("approval-reason").textContent = approvalRequest.reason || L("Bitte prüfe den gespeicherten Entwurf.", "Please review the stored draft.");
      const draft = (result.outputs || []).slice().reverse().find((output) => output.type === "notification_draft");
      if (draft) {
        $("approval-content").textContent = typeof draft.content === "string" ? draft.content : JSON.stringify(draft.content, null, 2);
        $("approval-expiry").textContent = L(`Gültig bis: ${String(draft.expires_at || "nicht angegeben")} · Versandstatus: ${draft.sent === false ? "nicht versendet" : "nicht bestätigt"}`, `Valid until: ${String(draft.expires_at || "not specified")} · Delivery status: ${draft.sent === false ? "not sent" : "not confirmed"}`);
        $("confirm-button").disabled = typeof draft.action_id !== "string" || typeof draft.content_hash !== "string";
        $("reject-button").disabled = $("confirm-button").disabled;
      } else {
        $("approval-content").textContent = L("Der Controller meldet eine Freigabeanforderung, aber keinen gespeicherten Entwurf.", "The controller reports an approval request but no stored draft.");
        $("approval-expiry").textContent = "";
        $("confirm-button").disabled = true;
        $("reject-button").disabled = true;
      }
    } else {
      $("confirm-button").disabled = true;
      $("reject-button").disabled = true;
    }
  }

  async function cancelRun() {
    if (!currentRunId) return;
    try {
      const result = await postJson(`/runs/${encodeURIComponent(currentRunId)}/cancel`, {});
      currentResult = result;
      renderResult(result);
      setMessage($("run-message"), L("Abbruch an den lokalen Controller übermittelt.", "Cancellation sent to the local controller."));
    } catch (error) {
      setMessage($("run-message"), error.message, true);
    }
  }

  async function resumeInput() {
    if (!currentRunId) return;
    try {
      const value = JSON.parse($("resume-json").value || "{}");
      if (value === null || typeof value !== "object" || Array.isArray(value)) {
        throw new Error(L("Die zusätzliche Eingabe muss ein JSON-Objekt sein.", "The additional input must be a JSON object."));
      }
      const payload = { input: value };
      const file = $("replacement-file").files && $("replacement-file").files[0];
      if (file) payload.source = await resolveSource(currentInputKind, $("replacement-file"));
      const result = await postJson(`/runs/${encodeURIComponent(currentRunId)}/input`, payload);
      currentResult = result;
      renderResult(result);
      if (pollTimer) window.clearTimeout(pollTimer);
      pollTimer = window.setTimeout(pollStatus, 150);
    } catch (error) {
      setMessage($("run-message"), error.message, true);
    }
  }

  async function decideApproval(approved) {
    if (!currentRunId || !currentResult) return;
    const draft = (currentResult.outputs || []).slice().reverse().find((output) => output.type === "notification_draft");
    if (!draft) return;
    try {
      const action = approved ? "confirm" : "reject";
      const result = await postJson(
        `/runs/${encodeURIComponent(currentRunId)}/${action}`,
        { action_id: draft.action_id, content_hash: draft.content_hash },
      );
      if (result && result.run && typeof result.run.status === "string") {
        currentResult = result.run;
        renderResult(result.run);
      } else {
        currentResult = null;
        await pollStatus();
      }
      setMessage($("run-message"), approved
        ? L("Der Entwurf wurde lokal bestätigt; es wurde nichts versendet.", "The draft was confirmed locally; nothing was sent.")
        : L("Der Entwurf wurde lokal abgelehnt.", "The draft was rejected locally."));
      if (pollTimer) window.clearTimeout(pollTimer);
      pollTimer = window.setTimeout(pollStatus, 150);
    } catch (error) {
      setMessage($("run-message"), error.message, true);
    }
  }

  async function loadReport() {
    if (!currentRunId) return;
    try {
      const response = await fetch(`/runs/${encodeURIComponent(currentRunId)}/report`, {
        headers: csrfToken ? { "X-CSRF-Token": csrfToken } : {},
        credentials: "same-origin",
        cache: "no-store",
      });
      if (!response.ok) {
        let payload = null;
        try { payload = await response.json(); } catch (_error) { payload = null; }
        throw apiError(payload, response.status);
      }
      reportText = await response.text();
      $("report-content").textContent = reportText;
    } catch (error) {
      setMessage($("run-message"), error.message, true);
    }
  }

  function showWorkspace(name) {
    const analysis = name === "analysis";
    $("workspace-analysis").hidden = !analysis;
    $("workspace-road").hidden = analysis;
    $("analysis-tab").setAttribute("aria-pressed", String(analysis));
    $("road-tab").setAttribute("aria-pressed", String(!analysis));
  }

  async function getClip(path) {
    const response = await fetch(path, {
      method: "GET",
      headers: {
        Accept: "video/mp4",
        ...(csrfToken ? { "X-CSRF-Token": csrfToken } : {}),
      },
      credentials: "same-origin",
      cache: "no-store",
    });
    if (!response.ok) {
      let payload = null;
      try { payload = await response.json(); } catch (_error) { payload = null; }
      throw apiError(payload, response.status);
    }
    const blob = await response.blob();
    if (!blob.type.startsWith("video/mp4") || blob.size <= 0 || blob.size > 128 * 1024 * 1024) {
      throw new Error(L("Der lokale Server lieferte keinen gültigen, begrenzten MP4-Clip.", "The local server returned no valid, bounded MP4 clip."));
    }
    return { blob, sha256: response.headers.get("X-Clip-SHA256") };
  }

  async function getRoadBytes(path) {
    const response = await fetch(path, {
      method: "GET",
      headers: {
        Accept: "image/png, image/jpeg, video/mp4",
        ...(csrfToken ? { "X-CSRF-Token": csrfToken } : {}),
      },
      credentials: "same-origin",
      cache: "no-store",
    });
    if (!response.ok) {
      let payload = null;
      try { payload = await response.json(); } catch (_error) { payload = null; }
      throw apiError(payload, response.status);
    }
    const blob = await response.blob();
    const mediaType = blob.type.toLowerCase();
    const isImage = mediaType.startsWith("image/png") || mediaType.startsWith("image/jpeg");
    const isVideo = mediaType.startsWith("video/mp4");
    const maximum = isVideo ? 128 * 1024 * 1024 : 16 * 1024 * 1024;
    if ((!isImage && !isVideo) || blob.size <= 0 || blob.size > maximum) {
      throw new Error(L("Der lokale Server lieferte keinen gültigen, begrenzten Bild- oder Clipbeleg.", "The local server returned no valid, bounded image or clip evidence."));
    }
    return blob;
  }

  function roadApiClient() {
    return {
      get: (path) => jsonRequest(path),
      post: (path, payload) => postJson(path, payload),
      getClip,
      getBytes: getRoadBytes,
      request: (method, path, payload) => {
        if (method === "GET") return jsonRequest(path);
        if (method === "POST") return postJson(path, payload || {});
        return Promise.reject(new Error(L("Diese lokale Ansicht unterstützt nur GET und POST.", "This local view only supports GET and POST.")));
      },
    };
  }

  function roadCaseEvent(event) {
    const detail = event && event.detail ? event.detail : {};
    const id = typeof detail.caseId === "string" ? detail.caseId : null;
    if (!id) return;
    roadActiveCaseId = id;
    roadTrackedCaseIds.add(id);
    // A new case on the same camera must not pull an open review out of view.
    if (event.type !== "road-case-started" || !ROAD_REVIEW_PHASES.has(roadCasePhases.get(roadViewedCaseId))) {
      roadViewedCaseId = id;
      roadSessionModule?.rememberRoadCase(id);
    }
    if (roadMount) roadMount.setCase(detail.case || { case_id: id });
    roadRefreshGeneration += 1;
    void refreshRoadViews();
  }

  function roadOpenCase(event) {
    const detail = event && event.detail ? event.detail : {};
    const id = typeof detail.caseId === "string" ? detail.caseId : null;
    if (!id) return;
    roadViewedCaseId = id;
    roadSessionModule?.rememberRoadCase(id);
    roadRefreshGeneration += 1;
    void refreshRoadViews();
  }

  // Phases in which a case can still change; every other phase is final.
  const ROAD_OPEN_PHASES = new Set(["starting", "running", "model_review", "human_review", "no_response"]);
  const ROAD_REVIEW_PHASES = new Set(["model_review", "human_review", "no_response"]);
  const roadTrackedCaseIds = new Set();
  const roadCasePhases = new Map();

  function renderRoadTimeline(snapshot) {
    const target = $("road-timeline-content");
    if (!target) return;
    target.replaceChildren();
    if (!snapshot || typeof snapshot !== "object") {
      const empty = document.createElement("p");
      empty.className = "road-empty-state";
      empty.textContent = L("Starte eine Kameraquelle, um den echten Trace und das Entscheidungsjournal zu sehen.", "Start a camera source to see the real trace and the decision journal.");
      target.append(empty);
      return;
    }
    const heading = document.createElement("p");
    heading.className = "road-timeline-case";
    heading.textContent = L("Fall ", "Case ") + String(snapshot.case_id || "–") +
      " · " + String(snapshot.phase || L("Status nicht angegeben", "status not specified"));
    target.append(heading);
    const events = [];
    const trace = Array.isArray(snapshot.perception_trace) ? snapshot.perception_trace : [];
    for (const entry of trace) {
      if (!entry || typeof entry !== "object") continue;
      const label = {
        state_change: L("Laufstatus geändert", "Run state changed"),
        frame_analyzed: L("Frame ausgewertet", "Frame analyzed"),
        monitor_stopped: L("Beobachtung beendet", "Observation stopped"),
        node_completed: L("Ablaufschritt abgeschlossen", "Workflow step completed"),
        run_started: L("Lauf gestartet", "Run started"),
      }[entry.type] || L("Laufereignis", "Run event");
      const parts = [label];
      if (typeof entry.node_id === "string") parts.push(entry.node_id);
      if (typeof entry.frame_id === "string") parts.push(L("Frame ", "Frame ") + entry.frame_id);
      if (typeof entry.timestamp_seconds === "number") {
        parts.push(L("Quellenzeit ", "Source time ") + entry.timestamp_seconds.toFixed(2) + L(" Sek.", " s"));
      }
      if (typeof entry.reason === "string") parts.push(entry.reason);
      events.push({
        order: events.length,
        time: entry.created_at || entry.timestamp || entry.timestamp_seconds || null,
        text: parts.join(" · "),
      });
    }
    const journal = Array.isArray(snapshot.journal) ? snapshot.journal : [];
    for (const entry of journal) {
      if (!entry || typeof entry !== "object") continue;
      const text = typeof entry.message === "string"
        ? entry.message
        : typeof entry.event === "string"
          ? entry.event
          : JSON.stringify(entry);
      events.push({
        order: events.length,
        time: entry.created_at || entry.timestamp || entry.at || null,
        text: L("Entscheidung · ", "Decision · ") + text,
      });
    }
    const ordered = events.slice();
    const hasComparableTimes = ordered.length > 1 && ordered.every((item) =>
      typeof item.time === "number" || (typeof item.time === "string" && !Number.isNaN(Date.parse(item.time))),
    );
    if (hasComparableTimes) {
      ordered.sort((left, right) => {
        const leftTime = typeof left.time === "number" ? left.time : Date.parse(left.time);
        const rightTime = typeof right.time === "number" ? right.time : Date.parse(right.time);
        return leftTime - rightTime || left.order - right.order;
      });
    }
    if (ordered.length === 0) {
      const empty = document.createElement("p");
      empty.className = "road-empty-state";
      empty.textContent = L("Für diesen Fall sind noch keine Trace- oder Journalereignisse vorhanden.", "No trace or journal events exist for this case yet.");
      target.append(empty);
      return;
    }
    const list = document.createElement("ol");
    list.className = "road-timeline-list";
    for (const entry of ordered) {
      const item = document.createElement("li");
      item.textContent = entry.text;
      list.append(item);
    }
    target.append(list);
  }

  async function refreshRoadViews() {
    if (!roadWorkspaceReady || roadRefreshInFlight) return;
    roadRefreshInFlight = true;
    const generation = ++roadRefreshGeneration;
    try {
      const inboxResult = await jsonRequest("/road/inbox");
      const inbox = Array.isArray(inboxResult)
        ? inboxResult
        : Array.isArray(inboxResult?.records)
          ? inboxResult.records
          : Array.isArray(inboxResult?.cases)
            ? inboxResult.cases
            : [];
      // Several cases can be open at once (one per detection), so poll all of them.
      const ids = [...new Set([roadActiveCaseId, roadViewedCaseId, ...roadTrackedCaseIds].filter(Boolean))];
      const loaded = new Map();
      await Promise.all(ids.map(async (caseId) => {
        try {
          const encoded = encodeURIComponent(caseId);
          const [snapshot, model] = await Promise.all([
            jsonRequest("/road/cases/" + encoded),
            jsonRequest("/road/cases/" + encoded + "/model"),
          ]);
          loaded.set(caseId, {
            snapshot: snapshot && snapshot.case ? snapshot.case : snapshot,
            exchanges: Array.isArray(model?.exchanges) ? model.exchanges : [],
          });
        } catch (error) {
          loaded.set(caseId, { error });
        }
      }));
      if (generation !== roadRefreshGeneration) return;
      const active = roadActiveCaseId ? loaded.get(roadActiveCaseId) : null;
      const viewed = roadViewedCaseId ? loaded.get(roadViewedCaseId) : null;
      for (const [caseId, entry] of loaded) {
        const phase = entry?.snapshot?.phase;
        if (typeof phase !== "string") continue;
        roadCasePhases.set(caseId, phase);
        if (!ROAD_OPEN_PHASES.has(phase)) roadTrackedCaseIds.delete(caseId);
      }
      if (roadWorkplaceMount) {
        roadWorkplaceMount.update(viewed?.snapshot || null, inbox);
      }
      if (roadModelMount) roadModelMount.update(viewed?.exchanges || []);
      renderRoadTimeline(viewed?.snapshot || null);
      const cases = {};
      for (const [caseId, entry] of loaded) {
        if (entry && entry.snapshot) cases[caseId] = { snapshot: entry.snapshot, exchanges: entry.exchanges };
      }
      $("workspace-road").dispatchEvent(new CustomEvent("road-state", {
        detail: { activeCaseId: roadActiveCaseId, viewedCaseId: roadViewedCaseId, cases, inbox },
      }));
      const failure = (viewed && viewed.error) || (active && active.error);
      const status = $("road-workspace-status");
      if (status) {
        status.textContent = failure
          ? L("Die lokale Fallansicht konnte nicht aktualisiert werden. Bitte erneut öffnen.", "The local case view could not be updated. Please open it again.")
          : "";
      }
    } catch (_error) {
      if (generation === roadRefreshGeneration) {
        const status = $("road-workspace-status");
        if (status) status.textContent = L("Fallinformationen oder Postfach konnten nicht geladen werden.", "Case information or inbox could not be loaded.");
      }
    } finally {
      roadRefreshInFlight = false;
    }
  }

  function scheduleRoadRefresh() {
    if (roadRefreshTimer) window.clearTimeout(roadRefreshTimer);
    roadRefreshTimer = window.setTimeout(async () => {
      await refreshRoadViews();
      if (roadWorkspaceReady) scheduleRoadRefresh();
    }, 750);
  }

  async function activateRoadWorkspace() {
    showWorkspace("road");
    try {
      if (!sessionReady) throw new Error(L("Die lokale Sitzung ist noch nicht bereit.", "The local session is not ready yet."));
      if (!roadWorkspaceReady) {
        roadModulePromise ||= Promise.all([
          import("/road-camera.js"),
          import("/road-model.js"),
          import("/road-workplace.js"),
          import("/road-session.js"),
        ]);
        const [cameraModule, modelModule, workplaceModule, sessionModule] = await roadModulePromise;
        roadSessionModule = sessionModule;
        if (!roadWorkspaceReady) {
          const client = roadApiClient();
          const cameraRoot = $("road-camera-root");
          const workplaceRoot = $("road-workplace-root");
          cameraRoot.addEventListener("road-case-started", roadCaseEvent);
          cameraRoot.addEventListener("road-case-changed", roadCaseEvent);
          workplaceRoot.addEventListener("road-open-case", roadOpenCase);
          workplaceRoot.addEventListener("roaddecision", (event) => {
            const caseId = event?.detail?.caseId;
            if (typeof caseId === "string") {
              roadViewedCaseId = caseId;
              roadRefreshGeneration += 1;
              void refreshRoadViews();
            }
          });
          roadModelMount = modelModule.mountRoadModel($("road-model-root"), client);
          roadWorkplaceMount = workplaceModule.mountRoadWorkplace(workplaceRoot, client);
          roadMount = cameraModule.mountRoadCamera(cameraRoot, {
            ...client,
            timelineContainer: $("road-controller-trace"),
          });
          window.roadCameraMount = roadMount;
          window.roadApiClient = client;
          roadWorkspaceReady = true;
          const restored = await sessionModule.restoreRoadCases(client);
          for (const snapshot of restored.cases) roadTrackedCaseIds.add(snapshot.case_id);
          roadViewedCaseId = restored.selectedCaseId;
          roadActiveCaseId = restored.cases.at(-1)?.case_id || null;
          if (restored.cases.length) await roadMount.restoreSource(restored.cases.at(-1));
          if (restored.sessionLost) {
            const notice = document.createElement("p");
            notice.setAttribute("role", "status");
            notice.id = "road-recovery-status";
            notice.textContent = L("Die vorherige Sitzung ist nicht mehr verfügbar.", "The previous session is no longer available.");
            $("workspace-road").prepend(notice);
          }
          await refreshRoadViews();
          scheduleRoadRefresh();
        }
      }
      if (window.RoadGame && !roadGameMount && roadMount) {
        roadGameMount = window.RoadGame.mountCartoonGame($("workspace-road"), {
          roadMount,
          apiClient: window.roadApiClient,
          workplace: roadWorkplaceMount,
          openCase: (caseId) => {
            if (typeof caseId !== "string" || !caseId) return;
            roadViewedCaseId = caseId;
            roadSessionModule?.rememberRoadCase(caseId);
            roadRefreshGeneration += 1;
            void refreshRoadViews();
          },
        });
        await refreshRoadViews();
      }
    } catch (error) {
      $("road-camera-root").textContent = error.message || L("Die Straßenkamera-Oberfläche konnte nicht geladen werden.", "The road camera interface could not be loaded.");
    }
  }

  async function bootstrap() {
    try {
      const sessionResponse = await fetch("/session", {
        credentials: "same-origin",
        cache: "no-store",
      });
      if (!sessionResponse.ok) throw new Error(L("Der lokale Sitzungsschutz konnte nicht eingerichtet werden.", "The local session protection could not be set up."));
      const session = await sessionResponse.json();
      if (typeof session.csrf_token !== "string" || !session.csrf_token) {
        throw new Error(L("Der lokale Server lieferte kein gültiges Sitzungstoken.", "The local server returned no valid session token."));
      }
      csrfToken = session.csrf_token;
      sessionReady = true;
      descriptors = (await jsonRequest("/tools")).tools || [];
      renderToolChoices();
      const publicDemo = session.public_demo === true;
      $("public-demo-note").hidden = !publicDemo;
      $("analysis-tab").hidden = publicDemo;
      $("analysis-tab").disabled = publicDemo;
      $("hero-reuse").hidden = publicDemo;
      $("connection-status").textContent = publicDemo
        ? L("Öffentliche Demo · Sitzung aktiv", "Public demo · session active")
        : L("Lokaler Server verbunden · Sitzung aktiv", "Local server connected · session active");
      $("road-tab").disabled = false;
      $("start-button").disabled = false;
      void activateRoadWorkspace();
    } catch (error) {
      sessionReady = false;
      $("road-tab").disabled = true;
      $("connection-status").textContent = L("Lokaler Server nicht erreichbar", "Local server unreachable");
      setMessage($("setup-message"), error.message, true);
      $("start-button").disabled = true;
    }
  }

  $("analysis-tab").addEventListener("click", () => showWorkspace("analysis"));
  $("road-tab").addEventListener("click", activateRoadWorkspace);
  window.addEventListener("pagehide", () => {
    if (roadGameMount) roadGameMount.destroy();
    if (roadMount) roadMount.destroy();
    if (roadModelMount) roadModelMount.destroy();
    if (roadWorkplaceMount) roadWorkplaceMount.destroy();
    if (pollTimer) window.clearTimeout(pollTimer);
    if (roadRefreshTimer) window.clearTimeout(roadRefreshTimer);
    clearEvidence();
  }, { once: true });
  $("road-tab").disabled = true;

  $("workflow-preset").addEventListener("change", applyPreset);
  $("input-kind").addEventListener("change", sourceKindChanged);
  $("tool-select").addEventListener("change", updateToolDescription);
  $("source-file").addEventListener("change", () => {
    const file = $("source-file").files && $("source-file").files[0];
    $("file-name").textContent = file ? L(`${file.name} · ${file.size.toLocaleString("de-DE")} Byte`, `${file.name} · ${file.size.toLocaleString("en-US")} bytes`) : L("Keine Datei ausgewählt.", "No file selected.");
    lastUploadedFile = null;
    lastUploadedKind = null;
    lastUploadedSource = null;
    $("start-button").disabled = false;
  });

  function generateSampleDocument(blurred = false) {
    const canvas = document.createElement("canvas");
    canvas.width = 640;
    canvas.height = 480;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    ctx.fillStyle = "#ffffff";
    ctx.fillRect(0, 0, 640, 480);

    if (blurred) {
      ctx.filter = "blur(6px)";
    }

    ctx.fillStyle = "#0f172a";
    ctx.fillRect(40, 40, 560, 45);
    ctx.fillStyle = "#ffffff";
    ctx.font = "bold 20px sans-serif";
    ctx.fillText(L("VisionFlow Prüfbericht · Musterbeleg", "VisionFlow inspection report · sample document"), 60, 70);

    ctx.fillStyle = "#1e293b";
    ctx.font = "14px monospace";
    ctx.fillText(L("Vorgang: VF-2026-QA-DOC-001", "Case: VF-2026-QA-DOC-001"), 60, 120);
    ctx.fillText(L("Prüfstelle: Leitwarte Nord · Automatisierte Dokumentenprüfung", "Inspection office: Control Room North · automated document check"), 60, 145);
    ctx.fillText(L("Status: Eingangsqualitätskontrolle", "Status: incoming quality control"), 60, 170);

    ctx.strokeStyle = "#cbd5e1";
    ctx.lineWidth = 1.5;
    ctx.strokeRect(60, 200, 520, 160);
    ctx.beginPath();
    ctx.moveTo(60, 240);
    ctx.lineTo(580, 240);
    ctx.stroke();

    ctx.fillStyle = "#334155";
    ctx.font = "bold 13px sans-serif";
    ctx.fillText(L("Position", "Item"), 75, 225);
    ctx.fillText(L("Beschreibung", "Description"), 180, 225);
    ctx.fillText(L("Messwert", "Measurement"), 440, 225);

    ctx.font = "12px sans-serif";
    ctx.fillText("01", 75, 270);
    ctx.fillText(L("Schärfemessung Laplace-Varianz", "Sharpness (Laplacian variance)"), 180, 270);
    ctx.fillText(blurred ? L("24.1 (UNSCHARF)", "24.1 (BLURRED)") : L("420.8 (SCHARF)", "420.8 (SHARP)"), 440, 270);

    ctx.fillText("02", 75, 305);
    ctx.fillText(L("Kontrast- und Helligkeitsprüfung", "Contrast and brightness check"), 180, 305);
    ctx.fillText("0.88 OK", 440, 305);

    ctx.fillText("03", 75, 340);
    ctx.fillText(L("Perspektivenentzerrung", "Perspective correction"), 180, 340);
    ctx.fillText(L("0.02 Radian", "0.02 rad"), 440, 340);

    ctx.save();
    ctx.translate(320, 420);
    ctx.fillStyle = blurred ? "rgba(239, 68, 68, 0.85)" : "rgba(16, 185, 129, 0.85)";
    ctx.font = "bold 15px sans-serif";
    ctx.textAlign = "center";
    ctx.fillText(blurred ? L("QUALITÄTSPRÜFUNG: UNSCHARF · NEUAUFNAHME ERFORDERLICH", "QUALITY CHECK: BLURRED · RE-CAPTURE REQUIRED") : L("QUALITÄTSPRÜFUNG: BESTANDEN · HOHE SCHÄRFE", "QUALITY CHECK: PASSED · HIGH SHARPNESS"), 0, 0);
    ctx.restore();

    canvas.toBlob((blob) => {
      if (!blob) return;
      const fileName = blurred
        ? L("beispieldokument-unscharf.png", "sample-document-blurred.png")
        : L("beispieldokument-scharf.png", "sample-document-sharp.png");
      const file = new File([blob], fileName, { type: "image/png" });
      const dt = new DataTransfer();
      dt.items.add(file);
      $("source-file").files = dt.files;
      $("input-kind").value = "image";
      sourceKindChanged();
      $("file-name").textContent = L(`${file.name} · ${file.size.toLocaleString("de-DE")} Byte (vom System erzeugt)`, `${file.name} · ${file.size.toLocaleString("en-US")} bytes (generated by the system)`);
      lastUploadedFile = null;
      lastUploadedKind = null;
      lastUploadedSource = null;
      $("start-button").disabled = false;
      setMessage($("setup-message"), L(`Beispieldokument (${blurred ? "unscharf" : "scharf"}) geladen. Klicke auf 'Lauf starten' oder 'Konfiguration prüfen'.`, `Sample document (${blurred ? "blurred" : "sharp"}) loaded. Click 'Start run' or 'Validate configuration'.`));
    }, "image/png");
  }

  if ($("sample-sharp-btn")) {
    $("sample-sharp-btn").addEventListener("click", () => generateSampleDocument(false));
  }
  if ($("sample-blurred-btn")) {
    $("sample-blurred-btn").addEventListener("click", () => generateSampleDocument(true));
  }

  $("validate-button").addEventListener("click", validateWorkflow);
  $("start-button").addEventListener("click", startRun);
  $("cancel-button").addEventListener("click", cancelRun);
  $("resume-button").addEventListener("click", resumeInput);
  $("confirm-button").addEventListener("click", () => decideApproval(true));
  $("reject-button").addEventListener("click", () => decideApproval(false));
  $("load-report-button").addEventListener("click", loadReport);
  const langButton = document.createElement("button");
  langButton.id = "app-lang-btn";
  langButton.type = "button";
  langButton.className = "button secondary app-lang-btn";
  langButton.textContent = L("🌐 EN", "🌐 DE");
  langButton.addEventListener("click", () => {
    if (window.VisionFlowI18n) window.VisionFlowI18n.setLang(window.VisionFlowI18n.isDe ? "en" : "de");
  });
  document.querySelector(".app-header").append(langButton);
  applyPreset();
  bootstrap();
})();
