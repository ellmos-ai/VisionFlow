/**
 * VisionFlow Emergency Leitstelle – Interactive Browser Game Engine
 * Inspired by Emergency / Bibi Blocksberg / Benjamin Blümchen Rettungszentrale
 * 
 * Manages 4 Rooms:
 *   Room 1: 🚦 Verkehrsraumüberwachung (B4 Nord & Techniker Alex)
 *   Room 2: 🏢 AI-Rechenzentrum (Serverraum & Dr. Kira)
 *   Room 3: 🚓 Polizeileitstelle Alpha (Officer Schmidt & Funk)
 *   Room 4: 🚨 Polizeileitstelle Beta (Officer Weber abwesend, echte Frist)
 *
 * All case data (tickets, verdicts, clips, hashes, deadlines) comes from the
 * local backend via app.js; the game never invents results.
 */
(function (global) {
  "use strict";
  const L = (de, en) => window.VisionFlowI18n ? window.VisionFlowI18n.L(de, en) : en;

  // --- Labels -----------------------------------------------------------------
  // Every ticket, verdict, hash, clip and deadline shown by the game comes from
  // the local backend (case snapshots, model exchanges, clip bytes). The game
  // only adds the cartoon presentation around that data.
  const EVENT_LABELS = {
    wrong_way: L("⚠️ Falschfahrer", "⚠️ Wrong-way driver"),
    bird: L("🐦 Vogel", "🐦 Bird"),
    leaf: L("🍂 Blatt", "🍂 Leaf"),
  };
  const PHASE_LABELS = {
    starting: L("Fall startet", "Case starting"),
    running: L("Analyse läuft", "Analysis running"),
    model_review: L("Modellprüfung", "Model review"),
    human_review: L("Menschliche Prüfung offen", "Human review pending"),
    no_response: L("Frist abgelaufen · automatisch gemeldet", "Deadline expired · reported automatically"),
    reported: L("An Behörde gemeldet", "Reported to authority"),
    dismissed: L("Entwarnung durch Modell", "All-clear by model"),
    rejected: L("Als Fehlalarm verworfen", "Dismissed as false alarm"),
    corrected: L("Entwarnung nachgesendet", "All-clear sent afterwards"),
    cancelled: L("Abgebrochen", "Cancelled"),
    normal: L("Kein Befund im Beobachtungsfenster", "No finding in observation window"),
    source_ended: L("Kameraquelle beendet", "Camera source ended"),
    failed: L("Fehlgeschlagen", "Failed"),
  };
  const OBJECT_LABELS = {
    vehicle: L("Fahrzeug", "Vehicle"),
    bird: L("Vogel", "Bird"),
    leaf: L("Blatt", "Leaf"),
    unknown: L("unklar", "unclear"),
  };
  const FIXTURE_PROFILE_LABELS = {
    pending: L("Fahrzeug, Gefahr, Sicherheit 0,80 → menschliche Prüfung", "Vehicle, hazard, confidence 0.80 → human review"),
    high: L("Fahrzeug, Gefahr, Sicherheit 0,99 → automatische Meldung", "Vehicle, hazard, confidence 0.99 → automatic report"),
    bird: L("Vogel, keine Gefahr (0,99)", "Bird, no hazard (0.99)"),
    leaf: L("Blatt, keine Gefahr (0,99)", "Leaf, no hazard (0.99)"),
    unclear: L("Unklares Ergebnis", "Unclear result"),
    followup: L("Fordert eine Folgebeobachtung an", "Requests a follow-up observation"),
    error: L("Modellfehler", "Model error"),
  };
  const INJECT_BLOCK_REASONS = {
    destroyed: L("Die Kameraansicht wurde geschlossen.", "The camera view was closed."),
    source_closed: L("Die Kamera läuft nicht.", "The camera is not running."),
    warming_up: L("Die Analyse wärmt sich noch auf (drei echte Startframes).", "The analysis is still warming up (three real start frames)."),
    run_inactive: L("Der Analyse-Lauf dieses Falls ist beendet.", "The analysis run of this case has ended."),
    source_changed: L("Die Kameraquelle wurde inzwischen gewechselt.", "The camera source has been changed in the meantime."),
    event_cooldown: L("Das vorherige Ereignis wird noch analysiert. Bitte warte den Countdown ab.", "The previous event is still being analysed. Please wait for the countdown."),
  };
  const INJECT_BUTTONS = ["btn-sim-wrongway", "btn-sim-bird", "btn-sim-leaf"];
  // A camera that stopped at one of its limits says so instead of silently showing STANDBY.
  function sourceEndedNotice(reason) {
    const isPublic = Boolean($("public-demo-note")) && !$("public-demo-note").hidden;
    if (reason === "recording_limit") {
      return isPublic
        ? L("Die Kamera hat das Aufnahmelimit der öffentlichen Demo erreicht und wurde beendet. Alle Tickets und Clips bleiben erhalten. Schalte die Kamera mit „Kamera ein / aus“ wieder ein, um mit einer neuen Aufnahme weiterzumachen.", "The camera reached the public demo's recording limit and was stopped. All tickets and clips are kept. Switch the camera back on with “Camera on / off” to continue with a new recording.")
        : L("Die Kamera hat ihr Aufnahmelimit erreicht und wurde beendet. Alle Tickets und Clips bleiben erhalten. Schalte die Kamera mit „Kamera ein / aus“ wieder ein, um mit einer neuen Aufnahme weiterzumachen.", "The camera reached its recording limit and was stopped. All tickets and clips are kept. Switch the camera back on with “Camera on / off” to continue with a new recording.");
    }
    if (reason === "time_limit") {
      return L("Die Kamera-Sitzung hat ihre Höchstdauer von zwei Minuten erreicht und wurde beendet. Alle Tickets und Clips bleiben erhalten. Schalte die Kamera mit „Kamera ein / aus“ wieder ein, um weiterzumachen.", "The camera session reached its two-minute maximum and was stopped. All tickets and clips are kept. Switch the camera back on with “Camera on / off” to continue.");
    }
    return null;
  }

  const REVIEW_PHASES = new Set(["model_review", "human_review"]);
  const ACTIVE_PHASES = new Set(["starting", "running", "model_review", "human_review"]);
  const MONITOR_PHASES = new Set(["starting", "running"]);
  // Every detection becomes a ticket; it sits in the room that currently handles it:
  // 2 datacenter (model), 3 dispatch Alpha (human decision), 4 deadline desk.
  const STATION_OF_PHASE = {
    model_review: 2, dismissed: 2, failed: 2,
    human_review: 3, reported: 3, rejected: 3,
    no_response: 4, corrected: 4,
  };
  const OPEN_PHASE_OF_STATION = { 2: "model_review", 3: "human_review", 4: "no_response" };

  // --- State ------------------------------------------------------------------
  let env = null; // { roadMount, apiClient, workplace, openCase }
  let mountedHandle = null;
  let currentRoom = 1;
  let currentLang = window.VisionFlowI18n ? window.VisionFlowI18n.lang : "en";
  let capabilities = null;
  const tickets = new Map(); // caseId -> ticket
  let ticketOrder = []; // newest first
  let ticketSequence = 0;
  let selectedCaseId = null;
  let startPromise = null;
  let eventBusy = false;
  let bannerTimer = null;
  let notifiedEndReason = null;
  let clipView = { caseId: null, clipId: null, url: null, generation: 0 };

  // Translation table (static labels; live case data is shown as delivered)
  const I18N = {
    de: {
      title: "VisionFlow · Verkehrsleitstelle",
      subtitle: "Folge einem Ticket von der Kamera bis zur Entscheidung.",
      soundOn: "🔊 Ton: An",
      soundOff: "🔇 Ton: Aus",
      langToggle: "🌐 EN",
      detailsShow: "🔍 Technische Fallansicht",
      detailsHide: "🔍 Fallansicht ausblenden",
      room1: "1. Kamera",
      room2: "2. Einordnung",
      room3: "3. Entscheidung",
      room4: "4. Frist & Meldung",
      followCableTo2: "Dem Datenkabel ins Rechenzentrum folgen ➔",
      followCableTo3: "Dem Alarmkabel zur Leitstelle folgen ➔",
      followCableTo4: "Dem Kabel zu Tisch 2 folgen ➔",
      alexIntro: "Starte die Kamera und blende ein Ereignis ein. Erkennt OpenCV Gegenbewegung, entsteht ein Ticket.",
      kiraIntro: "Wir ordnen das Ticket ein: Gefahr, Fehlalarm oder menschliche Prüfung?",
      schmidtIntro: "Sieh dir den Beweisclip an. Bestätige die Gefahr oder gib Entwarnung.",
      schmidtRadio: "📻 Gefahr bestätigen (Einsatz)",
      schmidtFalseAlarm: "❌ Gefahr verneinen",
      schmidtCoffee: "☕ Kaffeepause (niemand entscheidet)",
      schmidtCoffeeAway: "Ich hol mir schnell einen Kaffee! Niemand entscheidet jetzt – an Tisch 2 läuft die echte Frist des Falls weiter.",
      weberSticky: "☕ Bin kurz Kaffee holen – Weber",
      weberAlert: "Weber ist kurz weg. Ohne Entscheidung wird das Ticket bei Fristablauf automatisch gemeldet.",
      weberManual: "🚨 Gefahr manuell bestätigen"
    },
    en: {
      title: "VisionFlow · Traffic control room",
      subtitle: "Follow a ticket from the camera to the decision.",
      soundOn: "🔊 Sound: ON",
      soundOff: "🔇 Sound: OFF",
      langToggle: "🌐 DE",
      detailsShow: "🔍 Technical case view",
      detailsHide: "🔍 Hide case view",
      room1: "1. Camera",
      room2: "2. Assessment",
      room3: "3. Decision",
      room4: "4. Deadline & report",
      followCableTo2: "Follow Data Cable to Datacenter ➔",
      followCableTo3: "Follow Alert Cable to Dispatch ➔",
      followCableTo4: "Follow Cable to Desk 2 ➔",
      alexIntro: "Start the camera and inject an event. When OpenCV detects opposing motion, it opens a ticket.",
      kiraIntro: "We assess the ticket: hazard, false alarm or human review?",
      schmidtIntro: "Watch the evidence clip. Confirm the hazard or give the all-clear.",
      schmidtRadio: "📻 Confirm hazard (dispatch)",
      schmidtFalseAlarm: "❌ Deny hazard",
      schmidtCoffee: "☕ Coffee break (nobody decides)",
      schmidtCoffeeAway: "Grabbing a coffee! Nobody decides now – the case's real deadline keeps running at desk 2.",
      weberSticky: "☕ Grabbing coffee, be right back – Weber",
      weberAlert: "Weber is away. Without a decision, the ticket is reported automatically at the deadline.",
      weberManual: "🚨 Confirm hazard manually"
    }
  };

  function t(key) {
    return I18N[currentLang][key] || key;
  }

  // DOM Builder helper (text only for data; innerHTML is used for static SVG assets)
  function h(tag, attrs, children) {
    const el = document.createElement(tag);
    if (attrs) {
      for (const [k, v] of Object.entries(attrs)) {
        if (k === "className") el.className = v;
        else if (k === "textContent") el.textContent = v;
        else if (k === "innerHTML") el.innerHTML = v;
        else if (k === "onclick") el.addEventListener("click", v);
        else if (k === "hidden") el.hidden = Boolean(v);
        else if (k === "disabled") el.disabled = Boolean(v);
        else el.setAttribute(k, v);
      }
    }
    if (children) {
      for (const child of Array.isArray(children) ? children : [children]) {
        if (typeof child === "string") el.appendChild(document.createTextNode(child));
        else if (child instanceof Node) el.appendChild(child);
      }
    }
    return el;
  }

  function $(id) {
    return document.getElementById(id);
  }

  function setText(id, text) {
    const el = $(id);
    if (el && el.textContent !== text) el.textContent = text;
  }

  function formatDecimal(value, digits = 2) {
    return typeof value === "number" && Number.isFinite(value)
      ? value.toLocaleString(L("de-DE", "en-US"), { minimumFractionDigits: digits, maximumFractionDigits: digits })
      : "–";
  }

  function shortHash(value) {
    return typeof value === "string" && value ? value.replace(/^sha256:/, "").slice(0, 12) + "…" : "–";
  }

  // Rebuild clickable lists only when their content changes, so a periodic
  // refresh never replaces a card between mousedown and click.
  const listSignatures = new Map();
  function listChanged(id, signature) {
    if (listSignatures.get(id) === signature) return false;
    listSignatures.set(id, signature);
    return true;
  }

  function ticketSignature() {
    return JSON.stringify([selectedCaseId, ticketOrder.map((caseId) => {
      const item = tickets.get(caseId);
      const snapshot = item.snapshot || {};
      return [caseId, snapshot.phase, snapshot.assessment, snapshot.clip && snapshot.clip.clip_id, item.scenario.length];
    })]);
  }

  function wait(ms) {
    return new Promise((resolve) => window.setTimeout(resolve, ms));
  }

  // --- Ticket model -------------------------------------------------------------
  function ticketFor(caseId) {
    let ticket = tickets.get(caseId);
    if (!ticket) {
      ticket = {
        caseId,
        number: null, // assigned when the analysis detects something (the ticket is born)
        createdAt: new Date(),
        scenario: [],
        snapshot: null,
        exchanges: [],
        lastPhase: null,
        dispatched: false,
        observedLive: false, // set when this page started the case and saw its injections
      };
      tickets.set(caseId, ticket);
      ticketOrder.unshift(caseId);
    }
    return ticket;
  }

  function phaseOf(ticket) {
    return ticket && ticket.snapshot && typeof ticket.snapshot.phase === "string" ? ticket.snapshot.phase : null;
  }

  function phaseLabel(phase) {
    return phase ? PHASE_LABELS[phase] || phase : L("Fall startet", "Case starting");
  }

  function ticketName(ticket) {
    return ticket && ticket.number ? `Ticket #${ticket.number}` : L("Überwachung", "Monitoring");
  }

  function isDetection(ticket) {
    const snapshot = ticket && ticket.snapshot;
    if (!snapshot) return false;
    const phase = snapshot.phase;
    return Boolean(snapshot.clip) || (phase in STATION_OF_PHASE && phase !== "failed");
  }

  function stationOf(ticket) {
    return ticket && ticket.number ? STATION_OF_PHASE[phaseOf(ticket)] || 2 : 1;
  }

  function roomName(station) {
    return t(`room${station}`).replace(/^\S+\s+/, "");
  }

  // Tickets currently at a station, open ones first, newest first.
  function ticketsAt(station) {
    const list = ticketOrder.map((caseId) => tickets.get(caseId)).filter((item) => item.number && stationOf(item) === station);
    const open = OPEN_PHASE_OF_STATION[station];
    return [...list.filter((item) => phaseOf(item) === open), ...list.filter((item) => phaseOf(item) !== open)];
  }

  function focusAt(station) {
    const selected = selectedCaseId ? tickets.get(selectedCaseId) : null;
    if (selected && selected.number) return selected;
    return newestTicket() || ticketsAt(station)[0] || null;
  }

  function newestTicket() {
    for (const caseId of ticketOrder) {
      const item = tickets.get(caseId);
      if (item.number) return item;
    }
    return null;
  }

  function onTicketCreated(ticket) {
    // Injected events are only known for cases this page started; a resumed case says so.
    if (!ticket.observedLive) {
      showBanner(L(`${ticketName(ticket)} wiederaufgenommen: Die Bildanalyse hatte in diesem Fall angeschlagen. Eingeblendete Ereignisse sind nach dem Neuladen nicht bekannt. Clip gesichert → Rechenzentrum`, `${ticketName(ticket)} resumed: the image analysis had triggered in this case. Injected events are not known after reloading. Clip sealed → datacenter`));
      appendDatacenterLog(L(`[${ticketName(ticket)}] Ticket nach Wiederaufnahme aus gespeichertem Fall übernommen`, `[${ticketName(ticket)}] Ticket taken over from a stored case after resuming`));
      return;
    }
    const scenario = ticket.scenario.length ? scenarioText(ticket) : L("ohne eingeblendetes Ereignis", "without an injected event");
    showBanner(L(`${ticketName(ticket)} erstellt: Die Bildanalyse hat angeschlagen (${scenario}). Clip gesichert → Rechenzentrum`, `${ticketName(ticket)} created: the image analysis triggered (${scenario}). Clip sealed → datacenter`));
    appendDatacenterLog(L(`[${ticketName(ticket)}] Neues Ticket aus Befund der Bildanalyse`, `[${ticketName(ticket)}] New ticket from an image-analysis finding`));
  }

  function latestExchange(ticket) {
    const list = ticket && Array.isArray(ticket.exchanges) ? ticket.exchanges : [];
    return list.length ? list[list.length - 1] : null;
  }

  function modelSourceText(ticket) {
    const snapshot = ticket && ticket.snapshot;
    if (!snapshot) return "–";
    const exchange = latestExchange(ticket);
    const modelId = exchange && typeof exchange.model_id === "string" ? exchange.model_id : null;
    if (snapshot.mode === "fixture") {
      const profile = snapshot.fixture_profile || "pending";
      return L(`Offline-Testantwort (Profil „${profile}“${modelId ? ", " + modelId : ""})`, `Offline test answer (profile “${profile}”${modelId ? ", " + modelId : ""})`);
    }
    return modelId ? `${modelId} (${snapshot.mode})` : String(snapshot.mode || "–");
  }

  function assessmentText(assessment) {
    if (!assessment || typeof assessment !== "object") return null;
    const object = OBJECT_LABELS[assessment.object_class] || String(assessment.object_class || L("unklar", "unclear"));
    const hazard = assessment.hazard === true ? L("Gefahr", "Hazard") : assessment.hazard === false ? L("keine Gefahr", "no hazard") : L("Gefahr unklar", "hazard unclear");
    return L(`${object} · ${hazard} · Sicherheit ${formatDecimal(assessment.confidence)}`, `${object} · ${hazard} · confidence ${formatDecimal(assessment.confidence)}`);
  }

  function scenarioText(ticket) {
    if (ticket && ticket.scenario.length === 0 && !ticket.observedLive) return L("nicht bekannt (Fall nach dem Neuladen wiederaufgenommen)", "not known (case resumed after reloading)");
    if (!ticket || ticket.scenario.length === 0) return L("keine Ereignisse aus dem Spiel eingeblendet", "no events injected from the game");
    return ticket.scenario.map((entry) => EVENT_LABELS[entry.type] || entry.type).join(" → ");
  }

  function updateTicket(caseId, snapshot, exchanges) {
    if (typeof caseId !== "string" || !caseId || !snapshot || typeof snapshot !== "object") return;
    const ticket = ticketFor(caseId);
    ticket.snapshot = snapshot;
    if (Array.isArray(exchanges)) ticket.exchanges = exchanges;
    if (!ticket.number && isDetection(ticket)) {
      ticketSequence += 1;
      ticket.number = ticketSequence;
      onTicketCreated(ticket);
    }
    const phase = phaseOf(ticket);
    if (phase !== ticket.lastPhase) {
      const previous = ticket.lastPhase;
      ticket.lastPhase = phase;
      onPhaseChange(ticket, previous, phase);
    }
  }

  // --- Phase transitions (all derived from server snapshots) ------------------
  function onPhaseChange(ticket, previous, phase) {
    const label = ticketName(ticket);
    const snapshot = ticket.snapshot || {};
    appendDatacenterLog(`[${label}] Phase: ${phaseLabel(previous)} → ${phaseLabel(phase)}`);
    if (phase === "model_review" || (REVIEW_PHASES.has(phase) && !REVIEW_PHASES.has(previous))) {
      setCablePulsing("cable-1-2", true);
      const follow = $("follow-btn-1");
      if (follow) follow.hidden = false;
      if (snapshot.clip) {
        appendDatacenterLog(`[${label}] Clip ${String(snapshot.clip.clip_id).slice(0, 8)} · ${formatDecimal(snapshot.clip.start_seconds, 1)}–${formatDecimal(snapshot.clip.end_seconds, 1)} s · SHA-256 ${shortHash(snapshot.clip.sha256)}`);
      }
    }
    const assessment = assessmentText(snapshot.assessment);
    if (assessment && !ticket.loggedAssessment) {
      ticket.loggedAssessment = true;
      appendDatacenterLog(L(`[${label}] Modellantwort: ${assessment} · Quelle: ${modelSourceText(ticket)}`, `[${label}] Model answer: ${assessment} · Source: ${modelSourceText(ticket)}`));
    }
    if (snapshot.model_error) appendDatacenterLog(L(`[${label}] Modellfehler: ${snapshot.model_error}`, `[${label}] Model error: ${snapshot.model_error}`));
    if (phase === "human_review") {
      setCablePulsing("cable-2-3", true);
      const follow = $("follow-btn-2");
      if (follow) follow.hidden = false;
      if (global.RoadSFX) global.RoadSFX.playEventTrigger();
    }
    if (phase === "no_response" && !ticket.dispatched && snapshot.first_receipt) {
      ticket.dispatched = true;
      dispatchPatrolCar();
      appendDatacenterLog(L(`[${label}] Frist abgelaufen – automatische Meldung (Beleg ${snapshot.first_receipt.receipt_id || "–"})`, `[${label}] Deadline expired – automatic report (receipt ${snapshot.first_receipt.receipt_id || "–"})`));
    }
    if (phase === "reported" && !ticket.dispatched) {
      ticket.dispatched = true;
      dispatchPatrolCar();
    }
    if (phase && !ACTIVE_PHASES.has(phase)) {
      setCablePulsing("cable-2-3", false);
    }
  }

  // --- Live data feeds ----------------------------------------------------------
  function onCaseStarted(event) {
    const detail = event && event.detail ? event.detail : {};
    if (typeof detail.caseId !== "string") return;
    const ticket = ticketFor(detail.caseId);
    ticket.observedLive = true;
    if (detail.case) updateTicket(detail.caseId, detail.case);
    selectedCaseId = ticket.caseId;
    appendDatacenterLog(L(`[${ticketName(ticket)}] Neuer Fall auf ${detail.sourceId || "Kamera"} gestartet · ${modelSourceText(ticket)}`, `[${ticketName(ticket)}] New case started on ${detail.sourceId || "camera"} · ${modelSourceText(ticket)}`));
    render();
  }

  function onCaseChanged(event) {
    const detail = event && event.detail ? event.detail : {};
    if (typeof detail.caseId === "string" && detail.case) {
      updateTicket(detail.caseId, detail.case);
      render();
    }
  }

  function onRoadState(event) {
    const detail = event && event.detail ? event.detail : {};
    const cases = detail.cases && typeof detail.cases === "object" ? detail.cases : {};
    for (const [caseId, entry] of Object.entries(cases)) {
      if (entry && entry.snapshot) updateTicket(caseId, entry.snapshot, entry.exchanges);
    }
    if (!selectedCaseId && tickets.has(detail.viewedCaseId)) selectedCaseId = detail.viewedCaseId;
    render();
  }

  function onDecision(event) {
    const detail = event && event.detail ? event.detail : {};
    const ticket = typeof detail.caseId === "string" ? tickets.get(detail.caseId) : null;
    if (!ticket) return;
    if (detail.decision === "confirm") {
      if (!ticket.dispatched) {
        ticket.dispatched = true;
        dispatchPatrolCar();
      }
      setText(currentRoom === 4 ? "speech-weber" : "speech-schmidt", L(`Der Server hat meine Bestätigung für ${ticketName(ticket)} angenommen. Die Meldung geht an das simulierte Behördenpostfach – Streife rückt aus!`, `The server accepted my confirmation for ${ticketName(ticket)}. The report goes to the simulated authority inbox – patrol is on its way!`));
    } else {
      setText(currentRoom === 4 ? "speech-weber" : "speech-schmidt", L(`Der Server hat meine Verneinung für ${ticketName(ticket)} angenommen. Kein Einsatz; eine schon gesendete Meldung erhält eine Entwarnung.`, `The server accepted my denial for ${ticketName(ticket)}. No dispatch; a report already sent receives an all-clear.`));
    }
    appendDatacenterLog(L(`[${ticketName(ticket)}] Menschliche Entscheidung angenommen: ${detail.decision === "confirm" ? "Gefahr bestätigt" : "Gefahr verneint"}`, `[${ticketName(ticket)}] Human decision accepted: ${detail.decision === "confirm" ? "hazard confirmed" : "hazard denied"}`));
  }

  async function loadCapabilities() {
    if (!env || !env.apiClient) return;
    try {
      const result = await env.apiClient.get("/road/capabilities");
      capabilities = result && typeof result === "object" ? result : null;
    } catch (_error) {
      capabilities = null;
    }
    renderModelOptions();
    renderTransparency();
  }

  // --- Update Cable States ---
  function setCablePulsing(cableId, pulsing) {
    const cable = $(cableId);
    if (cable) {
      const was = cable.classList.contains("pulsing");
      cable.classList.toggle("pulsing", Boolean(pulsing));
      if (pulsing && !was && global.RoadSFX) global.RoadSFX.playCablePulse();
    }
  }

  // --- Dispatch Patrol Car Animation (only after a real report) ---
  function dispatchPatrolCar() {
    if (global.RoadSFX) global.RoadSFX.playSiren();
    const car = $("cartoon-patrol-car");
    if (!car) return;
    car.closest(".patrol-car-stage").hidden = false;
    car.classList.remove("dispatching");
    void car.offsetWidth; // trigger reflow
    car.classList.add("dispatching");
  }

  // --- Room Switching ---
  function switchRoom(roomNum) {
    if (![1, 2, 3, 4].includes(roomNum)) return;
    if (global.RoadSFX) global.RoadSFX.playClick();
    currentRoom = roomNum;
    [1, 2, 3, 4].forEach((idx) => {
      const navBtn = $(`nav-node-room-${idx}`);
      const roomEl = $(`cartoon-room-${idx}`);
      if (navBtn) {
        navBtn.classList.toggle("active", idx === currentRoom);
        navBtn.setAttribute("aria-current", idx === currentRoom ? "step" : "false");
      }
      if (roomEl) roomEl.hidden = idx !== currentRoom;
    });
    render();
  }

  // --- Room 1: camera and scenario events -------------------------------------
  function cameraState() {
    return env && env.roadMount && typeof env.roadMount.getState === "function" ? env.roadMount.getState() : null;
  }

  function selectedModelConfig() {
    const modeSelect = $("cartoon-model-mode");
    const profileSelect = $("cartoon-fixture-profile");
    const mode = modeSelect && modeSelect.value ? modeSelect.value : "fixture";
    return {
      mode,
      fixtureProfile: mode === "fixture" && profileSelect && profileSelect.value ? profileSelect.value : undefined,
    };
  }

  // sameSource: open the next case on the running camera instead of a new source.
  function startNewCase(sameSource) {
    if (startPromise) return startPromise;
    startPromise = (async () => {
      const mount = env.roadMount;
      if (typeof mount.configureCase === "function") mount.configureCase(selectedModelConfig());
      if (sameSource) await mount.startNextCase();
      else await mount.createSource();
    })().finally(() => {
      startPromise = null;
    });
    return startPromise;
  }

  async function waitForInjectable(timeoutMs) {
    const deadline = Date.now() + timeoutMs;
    for (;;) {
      const state = cameraState();
      if (!state) throw new Error(INJECT_BLOCK_REASONS.destroyed);
      if (!state.injectBlocker) return state;
      if (state.injectBlocker === "event_cooldown") throw new Error(INJECT_BLOCK_REASONS.event_cooldown);
      if (state.injectBlocker === "run_inactive" && state.warmedUp) {
        throw new Error(INJECT_BLOCK_REASONS[state.injectBlocker]);
      }
      if (Date.now() >= deadline) {
        throw new Error(INJECT_BLOCK_REASONS[state.injectBlocker] || L("Die Kamera ist noch nicht bereit.", "The camera is not ready yet."));
      }
      await wait(200);
    }
  }

  // True while the camera's current case still observes the scene. Its run can end a
  // moment before the polled case phase flips, so give the phase a few seconds.
  async function currentCaseObserving() {
    for (let attempt = 0; attempt < 12; attempt += 1) {
      const state = cameraState();
      if (!state || !state.caseId) return false;
      const phase = phaseOf(tickets.get(state.caseId));
      if (phase && !MONITOR_PHASES.has(phase)) return false;
      if (state.runActive || !state.warmedUp) return true;
      await wait(250);
    }
    return false;
  }

  function showBanner(text) {
    const feedMount = $("cartoon-feed-mount");
    if (!feedMount) return;
    let banner = $("live-anomaly-banner");
    if (!banner) {
      banner = h("div", { id: "live-anomaly-banner", className: "traffic-anomaly-banner" });
      feedMount.prepend(banner);
    }
    banner.textContent = text;
    banner.hidden = false;
    if (bannerTimer) window.clearTimeout(bannerTimer);
    bannerTimer = window.setTimeout(() => {
      banner.hidden = true;
      bannerTimer = null;
    }, 6000);
  }

  function setEventButtonsDisabled(disabled) {
    for (const id of ["btn-sim-leaf", "btn-sim-bird", "btn-sim-wrongway", "btn-toggle-cam"]) {
      const button = $(id);
      if (button) button.disabled = Boolean(disabled);
    }
    if (!disabled) renderEventCooldown();
  }

  // W4: one event at a time. The inject buttons count down until the server says the
  // previous event has been analysed (scene left without a candidate, or case reviewed).
  function renderEventCooldown() {
    const state = cameraState();
    const remaining = state && state.sourceOpen ? state.eventCooldownSeconds || 0 : 0;
    for (const id of INJECT_BUTTONS) {
      const button = $(id);
      if (!button) continue;
      if (!button.dataset.label) button.dataset.label = button.textContent;
      const label = remaining > 0 ? `${button.dataset.label} · ${remaining} s` : button.dataset.label;
      if (button.textContent !== label) button.textContent = label;
      if (!eventBusy) button.disabled = remaining > 0;
    }
    const note = $("event-cooldown-note");
    if (note) {
      note.hidden = remaining <= 0;
      setText("event-cooldown-note", remaining > 0
        ? L(`Nächstes Ereignis in ${remaining} s: Das vorherige Ereignis durchläuft noch die Analyse (Erkennung, Beweisclip, Modellprüfung).`, `Next event in ${remaining} s: the previous event is still being analysed (detection, evidence clip, model review).`)
        : "");
    }
  }

  async function triggerEvent(eventType) {
    if (eventBusy) return;
    if (!env || !env.roadMount) {
      setText("speech-alex", L("Die Straßenkamera ist nicht verbunden. Ohne VisionFlow-Server gibt es hier nichts zu analysieren.", "The road camera is not connected. Without the VisionFlow server there is nothing to analyze here."));
      return;
    }
    eventBusy = true;
    setEventButtonsDisabled(true);
    if (global.RoadSFX) global.RoadSFX.playEventTrigger();
    try {
      let state = cameraState();
      if (state && state.injectBlocker === "event_cooldown") {
        throw new Error(INJECT_BLOCK_REASONS.event_cooldown);
      }
      if (!state.available) {
        throw new Error((capabilities && capabilities.message) || L("Die Straßenkamera ist nicht verfügbar (FFmpeg mit libx264 erforderlich).", "The road camera is not available (FFmpeg with libx264 required)."));
      }
      if (!state.sourceOpen) {
        setText("speech-alex", L("Ich starte die Kamera mit einem neuen Fall und warte, bis die Analyse drei echte Startframes gesehen hat …", "I am starting the camera with a new case and waiting until the analysis has seen three real start frames …"));
        await startNewCase(false);
      } else if (!(await currentCaseObserving())) {
        // The previous case sealed its clip and moved on; the camera keeps running.
        setText("speech-alex", L("Die Kamera läuft weiter. Ich öffne darauf einen neuen Fall und warte auf drei echte Startframes …", "The camera keeps running. I am opening a new case on it and waiting for three real start frames …"));
        await startNewCase(true);
      }
      state = await waitForInjectable(20000);
      let result = await env.roadMount.injectScenarioEvent(eventType);
      if (result && !result.injected && result.reason === "run_inactive") {
        // The case ended between the click and the event; open the next one first.
        setText("speech-alex", L("Die Kamera läuft weiter. Ich öffne darauf einen neuen Fall und warte auf drei echte Startframes …", "The camera keeps running. I am opening a new case on it and waiting for three real start frames …"));
        await startNewCase(true);
        state = await waitForInjectable(20000);
        result = await env.roadMount.injectScenarioEvent(eventType);
      }
      if (!result || !result.injected) {
        throw new Error(INJECT_BLOCK_REASONS[result && result.reason] || L("Das Ereignis wurde nicht eingeblendet.", "The event was not injected."));
      }
      const ticket = ticketFor(result.caseId || state.caseId);
      ticket.scenario.push({ type: eventType, at: new Date() });
      selectedCaseId = ticket.caseId;
      const label = EVENT_LABELS[eventType] || eventType;
      showBanner(L(`Szenario eingeblendet: ${label} · OpenCV analysiert jetzt die echten Frames`, `Scenario injected: ${label} · OpenCV is now analyzing the real frames`));
      setText("speech-alex", L(`${label} ist jetzt in der Live-Szene. Das ist noch kein Befund: Erst wenn die Bildanalyse auf den echten Frames anschlägt, schneidet der Server einen Clip und legt ein Ticket an.`, `${label} is now in the live scene. That is not a finding yet: only when the image analysis triggers on the real frames does the server cut a clip and open a ticket.`));
      appendDatacenterLog(L(`[${ticketName(ticket)}] Szenario eingeblendet: ${label} (Spielaktion, kein Analysebefund)`, `[${ticketName(ticket)}] Scenario injected: ${label} (game action, not an analysis finding)`));
      render();
    } catch (error) {
      setText("speech-alex", error && error.message ? error.message : L("Das Ereignis konnte nicht eingeblendet werden.", "The event could not be injected."));
    } finally {
      eventBusy = false;
      setEventButtonsDisabled(false);
    }
  }

  async function toggleCamera() {
    if (eventBusy || !env || !env.roadMount) return;
    if (global.RoadSFX) global.RoadSFX.playClick();
    const state = cameraState();
    eventBusy = true;
    setEventButtonsDisabled(true);
    try {
      if (state.sourceOpen) {
        await env.roadMount.stopSource();
        setText("speech-alex", L("Kamera gestoppt. Die Aufnahme wurde als Clip gesichert.", "Camera stopped. The recording was saved as a clip."));
      } else {
        setText("speech-alex", L("Ich starte die Kamera mit einem neuen Fall …", "I am starting the camera with a new case …"));
        await startNewCase(false);
      }
    } catch (error) {
      setText("speech-alex", error && error.message ? error.message : L("Die Kamera konnte nicht umgeschaltet werden.", "The camera could not be toggled."));
    } finally {
      eventBusy = false;
      setEventButtonsDisabled(false);
      render();
    }
  }

  function renderModelOptions() {
    const modeSelect = $("cartoon-model-mode");
    const profileSelect = $("cartoon-fixture-profile");
    if (!modeSelect || !profileSelect) return;
    const modes = Array.isArray(capabilities && capabilities.modes) ? capabilities.modes : [];
    const profiles = Array.isArray(capabilities && capabilities.fixture_profiles) ? capabilities.fixture_profiles : [];
    const previousMode = modeSelect.value;
    const previousProfile = profileSelect.value;
    modeSelect.replaceChildren();
    if (modes.includes("fixture")) modeSelect.appendChild(h("option", { value: "fixture", textContent: L("Offline-Testantwort (lokal, ohne Netz)", "Offline test answer (local, no network)") }));
    if (modes.includes("gemini_video")) modeSelect.appendChild(h("option", { value: "gemini_video", textContent: L("Gemini-Video (extern, kostenpflichtig)", "Gemini video (external, paid)") }));
    if (!modeSelect.options.length) modeSelect.appendChild(h("option", { value: "", textContent: L("Kein Modellmodus verfügbar", "No model mode available") }));
    if ([...modeSelect.options].some((option) => option.value === previousMode)) modeSelect.value = previousMode;
    profileSelect.replaceChildren();
    const ordered = ["pending", "high", "bird", "leaf", "unclear"].filter((name) => profiles.includes(name));
    for (const name of ordered) {
      profileSelect.appendChild(h("option", { value: name, textContent: `${name}: ${FIXTURE_PROFILE_LABELS[name] || name}` }));
    }
    profileSelect.value = [...profileSelect.options].some((option) => option.value === previousProfile) ? previousProfile : "pending";
    profileSelect.disabled = modeSelect.value !== "fixture";
  }

  // --- Room 2: datacenter --------------------------------------------------------
  function appendDatacenterLog(msg) {
    const consoleEl = $("datacenter-console");
    if (!consoleEl) return;
    const line = document.createElement("div");
    line.textContent = `[${new Date().toLocaleTimeString()}] ${msg}`;
    consoleEl.appendChild(line);
    while (consoleEl.childElementCount > 200) consoleEl.firstElementChild.remove();
    consoleEl.scrollTop = consoleEl.scrollHeight;
  }

  // Room 2 explains the model stage of the ticket at hand: a ticket in the
  // datacenter, else the newest ticket, else the case the camera is watching.
  function datacenterTicket() {
    const state = cameraState();
    const selected = selectedCaseId ? tickets.get(selectedCaseId) : null;
    return (selected && selected.number ? selected : null) || newestTicket() || selected || (state && state.caseId ? tickets.get(state.caseId) : null);
  }

  function renderTransparency() {
    const nativeEnabled = Boolean(capabilities && capabilities.native_enabled);
    const ticket = datacenterTicket();
    const snapshot = ticket && ticket.snapshot;
    const pill = $("transparency-pill-text");
    const pillBox = $("transparency-pill");
    if (pill) {
      pill.textContent = snapshot && snapshot.mode === "gemini_video"
        ? L("Gemini Video · kostenpflichtig", "Gemini Video · paid")
        : L("Feste Offline-Testantwort", "Fixed offline test answer");
    }
    if (pillBox) pillBox.classList.toggle("fallback", !(snapshot && snapshot.mode === "gemini_video"));
    setText("transparency-profile-val", snapshot
      ? `${snapshot.mode}${snapshot.fixture_profile ? "/" + snapshot.fixture_profile : ""}`
      : L("noch kein Fall", "no case yet"));
    setText("transparency-native-val", nativeEnabled ? L("freigegeben (Server mit Kostenfreigabe)", "enabled (server with cost approval)") : L("nicht freigegeben", "not enabled"));
    setText("transparency-notice", snapshot && snapshot.mode === "gemini_video"
      ? L("Dieser Fall sendet den Beweisclip an Gemini Video. Meldungen bleiben simuliert.", "This case sends the evidence clip to Gemini Video. Reports remain simulated.")
      : L("Feste Testantwort, keine Bildbewertung. Bewegungserkennung und Beweisclip sind echt; Meldungen sind simuliert.", "Fixed test answer, no image assessment. Motion detection and evidence clip are real; reports are simulated."));
  }

  function ticketCard(item, className, detail) {
    return h("button", {
      type: "button",
      className: `${className} ${item.caseId === selectedCaseId ? "active-ticket" : ""}`,
      onclick: () => selectTicket(item.caseId),
    }, [
      h("strong", { textContent: `${ticketName(item)} · ${phaseLabel(phaseOf(item))}` }),
      h("br"),
      h("small", { textContent: detail }),
    ]);
  }

  function renderDatacenter() {
    const ticket = datacenterTicket();
    const snapshot = ticket && ticket.snapshot;
    const phase = phaseOf(ticket);

    const chip1 = $("stage-chip-1");
    const chip2 = $("stage-chip-2");
    if (chip1) chip1.classList.toggle("active-stage", phase === "running" || phase === "starting");
    if (chip2) chip2.classList.toggle("active-stage", Boolean(phase) && phase !== "running" && phase !== "starting" && phase !== "normal");

    const listEl = $("datacenter-queue-list");
    if (listEl && listChanged("datacenter-queue-list", ticketSignature())) {
      listEl.replaceChildren();
      const here = ticketsAt(2);
      if (here.length === 0) listEl.textContent = L("Kein Ticket im Rechenzentrum. Tickets entstehen, wenn die Bildanalyse in Raum 1 anschlägt.", "No ticket in the datacenter. Tickets are created when the image analysis in room 1 triggers.");
      for (const item of here.slice(0, 4)) {
        const clip = item.snapshot && item.snapshot.clip;
        const hazard = item.snapshot && item.snapshot.assessment && item.snapshot.assessment.hazard === true;
        listEl.appendChild(ticketCard(item, `queue-item ${hazard ? "high-risk" : ""}`,
          clip ? `Clip ${String(clip.clip_id).slice(0, 8)} · SHA-256 ${shortHash(clip.sha256)}` : L("noch kein Clip", "no clip yet")));
      }
    }

    const verdictBox = $("datacenter-verdict-box");
    if (verdictBox) {
      verdictBox.replaceChildren();
      if (!ticket) {
        verdictBox.textContent = L("Starte die Kamera und blende ein Ereignis ein.", "Start the camera and inject an event.");
        $("datacenter-case-detail")?.replaceChildren();
      } else {
        let stage1 = L("Beobachtet die Szene (Bewegung + Richtung auf echten Frames) …", "Observing the scene (motion + direction on real frames) …");
        if (phase === "normal") stage1 = L("Kein Befund im Beobachtungsfenster.", "No finding in observation window.");
        else if (phase === "source_ended" || phase === "cancelled") stage1 = L("Analyse ohne Befund beendet.", "Analysis ended without finding.");
        else if (snapshot && snapshot.clip) stage1 = L(`Befund ausgelöst → Clip ${String(snapshot.clip.clip_id).slice(0, 8)} (SHA-256 ${shortHash(snapshot.clip.sha256)})`, `Finding triggered → clip ${String(snapshot.clip.clip_id).slice(0, 8)} (SHA-256 ${shortHash(snapshot.clip.sha256)})`);
        else if (phase && phase !== "running" && phase !== "starting") stage1 = L("Befund ausgelöst.", "Finding triggered.");
        const assessment = assessmentText(snapshot && snapshot.assessment);
        let stage2 = L("wartet auf einen Befund aus Stufe 1", "waiting for a finding from stage 1");
        if (assessment) stage2 = assessment;
        else if (snapshot && snapshot.model_error) stage2 = L(`Modellfehler: ${snapshot.model_error}`, `Model error: ${snapshot.model_error}`);
        else if (phase === "model_review") stage2 = L("Modell prüft den Clip …", "Model is reviewing the clip …");
        const hazard = snapshot && snapshot.assessment ? snapshot.assessment.hazard : null;
        const fixture = snapshot && snapshot.mode === "fixture";
        verdictBox.append(
          h("h3", { textContent: ticketName(ticket) }),
          h("p", { className: "case-result", textContent: assessment || (snapshot && snapshot.model_error ? L("Bewertung nicht verfügbar", "Assessment unavailable") : stage2) }),
          h("p", { className: "case-status", textContent: phaseLabel(phase) }),
          h("p", { className: "case-next", textContent: nextStep(ticket) }),
        );
        const detail = $("datacenter-case-detail");
        if (detail) detail.replaceChildren(
          h("p", { textContent: L(`Stufe 1 (OpenCV): ${stage1}`, `Stage 1 (OpenCV): ${stage1}`) }),
          h("p", { textContent: L(`Stufe 2 (Modell): ${stage2}`, `Stage 2 (model): ${stage2}`) }),
          h("p", { textContent: L(`Eingeblendetes Szenario: ${scenarioText(ticket)}`, `Injected scenario: ${scenarioText(ticket)}`) }),
          h("p", { textContent: L(`Quelle: ${modelSourceText(ticket)}`, `Source: ${modelSourceText(ticket)}`) }),
          h("p", { textContent: L(`Fall-ID: ${ticket.caseId}`, `Case ID: ${ticket.caseId}`) }),
          h("p", { textContent: snapshot && snapshot.clip ? `SHA-256: ${snapshot.clip.sha256}` : L("Noch kein Clip", "No clip yet") }),
        );
      }
    }

    const speech = $("speech-kira");
    if (speech && ticket && snapshot) {
      const assessment = snapshot.assessment;
      if (assessment && assessment.hazard === true) {
        speech.textContent = L(`${ticketName(ticket)}: Das Modell meldet Gefahr (Sicherheit ${formatDecimal(assessment.confidence)}). ${phase === "human_review" ? "Ein Mensch muss jetzt entscheiden – ab in die Leitstelle!" : phaseLabel(phase) + "."}`, `${ticketName(ticket)}: the model reports a hazard (confidence ${formatDecimal(assessment.confidence)}). ${phase === "human_review" ? "A human must decide now – off to dispatch!" : phaseLabel(phase) + "."}`);
      } else if (assessment && assessment.hazard === false) {
        speech.textContent = L(`${ticketName(ticket)}: Das Modell meldet keine Gefahr (${OBJECT_LABELS[assessment.object_class] || assessment.object_class}). Kein Einsatz.`, `${ticketName(ticket)}: the model reports no hazard (${OBJECT_LABELS[assessment.object_class] || assessment.object_class}). No dispatch.`);
      } else if (phase === "model_review") {
        speech.textContent = L(`${ticketName(ticket)}: Der Clip liegt dem Modell vor …`, `${ticketName(ticket)}: the clip is with the model …`);
      } else if (phase === "running" || phase === "starting") {
        speech.textContent = L(`${ticketName(ticket)}: Noch kein Befund – die Bildanalyse beobachtet die Szene.`, `${ticketName(ticket)}: no finding yet – the image analysis is observing the scene.`);
      } else {
        speech.textContent = L(`${ticketName(ticket)}: ${phaseLabel(phase)}.`, `${ticketName(ticket)}: ${phaseLabel(phase)}.`);
      }
    }
    renderTransparency();
  }

  // --- Room 3: dispatch Alpha ----------------------------------------------------
  function selectTicket(caseId) {
    if (!tickets.has(caseId)) return;
    selectedCaseId = caseId;
    if (env && typeof env.openCase === "function") env.openCase(caseId);
    render();
  }

  // The review workplace holds one case at a time; open the one shown in this room.
  let requestedOpenCaseId = null;
  function ensureOpen(ticket, state) {
    if (!ticket || !env || typeof env.openCase !== "function") return;
    if (state.open) {
      requestedOpenCaseId = null;
    } else if (requestedOpenCaseId !== ticket.caseId) {
      requestedOpenCaseId = ticket.caseId;
      env.openCase(ticket.caseId);
    }
  }

  function decisionState(ticket) {
    if (!ticket || !env || !env.workplace || typeof env.workplace.getDecisionState !== "function") {
      return { open: false, canConfirm: false, canReject: false };
    }
    return env.workplace.getDecisionState(ticket.caseId);
  }

  function revokeClipView() {
    if (clipView.url) URL.revokeObjectURL(clipView.url);
    clipView = { caseId: null, clipId: null, url: null, generation: clipView.generation + 1 };
  }

  function renderReviewMonitor(ticket) {
    const box = $("cartoon-review-video-box");
    if (!box) return;
    const clip = ticket && ticket.snapshot && ticket.snapshot.clip;
    if (!ticket || !clip || typeof clip.clip_id !== "string") {
      if (clipView.caseId !== (ticket ? ticket.caseId : null) || clipView.clipId !== null) {
        revokeClipView();
        clipView.caseId = ticket ? ticket.caseId : null;
        box.replaceChildren(h("p", {
          className: "review-clip-meta",
          textContent: ticket ? L(`${ticketName(ticket)}: noch kein Beweisclip (${phaseLabel(phaseOf(ticket))}).`, `${ticketName(ticket)}: no evidence clip yet (${phaseLabel(phaseOf(ticket))}).`) : L("Kein Fall ausgewählt.", "No case selected."),
        }));
      }
      return;
    }
    if (clipView.caseId === ticket.caseId && clipView.clipId === clip.clip_id) return;
    revokeClipView();
    const generation = clipView.generation;
    clipView.caseId = ticket.caseId;
    clipView.clipId = clip.clip_id;
    box.replaceChildren(h("p", { className: "review-clip-meta", textContent: L(`Lade Beweisclip ${clip.clip_id.slice(0, 8)} vom Server …`, `Loading evidence clip ${clip.clip_id.slice(0, 8)} from the server …`) }));
    env.apiClient.getClip(`/road/clips/${encodeURIComponent(clip.clip_id)}`)
      .then(({ blob, sha256 }) => {
        if (generation !== clipView.generation) return;
        const verified = typeof sha256 === "string" && sha256 === clip.sha256;
        const url = URL.createObjectURL(blob);
        clipView.url = url;
        const video = h("video", { className: "road-video-player" });
        video.src = url;
        video.controls = true;
        video.muted = true;
        video.loop = true;
        video.playsInline = true;
        box.replaceChildren(
          h("p", { className: "review-clip-meta" }, [
            h("span", { className: "proof-badge", textContent: L(`Beweisclip ${clip.clip_id.slice(0, 8)} · ${formatDecimal(clip.start_seconds, 1)}–${formatDecimal(clip.end_seconds, 1)} s`, `Evidence clip ${clip.clip_id.slice(0, 8)} · ${formatDecimal(clip.start_seconds, 1)}–${formatDecimal(clip.end_seconds, 1)} s`) }),
            h("br"),
            h("small", { textContent: L(`SHA-256 ${shortHash(clip.sha256)} · ${verified ? "Prüfsumme vom Server bestätigt" : "Prüfsumme weicht ab!"}`, `SHA-256 ${shortHash(clip.sha256)} · ${verified ? "checksum confirmed by server" : "checksum mismatch!"}`) }),
          ]),
          video,
        );
        video.play().catch(() => {});
      })
      .catch((error) => {
        if (generation !== clipView.generation) return;
        box.replaceChildren(h("p", { className: "review-clip-meta", textContent: L(`Clip konnte nicht geladen werden: ${error && error.message ? error.message : "unbekannter Fehler"}`, `Clip could not be loaded: ${error && error.message ? error.message : "unknown error"}`) }));
      });
  }

  function renderSchmidt() {
    const ticket = focusAt(3);
    const inboxList = $("schmidt-ticket-list");
    if (inboxList && listChanged("schmidt-ticket-list", ticketSignature())) {
      inboxList.replaceChildren();
      const here = ticketsAt(3);
      if (here.length === 0) {
        inboxList.appendChild(h("p", { className: "hint", textContent: L("Kein Ticket wartet auf eine menschliche Entscheidung.", "No ticket is waiting for a human decision.") }));
      }
      for (const item of here.slice(0, 5)) {
        const caseId = item.caseId;
        const snapshot = item.snapshot || {};
        const hazard = snapshot.assessment ? snapshot.assessment.hazard : null;
        const badge = snapshot.assessment
          ? `${OBJECT_LABELS[snapshot.assessment.object_class] || snapshot.assessment.object_class} (${formatDecimal(snapshot.assessment.confidence)})`
          : phaseLabel(phaseOf(item));
        inboxList.appendChild(h("button", {
          type: "button",
          className: `schmidt-ticket-card ${hazard === true ? "danger-ticket" : hazard === false ? "safe-ticket" : ""} ${item === ticket ? "active-ticket" : ""}`,
          onclick: () => selectTicket(caseId),
        }, [
          h("div", { className: "ticket-header" }, [
            h("strong", { textContent: L(`${ticketName(item)}`, `${ticketName(item)}`) }),
            h("span", { className: `ticket-badge ${hazard === true ? "danger" : "safe"}`, textContent: badge }),
          ]),
          h("span", { className: "ticket-clip-title", textContent: L(`${phaseLabel(phaseOf(item))} · ${item.createdAt.toLocaleTimeString()}`, `${phaseLabel(phaseOf(item))} · ${item.createdAt.toLocaleTimeString()}`) }),
        ]));
      }
    }

    renderReviewMonitor(ticket);
    const humanDeadline = ticket && ticket.snapshot && ticket.snapshot.phase === "human_review" && ticket.snapshot.deadline_at;
    const humanRemaining = humanDeadline ? Math.max(0, Math.ceil((Date.parse(humanDeadline) - Date.now()) / 1000)) : null;
    setText("schmidt-countdown", humanRemaining !== null && Number.isFinite(humanRemaining)
      ? L(`Noch ${humanRemaining} s für deine Entscheidung`, `${humanRemaining} s left to decide`) : "");

    const state = decisionState(ticket);
    if (currentRoom === 3) ensureOpen(ticket, state);
    const coffee = $("btn-schmidt-coffee");
    if (coffee) coffee.disabled = !ticket || phaseOf(ticket) !== "human_review" || !(state.canConfirm || state.canReject);
    const radio = $("btn-schmidt-radio");
    const reject = $("btn-schmidt-reject");
    if (radio) radio.disabled = !state.canConfirm;
    setRejectButton(reject, state);
    const status = $("schmidt-decision-status");
    if (status) {
      if (!ticket) status.textContent = L("Kein Ticket in dieser Leitstelle.", "No ticket at this dispatch desk.");
      else if (!state.open) status.textContent = L(`${ticketName(ticket)} wird in der Prüfansicht geladen …`, `${ticketName(ticket)} is loading in the review view …`);
      else if (state.operationState === "in_flight") status.textContent = L("Entscheidung wird an den Server gesendet …", "Decision is being sent to the server …");
      else if (state.operationMessage) status.textContent = state.operationMessage;
      else if (state.canConfirm || state.canReject) status.textContent = L(`${ticketName(ticket)}: Entscheidung möglich (${phaseLabel(phaseOf(ticket))}).`, `${ticketName(ticket)}: decision possible (${phaseLabel(phaseOf(ticket))}).`);
      else status.textContent = L(`${ticketName(ticket)}: keine Entscheidung möglich (${phaseLabel(phaseOf(ticket))}).`, `${ticketName(ticket)}: no decision possible (${phaseLabel(phaseOf(ticket))}).`);
    }
  }

  function setRejectButton(button, state) {
    if (!button) return;
    button.disabled = !state.canReject;
    button.textContent = state.canReject && /Entwarnung|all-clear/i.test(state.rejectLabel || "")
      ? L("❌ Gefahr verneinen + Entwarnung senden", "❌ Deny hazard + send all-clear")
      : t("schmidtFalseAlarm");
  }

  function decideTicket(ticket, decision, speechId) {
    if (!ticket || !env || !env.workplace || typeof env.workplace.decide !== "function") return;
    const result = env.workplace.decide(ticket.caseId, decision);
    if (!result.started) {
      const reasons = {
        case_not_open: L("Dieser Fall ist in der Prüfansicht noch nicht geladen. Einen Moment …", "This case is not yet loaded in the review view. One moment …"),
        not_allowed: L(`Für ${ticketName(ticket)} ist diese Entscheidung gerade nicht möglich (${phaseLabel(phaseOf(ticket))}).`, `This decision is currently not possible for ${ticketName(ticket)} (${phaseLabel(phaseOf(ticket))}).`),
      };
      setText(speechId, reasons[result.reason] || L("Die Entscheidung konnte nicht gestartet werden.", "The decision could not be started."));
      if (result.reason === "case_not_open" && typeof env.openCase === "function") env.openCase(ticket.caseId);
      return;
    }
    if (global.RoadSFX) global.RoadSFX.playRadio();
    setText(speechId, L(`Entscheidung für ${ticketName(ticket)} an den VisionFlow-Server gesendet …`, `Decision for ${ticketName(ticket)} sent to the VisionFlow server …`));
    render();
  }

  function onSchmidtCoffee() {
    if (global.RoadSFX) global.RoadSFX.playCoffee();
    setText("speech-schmidt", t("schmidtCoffeeAway"));
    const away = $("schmidt-away");
    if (away) away.hidden = false;
    setCablePulsing("cable-3-4", true);
    const followBtn3 = $("follow-btn-3");
    if (followBtn3) followBtn3.hidden = false;
  }

  // --- Room 4: real deadline of the open case ---------------------------------------
  // Desk 2 watches the deadline of the ticket waiting in dispatch Alpha; when it
  // expires unanswered, the ticket lands here.
  function deadlineTicket() {
    const ticket = datacenterTicket();
    return ticket && ticket.number ? ticket : null;
  }

  function renderBeta() {
    const ticket = deadlineTicket();
    const snapshot = ticket && ticket.snapshot;
    const display = $("beta-countdown-clock");
    const caption = $("beta-countdown-caption");
    const alertBanner = $("beta-alert-msg");
    if (display) {
      if (snapshot && snapshot.phase === "human_review" && typeof snapshot.deadline_at === "string") {
        const remaining = Math.max(0, (Date.parse(snapshot.deadline_at) - Date.now()) / 1000);
        display.textContent = Number.isFinite(remaining) ? `${Math.ceil(remaining)}s` : "–";
      } else {
        display.textContent = "–";
      }
    }
    if (caption) {
      caption.textContent = ticket
        ? `${ticketName(ticket)} · ${phaseLabel(phaseOf(ticket))}${stationOf(ticket) === 3 ? L(" · wartet in Leitstelle Alpha", " · waiting at dispatch Alpha") : ""}`
        : L("Kein Ticket mit offener Frist.", "No ticket with an open deadline.");
    }
    const list = $("beta-ticket-list");
    if (list && listChanged("beta-ticket-list", ticketSignature())) {
      list.replaceChildren();
      for (const item of ticketsAt(4).slice(0, 4)) {
        const receipt = item.snapshot && item.snapshot.first_receipt;
        list.appendChild(ticketCard(item, "queue-item high-risk", receipt ? L("Meldung gespeichert", "Report recorded") : ""));
      }
    }
    const receiptDetail = $("beta-receipt-detail");
    if (receiptDetail) {
      const receipt = snapshot && snapshot.first_receipt;
      receiptDetail.textContent = receipt
        ? L(`Meldungs-ID: ${receipt.receipt_id || "–"} · Fall-ID: ${ticket.caseId}`, `Report ID: ${receipt.receipt_id || "–"} · Case ID: ${ticket.caseId}`)
        : L("Noch keine Meldung für dieses Ticket.", "No report for this ticket yet.");
    }
    const state = decisionState(ticket);
    if (currentRoom === 4) ensureOpen(ticket, state);
    const manual = $("btn-manual-dispatch");
    if (manual) manual.disabled = !state.canConfirm;
    setRejectButton($("btn-weber-reject"), state);
    if (alertBanner) {
      const receipt = snapshot && snapshot.first_receipt;
      if (snapshot && snapshot.phase === "no_response" && receipt) {
        alertBanner.textContent = L(`${ticketName(ticket)}: automatisch gemeldet. Du kannst weiterhin Entwarnung senden.`, `${ticketName(ticket)}: reported automatically. You can still send an all-clear.`);
        alertBanner.hidden = false;
      } else {
        alertBanner.hidden = true;
        alertBanner.textContent = "";
      }
    }
  }

  // --- Render all -----------------------------------------------------------------
  function render() {
    if (!mountedHandle) return;
    const state = cameraState();
    setText("cartoon-cam-status", state && state.sourceOpen
      ? (state.runActive ? L("[● LIVE · ANALYSE LÄUFT]", "[● LIVE · ANALYSIS RUNNING]") : "[● LIVE]")
      : "[● STANDBY]");
    const endedNotice = state && !state.sourceOpen ? sourceEndedNotice(state.endReason) : null;
    const notice = $("cartoon-source-notice");
    if (notice) {
      notice.hidden = !endedNotice;
      if (endedNotice) setText("cartoon-source-notice", endedNotice);
    }
    if (endedNotice && notifiedEndReason !== state.endReason) setText("speech-alex", endedNotice);
    notifiedEndReason = endedNotice ? state.endReason : null;
    renderDatacenter();
    renderSchmidt();
    renderBeta();
    renderTicketJourney();
    for (const station of [2, 3, 4]) {
      const badge = $(`nav-badge-${station}`);
      if (!badge) continue;
      const open = ticketsAt(station).filter((item) => phaseOf(item) === OPEN_PHASE_OF_STATION[station]).length;
      badge.hidden = open === 0;
      badge.textContent = String(open);
      badge.title = L(`${open} offene(s) Ticket(s)`, `${open} open ticket(s)`);
    }
  }

  // --- Build Full Cartoon Interface ---
  function nextStep(ticket) {
    const phase = phaseOf(ticket);
    if (phase === "human_review") return L("Nächster Schritt: Beweisclip prüfen und entscheiden.", "Next: review the evidence clip and decide.");
    if (phase === "no_response" || phase === "reported") return L("Meldung im simulierten Postfach. Entwarnung weiterhin möglich.", "Report in the simulated inbox. An all-clear is still possible.");
    if (["dismissed", "rejected", "corrected"].includes(phase)) return L("Fall abgeschlossen. Kein weiterer Einsatz nötig.", "Case closed. No further dispatch needed.");
    if (["cancelled", "failed", "source_ended", "normal"].includes(phase)) return L("Starte einen neuen Fall an der Kamera.", "Start a new case at the camera.");
    return L("Die Analyse läuft. Warte auf das Ergebnis.", "Analysis is running. Wait for the result.");
  }

  function renderTicketJourney() {
    const ticket = datacenterTicket();
    setText("journey-ticket-title", ticket ? ticketName(ticket) : L("Noch kein Ticket", "No ticket yet"));
    setText("journey-ticket-status", ticket ? phaseLabel(phaseOf(ticket)) : L("Beginne an der Kamera.", "Start at the camera."));
    setText("journey-ticket-next", ticket ? nextStep(ticket) : L("Kamera starten, Ereignis einblenden, Ticket verfolgen.", "Start the camera, inject an event, follow the ticket."));
    const select = $("journey-ticket-select");
    const numbered = ticketOrder.map(id => tickets.get(id)).filter(item => item.number);
    if (select && listChanged("journey-ticket-select", ticketSignature())) {
      select.replaceChildren(...numbered.map(item => h("option", { value: item.caseId, textContent: `${ticketName(item)} · ${phaseLabel(phaseOf(item))}` })));
      select.value = ticket && ticket.number ? ticket.caseId : "";
      select.disabled = numbered.length === 0;
      select.closest(".ticket-picker").hidden = numbered.length < 2;
    }
    for (const station of [1, 2, 3, 4]) {
      const node = $(`nav-node-room-${station}`);
      if (node) node.classList.toggle("ticket-location", Boolean(ticket && ticket.number && stationOf(ticket) === station));
    }
  }

  function buildCartoonDOM(container) {
    const root = h("div", { className: "cartoon-stage road-cartoon-stage" });
    const details = (title, children, className = "case-disclosure") => h("details", { className }, [h("summary", { textContent: title }), ...children]);
    const avatar = (name, alt) => h("div", { className: "cartoon-avatar" }, [h("img", { src: `/assets/${name}.png`, alt, width: "96", height: "96" })]);
    const person = (name, alt, title, speechId, copy) => h("div", { className: "cartoon-character-row" }, [
      avatar(name, alt),
      h("div", { className: "cartoon-speech-bubble" }, [h("div", { className: "cartoon-speaker-name", textContent: title }), h("p", { id: speechId, className: "cartoon-speech-text", "aria-live": "polite", textContent: copy })]),
    ]);
    const action = (id, copy, handler, variant = "", disabled = false) => h("button", { id, type: "button", className: `chunky-btn ${variant}`, textContent: copy, onclick: handler, disabled });
    const coffee = (id, hidden = false) => h("div", { id, className: "coffee-away", hidden }, [
      h("span", { className: "coffee-away-icon", "aria-hidden": "true", textContent: "☕" }),
      h("div", {}, [h("strong", { textContent: L("Ich bin kurz weg", "Be right back") }), h("p", { textContent: L("Die Frist läuft weiter.", "The deadline keeps running.") })]),
      h("span", { "aria-hidden": "true", textContent: "→" }),
    ]);
    const roomNav = (idx) => h("nav", { className: "room-pagination", "aria-label": L("Station wechseln", "Change station") }, [
      h("button", { id: `room-back-${idx}`, type: "button", className: "room-arrow", textContent: "←", "aria-label": L("Vorherige Station", "Previous station"), disabled: idx === 1, onclick: () => switchRoom(idx - 1) }),
      h("span", { className: "room-pagination-caption", textContent: `${idx} / 4` }),
      h("button", { id: `room-next-${idx}`, type: "button", className: "room-arrow", textContent: "→", "aria-label": L("Nächste Station", "Next station"), disabled: idx === 4, onclick: () => switchRoom(idx + 1) }),
    ]);
    const room = (idx, children) => root.appendChild(h("section", { id: `cartoon-room-${idx}`, className: "cartoon-room", hidden: idx !== 1, "aria-label": t(`room${idx}`) }, [h("div", { className: "cartoon-room__stage" }, children), roomNav(idx)]));

    root.appendChild(h("header", { className: "cartoon-header" }, [
      h("div", { className: "cartoon-brand" }, [h("div", {}, [h("h2", { id: "cartoon-title", className: "cartoon-brand__title", textContent: t("title") }), h("p", { id: "cartoon-subtitle", className: "cartoon-brand__subtitle", textContent: t("subtitle") })])]),
      h("div", { className: "cartoon-top-controls" }, [
        h("button", { id: "cartoon-details-btn", className: "cartoon-pill-btn", type: "button", "aria-expanded": "false", textContent: t("detailsShow"), onclick: () => {
          const open = container.classList.toggle("road-workspace--details");
          setText("cartoon-details-btn", open ? t("detailsHide") : t("detailsShow"));
          $("cartoon-details-btn").setAttribute("aria-expanded", String(open));
        } }),
        h("button", { id: "cartoon-sound-btn", className: "cartoon-pill-btn", type: "button", textContent: global.RoadSFX && global.RoadSFX.getMuted() ? t("soundOff") : t("soundOn"), onclick: () => {
          if (!global.RoadSFX) return;
          const next = !global.RoadSFX.getMuted(); global.RoadSFX.setMuted(next); setText("cartoon-sound-btn", next ? t("soundOff") : t("soundOn"));
        } }),
      ]),
    ]));

    const navNode = (idx) => h("button", { id: `nav-node-room-${idx}`, type: "button", className: `cartoon-station-node ${idx === 1 ? "active" : ""}`, "aria-current": idx === 1 ? "step" : "false", onclick: () => switchRoom(idx) }, [
      h("span", { id: `nav-text-${idx}`, textContent: t(`room${idx}`) }), h("span", { id: `nav-badge-${idx}`, className: "cartoon-station-badge", hidden: true }),
    ]);
    root.appendChild(h("nav", { className: "cartoon-conduit-nav", "aria-label": L("Der Weg des Tickets", "The ticket journey") }, [navNode(1), navNode(2), navNode(3), navNode(4)]));
    root.appendChild(h("div", { className: "ticket-journey", "aria-live": "polite" }, [
      h("div", { className: "ticket-journey-summary" }, [h("strong", { id: "journey-ticket-title" }), h("span", { id: "journey-ticket-status" }), h("p", { id: "journey-ticket-next" })]),
      h("label", { className: "ticket-picker" }, [h("span", { textContent: L("Ticket auswählen", "Choose a ticket") }), h("select", { id: "journey-ticket-select", className: "cartoon-select", disabled: true })]),
    ]));
    const inject = (event, id, image, copy, variant) => h("div", { className: "scenario-row" }, [
      h("img", { className: "scenario-icon", src: `/assets/${image}.png`, alt: "", width: "64", height: "64" }),
      action(id, copy, () => void triggerEvent(event), variant),
    ]);
    room(1, [
      person("alex", L("Comicporträt von Alex", "Comic portrait of Alex"), L("Alex · Techniker B4 Nord", "Alex · Technician B4 North"), "speech-alex", t("alexIntro")),
      h("div", { className: "traffic-screen-wrap" }, [
        h("div", { className: "traffic-monitor" }, [
          h("div", { className: "traffic-monitor__header" }, [h("span", { textContent: L("B4 Nord · synthetische Straße", "B4 North · synthetic road") }), h("span", { id: "cartoon-cam-status", textContent: "[● STANDBY]" }), action("btn-toggle-cam", L("Kamera ein / aus", "Camera on / off"), () => void toggleCamera(), "chunky-btn--power")]),
          h("p", { id: "cartoon-source-notice", className: "source-ended-notice", role: "status", hidden: true }),
          h("div", { id: "cartoon-feed-mount", className: "traffic-feed-box" }),
        ]),
        h("div", { className: "traffic-control-panel" }, [
          h("h3", { textContent: L("Ereignis einblenden", "Inject an event") }),
          h("label", { className: "cartoon-field" }, [h("span", { textContent: L("Modell für den nächsten Fall", "Model for the next case") }), h("select", { id: "cartoon-model-mode", className: "cartoon-select" })]),
          h("label", { className: "cartoon-field" }, [h("span", { textContent: L("Feste Offline-Testantwort (Profil)", "Fixed offline test answer (profile)") }), h("select", { id: "cartoon-fixture-profile", className: "cartoon-select" })]),
          h("div", { className: "cartoon-btn-grid" }, [
            inject("wrong_way", "btn-sim-wrongway", "wrong-way", L("Falschfahrer einblenden", "Inject wrong-way driver"), "chunky-btn--wrongway"),
            inject("bird", "btn-sim-bird", "bird", L("Vogel einblenden", "Inject bird"), "chunky-btn--bird"),
            inject("leaf", "btn-sim-leaf", "leaf", L("Blatt einblenden", "Inject leaf"), "chunky-btn--leaf"),
          ]),
          h("p", { id: "event-cooldown-note", className: "event-cooldown-note", role: "status", hidden: true }),
          h("p", { className: "scenario-note", textContent: L("Ein Ereignis verändert die Szene. Erst die Bildanalyse erstellt ein Ticket.", "An event changes the scene. Only the image analysis creates a ticket.") }),
        ]),
      ]),
    ]);
    room(2, [
      person("kira", L("Comicporträt von Dr. Kira", "Comic portrait of Dr. Kira"), L("Dr. Kira · Einordnung", "Dr. Kira · Assessment"), "speech-kira", t("kiraIntro")),
      h("div", { className: "inference-transparency-card" }, [
        h("div", { id: "transparency-pill", className: "transparency-pill" }, [h("span", { id: "transparency-pill-text" })]),
        h("p", { id: "transparency-notice", className: "transparency-notice" }),
      ]),
      h("div", { id: "datacenter-verdict-box", className: "datacenter-verdict" }),
      details(L("Analyse, Belege und Ereignisprotokoll", "Analysis, evidence and event log"), [
        h("div", { className: "pipeline-flow-banner" }, [h("div", { id: "stage-chip-1", textContent: L("Stufe 1 · OpenCV: Bewegung und Richtung", "Stage 1 · OpenCV: motion and direction") }), h("div", { id: "stage-chip-2", textContent: L("Stufe 2 · Modell: Einordnung des Clips", "Stage 2 · Model: clip assessment") })]),
        h("div", { className: "transparency-grid" }, [
          h("div", { className: "transparency-item" }, [h("span", { className: "label", textContent: L("Modus / Profil", "Mode / profile") }), h("span", { id: "transparency-profile-val", className: "value" })]),
          h("div", { className: "transparency-item" }, [h("span", { className: "label", textContent: L("Externes Modell", "External model") }), h("span", { id: "transparency-native-val", className: "value" })]),
        ]),
        h("div", { id: "datacenter-case-detail", className: "case-technical-data" }),
        h("h4", { textContent: L("Tickets in der Einordnung", "Tickets at assessment") }), h("div", { id: "datacenter-queue-list", className: "datacenter-queue-list" }),
        h("h4", { textContent: L("Ereignisprotokoll", "Event log") }), h("div", { id: "datacenter-console", className: "datacenter-console" }),
      ]),
    ]);
    room(3, [
      person("schmidt", L("Comicporträt von Officer Schmidt", "Comic portrait of Officer Schmidt"), L("Officer Schmidt · Leitstelle Alpha", "Officer Schmidt · Dispatch Alpha"), "speech-schmidt", t("schmidtIntro")),
      h("div", { className: "dispatch-grid" }, [
        h("div", { id: "schmidt-video-mount", className: "traffic-monitor" }, [h("div", { className: "traffic-monitor__header", textContent: L("Derselbe Beweisclip", "The same evidence clip") }), h("div", { id: "cartoon-review-video-box", className: "traffic-feed-box" }), details(L("Weitere Tickets an diesem Tisch", "Other tickets at this desk"), [h("div", { id: "schmidt-ticket-list", className: "schmidt-ticket-list" })])]),
        h("div", { className: "dispatch-desk" }, [
          h("h3", { textContent: L("Deine Entscheidung", "Your decision") }), h("p", { id: "schmidt-decision-status", className: "decision-status", "aria-live": "polite" }),
          h("p", { id: "schmidt-countdown", className: "decision-time", "aria-live": "off" }),
          h("div", { className: "dispatch-actions" }, [action("btn-schmidt-radio", t("schmidtRadio"), () => decideTicket(focusAt(3), "confirm", "speech-schmidt"), "chunky-btn--radio", true), action("btn-schmidt-reject", t("schmidtFalseAlarm"), () => decideTicket(focusAt(3), "reject", "speech-schmidt"), "", true), action("btn-schmidt-coffee", t("schmidtCoffee"), onSchmidtCoffee, "chunky-btn--coffee", true)]),
          coffee("schmidt-away", true),
        ]),
      ]),
    ]);
    room(4, [
      person("weber", L("Comicporträt von Officer Weber", "Comic portrait of Officer Weber"), L("Officer Weber · Leitstelle Beta", "Officer Weber · Dispatch Beta"), "speech-weber", t("weberAlert")),
      h("div", { className: "beta-alert-box" }, [
        coffee("weber-sticky"),
        h("div", { id: "beta-countdown-clock", className: "beta-countdown-display", textContent: "–" }),
        h("p", { id: "beta-countdown-caption", className: "cartoon-countdown-caption" }),
        h("div", { id: "beta-alert-msg", className: "case-report-message", "aria-live": "polite", hidden: true }),
        h("div", { className: "dispatch-actions" }, [action("btn-manual-dispatch", t("weberManual"), () => decideTicket(deadlineTicket(), "confirm", "speech-weber"), "chunky-btn--radio", true), action("btn-weber-reject", t("schmidtFalseAlarm"), () => decideTicket(deadlineTicket(), "reject", "speech-weber"), "", true)]),
        details(L("Meldungsbeleg und weitere Tickets", "Report receipt and other tickets"), [h("p", { id: "beta-receipt-detail", className: "case-technical-data" }), h("div", { id: "beta-ticket-list", className: "beta-ticket-list" })]),
      ]),
    ]);
    root.appendChild(h("div", { className: "patrol-car-stage", hidden: true, "aria-label": L("Simulierter Einsatz", "Simulated dispatch") }, [h("p", { className: "patrol-dispatch-label", textContent: L("Meldung an simuliertes Behördenpostfach gesendet", "Report sent to simulated authority inbox") }), h("div", { className: "patrol-car-road" }), h("div", { id: "cartoon-patrol-car", className: "patrol-car" }, [h("img", { src: "/assets/patrol.png", alt: L("Spielzeug-Polizeiauto für den simulierten Einsatz", "Toy police car for the simulated dispatch"), width: "210", height: "145" })])]));
    root.querySelector("#journey-ticket-select").addEventListener("change", event => selectTicket(event.target.value));
    return root;
  }

  function refreshI18n() {
    setText("cartoon-title", t("title"));
    setText("cartoon-subtitle", t("subtitle"));
    setText("cartoon-sound-btn", global.RoadSFX && global.RoadSFX.getMuted() ? t("soundOff") : t("soundOn"));
    const container = $("cartoon-details-btn") && $("cartoon-details-btn").closest(".road-workspace--game");
    setText("cartoon-details-btn", container && container.classList.contains("road-workspace--details") ? t("detailsHide") : t("detailsShow"));
    for (const idx of [1, 2, 3, 4]) setText(`nav-text-${idx}`, t(`room${idx}`));
    setText("follow-btn-1", t("followCableTo2"));
    setText("follow-btn-2", t("followCableTo3"));
    setText("follow-btn-3", t("followCableTo4"));
    setText("btn-schmidt-radio", t("schmidtRadio"));
    setText("btn-schmidt-coffee", t("schmidtCoffee"));
    setText("btn-manual-dispatch", t("weberManual"));

    setText("speech-weber", t("weberAlert"));
    render();
  }

  // --- Public Mount Function ---
  // options: { roadMount, apiClient, workplace, openCase } from app.js. Without a
  // road mount the game is not shown, so the real camera UI stays usable.
  function mountCartoonGame(container, options) {
    if (!container || !options || !options.roadMount) return null;
    if (mountedHandle) return mountedHandle;
    env = options;
    container.classList.add("road-workspace--game");
    const gameDom = buildCartoonDOM(container);
    container.prepend(gameDom);

    let viewport = null;
    let viewportHome = null;
    function attachLiveFeedToCartoon() {
      const feedBox = $("cartoon-feed-mount");
      const realViewport = container.querySelector(".road-camera__viewport");
      if (!feedBox || !realViewport) return false;
      if (realViewport.parentElement !== feedBox) {
        viewport = realViewport;
        viewportHome = { parent: realViewport.parentElement, next: realViewport.nextSibling };
        feedBox.appendChild(realViewport);
      }
      return true;
    }
    let attachTimer = attachLiveFeedToCartoon() ? null : window.setInterval(() => {
      if (attachLiveFeedToCartoon()) {
        window.clearInterval(attachTimer);
        attachTimer = null;
      }
    }, 150);

    const listeners = [
      ["road-case-started", onCaseStarted],
      ["road-case-changed", onCaseChanged],
      ["road-state", onRoadState],
      ["roaddecision", onDecision],
    ];
    for (const [name, handler] of listeners) container.addEventListener(name, handler);
    const modeSelect = $("cartoon-model-mode");
    if (modeSelect) modeSelect.addEventListener("change", renderModelOptions);
    const tickTimer = window.setInterval(() => {
      renderEventCooldown();
      renderBeta();
      if (currentRoom === 3) renderSchmidt();
    }, 500);

    mountedHandle = {
      destroy: () => {
        if (attachTimer) window.clearInterval(attachTimer);
        window.clearInterval(tickTimer);
        if (bannerTimer) window.clearTimeout(bannerTimer);
        bannerTimer = null;
        for (const [name, handler] of listeners) container.removeEventListener(name, handler);
        revokeClipView();
        if (viewport && viewportHome && viewportHome.parent && viewportHome.parent.isConnected) {
          viewportHome.parent.insertBefore(viewport, viewportHome.next && viewportHome.next.parentNode === viewportHome.parent ? viewportHome.next : null);
        }
        container.classList.remove("road-workspace--game", "road-workspace--details");
        gameDom.remove();
        tickets.clear();
        ticketOrder = [];
        listSignatures.clear();
        selectedCaseId = null;
        env = null;
        mountedHandle = null;
      }
    };

    const state = cameraState();
    if (state && state.caseId) ticketFor(state.caseId);
    renderModelOptions();
    void loadCapabilities();
    render();
    return mountedHandle;
  }

  global.RoadGame = {
    mountCartoonGame,
    switchRoom,
    triggerEvent,
  };
})(window);
