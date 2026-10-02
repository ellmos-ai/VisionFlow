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
      descriptors = (await jsonRequest("/tools")).tools || [];
      renderToolChoices();
      $("connection-status").textContent = "Lokaler Server verbunden · Sitzung aktiv";
      $("start-button").disabled = false;
    } catch (error) {
      $("connection-status").textContent = "Lokaler Server nicht erreichbar";
      setMessage($("setup-message"), error.message, true);
      $("start-button").disabled = true;
    }
  }

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
