const L = (de, en) => window.VisionFlowI18n ? window.VisionFlowI18n.L(de, en) : en;

const STATUS_LABELS = Object.freeze({
  pending: L("Wird geprüft", "Under review"),
  complete: L("Abgeschlossen", "Completed"),
  failed: L("Fehlgeschlagen", "Failed"),
  cancelled: L("Abgebrochen", "Cancelled"),
});

const ERROR_LABELS = Object.freeze({
  model_timeout: L("Die Modellanfrage hat das Zeitlimit erreicht.", "The model request reached its time limit."),
  invalid_json: L("Die Modellantwort ließ sich nicht als gültige Bewertung lesen.", "The model response could not be read as a valid assessment."),
  invalid_evidence: L("Die Modellantwort verwies auf ungültige Belege.", "The model response referenced invalid evidence."),
  cancelled: L("Die Modellprüfung wurde abgebrochen.", "The model review was cancelled."),
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

function displayValue(value, fallback = L("Nicht angegeben", "Not specified")) {
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
  return STATUS_LABELS[status] || (hasValue(status) ? L(`Unbekannter Status (${displayValue(status)})`, `Unknown status (${displayValue(status)})`) : L("Status nicht angegeben", "Status not specified"));
}

function errorLabel(code) {
  if (!hasValue(code)) return "";
  const safeCode = displayValue(code);
  return ERROR_LABELS[safeCode] || L(`Modellfehler (${safeCode}).`, `Model error (${safeCode}).`);
}

function modeLabel(mode) {
  if (mode === "fixture") return L("Offline-Testantwort (fixture)", "Offline test response (fixture)");
  if (mode === "gemini_video") return "Gemini Video (gemini_video)";
  return hasValue(mode) ? L(`Modus (${displayValue(mode)})`, `Mode (${displayValue(mode)})`) : L("Im Snapshot nicht angegeben", "Not specified in snapshot");
}

function hazardLabel(hazard) {
  if (hazard === true) return L("Ja", "Yes");
  if (hazard === false) return L("Nein", "No");
  return L("Unklar", "Unclear");
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
    throw new TypeError(L("container muss ein DOM-Element sein.", "container must be a DOM element."));
  }
  if (!apiClient || typeof apiClient.getBytes !== "function") {
    throw new TypeError(L("apiClient.getBytes muss verfügbar sein.", "apiClient.getBytes must be available."));
  }

  let destroyed = false;
  const entries = new Map();
  const blobRequests = new Map();

  const header = element("header", "road-panel-header");
  const title = element("h2", "road-panel-title", L("Modellprüfung und Ereignisclips", "Model review and event clips"));
  const viewStatus = element("span", "road-panel-meta", L("Warten auf Modellprüfungen", "Waiting for model reviews"));
  viewStatus.setAttribute("role", "status");
  viewStatus.setAttribute("aria-live", "polite");
  header.append(title, viewStatus);

  const list = element("div", "road-model-list");
  list.setAttribute("role", "list");
  list.setAttribute("aria-label", L("Modellprüfungen", "Model reviews"));
  const emptyState = element(
    "p",
    "road-empty-state",
    L("Noch liegt keine Modellprüfung vor. Sobald ein Ereignisclip geprüft wird, erscheinen hier der tatsächliche Prompt, die Belege und die Modellantwort.", "No model review yet. As soon as an event clip is reviewed, the actual prompt, the evidence and the model response appear here."),
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
    resource.status.textContent = L("Authentifizierter Bildbeleg wird geladen.", "Loading authenticated image evidence.");
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
            L("Bildbelegformat nicht unterstützt. Erlaubt sind PNG und JPEG.", "Image evidence format not supported. Only PNG and JPEG are allowed."),
          );
          return;
        }

        const objectUrl = URL.createObjectURL(blob);
        const image = element("img", "road-model-evidence-image-preview");
        image.alt = L("Gespeicherter Bildbeleg ", "Stored image evidence ") + resource.reference;
        image.addEventListener("load", () => {
          if (!isCurrentEvidenceImage(entry, resource, generation) || resource.image !== image) return;
          if (image.naturalWidth <= 0) {
            setEvidenceImageError(
              entry,
              resource,
              generation,
              L("Bildbeleg konnte nicht dargestellt werden. Bitte erneut laden.", "Image evidence could not be displayed. Please reload."),
            );
            return;
          }
          resource.state = "loaded";
          resource.status.textContent = L("Bildbeleg geladen.", "Image evidence loaded.");
          resource.status.classList.remove("road-status-error");
          resource.retry.disabled = false;
        }, { once: true });
        image.addEventListener("error", () => {
          setEvidenceImageError(
            entry,
            resource,
            generation,
            L("Bildbeleg konnte nicht dargestellt werden. Bitte erneut laden.", "Image evidence could not be displayed. Please reload."),
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
          L("Authentifizierter Bildbeleg konnte nicht geladen werden. Bitte erneut laden.", "Authenticated image evidence could not be loaded. Please reload."),
        );
      });
  }

  function createEvidenceImage(entry, runId, reference) {
    const figure = element("figure", "evidence-frame road-model-evidence-image");
    const caption = element("figcaption", "road-model-evidence-image-caption", L("Bildbeleg: ", "Image evidence: ") + reference);
    const status = element("p", "road-model-evidence-image-status", L("Authentifizierter Bildbeleg wird geladen.", "Loading authenticated image evidence."));
    status.setAttribute("role", "status");
    status.setAttribute("aria-live", "polite");
    const retry = element("button", "road-btn road-btn-secondary", L("Bild erneut laden", "Reload image"));
    retry.type = "button";
    retry.dataset.roadAction = "retry-evidence-image";
    retry.setAttribute("aria-label", L("Bildbeleg " + reference + " erneut laden", "Reload image evidence " + reference));
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
      setEvidenceGalleryMessage(entry, L("Bildbelege können ohne Lauf-ID nicht geladen werden.", "Image evidence cannot be loaded without a run ID."), true);
      return;
    }
    if (refs.length === 0) {
      setEvidenceGalleryMessage(entry, L("Keine Bildbelege im Snapshot vorhanden.", "No image evidence in the snapshot."));
      return;
    }
    setEvidenceGalleryMessage(
      entry,
      limited ? L("Es werden höchstens " + MAX_EVIDENCE_IMAGES + " eindeutige Bildbelege angezeigt.", "At most " + MAX_EVIDENCE_IMAGES + " unique image evidence items are shown.") : "",
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

  function requestClip(entry, clip, record, force = false) {
    const identity = clipIdentity(clip);
    if (!identity) {
      if (entry.clipIdentity !== null || entry.objectUrl || entry.clipPending) clearClip(entry);
      else setClipMessage(entry, L("Kein Clip im Snapshot vorhanden.", "No clip in the snapshot."));
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
    entry.video.setAttribute("aria-label", L(`Vollständiger Ereignisclip ${displayValue(clip.clip_id)} zur Modellprüfung`, `Full event clip ${displayValue(clip.clip_id)} for model review`));
    setClipMessage(entry, L("Authentifizierter Ereignisclip wird geladen.", "Loading authenticated event clip."));

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
        setClipMessage(entry, L("Clip geladen. Native Videosteuerung ist verfügbar.", "Clip loaded. Native video controls are available."));
      };
      const onError = () => {
        if (destroyed || entry.clipGeneration !== generation) return;
        entry.failedIdentity = scopedIdentity;
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
      if (destroyed || abortController.signal.aborted || entry.clipGeneration !== generation || entry.clipIdentity !== scopedIdentity) return;
      entry.clipPending = false;
      entry.abortController = null;
      entry.failedIdentity = scopedIdentity;
      setClipMessage(entry, L("Der authentifizierte Ereignisclip konnte nicht geladen werden.", "The authenticated event clip could not be loaded."), true);
    });
  }

  function buildEntry(exchangeId) {
    const card = element("article", "road-model-exchange");
    card.dataset.exchangeId = exchangeId;
    card.setAttribute("role", "listitem");

    const cardHeader = element("header", "road-panel-header");
    const name = element("h3", "road-panel-title", L(`Modellprüfung ${exchangeId}`, `Model review ${exchangeId}`));
    const status = element("span", "road-badge", L("Status nicht angegeben", "Status not specified"));
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
    video.setAttribute("aria-label", L(`Vollständiger Ereignisclip zur Modellprüfung ${exchangeId}`, `Full event clip for model review ${exchangeId}`));
    playerShell.appendChild(video);
    left.appendChild(playerShell);
    const clipStatus = element("p", "road-model-clip-status", L("Kein Clip im Snapshot vorhanden.", "No clip in the snapshot."));
    clipStatus.setAttribute("role", "status");
    const retryClip = element("button", "road-btn road-btn-secondary", L("Clip erneut laden", "Reload clip"));
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
    right.append(element("h4", "road-model-section-title", L("Echte Belegreferenzen und Frame-IDs", "Actual evidence references and frame IDs")), evidence);
    const evidenceGallery = element("section", "road-model-evidence-gallery");
    evidenceGallery.setAttribute("aria-label", L("Bildbelege aus dem Modelllauf", "Image evidence from the model run"));
    const evidenceGalleryTitle = element("h4", "road-model-section-title", L("Übergebene Bildbelege", "Submitted image evidence"));
    const evidenceGalleryStatus = element("p", "road-model-evidence-gallery-status", L("Keine Bildbelege im Snapshot vorhanden.", "No image evidence in the snapshot."));
    evidenceGalleryStatus.setAttribute("role", "status");
    evidenceGalleryStatus.setAttribute("aria-live", "polite");
    const evidenceImageList = element("div", "road-model-evidence-images");
    evidenceGallery.append(evidenceGalleryTitle, evidenceGalleryStatus, evidenceImageList);
    right.append(evidenceGallery);
    right.append(element("h4", "road-model-section-title", L("Tatsächlicher Modell-Prompt", "Actual model prompt")), prompt);
    right.append(element("h4", "road-model-section-title", L("Antwort-Deltas", "Response deltas")), deltas);
    right.append(element("h4", "road-model-section-title", L("Unveränderte Modellantwort", "Unmodified model response")), rawOutput);
    right.append(element("h4", "road-model-section-title", L("Validierte Bewertung", "Validated assessment")), assessment);
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
    entry.name.textContent = L(`Modellprüfung ${displayValue(exchange.exchange_id)}`, `Model review ${displayValue(exchange.exchange_id)}`);
    const status = statusLabel(exchange.status);
    if (entry.status.textContent !== status) entry.status.textContent = status;

    const clip = exchange.clip && typeof exchange.clip === "object" ? exchange.clip : null;
    entry.clipFacts.replaceChildren();
    appendField(entry.clipFacts, L("Modus", "Mode"), modeLabel(exchange.mode));
    appendField(entry.clipFacts, L("Clip-ID", "Clip ID"), displayValue(clip?.clip_id));
    appendField(entry.clipFacts, L("Clipversion", "Clip version"), hasValue(clip?.version) ? `v${displayValue(clip.version)}` : L("Nicht angegeben", "Not specified"));
    appendField(entry.clipFacts, "SHA-256", displayValue(clip?.sha256));
    appendField(entry.clipFacts, L("Aufnahmebildrate", "Capture frame rate"), hasValue(clip?.source_fps) ? `${displayValue(clip.source_fps)} fps` : L("Nicht angegeben", "Not specified"));
    appendField(entry.clipFacts, L("Verarbeitungsbildrate", "Processing frame rate"), hasValue(exchange.processing_fps) ? `${displayValue(exchange.processing_fps)} fps` : L("Nicht angegeben", "Not specified"));
    appendField(entry.clipFacts, L("Clipdauer", "Clip duration"), hasValue(clip?.duration_seconds)
      ? `${displayValue(clip.duration_seconds)} s`
      : hasValue(clip?.start_seconds) && hasValue(clip?.end_seconds)
        ? `${(Number(clip.end_seconds) - Number(clip.start_seconds)).toFixed(2)} s`
        : L("Nicht angegeben", "Not specified"));

    entry.modelFacts.replaceChildren();
    appendField(entry.modelFacts, L("Modell-ID", "Model ID"), displayValue(exchange.model_id));
    appendField(entry.modelFacts, L("Fall-ID", "Case ID"), displayValue(exchange.case_id));
    appendField(entry.modelFacts, L("Lauf-ID", "Run ID"), displayValue(exchange.run_id));
    appendField(entry.modelFacts, L("Austausch-ID", "Exchange ID"), displayValue(exchange.exchange_id));

    const evidenceValues = [];
    if (Array.isArray(exchange.evidence_refs)) evidenceValues.push(...exchange.evidence_refs);
    if (Array.isArray(exchange.assessment?.evidence_frame_ids)) evidenceValues.push(...exchange.assessment.evidence_frame_ids);
    if (Array.isArray(clip?.frame_ids)) evidenceValues.push(...clip.frame_ids);
    const uniqueEvidence = [...new Set(evidenceValues.map((item) => displayValue(item)))];
    entry.evidence.textContent = uniqueEvidence.length ? uniqueEvidence.join("\n") : L("Keine Belegreferenzen im Snapshot vorhanden.", "No evidence references in the snapshot.");
    syncEvidenceImages(entry, exchange);

    entry.prompt.textContent = hasValue(exchange.prompt) ? displayValue(exchange.prompt) : L("Kein Prompt im Snapshot vorhanden.", "No prompt in the snapshot.");
    const deltaText = Array.isArray(exchange.deltas)
      ? exchange.deltas.map((item) => displayValue(item, "")).join("")
      : "";
    entry.deltas.textContent = deltaText || L("Keine Antwort-Deltas im Snapshot vorhanden.", "No response deltas in the snapshot.");
    entry.rawOutput.textContent = hasValue(exchange.raw_output)
      ? displayValue(exchange.raw_output)
      : L("Keine Rohantwort im Snapshot vorhanden.", "No raw response in the snapshot.");

    entry.assessment.replaceChildren();
    const modelError = exchange.error_code;
    if (hasValue(modelError)) {
      const error = element("p", "road-status-error", errorLabel(modelError));
      const errorCode = element("code", "road-model-error-code", L(`Fehlercode: ${displayValue(modelError)}`, `Error code: ${displayValue(modelError)}`));
      entry.assessment.append(error, errorCode);
    }
    if (exchange.assessment && typeof exchange.assessment === "object") {
      const result = exchange.assessment;
      appendField(entry.assessment, L("Objektklasse", "Object class"), displayValue(result.object_class));
      appendField(entry.assessment, L("Gefahrenstatus", "Hazard status"), hazardLabel(result.hazard));
      if (typeof result.confidence === "number" && Number.isFinite(result.confidence)) {
        appendField(entry.assessment, L("Vom Modell angegebene Sicherheit", "Confidence reported by the model"), String(result.confidence));
      } else {
        appendField(entry.assessment, L("Sicherheit", "Confidence"), L("Im Snapshot nicht angegeben", "Not specified in snapshot"));
      }
      appendField(entry.assessment, L("Zusammenfassung", "Summary"), displayValue(result.summary));
      if (result.decision && typeof result.decision === "object") {
        appendField(entry.assessment, L("Validierte Folgeaktion", "Validated follow-up action"), displayValue(result.decision.action));
      }
    } else if (!hasValue(modelError)) {
      entry.assessment.textContent = L("Noch liegt keine validierte Bewertung vor.", "No validated assessment yet.");
    }

    if (!clip) clearClip(entry, L("Kein Clip im Snapshot vorhanden.", "No clip in the snapshot."));
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
      viewStatus.textContent = L("Keine Modellprüfungen vorhanden", "No model reviews");
      return;
    }

    if (emptyState.parentNode === list) emptyState.remove();
    viewStatus.textContent = L(`${records.size} Modellprüfung${records.size === 1 ? "" : "en"}`, `${records.size} model review${records.size === 1 ? "" : "s"}`);
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
