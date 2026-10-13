const L = (de, en) => window.VisionFlowI18n ? window.VisionFlowI18n.L(de, en) : en;
const PHASE_LABELS = Object.freeze({
  initializing: L("Fall wird vorbereitet", "Case is being prepared"),
  starting: L("Fallstart läuft", "Case start in progress"),
  running: L("Verkehr wird ausgewertet", "Traffic is being analyzed"),
  model_review: L("Modellprüfung läuft", "Model review in progress"),
  normal: L("Keine Auffälligkeit festgestellt", "No anomaly detected"),
  source_ended: L("Quelle beendet", "Source ended"),
  human_review: L("Menschliche Prüfung erforderlich", "Human review required"),
  no_response: L("Keine Antwort bis zum Fristende", "No answer by the deadline"),
  reported: L("Erstmeldung gesendet", "First report sent"),
  corrected: L("Entwarnung gesendet", "All-clear sent"),
  rejected: L("Gefahr verneint", "Hazard denied"),
  dismissed: L("Kein Gefahrverdacht", "No suspected hazard"),
  cancelled: L("Fall abgebrochen", "Case cancelled"),
});

const REASON_LABELS = Object.freeze({
  high_confidence: L("Automatische Erstmeldung nach bestätigter hoher Sicherheit und Belegen", "Automatic first report after confirmed high confidence and evidence"),
  human_confirmed: L("Erstmeldung nach menschlicher Bestätigung", "First report after human confirmation"),
  no_response: L("Erstmeldung nach Ablauf der serverseitigen Prüfzeit ohne Antwort", "First report after the server-side review time expired without an answer"),
});

