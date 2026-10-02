const STATUS_LABELS = Object.freeze({
  pending: "Wird geprüft",
  complete: "Abgeschlossen",
  failed: "Fehlgeschlagen",
  cancelled: "Abgebrochen",
});

const ERROR_LABELS = Object.freeze({
  model_timeout: "Die Modellanfrage hat das Zeitlimit erreicht.",
  invalid_json: "Die Modellantwort ließ sich nicht als gültige Bewertung lesen.",
  invalid_evidence: "Die Modellantwort verwies auf ungültige Belege.",
  cancelled: "Die Modellprüfung wurde abgebrochen.",
});

const MAX_EVIDENCE_IMAGES = 8;
const MAX_EVIDENCE_REF_LENGTH = 2048;

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

function displayValue(value, fallback = "Nicht angegeben") {
  if (!hasValue(value)) return fallback;
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return fallback;
  }
}

function statusLabel(status) {
  return STATUS_LABELS[status] || (hasValue(status) ? `Unbekannter Status (${displayValue(status)})` : "Status nicht angegeben");
}

function errorLabel(code) {
  if (!hasValue(code)) return "";
  const safeCode = displayValue(code);
  return ERROR_LABELS[safeCode] || `Modellfehler (${safeCode}).`;
}

function modeLabel(mode) {
  if (mode === "fixture") return "Offline-Testantwort (fixture)";
  if (mode === "gemini_video") return "Gemini Video (gemini_video)";
  return hasValue(mode) ? `Modus (${displayValue(mode)})` : "Im Snapshot nicht angegeben";
}

function hazardLabel(hazard) {
  if (hazard === true) return "Ja";
  if (hazard === false) return "Nein";
  return "Unklar";
}

function clipIdentity(clip) {
  if (!clip || !hasValue(clip.clip_id)) return null;
  const hash = hasValue(clip.sha256) ? displayValue(clip.sha256) : null;
  const version = hasValue(clip.version) ? displayValue(clip.version) : "";
  return hash ? `sha256:${hash}` : `clip:${displayValue(clip.clip_id)}:v${version}`;
}

function clipFetchKey(clip) {
  const hash = hasValue(clip?.sha256) ? displayValue(clip.sha256) : null;
  return hash || `${displayValue(clip?.clip_id, "clip")}:v${displayValue(clip?.version, "")}`;
}

function appendField(parent, label, value, className = "road-model-field-value") {
  const row = element("div", "road-model-field");
  const name = element("strong", "road-model-field-label", `${label}: `);
  const content = element("span", className, value);
  row.append(name, content);
  parent.appendChild(row);
  return content;
}

function validExchange(record) {
  return Boolean(record && typeof record === "object" && typeof record.exchange_id === "string" && record.exchange_id.length > 0);
}

function imageEvidenceRefs(exchange) {
  const source = Array.isArray(exchange?.evidence_refs) ? exchange.evidence_refs : [];
  const refs = [];
  const seen = new Set();
  for (const reference of source) {
    if (
      typeof reference !== "string" ||
      reference.trim().length === 0 ||
      reference.length > MAX_EVIDENCE_REF_LENGTH ||
      seen.has(reference)
    ) continue;
    seen.add(reference);
    if (refs.length === MAX_EVIDENCE_IMAGES) return { refs, limited: true };
    refs.push(reference);
  }
  return { refs, limited: false };
}

