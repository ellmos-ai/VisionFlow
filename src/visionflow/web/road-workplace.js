const PHASE_LABELS = Object.freeze({
  initializing: "Fall wird vorbereitet",
  starting: "Fallstart läuft",
  running: "Verkehr wird ausgewertet",
  model_review: "Modellprüfung läuft",
  normal: "Keine Auffälligkeit festgestellt",
  source_ended: "Quelle beendet",
  human_review: "Menschliche Prüfung erforderlich",
  no_response: "Keine Antwort bis zum Fristende",
  reported: "Erstmeldung gesendet",
  corrected: "Entwarnung gesendet",
  rejected: "Gefahr verneint",
  dismissed: "Kein Gefahrverdacht",
  cancelled: "Fall abgebrochen",
});

const REASON_LABELS = Object.freeze({
  high_confidence: "Automatische Erstmeldung nach bestätigter hoher Sicherheit und Belegen",
  human_confirmed: "Erstmeldung nach menschlicher Bestätigung",
  no_response: "Erstmeldung nach Ablauf der serverseitigen Prüfzeit ohne Antwort",
});

const ERROR_LABELS = Object.freeze({
  model_timeout: "Die Modellanfrage hat das Zeitlimit erreicht.",
  invalid_json: "Die Modellantwort ließ sich nicht als gültige Bewertung lesen.",
  invalid_evidence: "Die Modellantwort verwies auf ungültige Belege.",
  stale_version: "Der Fall wurde inzwischen geändert. Bitte aktualisiere ihn und prüfe die Entscheidung erneut.",
  stale_content_hash: "Der Fallinhalt wurde inzwischen geändert. Bitte aktualisiere ihn und prüfe die Entscheidung erneut.",
});

function element(tagName, className, text) {
  const node = document.createElement(tagName);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function hasValue(value) {
  return value !== null && value !== undefined && value !== "";
}

function setHidden(node, hidden) {
  node.hidden = Boolean(hidden);
}

function valueText(value, fallback = "Nicht angegeben") {
  if (!hasValue(value)) return fallback;
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return fallback;
  }
}

function hazardLabel(hazard) {
  if (hazard === true) return "Ja";
  if (hazard === false) return "Nein";
  return "Unklar";
}

function phaseLabel(phase) {
  return PHASE_LABELS[phase] || (hasValue(phase) ? `Unbekannter Fallstatus (${valueText(phase)})` : "Fallstatus nicht angegeben");
}

function errorLabel(error) {
  const code = typeof error === "string" ? error : error?.code;
  if (hasValue(code) && ERROR_LABELS[code]) return ERROR_LABELS[code];
  const status = Number(error?.status);
  if (status === 409) return "Der Fall wurde inzwischen geändert. Bitte aktualisiere ihn und prüfe die Entscheidung erneut.";
  if (status >= 400 && status < 500) return "Der Server hat die Entscheidung abgelehnt. Bitte aktualisiere den Fall und prüfe ihn erneut.";
  return "Die Antwort des Servers ist nicht angekommen. Der gleiche Antrag kann mit derselben Kennung erneut gesendet werden.";
}

function isConflict(error) {
  return Number(error?.status) === 409 || ["stale_version", "stale_content_hash", "decision_conflict"].includes(error?.code);
}

function isPermanentClientError(error) {
  const status = Number(error?.status);
  return (status >= 400 && status < 500 && status !== 409) || ["invalid_version", "invalid_decision", "invalid_idempotency_key"].includes(error?.code);
}

function clipIdentity(clip, scope) {
  if (!clip || !hasValue(clip.clip_id)) return null;
  const content = hasValue(clip.sha256)
    ? `sha256:${valueText(clip.sha256)}`
    : `clip:${valueText(clip.clip_id)}:v${valueText(clip.version, "")}`;
  return `${valueText(scope, "scope")}:${content}`;
}

function clipFetchKey(clip) {
  return hasValue(clip?.sha256)
    ? valueText(clip.sha256)
    : `${valueText(clip?.clip_id, "clip")}:v${valueText(clip?.version, "")}`;
}

function setField(parent, label, value) {
  const row = element("div", "road-case-field");
  row.append(element("strong", "road-case-field-label", `${label}: `));
  row.append(element("span", "road-case-field-value", value));
  parent.appendChild(row);
}

function policyFields(snapshot) {
  const nested = snapshot?.decision && typeof snapshot.decision === "object" ? snapshot.decision : null;
  const pick = (name) => hasValue(snapshot?.[name]) ? snapshot[name] : nested?.[name];
  return {
    caseId: snapshot?.case_id,
    runId: pick("run_id"),
    version: pick("version"),
    contentHash: pick("content_hash"),
    phase: pick("phase") || pick("status"),
    deadlineAt: pick("deadline_at"),
    remainingSeconds: pick("remaining_seconds"),
    assessment: pick("assessment"),
    modelError: pick("model_error"),
    clip: pick("clip"),
    firstReceipt: pick("first_receipt"),
    correctionReceipt: pick("correction_receipt"),
  };
}

function idempotencyKey() {
  if (globalThis.crypto?.randomUUID) return globalThis.crypto.randomUUID();
  if (!globalThis.crypto?.getRandomValues) throw new Error("secure_random_unavailable");
  const bytes = new Uint8Array(16);
  globalThis.crypto.getRandomValues(bytes);
  return [...bytes].map((value) => value.toString(16).padStart(2, "0")).join("");
}

function keyForBinding(caseId, version, contentHash, decision) {
  return JSON.stringify([caseId, version, contentHash, decision]);
}

