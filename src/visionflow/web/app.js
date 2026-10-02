"use strict";

(() => {
  const $ = (id) => document.getElementById(id);
  const activeStates = new Set([
    "queued",
    "running",
    "observing",
    "waiting_for_input",
    "waiting_for_approval",
  ]);
  const stateLabels = {
    idle: "Noch kein Lauf",
    queued: "Warteschlange",
    running: "Läuft",
    observing: "Beobachtet",
    waiting_for_input: "Eingabe erforderlich",
    waiting_for_approval: "Freigabe erforderlich",
    completed: "Abgeschlossen",
    failed: "Fehlgeschlagen",
    cancelled: "Abgebrochen",
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

  function apiError(payload, status) {
    const error = payload && payload.error;
    if (error && typeof error.message === "string") {
      return new Error(`${error.message} (HTTP ${status})`);
    }
    return new Error(`Die lokale Anfrage ist fehlgeschlagen (HTTP ${status}).`);
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
      throw new Error(`Der lokale Server lieferte keine gültige JSON-Antwort (HTTP ${response.status}).`);
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
      $("preset-description").textContent = "Der Ablauf misst Schärfe und Helligkeit, fordert dann bewusst eine gezielte Neuaufnahme an und misst das Ersatzbild erneut. Es gibt keinen automatischen Messwert-Grenzwert.";
    } else {
      if ($("input-kind").value === "image") $("input-kind").value = "video";
      $("approval-draft").checked = true;
      $("preset-description").textContent = "Der Ablauf misst Videoframes, prüft die begrenzte Folgebeobachtung und legt anschließend einen lokalen Benachrichtigungsentwurf zur Freigabe vor. Es findet kein Versand statt.";
    }
    setSourceControls();
  }

  function sourceKindChanged() {
    const kind = $("input-kind").value;
    if (kind === "image") {
      $("workflow-preset").value = "document-quality";
      $("model-question-group").hidden = true;
      $("approval-draft").checked = false;
      $("preset-description").textContent = "Der Ablauf misst Schärfe und Helligkeit, fordert dann bewusst eine gezielte Neuaufnahme an und misst das Ersatzbild erneut. Es gibt keinen automatischen Messwert-Grenzwert.";
    } else {
      $("workflow-preset").value = "motion-observation";
      $("model-question-group").hidden = false;
      $("preset-description").textContent = "Der Ablauf misst Videoframes, prüft die begrenzte Folgebeobachtung und legt anschließend einen lokalen Benachrichtigungsentwurf zur Freigabe vor. Es findet kein Versand statt.";
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
      option.title = descriptor.description || "";
      select.append(option);
    }
    select.disabled = available.length === 0;
    if (available.length === 0) {
      const option = document.createElement("option");
      option.value = "";
      option.textContent = "Kein Werkzeug für diese Quelle";
      select.append(option);
    } else {
      const preferred = kind === "image" ? "quality" : "motion";
      select.value = available.some((item) => item.name === preferred)
        ? preferred
        : available[0].name;
    }
    updateToolDescription();
  }

  function updateToolDescription() {
    const descriptor = descriptors.find((item) => item.name === $("tool-select").value);
    $("tool-description").textContent = descriptor ? descriptor.description : "";
  }

  async function resolveSource(kind, input = $("source-file")) {
    if (kind === "camera") {
      const name = $("camera-name").value.trim();
      if (!name) throw new Error("Bitte gib den Namen einer ausdrücklich freigegebenen Kamera ein.");
      return name;
    }
    const file = input.files && input.files[0];
    if (!file) throw new Error("Bitte wähle zuerst eine lokale Bild- oder Videodatei aus.");
    if (file.size <= 0 || file.size > 64 * 1024 * 1024) {
      throw new Error("Die ausgewählte Datei muss zwischen 1 Byte und 64 MiB groß sein.");
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
      throw new Error("Der Server lieferte keine sichere lokale Medienreferenz.");
    }
    lastUploadedFile = file;
    lastUploadedKind = kind;
    lastUploadedSource = payload.source;
    return payload.source;
  }

  function positiveNumber(id, label, integer = false) {
    const value = Number($(id).value);
    if (!Number.isFinite(value) || value <= 0 || (integer && !Number.isInteger(value))) {
      throw new Error(`${label} muss eine positive${integer ? " ganze" : "e"} Zahl sein.`);
    }
    return value;
  }

  async function buildConfig() {
    const kind = $("input-kind").value;
    const preset = $("workflow-preset").value;
    const tool = $("tool-select").value;
    const descriptor = descriptors.find((item) => item.name === tool);
    if (!descriptor || !descriptor.input_kinds.includes(kind)) {
      throw new Error("Für diese Quellenart ist kein passendes Analysewerkzeug verfügbar.");
    }
    const source = await resolveSource(kind);
    const observationSeconds = positiveNumber("observation-limit", "Beobachtungsgrenze", true);
    const sampleFps = positiveNumber("sample-fps", "Abtastrate");
    const maxFrames = positiveNumber("max-frames", "Frame-Limit", true);
    const maxModelCalls = positiveNumber("max-model-calls", "Modellaufruf-Limit", true);
    const name = $("workflow-name").value.trim() || "VisionFlow-Lauf";
    let nodes = [];
    if (preset === "document-quality") {
      if (kind !== "image" || tool !== "quality") {
        throw new Error("Die Vorlage für Dokumentqualität benötigt das Einzelbild-Werkzeug quality.");
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
          params: { reason: "Diese Dokumentvorlage fordert eine gezielte Neuaufnahme an. Die gespeicherten Messwerte werden nicht automatisch mit einem Grenzwert bewertet." },
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
        throw new Error("Die Vorlage für Bewegungsbeobachtung benötigt Video oder eine freigegebene Kamera.");
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
            question: $("model-question").value.trim() || "Welche Auffälligkeit zeigen die Messwerte?",
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
    setBusy(button, true, "Prüfe …");
    try {
      const config = await buildConfig();
      const result = await postJson("/validate", config);
      setMessage($("setup-message"), `Konfiguration geprüft: ${result.name}`);
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
    setBusy(button, true, "Starte …");
    try {
      const config = await buildConfig();
      const validation = await postJson("/validate", config);
      if (!validation.valid) throw new Error("Der Server konnte die Konfiguration nicht bestätigen.");
      const result = await postJson("/runs", config);
      currentRunId = result.run_id;
      currentInputKind = config.input.kind;
      currentResult = result;
      reportText = "";
      $("run-id").textContent = result.run_id;
      $("run-message").textContent = "Der Lauf wurde vom lokalen Controller angenommen.";
      $("report-content").textContent = "Der gespeicherte Bericht ist noch nicht geladen.";
      clearEvidence();
      renderResult(result);
      await pollStatus();
    } catch (error) {
      setMessage($("setup-message"), error.message, true);
    } finally {
      button.textContent = button.dataset.originalText || "Lauf starten";
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
      $("run-message").textContent = "Der Controller hat den Lauf als fehlgeschlagen markiert. Gespeicherte Befunde bleiben sichtbar.";
    } else if (status === "completed") {
      $("run-message").textContent = "Der Controller hat den Lauf abgeschlossen.";
    } else if (status === "cancelled") {
      $("run-message").textContent = "Der Controller hat den Lauf abgebrochen.";
    } else if (status !== "idle") {
      $("run-message").textContent = `Aktueller Controllerstatus: ${status}`;
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
      $("budget-summary").textContent = "Noch kein Budget-Snapshot im Controller-Trace verfügbar.";
      return;
    }
    const { used, limits, remaining } = snapshotEvent.budget;
    const display = (value) => typeof value === "number" && Number.isFinite(value)
      ? value.toLocaleString("de-DE", { maximumFractionDigits: 2 })
      : "nicht protokolliert";
    $("budget-summary").textContent = [
      `Beobachtungszeit: ${display(used.observation_seconds)} / ${display(limits.observation_seconds)} Sek. · Rest ${display(remaining.observation_seconds)} Sek.`,
      `Frames: ${display(used.frames)} / ${display(limits.frames)} · Rest ${display(remaining.frames)}`,
      `Modellaufrufe: ${display(used.llm_calls)} / ${display(limits.llm_calls)} · Rest ${display(remaining.llm_calls)}`,
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
      appendText(container, "p", "Noch keine Befunde gespeichert.", "empty-state");
      return;
    }
    findings.forEach((finding, index) => {
      const article = document.createElement("article");
      article.className = "finding-card";
      appendText(article, "h4", `Befund ${index + 1}: ${String(finding.tool || "Werkzeug")}`);
      appendText(article, "p", `Frames: ${(finding.frame_ids || []).map(String).join(", ") || "keine"}`, "hint");
      const metrics = document.createElement("dl");
      metrics.className = "metrics-list";
      for (const [key, value] of Object.entries(finding.metrics || {})) {
        appendText(metrics, "dt", key);
        appendText(metrics, "dd", JSON.stringify(value));
      }
      article.append(metrics);
      if (typeof finding.evidence_ref === "string" && finding.evidence_ref) {
        const button = appendText(article, "button", "Gespeicherten Bildbeleg öffnen", "button secondary evidence-button");
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
    $("evidence-caption").textContent = "Ein Befund mit Belegreferenz zeigt hier sein gespeichertes Bild.";
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
      if (!blob.type.startsWith("image/")) throw new Error("Der Beleg ist keine unterstützte Bilddatei.");
      const nextUrl = URL.createObjectURL(blob);
      const previousUrl = evidenceObjectUrl;
      evidenceObjectUrl = nextUrl;
      const image = $("evidence-image");
      image.onload = () => {
        if (previousUrl) URL.revokeObjectURL(previousUrl);
        $("evidence-caption").textContent = `Gespeicherter Beleg: ${reference}`;
      };
      image.onerror = () => {
        URL.revokeObjectURL(nextUrl);
        if (evidenceObjectUrl === nextUrl) evidenceObjectUrl = null;
        image.hidden = true;
        setMessage($("run-message"), "Der gespeicherte Bildbeleg konnte nicht angezeigt werden.", true);
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
      $("input-reason").textContent = inputRequest.reason || "Der Lauf wartet auf eine Eingabe.";
      $("replacement-file").hidden = currentInputKind === "camera";
      $("replacement-file").accept = currentInputKind === "image" ? "image/*" : "video/*,.avi,.mp4,.mov,.mkv,.webm";
    }
    if (waitingForApproval) {
      $("approval-reason").textContent = approvalRequest.reason || "Bitte prüfe den gespeicherten Entwurf.";
      const draft = (result.outputs || []).slice().reverse().find((output) => output.type === "notification_draft");
      if (draft) {
        $("approval-content").textContent = typeof draft.content === "string" ? draft.content : JSON.stringify(draft.content, null, 2);
        $("approval-expiry").textContent = `Gültig bis: ${String(draft.expires_at || "nicht angegeben")} · Versandstatus: ${draft.sent === false ? "nicht versendet" : "nicht bestätigt"}`;
        $("confirm-button").disabled = typeof draft.action_id !== "string" || typeof draft.content_hash !== "string";
        $("reject-button").disabled = $("confirm-button").disabled;
      } else {
        $("approval-content").textContent = "Der Controller meldet eine Freigabeanforderung, aber keinen gespeicherten Entwurf.";
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
      setMessage($("run-message"), "Abbruch an den lokalen Controller übermittelt.");
    } catch (error) {
      setMessage($("run-message"), error.message, true);
    }
  }

  async function resumeInput() {
    if (!currentRunId) return;
    try {
      const value = JSON.parse($("resume-json").value || "{}");
      if (value === null || typeof value !== "object" || Array.isArray(value)) {
        throw new Error("Die zusätzliche Eingabe muss ein JSON-Objekt sein.");
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
        ? "Der Entwurf wurde lokal bestätigt; es wurde nichts versendet."
        : "Der Entwurf wurde lokal abgelehnt.");
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
      throw new Error("Der lokale Server lieferte keinen gültigen, begrenzten MP4-Clip.");
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
      throw new Error("Der lokale Server lieferte keinen gültigen, begrenzten Bild- oder Clipbeleg.");
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
        return Promise.reject(new Error("Diese lokale Ansicht unterstützt nur GET und POST."));
      },
    };
  }

  function roadCaseEvent(event) {
    const detail = event && event.detail ? event.detail : {};
    const id = typeof detail.caseId === "string" ? detail.caseId : null;
    if (!id) return;
    roadActiveCaseId = id;
    roadViewedCaseId = id;
    if (roadMount) roadMount.setCase(detail.case || { case_id: id });
    roadRefreshGeneration += 1;
    void refreshRoadViews();
  }

  function roadOpenCase(event) {
    const detail = event && event.detail ? event.detail : {};
    const id = typeof detail.caseId === "string" ? detail.caseId : null;
    if (!id) return;
    roadViewedCaseId = id;
    roadRefreshGeneration += 1;
    void refreshRoadViews();
  }

  function phaseLocksCamera(snapshot) {
    return Boolean(snapshot && [
      "model_review",
      "human_review",
      "no_response",
    ].includes(snapshot.phase));
  }

  function renderRoadTimeline(snapshot) {
    const target = $("road-timeline-content");
    if (!target) return;
    target.replaceChildren();
    if (!snapshot || typeof snapshot !== "object") {
      const empty = document.createElement("p");
      empty.className = "road-empty-state";
      empty.textContent = "Starte eine Kameraquelle, um den echten Trace und das Entscheidungsjournal zu sehen.";
      target.append(empty);
      return;
    }
    const heading = document.createElement("p");
    heading.className = "road-timeline-case";
    heading.textContent = "Fall " + String(snapshot.case_id || "–") +
      " · " + String(snapshot.phase || "Status nicht angegeben");
    target.append(heading);
    const events = [];
    const trace = Array.isArray(snapshot.perception_trace) ? snapshot.perception_trace : [];
    for (const entry of trace) {
      if (!entry || typeof entry !== "object") continue;
      const label = {
        state_change: "Laufstatus geändert",
        frame_analyzed: "Frame ausgewertet",
        monitor_stopped: "Beobachtung beendet",
        node_completed: "Ablaufschritt abgeschlossen",
        run_started: "Lauf gestartet",
      }[entry.type] || "Laufereignis";
      const parts = [label];
      if (typeof entry.node_id === "string") parts.push(entry.node_id);
      if (typeof entry.frame_id === "string") parts.push("Frame " + entry.frame_id);
      if (typeof entry.timestamp_seconds === "number") {
        parts.push("Quellenzeit " + entry.timestamp_seconds.toFixed(2) + " Sek.");
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
        text: "Entscheidung · " + text,
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
      empty.textContent = "Für diesen Fall sind noch keine Trace- oder Journalereignisse vorhanden.";
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
      const ids = [...new Set([roadActiveCaseId, roadViewedCaseId].filter(Boolean))];
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
      if (roadMount) roadMount.setLocked(phaseLocksCamera(active?.snapshot));
      if (roadWorkplaceMount) {
        roadWorkplaceMount.update(viewed?.snapshot || null, inbox);
      }
      if (roadModelMount) roadModelMount.update(viewed?.exchanges || []);
      renderRoadTimeline(viewed?.snapshot || null);
      const failure = (viewed && viewed.error) || (active && active.error);
      const status = $("road-workspace-status");
      if (status) {
        status.textContent = failure
          ? "Die lokale Fallansicht konnte nicht aktualisiert werden. Bitte erneut öffnen."
          : "";
      }
    } catch (_error) {
      if (generation === roadRefreshGeneration) {
        const status = $("road-workspace-status");
        if (status) status.textContent = "Fallinformationen oder Postfach konnten nicht geladen werden.";
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
      if (!sessionReady) throw new Error("Die lokale Sitzung ist noch nicht bereit.");
      if (!roadWorkspaceReady) {
        roadModulePromise ||= Promise.all([
          import("/road-camera.js"),
          import("/road-model.js"),
          import("/road-workplace.js"),
        ]);
        const [cameraModule, modelModule, workplaceModule] = await roadModulePromise;
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
          if (window.RoadGame && !roadGameMount) {
            roadGameMount = window.RoadGame.mountCartoonGame($("workspace-road"));
          }
          roadWorkspaceReady = true;
          await refreshRoadViews();
          scheduleRoadRefresh();
        }
      }
    } catch (error) {
      $("road-camera-root").textContent = error.message || "Die Straßenkamera-Oberfläche konnte nicht geladen werden.";
    }
  }

  async function bootstrap() {
    try {
      const sessionResponse = await fetch("/session", {
        credentials: "same-origin",
        cache: "no-store",
      });
      if (!sessionResponse.ok) throw new Error("Der lokale Sitzungsschutz konnte nicht eingerichtet werden.");
      const session = await sessionResponse.json();
      if (typeof session.csrf_token !== "string" || !session.csrf_token) {
        throw new Error("Der lokale Server lieferte kein gültiges Sitzungstoken.");
      }
      csrfToken = session.csrf_token;
      sessionReady = true;
      descriptors = (await jsonRequest("/tools")).tools || [];
      renderToolChoices();
      $("connection-status").textContent = "Lokaler Server verbunden · Sitzung aktiv";
      $("road-tab").disabled = false;
      $("start-button").disabled = false;
      void activateRoadWorkspace();
    } catch (error) {
      sessionReady = false;
      $("road-tab").disabled = true;
      $("connection-status").textContent = "Lokaler Server nicht erreichbar";
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
    $("file-name").textContent = file ? `${file.name} · ${file.size.toLocaleString("de-DE")} Byte` : "Keine Datei ausgewählt.";
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
    ctx.fillText("VisionFlow Prüfbericht · Musterbeleg", 60, 70);

    ctx.fillStyle = "#1e293b";
    ctx.font = "14px monospace";
    ctx.fillText("Vorgang: VF-2026-QA-DOC-001", 60, 120);
    ctx.fillText("Prüfstelle: Leitwarte Nord · Automatisierte Dokumentenprüfung", 60, 145);
    ctx.fillText("Status: Eingangsqualitätskontrolle", 60, 170);

    ctx.strokeStyle = "#cbd5e1";
    ctx.lineWidth = 1.5;
    ctx.strokeRect(60, 200, 520, 160);
    ctx.beginPath();
    ctx.moveTo(60, 240);
    ctx.lineTo(580, 240);
    ctx.stroke();

    ctx.fillStyle = "#334155";
    ctx.font = "bold 13px sans-serif";
    ctx.fillText("Position", 75, 225);
    ctx.fillText("Beschreibung", 180, 225);
    ctx.fillText("Messwert", 440, 225);

    ctx.font = "12px sans-serif";
    ctx.fillText("01", 75, 270);
    ctx.fillText("Schärfemessung Laplace-Varianz", 180, 270);
    ctx.fillText(blurred ? "24.1 (UNSCHARF)" : "420.8 (SCHARF)", 440, 270);

    ctx.fillText("02", 75, 305);
    ctx.fillText("Kontrast- und Helligkeitsprüfung", 180, 305);
    ctx.fillText("0.88 OK", 440, 305);

    ctx.fillText("03", 75, 340);
    ctx.fillText("Perspektivenentzerrung", 180, 340);
    ctx.fillText("0.02 Radian", 440, 340);

    ctx.save();
    ctx.translate(320, 420);
    ctx.fillStyle = blurred ? "rgba(239, 68, 68, 0.85)" : "rgba(16, 185, 129, 0.85)";
    ctx.font = "bold 15px sans-serif";
    ctx.textAlign = "center";
    ctx.fillText(blurred ? "QUALITÄTSPRÜFUNG: UNSCHARF · NEUAUFNAHME ERFORDERLICH" : "QUALITÄTSPRÜFUNG: BESTANDEN · HOHE SCHÄRFE", 0, 0);
    ctx.restore();

    canvas.toBlob((blob) => {
      if (!blob) return;
      const fileName = blurred ? "beispieldokument-unscharf.png" : "beispieldokument-scharf.png";
      const file = new File([blob], fileName, { type: "image/png" });
      const dt = new DataTransfer();
      dt.items.add(file);
      $("source-file").files = dt.files;
      $("input-kind").value = "image";
      sourceKindChanged();
      $("file-name").textContent = `${file.name} · ${file.size.toLocaleString("de-DE")} Byte (vom System erzeugt)`;
      lastUploadedFile = null;
      lastUploadedKind = null;
      lastUploadedSource = null;
      $("start-button").disabled = false;
      setMessage($("setup-message"), `Beispieldokument (${blurred ? "unscharf" : "scharf"}) geladen. Klicke auf 'Lauf starten' oder 'Konfiguration prüfen'.`);
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
  applyPreset();
  bootstrap();
})();