export function mountRoadModel(container, apiClient) {
  if (!container || typeof container.replaceChildren !== "function") {
    throw new TypeError("container muss ein DOM-Element sein.");
  }
  if (!apiClient || typeof apiClient.getBytes !== "function") {
    throw new TypeError("apiClient.getBytes muss verfügbar sein.");
  }

  let destroyed = false;
  const entries = new Map();
  const blobRequests = new Map();

  const header = element("header", "road-panel-header");
  const title = element("h2", "road-panel-title", "Modellprüfung und Ereignisclips");
  const viewStatus = element("span", "road-panel-meta", "Warten auf Modellprüfungen");
  viewStatus.setAttribute("role", "status");
  viewStatus.setAttribute("aria-live", "polite");
  header.append(title, viewStatus);

  const list = element("div", "road-model-list");
  list.setAttribute("role", "list");
  list.setAttribute("aria-label", "Modellprüfungen");
  const emptyState = element(
    "p",
    "road-empty-state",
    "Noch liegt keine Modellprüfung vor. Sobald ein Ereignisclip geprüft wird, erscheinen hier der tatsächliche Prompt, die Belege und die Modellantwort.",
  );
  list.appendChild(emptyState);
  container.replaceChildren(header, list);

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
    entry.metadataListener && entry.video.removeEventListener("loadedmetadata", entry.metadataListener);
    entry.errorListener && entry.video.removeEventListener("error", entry.errorListener);
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
    setHidden(entry.retryClip, !isError);
    entry.retryClip.disabled = destroyed;
  }

  function setEvidenceGalleryMessage(entry, message, isError = false) {
    entry.evidenceGalleryStatus.textContent = message;
    entry.evidenceGalleryStatus.classList.toggle("road-status-error", isError);
    setHidden(entry.evidenceGalleryStatus, !message);
  }

  function releaseEvidenceImageUrl(resource) {
    const image = resource.image;
    resource.image = null;
    if (image) {
      image.removeAttribute("src");
      image.remove();
    }
    if (resource.objectUrl) URL.revokeObjectURL(resource.objectUrl);
    resource.objectUrl = null;
  }

  function disposeEvidenceImage(resource) {
    resource.generation += 1;
    resource.state = "removed";
    releaseEvidenceImageUrl(resource);
    resource.figure.remove();
  }

  function isCurrentEvidenceImage(entry, resource, generation) {
    return Boolean(
      !destroyed &&
      generation === resource.generation &&
      entry.evidenceRunId === resource.runId &&
      entry.evidenceImages.get(resource.reference) === resource &&
      entries.get(entry.exchangeId) === entry,
    );
  }

  function setEvidenceImageError(entry, resource, generation, message) {
    if (!isCurrentEvidenceImage(entry, resource, generation)) return;
    resource.state = "failed";
    releaseEvidenceImageUrl(resource);
    resource.status.textContent = message;
    resource.status.classList.add("road-status-error");
    resource.retry.disabled = false;
    setHidden(resource.retry, false);
  }

  function requestEvidenceImage(entry, resource) {
    if (
      destroyed ||
      entries.get(entry.exchangeId) !== entry ||
      entry.evidenceRunId !== resource.runId ||
      entry.evidenceImages.get(resource.reference) !== resource ||
      resource.state === "pending" ||
      resource.state === "decoding" ||
      resource.state === "loaded"
    ) return;

    resource.generation += 1;
    const generation = resource.generation;
    resource.state = "pending";
    releaseEvidenceImageUrl(resource);
    resource.status.textContent = "Authentifizierter Bildbeleg wird geladen.";
    resource.status.classList.remove("road-status-error");
    resource.retry.disabled = true;
    setHidden(resource.retry, true);

    const path = "/runs/" + encodeURIComponent(resource.runId) +
      "/evidence?ref=" + encodeURIComponent(resource.reference);
    Promise.resolve()
      .then(() => apiClient.getBytes(path))
      .then((blob) => {
        if (!isCurrentEvidenceImage(entry, resource, generation)) return;
        const mime = blob instanceof Blob && typeof blob.type === "string"
          ? blob.type.trim().toLowerCase()
          : "";
        if (mime !== "image/png" && mime !== "image/jpeg") {
          setEvidenceImageError(
            entry,
            resource,
            generation,
            "Bildbelegformat nicht unterstützt. Erlaubt sind PNG und JPEG.",
          );
          return;
        }

        const objectUrl = URL.createObjectURL(blob);
        const image = element("img", "road-model-evidence-image-preview");
        image.alt = "Gespeicherter Bildbeleg " + resource.reference;
        image.addEventListener("load", () => {
          if (!isCurrentEvidenceImage(entry, resource, generation) || resource.image !== image) return;
          if (image.naturalWidth <= 0) {
            setEvidenceImageError(
              entry,
              resource,
              generation,
              "Bildbeleg konnte nicht dargestellt werden. Bitte erneut laden.",
            );
            return;
          }
          resource.state = "loaded";
          resource.status.textContent = "Bildbeleg geladen.";
          resource.status.classList.remove("road-status-error");
          resource.retry.disabled = false;
        }, { once: true });
        image.addEventListener("error", () => {
          setEvidenceImageError(
            entry,
            resource,
            generation,
            "Bildbeleg konnte nicht dargestellt werden. Bitte erneut laden.",
          );
        }, { once: true });

        resource.objectUrl = objectUrl;
        resource.image = image;
        resource.state = "decoding";
        resource.figure.insertBefore(image, resource.caption);
        image.src = objectUrl;
      })
      .catch(() => {
        setEvidenceImageError(
          entry,
          resource,
          generation,
          "Authentifizierter Bildbeleg konnte nicht geladen werden. Bitte erneut laden.",
        );
      });
  }

  function createEvidenceImage(entry, runId, reference) {
    const figure = element("figure", "evidence-frame road-model-evidence-image");
    const caption = element("figcaption", "road-model-evidence-image-caption", "Bildbeleg: " + reference);
    const status = element("p", "road-model-evidence-image-status", "Authentifizierter Bildbeleg wird geladen.");
    status.setAttribute("role", "status");
    status.setAttribute("aria-live", "polite");
    const retry = element("button", "road-btn road-btn-secondary", "Bild erneut laden");
    retry.type = "button";
    retry.dataset.roadAction = "retry-evidence-image";
    retry.setAttribute("aria-label", "Bildbeleg " + reference + " erneut laden");
    setHidden(retry, true);
    const resource = {
      runId,
      reference,
      figure,
      caption,
      status,
      retry,
      image: null,
      objectUrl: null,
      generation: 0,
      state: "idle",
    };
    figure.append(caption, status, retry);
    retry.addEventListener("click", () => {
      if (isCurrentEvidenceImage(entry, resource, resource.generation)) requestEvidenceImage(entry, resource);
    });
    return resource;
  }

  function syncEvidenceImages(entry, exchange) {
    const runId = typeof exchange.run_id === "string" && exchange.run_id.trim()
      ? exchange.run_id
      : null;
    const { refs, limited } = imageEvidenceRefs(exchange);

    if (entry.evidenceRunId !== runId) {
      for (const resource of entry.evidenceImages.values()) disposeEvidenceImage(resource);
      entry.evidenceImages.clear();
      entry.evidenceRunId = runId;
    }

    const wanted = new Set(refs);
    for (const [reference, resource] of entry.evidenceImages) {
      if (!runId || !wanted.has(reference)) {
        disposeEvidenceImage(resource);
        entry.evidenceImages.delete(reference);
      }
    }

    if (!runId) {
      setEvidenceGalleryMessage(entry, "Bildbelege können ohne Lauf-ID nicht geladen werden.", true);
      return;
    }
    if (refs.length === 0) {
      setEvidenceGalleryMessage(entry, "Keine Bildbelege im Snapshot vorhanden.");
      return;
    }
    setEvidenceGalleryMessage(
      entry,
      limited ? "Es werden höchstens " + MAX_EVIDENCE_IMAGES + " eindeutige Bildbelege angezeigt." : "",
    );

    const created = [];
    const ordered = refs.map((reference) => {
      let resource = entry.evidenceImages.get(reference);
      if (!resource) {
        resource = createEvidenceImage(entry, runId, reference);
        entry.evidenceImages.set(reference, resource);
        created.push(resource);
      }
      return resource;
    });
    for (let index = 0; index < ordered.length; index += 1) {
      const figure = ordered[index].figure;
      const current = entry.evidenceImageList.children[index] || null;
      if (figure !== current) entry.evidenceImageList.insertBefore(figure, current);
    }
    for (const resource of created) requestEvidenceImage(entry, resource);
  }

  function clearEvidenceImages(entry) {
    for (const resource of entry.evidenceImages.values()) disposeEvidenceImage(resource);
    entry.evidenceImages.clear();
    entry.evidenceRunId = null;
    entry.evidenceImageList.replaceChildren();
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

  function requestClip(entry, clip, record, force = false) {
    const identity = clipIdentity(clip);
    if (!identity) {
      if (entry.clipIdentity !== null || entry.objectUrl || entry.clipPending) clearClip(entry);
      else setClipMessage(entry, "Kein Clip im Snapshot vorhanden.");
      return;
    }
    const scopedIdentity = `${displayValue(record.case_id, "case")}:${identity}`;
    if (!force && entry.clipIdentity === scopedIdentity && (entry.objectUrl || entry.clipPending || entry.failedIdentity === scopedIdentity)) {
      entry.sourceRecord = record;
      return;
    }

    const previousRecord = entry.sourceRecord;
    const previousTime = Number.isFinite(entry.video.currentTime) ? entry.video.currentTime : 0;
    const preserveTime = Boolean(
      previousRecord &&
      hasValue(previousRecord.case_id) &&
      previousRecord.case_id === record.case_id &&
      hasValue(previousRecord.run_id) &&
      previousRecord.run_id === record.run_id &&
      previousTime > 0,
    );

    cancelClipWork(entry);
    detachMedia(entry);
    entry.clipIdentity = scopedIdentity;
    entry.failedIdentity = null;
    entry.clipPending = true;
    entry.sourceRecord = record;
    const generation = entry.clipGeneration;
    const abortController = new AbortController();
    entry.abortController = abortController;
    entry.video.dataset.clipId = displayValue(clip.clip_id);
    entry.video.dataset.clipHash = displayValue(clip.sha256, "");
    entry.video.setAttribute("aria-label", `Vollständiger Ereignisclip ${displayValue(clip.clip_id)} zur Modellprüfung`);
    setClipMessage(entry, "Authentifizierter Ereignisclip wird geladen.");

    getBlob(clip).then((blob) => {
      if (
        destroyed ||
        abortController.signal.aborted ||
        entry.clipGeneration !== generation ||
        entry.clipIdentity !== scopedIdentity ||
        entries.get(entry.exchangeId) !== entry
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
            // The metadata event is authoritative; a non-seekable clip remains at its start.
          }
        }
        setClipMessage(entry, "Clip geladen. Native Videosteuerung ist verfügbar.");
      };
      const onError = () => {
        if (destroyed || entry.clipGeneration !== generation) return;
        entry.failedIdentity = scopedIdentity;
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
      if (destroyed || abortController.signal.aborted || entry.clipGeneration !== generation || entry.clipIdentity !== scopedIdentity) return;
      entry.clipPending = false;
      entry.abortController = null;
      entry.failedIdentity = scopedIdentity;
      setClipMessage(entry, "Der authentifizierte Ereignisclip konnte nicht geladen werden.", true);
    });
  }

  function buildEntry(exchangeId) {
    const card = element("article", "road-model-exchange");
    card.dataset.exchangeId = exchangeId;
    card.setAttribute("role", "listitem");

    const cardHeader = element("header", "road-panel-header");
    const name = element("h3", "road-panel-title", `Modellprüfung ${exchangeId}`);
    const status = element("span", "road-badge", "Status nicht angegeben");
    status.setAttribute("role", "status");
    status.setAttribute("aria-live", "polite");
    cardHeader.append(name, status);

    const grid = element("div", "road-model-grid");
    const left = element("div", "road-model-media-column");
    const playerShell = element("div", "road-video-container");
    const video = document.createElement("video");
    video.className = "road-video-player";
    video.controls = true;
    video.playsInline = true;
    video.preload = "metadata";
    video.setAttribute("aria-label", `Vollständiger Ereignisclip zur Modellprüfung ${exchangeId}`);
    playerShell.appendChild(video);
    left.appendChild(playerShell);
    const clipStatus = element("p", "road-model-clip-status", "Kein Clip im Snapshot vorhanden.");
    clipStatus.setAttribute("role", "status");
    const retryClip = element("button", "road-btn road-btn-secondary", "Clip erneut laden");
    retryClip.type = "button";
    retryClip.dataset.roadAction = "retry-clip";
    setHidden(retryClip, true);
    left.append(clipStatus, retryClip);
    const clipFacts = element("div", "road-inspector-box road-model-clip-facts");
    left.appendChild(clipFacts);

    const right = element("div", "road-model-details-column");
    const modelFacts = element("div", "road-inspector-box road-model-facts");
    const evidence = element("pre", "road-stream-output road-model-evidence");
    const prompt = element("pre", "road-stream-output road-model-prompt");
    const deltas = element("pre", "road-stream-output road-model-deltas");
    const rawOutput = element("pre", "road-stream-output road-model-raw-output");
    const assessment = element("div", "road-inspector-box road-model-assessment");
    right.append(modelFacts);
    right.append(element("h4", "road-model-section-title", "Echte Belegreferenzen und Frame-IDs"), evidence);
    const evidenceGallery = element("section", "road-model-evidence-gallery");
    evidenceGallery.setAttribute("aria-label", "Bildbelege aus dem Modelllauf");
    const evidenceGalleryTitle = element("h4", "road-model-section-title", "Übergebene Bildbelege");
    const evidenceGalleryStatus = element("p", "road-model-evidence-gallery-status", "Keine Bildbelege im Snapshot vorhanden.");
    evidenceGalleryStatus.setAttribute("role", "status");
    evidenceGalleryStatus.setAttribute("aria-live", "polite");
    const evidenceImageList = element("div", "road-model-evidence-images");
    evidenceGallery.append(evidenceGalleryTitle, evidenceGalleryStatus, evidenceImageList);
    right.append(evidenceGallery);
    right.append(element("h4", "road-model-section-title", "Tatsächlicher Modell-Prompt"), prompt);
    right.append(element("h4", "road-model-section-title", "Antwort-Deltas"), deltas);
    right.append(element("h4", "road-model-section-title", "Unveränderte Modellantwort"), rawOutput);
    right.append(element("h4", "road-model-section-title", "Validierte Bewertung"), assessment);
    grid.append(left, right);
    card.append(cardHeader, grid);

    const entry = {
      exchangeId,
      card,
      name,
      status,
      video,
      clipStatus,
      retryClip,
      clipFacts,
      modelFacts,
      evidence,
      prompt,
      deltas,
      rawOutput,
      assessment,
      evidenceGalleryStatus,
      evidenceImageList,
      evidenceRunId: null,
      evidenceImages: new Map(),
      exchange: null,
      sourceRecord: null,
      clipIdentity: null,
      failedIdentity: null,
      clipGeneration: 0,
      clipPending: false,
      abortController: null,
      metadataListener: null,
      errorListener: null,
      objectUrl: null,
    };
    retryClip.addEventListener("click", () => {
      if (!destroyed && entry.exchange) requestClip(entry, entry.exchange.clip, entry.exchange, true);
    });
    return entry;
  }

  function renderExchange(entry, exchange) {
    entry.exchange = exchange;
    entry.name.textContent = `Modellprüfung ${displayValue(exchange.exchange_id)}`;
    const status = statusLabel(exchange.status);
    if (entry.status.textContent !== status) entry.status.textContent = status;

    const clip = exchange.clip && typeof exchange.clip === "object" ? exchange.clip : null;
    entry.clipFacts.replaceChildren();
    appendField(entry.clipFacts, "Modus", modeLabel(exchange.mode));
    appendField(entry.clipFacts, "Clip-ID", displayValue(clip?.clip_id));
    appendField(entry.clipFacts, "Clipversion", hasValue(clip?.version) ? `v${displayValue(clip.version)}` : "Nicht angegeben");
    appendField(entry.clipFacts, "SHA-256", displayValue(clip?.sha256));
    appendField(entry.clipFacts, "Aufnahmebildrate", hasValue(clip?.source_fps) ? `${displayValue(clip.source_fps)} fps` : "Nicht angegeben");
    appendField(entry.clipFacts, "Verarbeitungsbildrate", hasValue(exchange.processing_fps) ? `${displayValue(exchange.processing_fps)} fps` : "Nicht angegeben");
    appendField(entry.clipFacts, "Clipdauer", hasValue(clip?.duration_seconds)
      ? `${displayValue(clip.duration_seconds)} s`
      : hasValue(clip?.start_seconds) && hasValue(clip?.end_seconds)
        ? `${(Number(clip.end_seconds) - Number(clip.start_seconds)).toFixed(2)} s`
        : "Nicht angegeben");

    entry.modelFacts.replaceChildren();
    appendField(entry.modelFacts, "Modell-ID", displayValue(exchange.model_id));
    appendField(entry.modelFacts, "Fall-ID", displayValue(exchange.case_id));
    appendField(entry.modelFacts, "Lauf-ID", displayValue(exchange.run_id));
    appendField(entry.modelFacts, "Austausch-ID", displayValue(exchange.exchange_id));

    const evidenceValues = [];
    if (Array.isArray(exchange.evidence_refs)) evidenceValues.push(...exchange.evidence_refs);
    if (Array.isArray(exchange.assessment?.evidence_frame_ids)) evidenceValues.push(...exchange.assessment.evidence_frame_ids);
    if (Array.isArray(clip?.frame_ids)) evidenceValues.push(...clip.frame_ids);
    const uniqueEvidence = [...new Set(evidenceValues.map((item) => displayValue(item)))];
    entry.evidence.textContent = uniqueEvidence.length ? uniqueEvidence.join("\n") : "Keine Belegreferenzen im Snapshot vorhanden.";
    syncEvidenceImages(entry, exchange);

    entry.prompt.textContent = hasValue(exchange.prompt) ? displayValue(exchange.prompt) : "Kein Prompt im Snapshot vorhanden.";
    const deltaText = Array.isArray(exchange.deltas)
      ? exchange.deltas.map((item) => displayValue(item, "")).join("")
      : "";
    entry.deltas.textContent = deltaText || "Keine Antwort-Deltas im Snapshot vorhanden.";
    entry.rawOutput.textContent = hasValue(exchange.raw_output)
      ? displayValue(exchange.raw_output)
      : "Keine Rohantwort im Snapshot vorhanden.";

    entry.assessment.replaceChildren();
    const modelError = exchange.error_code;
    if (hasValue(modelError)) {
      const error = element("p", "road-status-error", errorLabel(modelError));
      const errorCode = element("code", "road-model-error-code", `Fehlercode: ${displayValue(modelError)}`);
      entry.assessment.append(error, errorCode);
    }
    if (exchange.assessment && typeof exchange.assessment === "object") {
      const result = exchange.assessment;
      appendField(entry.assessment, "Objektklasse", displayValue(result.object_class));
      appendField(entry.assessment, "Gefahrenstatus", hazardLabel(result.hazard));
      if (typeof result.confidence === "number" && Number.isFinite(result.confidence)) {
        appendField(entry.assessment, "Vom Modell angegebene Sicherheit", String(result.confidence));
      } else {
        appendField(entry.assessment, "Sicherheit", "Im Snapshot nicht angegeben");
      }
      appendField(entry.assessment, "Zusammenfassung", displayValue(result.summary));
      if (result.decision && typeof result.decision === "object") {
        appendField(entry.assessment, "Validierte Folgeaktion", displayValue(result.decision.action));
      }
    } else if (!hasValue(modelError)) {
      entry.assessment.textContent = "Noch liegt keine validierte Bewertung vor.";
    }

    if (!clip) clearClip(entry, "Kein Clip im Snapshot vorhanden.");
    else requestClip(entry, clip, exchange);
  }

  function update(exchanges) {
    if (destroyed) return;
    const records = new Map();
    if (Array.isArray(exchanges)) {
      for (const exchange of exchanges) if (validExchange(exchange)) records.set(exchange.exchange_id, exchange);
    }

    for (const [exchangeId, entry] of entries) {
      if (!records.has(exchangeId)) {
        cancelClipWork(entry);
        detachMedia(entry);
        clearEvidenceImages(entry);
        entry.card.remove();
        entries.delete(exchangeId);
      }
    }

    if (records.size === 0) {
      if (emptyState.parentNode !== list) list.appendChild(emptyState);
      viewStatus.textContent = "Keine Modellprüfungen vorhanden";
      return;
    }

    if (emptyState.parentNode === list) emptyState.remove();
    viewStatus.textContent = `${records.size} Modellprüfung${records.size === 1 ? "" : "en"}`;
    const ordered = [];
    for (const [exchangeId, exchange] of records) {
      let entry = entries.get(exchangeId);
      if (!entry) {
        entry = buildEntry(exchangeId);
        entries.set(exchangeId, entry);
      }
      renderExchange(entry, exchange);
      ordered.push(entry);
    }

    let cursor = list.firstElementChild;
    for (const entry of ordered) {
      if (entry.card === cursor) cursor = cursor.nextElementSibling;
      else list.insertBefore(entry.card, cursor);
    }
  }

  function destroy() {
    if (destroyed) return;
    destroyed = true;
    for (const entry of entries.values()) {
      cancelClipWork(entry);
      detachMedia(entry);
      clearEvidenceImages(entry);
    }
    entries.clear();
    blobRequests.clear();
    container.replaceChildren();
  }

  return { update, destroy };
}