function validClipRecord(record) {
  return record && typeof record === "object" && typeof record.clip_id === "string" && record.clip_id.length > 0;
}

export function mountRoadWorkplace(container, apiClient) {
  if (!container || typeof container.replaceChildren !== "function") {
    throw new TypeError("container muss ein DOM-Element sein.");
  }
  if (!apiClient || typeof apiClient.request !== "function" || typeof apiClient.getBytes !== "function") {
    throw new TypeError("apiClient.request und apiClient.getBytes müssen verfügbar sein.");
  }

  let destroyed = false;
  let activeCaseId = null;
  let activeBinding = null;
  let activeFields = null;
  let activeSnapshot = null;
  let activeViewGeneration = 0;
  let phaseAnnouncement = "";
  let remainingAtUpdate = null;
  let remainingAtPerformance = 0;
  let latestInbox = [];
  let operationSequence = 0;
  let refreshGeneration = 0;
  const operations = new Map();
  const blobRequests = new Map();
  const inboxEntries = new Map();

  const header = element("header", "road-panel-header");
  const title = element("h2", "road-panel-title", "Menschlicher Arbeitsplatz und Demo-Postfach");
  const caseBadge = element("span", "road-panel-meta", "Kein offener Fall");
  caseBadge.setAttribute("role", "status");
  caseBadge.setAttribute("aria-live", "polite");
  header.append(title, caseBadge);

  const office = element("section", "road-office-scene");
  office.setAttribute("aria-label", "Büroarbeitsplatz mit Laptop");
  const deskCaption = element("p", "road-office-decor", "Schreibtisch – menschliche Fallprüfung");
  const laptopWrapper = element("div", "road-laptop-wrapper");
  const screenFrame = element("div", "road-laptop-screen-frame");
  const screen = element("section", "road-laptop-screen-content");
  screen.setAttribute("role", "region");
  screen.setAttribute("aria-label", "Einsatz-Arbeitsplatz auf dem Laptop");

  const notification = element("div", "road-countdown-banner");
  notification.setAttribute("role", "status");
  notification.setAttribute("aria-live", "polite");
  notification.textContent = "Noch keine Benachrichtigung für eine menschliche Prüfung.";
  screen.appendChild(notification);

  const emptyWorkplaceState = element("p", "road-empty-state", "Warten auf einen Fall zur menschlichen Prüfung.");
  screen.appendChild(emptyWorkplaceState);

  const caseContainer = element("div", "road-case-container");
  setHidden(caseContainer, true);
  const caseHeader = element("header", "road-panel-header");
  const caseTitle = element("h3", "road-panel-title", "Fallzusammenfassung");
  const phaseBadge = element("span", "road-badge", "Fallstatus nicht angegeben");
  phaseBadge.setAttribute("role", "status");
  phaseBadge.setAttribute("aria-live", "polite");
  caseHeader.append(caseTitle, phaseBadge);
  caseContainer.appendChild(caseHeader);

  const countdown = element("div", "road-countdown-banner road-case-countdown");
  const countdownTitle = element("strong", "road-countdown-title", "Serverfrist für die menschliche Prüfung");
  const countdownClock = element("span", "road-countdown-clock", "-- s");
  countdownClock.setAttribute("aria-live", "off");
  const deadline = element("span", "road-case-deadline", "Frist: im Snapshot nicht angegeben");
  countdown.append(countdownTitle, countdownClock, deadline);
  caseContainer.appendChild(countdown);

  const activeVideoContainer = element("div", "road-video-container road-case-video-container");
  const activeVideo = document.createElement("video");
  activeVideo.className = "road-video-player";
  activeVideo.controls = true;
  activeVideo.playsInline = true;
  activeVideo.preload = "metadata";
  activeVideo.setAttribute("aria-label", "Vollständiger Ereignisclip zur menschlichen Fallprüfung");
  activeVideoContainer.appendChild(activeVideo);
  const activeClipStatus = element("p", "road-model-clip-status", "Kein Clip im Snapshot vorhanden.");
  activeClipStatus.setAttribute("role", "status");
  const retryActiveClip = element("button", "road-btn road-btn-secondary", "Clip erneut laden");
  retryActiveClip.type = "button";
  retryActiveClip.dataset.roadAction = "retry-active-clip";
  setHidden(retryActiveClip, true);
  caseContainer.append(activeVideoContainer, activeClipStatus, retryActiveClip);

  const caseDetails = element("div", "road-inspector-box road-case-details");
  caseContainer.appendChild(caseDetails);

  const decisionFeedback = element("p", "road-decision-feedback");
  decisionFeedback.setAttribute("role", "status");
  decisionFeedback.setAttribute("aria-live", "polite");
  setHidden(decisionFeedback, true);
  caseContainer.appendChild(decisionFeedback);
  const decisionActions = element("div", "road-btn-group road-decision-actions");
  const confirmButton = element("button", "road-btn road-btn-danger", "Gefahr bestätigen und Behörden informieren");
  confirmButton.type = "button";
  confirmButton.id = "btn-operator-confirm";
  confirmButton.disabled = true;
  const denyButton = element("button", "road-btn road-btn-secondary", "Gefahr verneinen");
  denyButton.type = "button";
  denyButton.id = "btn-operator-deny";
  denyButton.disabled = true;
  decisionActions.append(confirmButton, denyButton);
  caseContainer.appendChild(decisionActions);

  const retryDecisionButton = element("button", "road-btn road-btn-secondary", "Gleiche Entscheidung erneut senden");
  retryDecisionButton.type = "button";
  retryDecisionButton.dataset.roadAction = "retry-decision";
  setHidden(retryDecisionButton, true);
  caseContainer.appendChild(retryDecisionButton);
  const refreshCaseButton = element("button", "road-btn road-btn-secondary", "Fall aktualisieren");
  refreshCaseButton.type = "button";
  refreshCaseButton.dataset.roadAction = "refresh-case";
  setHidden(refreshCaseButton, true);
  caseContainer.appendChild(refreshCaseButton);

  screen.appendChild(caseContainer);
  screenFrame.appendChild(screen);
  laptopWrapper.appendChild(screenFrame);
  const laptopBase = element("div", "road-laptop-base");
  laptopBase.appendChild(element("span", "road-laptop-notch"));
  laptopWrapper.appendChild(laptopBase);
  office.append(deskCaption, laptopWrapper);

  const inboxSection = element("section", "road-inbox-container");
  inboxSection.setAttribute("aria-label", "Simulierter Behördeneingang");
  const inboxTitle = element("h3", "road-panel-title", "Demo-Postfach des Behördenempfängers");
  const disclaimer = element(
    "p",
    "road-simulation-disclaimer",
    "Simulierter Empfänger: Meldungen bleiben in dieser lokalen Demo. Es wird keine Behörde kontaktiert.",
  );
  const inboxList = element("div", "road-inbox-list");
  inboxList.setAttribute("role", "list");
  inboxList.setAttribute("aria-label", "Erstmeldungen und Entwarnungen");
  const emptyInbox = element("p", "road-empty-state", "Im Demo-Postfach ist noch keine Meldung eingegangen.");
  inboxList.appendChild(emptyInbox);
  inboxSection.append(inboxTitle, disclaimer, inboxList);
  container.replaceChildren(header, office, inboxSection);

  const activeClip = {
    video: activeVideo,
    clipStatus: activeClipStatus,
    retryButton: retryActiveClip,
    clipIdentity: null,
    failedIdentity: null,
    clipGeneration: 0,
    clipPending: false,
    abortController: null,
    metadataListener: null,
    errorListener: null,
    objectUrl: null,
    sourceRecord: null,
    isMounted: true,
  };

  function getBlob(clip) {
    const key = clipFetchKey(clip);
    const cached = blobRequests.get(key);
    if (cached) return cached;
    const path = `/road/clips/${encodeURIComponent(clip.clip_id)}`;
    const request = Promise.resolve().then(() => apiClient.getBytes(path));
    const guarded = request.then((blob) => {
      if (!(blob instanceof Blob)) throw new TypeError("Die Cliproute lieferte keine Blob-Daten.");
      return blob;
    }).catch((error) => {
      if (blobRequests.get(key) === guarded) blobRequests.delete(key);
      throw error;
    });
    blobRequests.set(key, guarded);
    return guarded;
  }

  function detachMedia(entry) {
    if (entry.metadataListener) entry.video.removeEventListener("loadedmetadata", entry.metadataListener);
    if (entry.errorListener) entry.video.removeEventListener("error", entry.errorListener);
    entry.metadataListener = null;
    entry.errorListener = null;
    entry.video.pause();
    entry.video.removeAttribute("src");
    entry.video.load();
    if (entry.objectUrl) URL.revokeObjectURL(entry.objectUrl);
    entry.objectUrl = null;
  }

  function cancelClipWork(entry) {
    entry.clipGeneration += 1;
    if (entry.abortController) entry.abortController.abort();
    entry.abortController = null;
    entry.clipPending = false;
  }

  function setClipMessage(entry, message, isError = false) {
    entry.clipStatus.textContent = message;
    entry.clipStatus.classList.toggle("road-status-error", isError);
    setHidden(entry.retryButton, !isError);
    entry.retryButton.disabled = destroyed;
  }

  function clearClip(entry, message = "Kein Clip im Snapshot vorhanden.") {
    cancelClipWork(entry);
    detachMedia(entry);
    entry.clipIdentity = null;
    entry.failedIdentity = null;
    entry.sourceRecord = null;
    entry.video.dataset.clipId = "";
    entry.video.dataset.clipHash = "";
    setClipMessage(entry, message);
  }

  function requestClip(entry, clip, sourceRecord, force = false) {
    if (!validClipRecord(clip)) {
      if (entry.clipIdentity !== null || entry.objectUrl || entry.clipPending) clearClip(entry);
      else setClipMessage(entry, "Kein Clip im Snapshot vorhanden.");
      return;
    }
    const identity = clipIdentity(clip, sourceRecord.case_id || sourceRecord.receipt_id || "record");
    if (!force && entry.clipIdentity === identity && (entry.objectUrl || entry.clipPending || entry.failedIdentity === identity)) {
      entry.sourceRecord = sourceRecord;
      return;
    }

    const previousRecord = entry.sourceRecord;
    const previousTime = Number.isFinite(entry.video.currentTime) ? entry.video.currentTime : 0;
    const preserveTime = Boolean(
      previousRecord &&
      hasValue(previousRecord.case_id) &&
      previousRecord.case_id === sourceRecord.case_id &&
      hasValue(previousRecord.run_id) &&
      previousRecord.run_id === sourceRecord.run_id &&
      previousTime > 0,
    );

    cancelClipWork(entry);
    detachMedia(entry);
    entry.clipIdentity = identity;
    entry.failedIdentity = null;
    entry.clipPending = true;
    entry.sourceRecord = sourceRecord;
    const generation = entry.clipGeneration;
    const abortController = new AbortController();
    entry.abortController = abortController;
    entry.video.dataset.clipId = valueText(clip.clip_id);
    entry.video.dataset.clipHash = valueText(clip.sha256, "");
    entry.video.setAttribute("aria-label", `Vollständiger Ereignisclip ${valueText(clip.clip_id)} zur menschlichen Prüfung`);
    setClipMessage(entry, "Authentifizierter Ereignisclip wird geladen.");

    getBlob(clip).then((blob) => {
      if (
        destroyed ||
        abortController.signal.aborted ||
        entry.clipGeneration !== generation ||
        entry.clipIdentity !== identity ||
        !entry.isMounted
      ) return;
      const objectUrl = URL.createObjectURL(blob);
      entry.objectUrl = objectUrl;
      entry.clipPending = false;
      entry.abortController = null;

      const onMetadata = () => {
        if (destroyed || abortController.signal.aborted || entry.clipGeneration !== generation || entry.objectUrl !== objectUrl) return;
        const duration = Number.isFinite(entry.video.duration) ? entry.video.duration : 0;
        const targetTime = preserveTime && duration > 0 ? Math.min(previousTime, Math.max(0, duration - 0.05)) : 0;
        if (targetTime > 0 && Math.abs(entry.video.currentTime - targetTime) > 0.05) {
          try {
            entry.video.currentTime = targetTime;
          } catch {
            // Some native media sources are not seekable even after metadata loads.
          }
        }
        setClipMessage(entry, "Clip geladen. Native Videosteuerung ist verfügbar.");
      };
      const onError = () => {
        if (destroyed || entry.clipGeneration !== generation) return;
        entry.failedIdentity = identity;
        setClipMessage(entry, "Der Ereignisclip konnte nicht geöffnet werden.", true);
      };
      entry.metadataListener = onMetadata;
      entry.errorListener = onError;
      entry.video.addEventListener("loadedmetadata", onMetadata, { once: true });
      entry.video.addEventListener("error", onError, { once: true });
      entry.video.src = objectUrl;
      entry.video.load();
      if (entry.video.readyState >= 1) onMetadata();
    }).catch(() => {
      if (destroyed || abortController.signal.aborted || entry.clipGeneration !== generation || entry.clipIdentity !== identity) return;
      entry.clipPending = false;
      entry.abortController = null;
      entry.failedIdentity = identity;
      setClipMessage(entry, "Der authentifizierte Ereignisclip konnte nicht geladen werden.", true);
    });
  }

  function operationForCase(caseId) {
    let found = null;
    for (const operation of operations.values()) {
      if (operation.caseId !== caseId) continue;
      if (!["in_flight", "retryable", "conflict", "failed"].includes(operation.state)) continue;
      if (!found || operation.sequence > found.sequence) found = operation;
    }
    return found;
  }

  function bindingOperation(fields, decision) {
    if (!fields || !hasValue(fields.caseId) || !Number.isInteger(fields.version) || typeof fields.contentHash !== "string" || !fields.contentHash) return null;
    return operations.get(keyForBinding(fields.caseId, fields.version, fields.contentHash, decision)) || null;
  }

  function updateButtons() {
    const fields = activeFields;
    const phase = fields?.phase;
    const firstReceipt = fields?.firstReceipt;
    const allowedPhase = ["human_review", "no_response", "reported"].includes(phase);
    const hasFirstReceipt = Boolean(firstReceipt && typeof firstReceipt === "object" && hasValue(firstReceipt.receipt_id));
    const bindingValid = Boolean(fields && hasValue(fields.caseId) && Number.isInteger(fields.version) && typeof fields.contentHash === "string" && fields.contentHash);
    const currentOperation = fields ? operationForCase(fields.caseId) : null;
    const confirmOperation = fields ? bindingOperation(fields, "confirm") : null;
    const denyOperation = fields ? bindingOperation(fields, "reject") : null;
    const completedForBinding = confirmOperation?.state === "succeeded" || denyOperation?.state === "succeeded";
    const blocked = Boolean(completedForBinding || currentOperation && ["in_flight", "retryable", "conflict", "failed"].includes(currentOperation.state));
    const controlsAllowed = phase === "human_review" || hasFirstReceipt && ["no_response", "reported"].includes(phase);
    const canConfirm = phase === "human_review" && !hasFirstReceipt && !confirmOperation;
    const canReject = phase === "human_review" && !hasFirstReceipt || hasFirstReceipt && allowedPhase;

    setHidden(decisionActions, !controlsAllowed);
    confirmButton.disabled = destroyed || !bindingValid || !canConfirm || blocked || confirmOperation?.state === "succeeded";
    denyButton.disabled = destroyed || !bindingValid || !canReject || blocked || denyOperation?.state === "succeeded";
    denyButton.textContent = hasFirstReceipt
      ? "Gefahr verneinen und Entwarnung an Behörden senden"
      : "Gefahr verneinen";
  }

  function setFeedback(message, visible = true) {
    decisionFeedback.textContent = message;
    setHidden(decisionFeedback, !visible);
  }

  function renderOperationState() {
    if (!activeFields) {
      setHidden(decisionFeedback, true);
      setHidden(retryDecisionButton, true);
      setHidden(refreshCaseButton, true);
      updateButtons();
      return;
    }
    const operation = operationForCase(activeFields.caseId);
    setHidden(retryDecisionButton, true);
    setHidden(refreshCaseButton, true);
    if (!operation) {
      setFeedback("", false);
      updateButtons();
      return;
    }
    if (operation.state === "in_flight") {
      setFeedback("Die Entscheidung wird übertragen. Weitere Entscheidungen bleiben bis zur Antwort gesperrt.");
    } else if (operation.state === "retryable") {
      setFeedback("Die Antwort ist nicht angekommen. Es ist unklar, ob der Server den Antrag gespeichert hat. Du kannst exakt denselben Antrag mit derselben Kennung erneut senden.");
      setHidden(retryDecisionButton, false);
      retryDecisionButton.disabled = false;
    } else if (operation.state === "conflict") {
      setFeedback("Der Fall wurde inzwischen geändert. Bitte aktualisiere ihn und prüfe die Entscheidung erneut.");
      setHidden(refreshCaseButton, false);
      refreshCaseButton.disabled = false;
    } else if (operation.state === "failed") {
      setFeedback(operation.message || "Der Server hat die Entscheidung abgelehnt. Bitte aktualisiere den Fall und prüfe ihn erneut.");
      setHidden(refreshCaseButton, false);
      refreshCaseButton.disabled = false;
    } else {
      setFeedback(operation.message || "Entscheidung vom Server angenommen.");
    }
    updateButtons();
  }

  function announcePhase(message) {
    if (notification.textContent !== message) notification.textContent = message;
    if (phaseAnnouncement !== message) {
      phaseAnnouncement = message;
    }
  }

  function renderCaseDetails(fields) {
    caseDetails.replaceChildren();
    setField(caseDetails, "Fall-ID", valueText(fields.caseId));
    setField(caseDetails, "Fallversion", hasValue(fields.version) ? String(fields.version) : "Nicht angegeben");
    setField(caseDetails, "Inhaltshash", valueText(fields.contentHash));
    setField(caseDetails, "Fallphase", phaseLabel(fields.phase));
    if (fields.assessment && typeof fields.assessment === "object") {
      setField(caseDetails, "Objektklasse", valueText(fields.assessment.object_class));
      setField(caseDetails, "Gefahrenstatus", hazardLabel(fields.assessment.hazard));
      if (typeof fields.assessment.confidence === "number" && Number.isFinite(fields.assessment.confidence)) {
        setField(caseDetails, "Vom Modell angegebene Sicherheit", String(fields.assessment.confidence));
      }
      setField(caseDetails, "Zusammenfassung", valueText(fields.assessment.summary));
      const evidenceIds = Array.isArray(fields.assessment.evidence_frame_ids) ? fields.assessment.evidence_frame_ids : [];
      setField(caseDetails, "Belegte Frame-IDs", evidenceIds.length ? evidenceIds.map((item) => valueText(item)).join(", ") : "Keine Frame-IDs im Snapshot vorhanden");
    } else if (hasValue(fields.modelError)) {
      setField(caseDetails, "Modellprüfung", ERROR_LABELS[fields.modelError] || `Modellfehler (${valueText(fields.modelError)}).`);
    }
    if (fields.clip && typeof fields.clip === "object") {
      setField(caseDetails, "Clip-ID", valueText(fields.clip.clip_id));
      setField(caseDetails, "Clipversion", hasValue(fields.clip.version) ? `v${valueText(fields.clip.version)}` : "Nicht angegeben");
      setField(caseDetails, "Clip-Hash", valueText(fields.clip.sha256));
    }
    if (fields.firstReceipt && typeof fields.firstReceipt === "object") {
      setField(caseDetails, "Erstmeldung", valueText(fields.firstReceipt.receipt_id));
      setField(caseDetails, "Meldungsgrund", REASON_LABELS[fields.firstReceipt.reason] || "Meldungsgrund nicht übersetzt");
    }
    if (fields.correctionReceipt && typeof fields.correctionReceipt === "object") {
      setField(caseDetails, "Entwarnung", valueText(fields.correctionReceipt.receipt_id));
      setField(caseDetails, "Verknüpft mit Erstmeldung", valueText(fields.correctionReceipt.original_receipt_id));
    }
  }

  function updateCountdown() {
    if (remainingAtUpdate === null || !Number.isFinite(remainingAtUpdate)) {
      countdownClock.textContent = "-- s";
      return;
    }
    const elapsed = Math.max(0, (performance.now() - remainingAtPerformance) / 1000);
    const remaining = Math.max(0, remainingAtUpdate - elapsed);
    countdownClock.textContent = `${remaining.toFixed(1)} s`;
    countdownClock.classList.toggle("road-badge-danger", remaining <= 5);
  }

  function renderActiveCase(snapshot) {
    if (!snapshot || typeof snapshot !== "object" || !hasValue(snapshot.case_id)) {
      activeViewGeneration += 1;
      refreshGeneration += 1;
      activeCaseId = null;
      activeBinding = null;
      activeFields = null;
      activeSnapshot = null;
      remainingAtUpdate = null;
      activeVideo.dataset.caseId = "";
      setHidden(caseContainer, true);
      setHidden(emptyWorkplaceState, false);
      phaseAnnouncement = "";
      caseBadge.textContent = "Kein offener Fall";
      notification.textContent = "Noch keine Benachrichtigung für eine menschliche Prüfung.";
      phaseBadge.textContent = "Fallstatus nicht angegeben";
      countdownClock.textContent = "-- s";
      deadline.textContent = "Frist: im Snapshot nicht angegeben";
      caseDetails.replaceChildren();
      setFeedback("", false);
      clearClip(activeClip);
      renderOperationState();
      return;
    }

    const fields = policyFields(snapshot);
    const previousCaseId = activeCaseId;
    const previousBinding = activeBinding;
    const nextBinding = {
      caseId: fields.caseId,
      version: fields.version,
      contentHash: fields.contentHash,
    };
    const bindingChanged = !previousBinding ||
      previousBinding.caseId !== nextBinding.caseId ||
      previousBinding.version !== nextBinding.version ||
      previousBinding.contentHash !== nextBinding.contentHash;
    if (bindingChanged) activeViewGeneration += 1;
    if (previousCaseId !== fields.caseId) {
      activeVideo.dataset.caseId = valueText(fields.caseId);
    }
    activeCaseId = fields.caseId;
    activeBinding = nextBinding;
    activeFields = fields;
    activeSnapshot = snapshot;
    setHidden(caseContainer, false);
    setHidden(emptyWorkplaceState, true);
    const translatedPhase = phaseLabel(fields.phase);
    phaseBadge.textContent = translatedPhase;
    caseBadge.textContent = `Fall ${valueText(fields.caseId)} · ${translatedPhase}`;

    if (fields.phase === "human_review") {
      announcePhase("Benachrichtigung: Ein Fall wartet auf deine menschliche Prüfung.");
    } else if (fields.phase === "no_response") {
      announcePhase("Benachrichtigung: Die serverseitige Frist endete ohne Antwort; eine Erstmeldung liegt vor.");
    } else if (fields.phase === "reported") {
      announcePhase("Benachrichtigung: Die Erstmeldung wurde an den simulierten Empfänger gesendet.");
    } else {
      announcePhase(`Fallphase: ${translatedPhase}`);
    }

    const remaining = fields.remainingSeconds;
    remainingAtUpdate = typeof remaining === "number" && Number.isFinite(remaining) ? Math.max(0, remaining) : null;
    remainingAtPerformance = performance.now();
    updateCountdown();
    deadline.textContent = hasValue(fields.deadlineAt)
      ? `Serverfrist endet: ${valueText(fields.deadlineAt)}`
      : "Serverfrist: im Snapshot nicht angegeben";
    renderCaseDetails(fields);
    requestClip(activeClip, fields.clip, {
      case_id: fields.caseId,
      run_id: fields.runId,
      clip: fields.clip,
    });
    renderOperationState();
  }

  function latestOpenOperation(caseId) {
    return operationForCase(caseId);
  }

  function setDecisionResult(operation, error = null) {
    if (error) {
      if (isConflict(error)) operation.state = "conflict";
      else if (isPermanentClientError(error)) operation.state = "failed";
      else operation.state = "retryable";
      operation.message = errorLabel(error);
      operation.errorCode = hasValue(error?.code) ? valueText(error.code) : null;
    } else {
      operation.state = "succeeded";
      operation.message = "Entscheidung vom Server angenommen.";
    }
  }

  async function sendOperation(operation) {
    if (destroyed || operation.state === "in_flight" || operation.state === "succeeded") return;
    operation.state = "in_flight";
    operation.attempts += 1;
    const viewGeneration = activeViewGeneration;
    const startedForCase = activeCaseId === operation.caseId;
    if (startedForCase) renderOperationState();
    try {
      const result = await apiClient.request("POST", operation.path, operation.body);
      if (result && (Number(result.status) >= 400 || result.ok === false)) {
        const error = new Error("decision_response_error");
        error.status = result.status;
        error.code = result.code || result.error_code;
        throw error;
      }
      setDecisionResult(operation);
      if (!destroyed && activeCaseId === operation.caseId && startedForCase) renderOperationState();
      if (
        !destroyed &&
        activeCaseId === operation.caseId &&
        startedForCase &&
        activeViewGeneration === viewGeneration &&
        activeBinding?.version === operation.version &&
        activeBinding?.contentHash === operation.contentHash
      ) {
        container.dispatchEvent(new CustomEvent("roaddecision", {
          bubbles: true,
          detail: { caseId: operation.caseId, decision: operation.decision },
        }));
      }
    } catch (error) {
      setDecisionResult(operation, error);
      if (!destroyed && activeCaseId === operation.caseId) renderOperationState();
    }
  }

  function startDecision(decision, retryExisting = null) {
    if (destroyed) return;
    if (retryExisting) {
      if (retryExisting.state !== "retryable") return;
      void sendOperation(retryExisting);
      return;
    }
    const fields = activeFields;
    if (!fields || !activeBinding || operationForCase(fields.caseId)) return;
    const firstReceipt = fields.firstReceipt && typeof fields.firstReceipt === "object" && hasValue(fields.firstReceipt.receipt_id);
    const allowed = decision === "confirm"
      ? fields.phase === "human_review" && !firstReceipt
      : decision === "reject" && (
        fields.phase === "human_review" && !firstReceipt ||
        firstReceipt && ["human_review", "no_response", "reported"].includes(fields.phase)
      );
    if (!allowed) return;
    const existing = bindingOperation(fields, decision);
    if (existing) {
      if (existing.state === "retryable") void sendOperation(existing);
      return;
    }

    let key;
    try {
      key = idempotencyKey();
    } catch {
      setFeedback("Für diese Entscheidung konnte keine sichere Kennung erstellt werden. Bitte lade den Fall erneut.");
      setHidden(refreshCaseButton, false);
      return;
    }
    const body = Object.freeze({
      version: activeBinding.version,
      content_hash: activeBinding.contentHash,
      decision,
      idempotency_key: key,
    });
    const operation = {
      caseId: activeBinding.caseId,
      version: activeBinding.version,
      contentHash: activeBinding.contentHash,
      decision,
      path: `/road/cases/${encodeURIComponent(activeBinding.caseId)}/decision`,
      body,
      sequence: ++operationSequence,
      attempts: 0,
      state: "created",
      message: "",
    };
    operations.set(keyForBinding(operation.caseId, operation.version, operation.contentHash, operation.decision), operation);
    void sendOperation(operation);
  }

  async function refreshCurrentCase() {
    if (destroyed || !activeCaseId) return;
    const caseId = activeCaseId;
    const generation = activeViewGeneration;
    const requestGeneration = ++refreshGeneration;
    refreshCaseButton.disabled = true;
    setFeedback("Der aktuelle Fall wird vom Server neu geladen.");
    try {
      const fresh = await apiClient.request("GET", `/road/cases/${encodeURIComponent(caseId)}`);
      if (
        destroyed ||
        refreshGeneration !== requestGeneration ||
        activeCaseId !== caseId ||
        activeViewGeneration !== generation
      ) return;
      if (!fresh || typeof fresh !== "object" || fresh.case_id !== caseId) {
        throw new Error("case_refresh_invalid_snapshot");
      }
      const fields = policyFields(fresh);
      for (const operation of operations.values()) {
        if (
          operation.caseId === caseId &&
          (operation.state === "conflict" || operation.state === "failed") &&
          (operation.version !== fields.version || operation.contentHash !== fields.contentHash)
        ) operation.state = "superseded";
      }
      renderActiveCase(fresh);
      refreshCaseButton.disabled = false;
    } catch {
      if (!destroyed && activeCaseId === caseId) {
        refreshCaseButton.disabled = false;
        setFeedback("Der Fall konnte nicht aktualisiert werden. Bitte versuche es erneut.");
      }
    }
  }

  function reasonText(record) {
    return REASON_LABELS[record.reason] || "Meldungsgrund im Snapshot nicht übersetzt";
  }

  function buildInboxEntry(key, record) {
    const card = element("article", "road-inbox-card");
    card.dataset.receiptId = valueText(record.receipt_id, key);
    card.setAttribute("role", "listitem");
    const heading = element("header", "road-inbox-card-header");
    const title = element("h4", "road-inbox-card-title");
    const receipt = element("span", "road-badge");
    heading.append(title, receipt);
    const reason = element("p", "road-inbox-reason");
    const receiptLink = element("p", "road-inbox-receipt-link");
    const hash = element("p", "road-inbox-hash");
    const assessment = element("div", "road-inbox-assessment");
    const clipShell = element("div", "road-video-container road-inbox-video-container");
    const video = document.createElement("video");
    video.className = "road-video-player";
    video.controls = true;
    video.playsInline = true;
    video.preload = "metadata";
    clipShell.appendChild(video);
    const clipStatus = element("p", "road-model-clip-status", "Kein Clip in der Meldung vorhanden.");
    clipStatus.setAttribute("role", "status");
    const retryButton = element("button", "road-btn road-btn-secondary", "Clip erneut laden");
    retryButton.type = "button";
    retryButton.dataset.roadAction = "retry-inbox-clip";
    setHidden(retryButton, true);
    const openCaseButton = element("button", "road-btn road-btn-primary", "Fall öffnen");
    openCaseButton.type = "button";
    openCaseButton.dataset.roadAction = "open-case";
    const clipEntry = {
      video,
      clipStatus,
      retryButton,
      clipIdentity: null,
      failedIdentity: null,
      clipGeneration: 0,
      clipPending: false,
      abortController: null,
      metadataListener: null,
      errorListener: null,
      objectUrl: null,
      sourceRecord: null,
      isMounted: true,
    };
    retryButton.addEventListener("click", () => {
      const current = entry.record;
      if (current) requestClip(clipEntry, current.clip, current, true);
    });
    openCaseButton.addEventListener("click", () => {
      const current = entry.record;
      if (!destroyed && current && hasValue(current.case_id)) {
        container.dispatchEvent(new CustomEvent("road-open-case", {
          bubbles: true,
          detail: { caseId: current.case_id },
        }));
      }
    });
    const entry = {
      key,
      card,
      heading,
      title,
      receipt,
      reason,
      receiptLink,
      hash,
      assessment,
      clipShell,
      clipEntry,
      clipStatus,
      retryButton,
      openCaseButton,
      record,
    };
    card.append(heading, reason, receiptLink, hash, assessment, clipShell, clipStatus, retryButton, openCaseButton);
    return entry;
  }

  function renderInboxEntry(entry, record) {
    entry.record = record;
    const correction = record.kind === "correction" || hasValue(record.original_receipt_id);
    entry.card.dataset.kind = correction ? "correction" : "first_report";
    entry.card.classList.toggle("correction", correction);
    entry.title.textContent = correction
      ? `Entwarnung zu Fall ${valueText(record.case_id)}`
      : `Erstmeldung zu Fall ${valueText(record.case_id)}`;
    entry.receipt.textContent = hasValue(record.receipt_id) ? `Meldungs-ID: ${valueText(record.receipt_id)}` : "Meldung";
    entry.reason.textContent = correction
      ? "Grund: Der Fall wurde nach der Erstmeldung menschlich korrigiert."
      : `Grund: ${reasonText(record)}`;
    entry.receiptLink.textContent = correction && hasValue(record.original_receipt_id)
      ? `Verknüpft mit ursprünglicher Erstmeldung ${valueText(record.original_receipt_id)}.`
      : "";
    setHidden(entry.receiptLink, !(correction && hasValue(record.original_receipt_id)));
    entry.hash.textContent = hasValue(record.content_hash) ? `Gebundener Fallinhalt: ${valueText(record.content_hash)}` : "Fallinhaltshash nicht angegeben.";
    entry.assessment.replaceChildren();
    if (record.assessment && typeof record.assessment === "object") {
      setField(entry.assessment, "Objektklasse", valueText(record.assessment.object_class));
      setField(entry.assessment, "Gefahrenstatus", hazardLabel(record.assessment.hazard));
      if (typeof record.assessment.confidence === "number" && Number.isFinite(record.assessment.confidence)) {
        setField(entry.assessment, "Vom Modell angegebene Sicherheit", String(record.assessment.confidence));
      }
      setField(entry.assessment, "Zusammenfassung", valueText(record.assessment.summary));
    }

    const isFirstReport = record.kind === "first_report" && !correction;
    setHidden(entry.clipShell, !isFirstReport);
    setHidden(entry.clipStatus, !isFirstReport);
    setHidden(entry.retryButton, !isFirstReport || !entry.clipEntry.failedIdentity);
    setHidden(entry.openCaseButton, !isFirstReport);
    entry.openCaseButton.disabled = !isFirstReport || !hasValue(record.case_id);
    entry.openCaseButton.dataset.caseId = isFirstReport ? valueText(record.case_id, "") : "";
    if (isFirstReport) requestClip(entry.clipEntry, record.clip, record);
    else clearClip(entry.clipEntry, "Für diese Korrektur gibt es keinen eigenen Clip.");
  }

  function updateInbox(inbox) {
    const records = new Map();
    if (Array.isArray(inbox)) {
      for (const record of inbox) {
        if (!record || typeof record !== "object") continue;
        const key = hasValue(record.receipt_id)
          ? valueText(record.receipt_id)
          : `${valueText(record.kind, "record")}:${valueText(record.case_id, "case")}:${valueText(record.content_hash, "hash")}`;
        records.set(key, record);
      }
    }
    for (const [key, entry] of inboxEntries) {
      if (!records.has(key)) {
        entry.clipEntry.isMounted = false;
        cancelClipWork(entry.clipEntry);
        detachMedia(entry.clipEntry);
        entry.card.remove();
        inboxEntries.delete(key);
      }
    }
    if (records.size === 0) {
      if (emptyInbox.parentNode !== inboxList) inboxList.appendChild(emptyInbox);
      return;
    }
    if (emptyInbox.parentNode === inboxList) emptyInbox.remove();
    const ordered = [];
    for (const [key, record] of records) {
      let entry = inboxEntries.get(key);
      if (!entry) {
        entry = buildInboxEntry(key, record);
        inboxEntries.set(key, entry);
      }
      renderInboxEntry(entry, record);
      ordered.push(entry);
    }
    let cursor = inboxList.firstElementChild;
    for (const entry of ordered) {
      if (entry.card === cursor) cursor = cursor.nextElementSibling;
      else inboxList.insertBefore(entry.card, cursor);
    }
  }

  confirmButton.addEventListener("click", () => startDecision("confirm"));
  denyButton.addEventListener("click", () => startDecision("reject"));
  retryDecisionButton.addEventListener("click", () => {
    if (activeFields) {
      const operation = latestOpenOperation(activeFields.caseId);
      if (operation?.state === "retryable") startDecision(operation.decision, operation);
    }
  });
  refreshCaseButton.addEventListener("click", () => void refreshCurrentCase());
  retryActiveClip.addEventListener("click", () => {
    if (activeFields) requestClip(activeClip, activeFields.clip, { case_id: activeFields.caseId, run_id: activeFields.runId }, true);
  });

  const countdownTimer = globalThis.setInterval(updateCountdown, 250);

  function update(caseSnapshot, inbox) {
    if (destroyed) return;
    if (Array.isArray(inbox)) latestInbox = inbox;
    renderActiveCase(caseSnapshot);
    updateInbox(latestInbox);
  }

  function destroy() {
    if (destroyed) return;
    destroyed = true;
    globalThis.clearInterval(countdownTimer);
    activeViewGeneration += 1;
    refreshGeneration += 1;
    activeClip.isMounted = false;
    cancelClipWork(activeClip);
    detachMedia(activeClip);
    for (const entry of inboxEntries.values()) {
      entry.clipEntry.isMounted = false;
      cancelClipWork(entry.clipEntry);
      detachMedia(entry.clipEntry);
    }
    inboxEntries.clear();
    operations.clear();
    blobRequests.clear();
    container.replaceChildren();
  }

  // Programmatic decision for another view (the mission-control game). It only
  // acts when exactly this case is bound here and the same button would be
  // enabled, so a caller can never decide a different case by accident.
  function decide(caseId, decision) {
    if (destroyed) return { started: false, reason: "destroyed" };
    if (!activeFields || activeFields.caseId !== caseId) return { started: false, reason: "case_not_open" };
    const control = decision === "confirm" ? confirmButton : decision === "reject" ? denyButton : null;
    if (!control) return { started: false, reason: "invalid_decision" };
    if (control.disabled || decisionActions.hidden) return { started: false, reason: "not_allowed" };
    startDecision(decision);
    return { started: true, reason: null };
  }

  function getDecisionState(caseId) {
    if (!activeFields || activeFields.caseId !== caseId) return { open: false, canConfirm: false, canReject: false };
    const visible = !decisionActions.hidden;
    const operation = operationForCase(caseId);
    return {
      open: true,
      canConfirm: visible && !confirmButton.disabled,
      canReject: visible && !denyButton.disabled,
      rejectLabel: denyButton.textContent,
      operationState: operation ? operation.state : null,
      operationMessage: operation ? operation.message : "",
    };
  }

  return { update, destroy, decide, getDecisionState };
}