const ERROR_LABELS = Object.freeze({
  model_timeout: L("Die Modellanfrage hat das Zeitlimit erreicht.", "The model request reached its time limit."),
  invalid_json: L("Die Modellantwort ließ sich nicht als gültige Bewertung lesen.", "The model answer could not be read as a valid assessment."),
  invalid_evidence: L("Die Modellantwort verwies auf ungültige Belege.", "The model answer referred to invalid evidence."),
  stale_version: L("Der Fall wurde inzwischen geändert. Bitte aktualisiere ihn und prüfe die Entscheidung erneut.", "The case has been changed in the meantime. Please refresh it and review the decision again."),
  stale_content_hash: L("Der Fallinhalt wurde inzwischen geändert. Bitte aktualisiere ihn und prüfe die Entscheidung erneut.", "The case content has been changed in the meantime. Please refresh it and review the decision again."),
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

function valueText(value, fallback = L("Nicht angegeben", "Not specified")) {
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
  if (hazard === true) return L("Ja", "Yes");
  if (hazard === false) return L("Nein", "No");
  return L("Unklar", "Unclear");
}

function phaseLabel(phase) {
  return PHASE_LABELS[phase] || (hasValue(phase) ? L(`Unbekannter Fallstatus (${valueText(phase)})`, `Unknown case status (${valueText(phase)})`) : L("Fallstatus nicht angegeben", "Case status not specified"));
}

function errorLabel(error) {
  const code = typeof error === "string" ? error : error?.code;
  if (hasValue(code) && ERROR_LABELS[code]) return ERROR_LABELS[code];
  const status = Number(error?.status);
  if (status === 409) return L("Der Fall wurde inzwischen geändert. Bitte aktualisiere ihn und prüfe die Entscheidung erneut.", "The case has been changed in the meantime. Please refresh it and review the decision again.");
  if (status >= 400 && status < 500) return L("Der Server hat die Entscheidung abgelehnt. Bitte aktualisiere den Fall und prüfe ihn erneut.", "The server rejected the decision. Please refresh the case and review it again.");
  return L("Die Antwort des Servers ist nicht angekommen. Der gleiche Antrag kann mit derselben Kennung erneut gesendet werden.", "The server's response did not arrive. The same request can be sent again with the same identifier.");
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
  const title = element("h2", "road-panel-title", L("Menschlicher Arbeitsplatz und Demo-Postfach", "Human workplace and demo inbox"));
  const caseBadge = element("span", "road-panel-meta", L("Kein offener Fall", "No open case"));
  caseBadge.setAttribute("role", "status");
  caseBadge.setAttribute("aria-live", "polite");
  header.append(title, caseBadge);

  const office = element("section", "road-office-scene");
  office.setAttribute("aria-label", L("Büroarbeitsplatz mit Laptop", "Office workplace with laptop"));
  const deskCaption = element("p", "road-office-decor", L("Schreibtisch – menschliche Fallprüfung", "Desk – human case review"));
  const laptopWrapper = element("div", "road-laptop-wrapper");
  const screenFrame = element("div", "road-laptop-screen-frame");
  const screen = element("section", "road-laptop-screen-content");
  screen.setAttribute("role", "region");
  screen.setAttribute("aria-label", L("Einsatz-Arbeitsplatz auf dem Laptop", "Dispatch workplace on the laptop"));

  const notification = element("div", "road-countdown-banner");
  notification.setAttribute("role", "status");
  notification.setAttribute("aria-live", "polite");
  notification.textContent = L("Noch keine Benachrichtigung für eine menschliche Prüfung.", "No notification for a human review yet.");
  screen.appendChild(notification);

  const emptyWorkplaceState = element("p", "road-empty-state", L("Warten auf einen Fall zur menschlichen Prüfung.", "Waiting for a case for human review."));
  screen.appendChild(emptyWorkplaceState);

  const caseContainer = element("div", "road-case-container");
  setHidden(caseContainer, true);
  const caseHeader = element("header", "road-panel-header");
  const caseTitle = element("h3", "road-panel-title", L("Fallzusammenfassung", "Case summary"));
  const phaseBadge = element("span", "road-badge", L("Fallstatus nicht angegeben", "Case status not specified"));
  phaseBadge.setAttribute("role", "status");
  phaseBadge.setAttribute("aria-live", "polite");
  caseHeader.append(caseTitle, phaseBadge);
  caseContainer.appendChild(caseHeader);

  const countdown = element("div", "road-countdown-banner road-case-countdown");
  const countdownTitle = element("strong", "road-countdown-title", L("Serverfrist für die menschliche Prüfung", "Server deadline for the human review"));
  const countdownClock = element("span", "road-countdown-clock", "-- s");
  countdownClock.setAttribute("aria-live", "off");
  const deadline = element("span", "road-case-deadline", L("Frist: im Snapshot nicht angegeben", "Deadline: not specified in the snapshot"));
  countdown.append(countdownTitle, countdownClock, deadline);
  caseContainer.appendChild(countdown);

  const activeVideoContainer = element("div", "road-video-container road-case-video-container");
  const activeVideo = document.createElement("video");
  activeVideo.className = "road-video-player";
  activeVideo.controls = true;
  activeVideo.playsInline = true;
  activeVideo.preload = "metadata";
  activeVideo.setAttribute("aria-label", L("Vollständiger Ereignisclip zur menschlichen Fallprüfung", "Full event clip for the human case review"));
  activeVideoContainer.appendChild(activeVideo);
  const activeClipStatus = element("p", "road-model-clip-status", L("Kein Clip im Snapshot vorhanden.", "No clip in the snapshot."));
  activeClipStatus.setAttribute("role", "status");
  const retryActiveClip = element("button", "road-btn road-btn-secondary", L("Clip erneut laden", "Reload clip"));
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
  const confirmButton = element("button", "road-btn road-btn-danger", L("Gefahr bestätigen und Behörden informieren", "Confirm hazard and notify authorities"));
  confirmButton.type = "button";
  confirmButton.id = "btn-operator-confirm";
  confirmButton.disabled = true;
  const denyButton = element("button", "road-btn road-btn-secondary", L("Gefahr verneinen", "Deny hazard"));
  denyButton.type = "button";
  denyButton.id = "btn-operator-deny";
  denyButton.disabled = true;
  decisionActions.append(confirmButton, denyButton);
  caseContainer.appendChild(decisionActions);

  const retryDecisionButton = element("button", "road-btn road-btn-secondary", L("Gleiche Entscheidung erneut senden", "Resend the same decision"));
  retryDecisionButton.type = "button";
  retryDecisionButton.dataset.roadAction = "retry-decision";
  setHidden(retryDecisionButton, true);
  caseContainer.appendChild(retryDecisionButton);
  const refreshCaseButton = element("button", "road-btn road-btn-secondary", L("Fall aktualisieren", "Refresh case"));
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
  inboxSection.setAttribute("aria-label", L("Simulierter Behördeneingang", "Simulated authority inbox"));
  const inboxTitle = element("h3", "road-panel-title", L("Demo-Postfach des Behördenempfängers", "Demo inbox of the authority recipient"));
  const disclaimer = element(
    "p",
    "road-simulation-disclaimer",
    L("Simulierter Empfänger: Meldungen bleiben in dieser lokalen Demo. Es wird keine Behörde kontaktiert.", "Simulated recipient: reports stay in this local demo. No authority is contacted."),
  );
  const inboxList = element("div", "road-inbox-list");
  inboxList.setAttribute("role", "list");
  inboxList.setAttribute("aria-label", L("Erstmeldungen und Entwarnungen", "First reports and all-clears"));
  const emptyInbox = element("p", "road-empty-state", L("Im Demo-Postfach ist noch keine Meldung eingegangen.", "No report has arrived in the demo inbox yet."));
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
      if (!(blob instanceof Blob)) throw new TypeError(L("Die Cliproute lieferte keine Blob-Daten.", "The clip route returned no blob data."));
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

  function clearClip(entry, message = L("Kein Clip im Snapshot vorhanden.", "No clip in the snapshot.")) {
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
      else setClipMessage(entry, L("Kein Clip im Snapshot vorhanden.", "No clip in the snapshot."));
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
    entry.video.setAttribute("aria-label", L(`Vollständiger Ereignisclip ${valueText(clip.clip_id)} zur menschlichen Prüfung`, `Full event clip ${valueText(clip.clip_id)} for human review`));
    setClipMessage(entry, L("Authentifizierter Ereignisclip wird geladen.", "Authenticated event clip is loading."));

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
        setClipMessage(entry, L("Clip geladen. Native Videosteuerung ist verfügbar.", "Clip loaded. Native video controls are available."));
      };
      const onError = () => {
        if (destroyed || entry.clipGeneration !== generation) return;
        entry.failedIdentity = identity;
        setClipMessage(entry, L("Der Ereignisclip konnte nicht geöffnet werden.", "The event clip could not be opened."), true);
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
      setClipMessage(entry, L("Der authentifizierte Ereignisclip konnte nicht geladen werden.", "The authenticated event clip could not be loaded."), true);
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
      ? L("Gefahr verneinen und Entwarnung an Behörden senden", "Deny hazard and send all-clear to authorities")
      : L("Gefahr verneinen", "Deny hazard");
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
      setFeedback(L("Die Entscheidung wird übertragen. Weitere Entscheidungen bleiben bis zur Antwort gesperrt.", "The decision is being transmitted. Further decisions stay locked until the response arrives."));
    } else if (operation.state === "retryable") {
      setFeedback(L("Die Antwort ist nicht angekommen. Es ist unklar, ob der Server den Antrag gespeichert hat. Du kannst exakt denselben Antrag mit derselben Kennung erneut senden.", "The response did not arrive. It is unclear whether the server stored the request. You can resend exactly the same request with the same identifier."));
      setHidden(retryDecisionButton, false);
      retryDecisionButton.disabled = false;
    } else if (operation.state === "conflict") {
      setFeedback(L("Der Fall wurde inzwischen geändert. Bitte aktualisiere ihn und prüfe die Entscheidung erneut.", "The case has been changed in the meantime. Please refresh it and review the decision again."));
      setHidden(refreshCaseButton, false);
      refreshCaseButton.disabled = false;
    } else if (operation.state === "failed") {
      setFeedback(operation.message || L("Der Server hat die Entscheidung abgelehnt. Bitte aktualisiere den Fall und prüfe ihn erneut.", "The server rejected the decision. Please refresh the case and review it again."));
      setHidden(refreshCaseButton, false);
      refreshCaseButton.disabled = false;
    } else {
      setFeedback(operation.message || L("Entscheidung vom Server angenommen.", "Decision accepted by the server."));
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
    setField(caseDetails, L("Fall-ID", "Case ID"), valueText(fields.caseId));
    setField(caseDetails, L("Fallversion", "Case version"), hasValue(fields.version) ? String(fields.version) : L("Nicht angegeben", "Not specified"));
    setField(caseDetails, L("Inhaltshash", "Content hash"), valueText(fields.contentHash));
    setField(caseDetails, L("Fallphase", "Case phase"), phaseLabel(fields.phase));
    if (fields.assessment && typeof fields.assessment === "object") {
      setField(caseDetails, L("Objektklasse", "Object class"), valueText(fields.assessment.object_class));
      setField(caseDetails, L("Gefahrenstatus", "Hazard status"), hazardLabel(fields.assessment.hazard));
      if (typeof fields.assessment.confidence === "number" && Number.isFinite(fields.assessment.confidence)) {
        setField(caseDetails, L("Vom Modell angegebene Sicherheit", "Confidence reported by the model"), String(fields.assessment.confidence));
      }
      setField(caseDetails, L("Zusammenfassung", "Summary"), valueText(fields.assessment.summary));
      const evidenceIds = Array.isArray(fields.assessment.evidence_frame_ids) ? fields.assessment.evidence_frame_ids : [];
      setField(caseDetails, L("Belegte Frame-IDs", "Evidenced frame IDs"), evidenceIds.length ? evidenceIds.map((item) => valueText(item)).join(", ") : L("Keine Frame-IDs im Snapshot vorhanden", "No frame IDs in the snapshot"));
    } else if (hasValue(fields.modelError)) {
      setField(caseDetails, L("Modellprüfung", "Model review"), ERROR_LABELS[fields.modelError] || L(`Modellfehler (${valueText(fields.modelError)}).`, `Model error (${valueText(fields.modelError)}).`));
    }
    if (fields.clip && typeof fields.clip === "object") {
      setField(caseDetails, L("Clip-ID", "Clip ID"), valueText(fields.clip.clip_id));
      setField(caseDetails, L("Clipversion", "Clip version"), hasValue(fields.clip.version) ? `v${valueText(fields.clip.version)}` : L("Nicht angegeben", "Not specified"));
      setField(caseDetails, L("Clip-Hash", "Clip hash"), valueText(fields.clip.sha256));
    }
    if (fields.firstReceipt && typeof fields.firstReceipt === "object") {
      setField(caseDetails, L("Erstmeldung", "First report"), valueText(fields.firstReceipt.receipt_id));
      setField(caseDetails, L("Meldungsgrund", "Report reason"), REASON_LABELS[fields.firstReceipt.reason] || L("Meldungsgrund nicht übersetzt", "Report reason not translated"));
    }
    if (fields.correctionReceipt && typeof fields.correctionReceipt === "object") {
      setField(caseDetails, L("Entwarnung", "All-clear"), valueText(fields.correctionReceipt.receipt_id));
      setField(caseDetails, L("Verknüpft mit Erstmeldung", "Linked to first report"), valueText(fields.correctionReceipt.original_receipt_id));
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
      caseBadge.textContent = L("Kein offener Fall", "No open case");
      notification.textContent = L("Noch keine Benachrichtigung für eine menschliche Prüfung.", "No notification for a human review yet.");
      phaseBadge.textContent = L("Fallstatus nicht angegeben", "Case status not specified");
      countdownClock.textContent = "-- s";
      deadline.textContent = L("Frist: im Snapshot nicht angegeben", "Deadline: not specified in the snapshot");
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
    caseBadge.textContent = L(`Fall ${valueText(fields.caseId)} · ${translatedPhase}`, `Case ${valueText(fields.caseId)} · ${translatedPhase}`);

    if (fields.phase === "human_review") {
      announcePhase(L("Benachrichtigung: Ein Fall wartet auf deine menschliche Prüfung.", "Notification: a case is waiting for your human review."));
    } else if (fields.phase === "no_response") {
      announcePhase(L("Benachrichtigung: Die serverseitige Frist endete ohne Antwort; eine Erstmeldung liegt vor.", "Notification: the server-side deadline ended without an answer; a first report exists."));
    } else if (fields.phase === "reported") {
      announcePhase(L("Benachrichtigung: Die Erstmeldung wurde an den simulierten Empfänger gesendet.", "Notification: the first report was sent to the simulated recipient."));
    } else {
      announcePhase(L(`Fallphase: ${translatedPhase}`, `Case phase: ${translatedPhase}`));
    }

    const remaining = fields.remainingSeconds;
    remainingAtUpdate = typeof remaining === "number" && Number.isFinite(remaining) ? Math.max(0, remaining) : null;
    remainingAtPerformance = performance.now();
    updateCountdown();
    deadline.textContent = hasValue(fields.deadlineAt)
      ? L(`Serverfrist endet: ${valueText(fields.deadlineAt)}`, `Server deadline ends: ${valueText(fields.deadlineAt)}`)
      : L("Serverfrist: im Snapshot nicht angegeben", "Server deadline: not specified in the snapshot");
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
      operation.message = L("Entscheidung vom Server angenommen.", "Decision accepted by the server.");
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
      setFeedback(L("Für diese Entscheidung konnte keine sichere Kennung erstellt werden. Bitte lade den Fall erneut.", "No secure identifier could be created for this decision. Please reload the case."));
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
    setFeedback(L("Der aktuelle Fall wird vom Server neu geladen.", "The current case is being reloaded from the server."));
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
        setFeedback(L("Der Fall konnte nicht aktualisiert werden. Bitte versuche es erneut.", "The case could not be refreshed. Please try again."));
      }
    }
  }

  function reasonText(record) {
    return REASON_LABELS[record.reason] || L("Meldungsgrund im Snapshot nicht übersetzt", "Report reason not translated in the snapshot");
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
    const clipStatus = element("p", "road-model-clip-status", L("Kein Clip in der Meldung vorhanden.", "No clip in the report."));
    clipStatus.setAttribute("role", "status");
    const retryButton = element("button", "road-btn road-btn-secondary", L("Clip erneut laden", "Reload clip"));
    retryButton.type = "button";
    retryButton.dataset.roadAction = "retry-inbox-clip";
    setHidden(retryButton, true);
    const openCaseButton = element("button", "road-btn road-btn-primary", L("Fall öffnen", "Open case"));
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
      ? L(`Entwarnung zu Fall ${valueText(record.case_id)}`, `All-clear for case ${valueText(record.case_id)}`)
      : L(`Erstmeldung zu Fall ${valueText(record.case_id)}`, `First report for case ${valueText(record.case_id)}`);
    entry.receipt.textContent = hasValue(record.receipt_id) ? L(`Meldungs-ID: ${valueText(record.receipt_id)}`, `Report ID: ${valueText(record.receipt_id)}`) : L("Meldung", "Report");
    entry.reason.textContent = correction
      ? L("Grund: Der Fall wurde nach der Erstmeldung menschlich korrigiert.", "Reason: the case was corrected by a human after the first report.")
      : L(`Grund: ${reasonText(record)}`, `Reason: ${reasonText(record)}`);
    entry.receiptLink.textContent = correction && hasValue(record.original_receipt_id)
      ? L(`Verknüpft mit ursprünglicher Erstmeldung ${valueText(record.original_receipt_id)}.`, `Linked to the original first report ${valueText(record.original_receipt_id)}.`)
      : "";
    setHidden(entry.receiptLink, !(correction && hasValue(record.original_receipt_id)));
    entry.hash.textContent = hasValue(record.content_hash) ? L(`Gebundener Fallinhalt: ${valueText(record.content_hash)}`, `Bound case content: ${valueText(record.content_hash)}`) : L("Fallinhaltshash nicht angegeben.", "Case content hash not specified.");
    entry.assessment.replaceChildren();
    if (record.assessment && typeof record.assessment === "object") {
      setField(entry.assessment, L("Objektklasse", "Object class"), valueText(record.assessment.object_class));
      setField(entry.assessment, L("Gefahrenstatus", "Hazard status"), hazardLabel(record.assessment.hazard));
      if (typeof record.assessment.confidence === "number" && Number.isFinite(record.assessment.confidence)) {
        setField(entry.assessment, L("Vom Modell angegebene Sicherheit", "Confidence reported by the model"), String(record.assessment.confidence));
      }
      setField(entry.assessment, L("Zusammenfassung", "Summary"), valueText(record.assessment.summary));
    }

    const isFirstReport = record.kind === "first_report" && !correction;
    setHidden(entry.clipShell, !isFirstReport);
    setHidden(entry.clipStatus, !isFirstReport);
    setHidden(entry.retryButton, !isFirstReport || !entry.clipEntry.failedIdentity);
    setHidden(entry.openCaseButton, !isFirstReport);
    entry.openCaseButton.disabled = !isFirstReport || !hasValue(record.case_id);
    entry.openCaseButton.dataset.caseId = isFirstReport ? valueText(record.case_id, "") : "";
    if (isFirstReport) requestClip(entry.clipEntry, record.clip, record);
    else clearClip(entry.clipEntry, L("Für diese Korrektur gibt es keinen eigenen Clip.", "This correction has no clip of its own."));
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
