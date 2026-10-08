/**
 * FlowVision Emergency Leitstelle – Interactive Browser Game Engine
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

  // --- Cartoon SVG Assets (Clean inline SVG Blobs) ---
  const SVGS = {
    technicianAlex: `<svg viewBox="0 0 100 100" width="72" height="72" xmlns="http://www.w3.org/2000/svg">
      <circle cx="50" cy="50" r="46" fill="#fef08a" stroke="#1e293b" stroke-width="4"/>
      <!-- Cap -->
      <path d="M 22 40 Q 50 18 78 40 L 86 44 Q 50 36 14 44 Z" fill="#0284c7" stroke="#1e293b" stroke-width="3"/>
      <!-- Face -->
      <circle cx="50" cy="52" r="26" fill="#fed7aa" stroke="#1e293b" stroke-width="3"/>
      <!-- Eyes & Smile -->
      <circle cx="42" cy="48" r="3.5" fill="#1e293b"/>
      <circle cx="58" cy="48" r="3.5" fill="#1e293b"/>
      <path d="M 44 58 Q 50 64 56 58" fill="none" stroke="#1e293b" stroke-width="3" stroke-linecap="round"/>
      <!-- Headset -->
      <path d="M 28 48 Q 28 32 50 30 Q 72 32 72 48" fill="none" stroke="#334155" stroke-width="4"/>
      <circle cx="28" cy="48" r="5" fill="#0284c7" stroke="#1e293b" stroke-width="2"/>
      <path d="M 28 48 Q 38 66 46 62" fill="none" stroke="#334155" stroke-width="3"/>
      <circle cx="46" cy="62" r="3" fill="#f87171"/>
      <!-- Safety Vest -->
      <path d="M 28 78 L 72 78 L 68 96 L 32 96 Z" fill="#eab308" stroke="#1e293b" stroke-width="3"/>
      <line x1="42" y1="78" x2="40" y2="96" stroke="#ffffff" stroke-width="3"/>
      <line x1="58" y1="78" x2="60" y2="96" stroke="#ffffff" stroke-width="3"/>
    </svg>`,

    drKira: `<svg viewBox="0 0 100 100" width="72" height="72" xmlns="http://www.w3.org/2000/svg">
      <circle cx="50" cy="50" r="46" fill="#e0f2fe" stroke="#1e293b" stroke-width="4"/>
      <!-- Hair Bun -->
      <circle cx="50" cy="24" r="14" fill="#78350f" stroke="#1e293b" stroke-width="3"/>
      <!-- Hair Base -->
      <path d="M 24 50 Q 50 24 76 50 Q 80 68 76 74 Q 50 64 24 74 Z" fill="#92400e" stroke="#1e293b" stroke-width="3"/>
      <!-- Face -->
      <circle cx="50" cy="52" r="25" fill="#ffedd5" stroke="#1e293b" stroke-width="3"/>
      <!-- Glasses -->
      <rect x="34" y="44" width="13" height="10" rx="3" fill="none" stroke="#0284c7" stroke-width="3"/>
      <rect x="53" y="44" width="13" height="10" rx="3" fill="none" stroke="#0284c7" stroke-width="3"/>
      <line x1="47" y1="49" x2="53" y2="49" stroke="#0284c7" stroke-width="3"/>
      <circle cx="40" cy="49" r="2.5" fill="#1e293b"/>
      <circle cx="60" cy="49" r="2.5" fill="#1e293b"/>
      <!-- Smile -->
      <path d="M 45 60 Q 50 65 55 60" fill="none" stroke="#1e293b" stroke-width="3" stroke-linecap="round"/>
      <!-- Lab Coat -->
      <path d="M 28 77 L 72 77 L 68 96 L 32 96 Z" fill="#ffffff" stroke="#1e293b" stroke-width="3"/>
      <path d="M 44 77 L 50 86 L 56 77" fill="#0284c7"/>
    </svg>`,

    officerSchmidt: `<svg viewBox="0 0 100 100" width="72" height="72" xmlns="http://www.w3.org/2000/svg">
      <circle cx="50" cy="50" r="46" fill="#dbeafe" stroke="#1e293b" stroke-width="4"/>
      <!-- Police Cap -->
      <path d="M 20 40 Q 50 16 80 40 L 86 46 Q 50 36 14 46 Z" fill="#1e3a8a" stroke="#1e293b" stroke-width="3"/>
      <polygon points="50,28 54,34 46,34" fill="#fbbf24" stroke="#b45309" stroke-width="1"/>
      <!-- Face -->
      <circle cx="50" cy="54" r="26" fill="#fed7aa" stroke="#1e293b" stroke-width="3"/>
      <!-- Eyes & Friendly Mustache -->
      <circle cx="42" cy="50" r="3.5" fill="#1e293b"/>
      <circle cx="58" cy="50" r="3.5" fill="#1e293b"/>
      <path d="M 38 60 Q 45 56 50 61 Q 55 56 62 60 Q 50 66 38 60 Z" fill="#78350f" stroke="#1e293b" stroke-width="2"/>
      <!-- Uniform Shirt & Tie -->
      <path d="M 26 78 L 74 78 L 70 96 L 30 96 Z" fill="#3b82f6" stroke="#1e293b" stroke-width="3"/>
      <polygon points="50,78 54,92 50,96 46,92" fill="#1e293b"/>
      <circle cx="36" cy="85" r="3" fill="#fbbf24"/>
    </svg>`,

    officerWeberEmpty: `<svg viewBox="0 0 100 100" width="72" height="72" xmlns="http://www.w3.org/2000/svg">
      <circle cx="50" cy="50" r="46" fill="#fee2e2" stroke="#1e293b" stroke-width="4"/>
      <!-- Empty Office Chair Backrest -->
      <rect x="30" y="24" width="40" height="42" rx="8" fill="#475569" stroke="#1e293b" stroke-width="3"/>
      <line x1="38" y1="36" x2="62" y2="36" stroke="#64748b" stroke-width="2"/>
      <line x1="38" y1="46" x2="62" y2="46" stroke="#64748b" stroke-width="2"/>
      <line x1="38" y1="56" x2="62" y2="56" stroke="#64748b" stroke-width="2"/>
      <!-- Coffee Mug on desk -->
      <rect x="42" y="70" width="16" height="18" rx="2" fill="#ef4444" stroke="#1e293b" stroke-width="2"/>
      <path d="M 58 74 Q 65 78 58 84" fill="none" stroke="#1e293b" stroke-width="2"/>
      <!-- Steam -->
      <path d="M 46 66 Q 44 60 48 56" fill="none" stroke="#94a3b8" stroke-width="2" stroke-linecap="round"/>
      <path d="M 54 66 Q 56 60 52 56" fill="none" stroke="#94a3b8" stroke-width="2" stroke-linecap="round"/>
    </svg>`,

    patrolCar: `<svg viewBox="0 0 160 80" width="140" height="70" xmlns="http://www.w3.org/2000/svg">
      <!-- Car Body -->
      <path d="M 15 55 L 25 35 L 55 24 L 115 24 L 138 38 L 155 45 L 155 60 L 15 60 Z" fill="#ffffff" stroke="#1e293b" stroke-width="3"/>
      <!-- Police Blue Stripe -->
      <path d="M 15 48 L 155 48 L 155 58 L 15 58 Z" fill="#0284c7"/>
      <text x="85" y="56" fill="#ffffff" font-family="system-ui, sans-serif" font-weight="900" font-size="9" text-anchor="middle" letter-spacing="1">POLIZEI</text>
      <!-- Windows -->
      <polygon points="56,27 82,27 82,42 42,42" fill="#bae6fd" stroke="#1e293b" stroke-width="2"/>
      <polygon points="88,27 114,27 132,42 88,42" fill="#bae6fd" stroke="#1e293b" stroke-width="2"/>
      <!-- Siren on Roof -->
      <rect x="80" y="16" width="12" height="8" rx="2" fill="#ef4444" stroke="#1e293b" stroke-width="2" class="patrol-car-siren-light"/>
      <line x1="86" y1="12" x2="86" y2="16" stroke="#fbbf24" stroke-width="2"/>
      <!-- Wheels -->
      <circle cx="42" cy="62" r="14" fill="#334155" stroke="#1e293b" stroke-width="3"/>
      <circle cx="42" cy="62" r="6" fill="#cbd5e1" stroke="#1e293b" stroke-width="2"/>
      <circle cx="128" cy="62" r="14" fill="#334155" stroke="#1e293b" stroke-width="3"/>
      <circle cx="128" cy="62" r="6" fill="#cbd5e1" stroke="#1e293b" stroke-width="2"/>
      <!-- Headlights -->
      <polygon points="152,47 156,47 156,53 152,53" fill="#fde047" stroke="#1e293b" stroke-width="1.5"/>
    </svg>`
  };


  // --- Labels -----------------------------------------------------------------
  // Every ticket, verdict, hash, clip and deadline shown by the game comes from
  // the local backend (case snapshots, model exchanges, clip bytes). The game
  // only adds the cartoon presentation around that data.
  const EVENT_LABELS = {
    wrong_way: "⚠️ Falschfahrer",
    bird: "🐦 Vogel",
    leaf: "🍂 Blatt",
  };
  const PHASE_LABELS = {
    starting: "Fall startet",
    running: "Analyse läuft",
    model_review: "Modellprüfung",
    human_review: "Menschliche Prüfung offen",
    no_response: "Frist abgelaufen · automatisch gemeldet",
    reported: "An Behörde gemeldet",
    dismissed: "Entwarnung durch Modell",
    rejected: "Als Fehlalarm verworfen",
    corrected: "Entwarnung nachgesendet",
    cancelled: "Abgebrochen",
    normal: "Kein Befund im Beobachtungsfenster",
    source_ended: "Kameraquelle beendet",
    failed: "Fehlgeschlagen",
  };
  const OBJECT_LABELS = {
    vehicle: "Fahrzeug",
    bird: "Vogel",
    leaf: "Blatt",
    unknown: "unklar",
  };
  const FIXTURE_PROFILE_LABELS = {
    pending: "Fahrzeug, Gefahr, Sicherheit 0,80 → menschliche Prüfung",
    high: "Fahrzeug, Gefahr, Sicherheit 0,99 → automatische Meldung",
    bird: "Vogel, keine Gefahr (0,99)",
    leaf: "Blatt, keine Gefahr (0,99)",
    unclear: "Unklares Ergebnis",
    followup: "Fordert eine Folgebeobachtung an",
    error: "Modellfehler",
  };
  const INJECT_BLOCK_REASONS = {
    destroyed: "Die Kameraansicht wurde geschlossen.",
    source_closed: "Die Kamera läuft nicht.",
    review_locked: "Der aktuelle Fall ist in Prüfung – neue Ereignisse sind bis zur Entscheidung gesperrt.",
    warming_up: "Die Analyse wärmt sich noch auf (drei echte Startframes).",
    run_inactive: "Der Analyse-Lauf dieses Falls ist beendet.",
    source_changed: "Die Kameraquelle wurde inzwischen gewechselt.",
  };
  const REVIEW_PHASES = new Set(["model_review", "human_review"]);
  const ACTIVE_PHASES = new Set(["starting", "running", "model_review", "human_review"]);

  // --- State ------------------------------------------------------------------
  let env = null; // { roadMount, apiClient, workplace, openCase }
  let mountedHandle = null;
  let currentRoom = 1;
  let currentLang = "de";
  let capabilities = null;
  const tickets = new Map(); // caseId -> ticket
  let ticketOrder = []; // newest first
  let ticketSequence = 0;
  let selectedCaseId = null;
  let startPromise = null;
  let eventBusy = false;
  let bannerTimer = null;
  let clipView = { caseId: null, clipId: null, url: null, generation: 0 };

  // Translation table (static labels; live case data is shown as delivered)
  const I18N = {
    de: {
      title: "FlowVision – Emergency Einsatzleitstelle",
      subtitle: "Video-Analyse-Demonstrator · alle Falldaten live vom lokalen Server",
      soundOn: "🔊 Ton: An",
      soundOff: "🔇 Ton: Aus",
      langToggle: "🌐 EN",
      detailsShow: "🔍 Technische Fallansicht",
      detailsHide: "🔍 Fallansicht ausblenden",
      room1: "🚦 1. Verkehrsraum",
      room2: "🏢 2. Rechenzentrum",
      room3: "🚓 3. Leitstelle Alpha",
      room4: "🚨 4. Leitstelle Beta (Frist)",
      followCableTo2: "Dem Datenkabel ins Rechenzentrum folgen ➔",
      followCableTo3: "Dem Alarmkabel zur Leitstelle folgen ➔",
      followCableTo4: "Dem Kabel zu Tisch 2 folgen ➔",
      alexIntro: "Hallo! Ich bin Techniker Alex an B4 Nord. Die Kamera zeigt eine synthetische Verkehrsszene. OpenCV analysiert die echten gerenderten Frames auf Bewegung und Gegenverkehr. Blende mit den Knöpfen ein Ereignis ein – ob daraus ein Befund wird, entscheidet allein die Analyse.",
      kiraIntro: "Willkommen im Rechenzentrum! Löst die Bildanalyse einen Befund aus, schneidet der Server einen Clip (5 s Vorlauf, 10 s Nachlauf), hasht ihn mit SHA-256 und legt ihn dem Modell vor. Hier siehst du, was das Modell tatsächlich geantwortet hat.",
      schmidtIntro: "Leitstelle Alpha, Officer Schmidt. Ich prüfe die Fälle, die das Modell an Menschen weitergibt – mit dem echten Beweisclip vom Server.",
      schmidtRadio: "📻 Gefahr bestätigen (Einsatz)",
      schmidtFalseAlarm: "❌ Gefahr verneinen",
      schmidtCoffee: "☕ Kaffeepause (niemand entscheidet)",
      schmidtCoffeeAway: "Ich hol mir schnell einen Kaffee! Niemand entscheidet jetzt – an Tisch 2 läuft die echte Frist des Falls weiter.",
      weberSticky: "☕ Bin kurz Kaffee holen – Weber",
      weberAlert: "Officer Weber ist abwesend. Läuft die Frist eines Falls ohne Entscheidung ab, meldet der Server ihn automatisch an das simulierte Behördenpostfach.",
      weberManual: "🚨 Gefahr manuell bestätigen"
    },
    en: {
      title: "FlowVision – Emergency Dispatch Mission Control",
      subtitle: "Video analysis demonstrator · all case data live from the local server",
      soundOn: "🔊 Sound: ON",
      soundOff: "🔇 Sound: OFF",
      langToggle: "🌐 DE",
      detailsShow: "🔍 Technical case view",
      detailsHide: "🔍 Hide case view",
      room1: "🚦 1. Traffic Outpost",
      room2: "🏢 2. Datacenter",
      room3: "🚓 3. Dispatch Alpha",
      room4: "🚨 4. Dispatch Beta (Deadline)",
      followCableTo2: "Follow Data Cable to Datacenter ➔",
      followCableTo3: "Follow Alert Cable to Dispatch ➔",
      followCableTo4: "Follow Cable to Desk 2 ➔",
      alexIntro: "Hello! I am technician Alex at B4 North. The camera shows a synthetic traffic scene. OpenCV analyses the real rendered frames for motion and opposing traffic. Inject an event with the buttons – only the analysis decides whether it becomes a finding.",
      kiraIntro: "Welcome to the datacenter! When the image analysis raises a finding, the server cuts a clip (5 s before, 10 s after), hashes it with SHA-256 and sends it to the model. Here you see what the model actually answered.",
      schmidtIntro: "Dispatch Alpha, Officer Schmidt. I review the cases the model hands over to humans – with the real evidence clip from the server.",
      schmidtRadio: "📻 Confirm hazard (dispatch)",
      schmidtFalseAlarm: "❌ Deny hazard",
      schmidtCoffee: "☕ Coffee break (nobody decides)",
      schmidtCoffeeAway: "Grabbing a coffee! Nobody decides now – the case's real deadline keeps running at desk 2.",
      weberSticky: "☕ Grabbing coffee, be right back – Weber",
      weberAlert: "Officer Weber is away. If a case deadline expires without a decision, the server reports it automatically to the simulated authority inbox.",
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
    if (el) el.textContent = text;
  }

  function formatDecimal(value, digits = 2) {
    return typeof value === "number" && Number.isFinite(value)
      ? value.toLocaleString("de-DE", { minimumFractionDigits: digits, maximumFractionDigits: digits })
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
      ticketSequence += 1;
      ticket = {
        caseId,
        number: ticketSequence,
        createdAt: new Date(),
        scenario: [],
        snapshot: null,
        exchanges: [],
        lastPhase: null,
        dispatched: false,
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
    return phase ? PHASE_LABELS[phase] || phase : "Fall startet";
  }

  function focusTicket() {
    return (selectedCaseId && tickets.get(selectedCaseId)) || (ticketOrder.length ? tickets.get(ticketOrder[0]) : null);
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
      return `Offline-Testantwort (Profil „${profile}“${modelId ? ", " + modelId : ""})`;
    }
    return modelId ? `${modelId} (${snapshot.mode})` : String(snapshot.mode || "–");
  }

  function assessmentText(assessment) {
    if (!assessment || typeof assessment !== "object") return null;
    const object = OBJECT_LABELS[assessment.object_class] || String(assessment.object_class || "unklar");
    const hazard = assessment.hazard === true ? "Gefahr" : assessment.hazard === false ? "keine Gefahr" : "Gefahr unklar";
    return `${object} · ${hazard} · Sicherheit ${formatDecimal(assessment.confidence)}`;
  }

  function scenarioText(ticket) {
    if (!ticket || ticket.scenario.length === 0) return "keine Ereignisse aus dem Spiel eingeblendet";
    return ticket.scenario.map((entry) => EVENT_LABELS[entry.type] || entry.type).join(" → ");
  }

  function updateTicket(caseId, snapshot, exchanges) {
    if (typeof caseId !== "string" || !caseId || !snapshot || typeof snapshot !== "object") return;
    const ticket = ticketFor(caseId);
    ticket.snapshot = snapshot;
    if (Array.isArray(exchanges)) ticket.exchanges = exchanges;
    const phase = phaseOf(ticket);
    if (phase !== ticket.lastPhase) {
      const previous = ticket.lastPhase;
      ticket.lastPhase = phase;
      onPhaseChange(ticket, previous, phase);
    }
  }

  // --- Phase transitions (all derived from server snapshots) ------------------
  function onPhaseChange(ticket, previous, phase) {
    const label = `Fall #${ticket.number}`;
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
      appendDatacenterLog(`[${label}] Modellantwort: ${assessment} · Quelle: ${modelSourceText(ticket)}`);
    }
    if (snapshot.model_error) appendDatacenterLog(`[${label}] Modellfehler: ${snapshot.model_error}`);
    if (phase === "human_review") {
      setCablePulsing("cable-2-3", true);
      const follow = $("follow-btn-2");
      if (follow) follow.hidden = false;
      if (global.RoadSFX) global.RoadSFX.playEventTrigger();
    }
    if (phase === "no_response" && !ticket.dispatched && snapshot.first_receipt) {
      ticket.dispatched = true;
      dispatchPatrolCar();
      appendDatacenterLog(`[${label}] Frist abgelaufen – automatische Meldung (Beleg ${snapshot.first_receipt.receipt_id || "–"})`);
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
    if (detail.case) updateTicket(detail.caseId, detail.case);
    selectedCaseId = ticket.caseId;
    appendDatacenterLog(`[Fall #${ticket.number}] Neuer Fall auf ${detail.sourceId || "Kamera"} gestartet · ${modelSourceText(ticket)}`);
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
      setText("speech-schmidt", `Der Server hat meine Bestätigung für Fall #${ticket.number} angenommen. Die Meldung geht an das simulierte Behördenpostfach – Streife rückt aus!`);
    } else {
      setText("speech-schmidt", `Der Server hat meine Verneinung für Fall #${ticket.number} angenommen. Kein Einsatz; eine schon gesendete Meldung erhält eine Entwarnung.`);
    }
    appendDatacenterLog(`[Fall #${ticket.number}] Menschliche Entscheidung angenommen: ${detail.decision === "confirm" ? "Gefahr bestätigt" : "Gefahr verneint"}`);
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
    car.classList.remove("dispatching");
    void car.offsetWidth; // trigger reflow
    car.classList.add("dispatching");
  }

  // --- Room Switching ---
  function switchRoom(roomNum) {
    if (global.RoadSFX) global.RoadSFX.playClick();
    currentRoom = roomNum;
    [1, 2, 3, 4].forEach((idx) => {
      const navBtn = $(`nav-node-room-${idx}`);
      const roomEl = $(`cartoon-room-${idx}`);
      if (navBtn) navBtn.classList.toggle("active", idx === currentRoom);
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

  function startNewCase() {
    if (startPromise) return startPromise;
    startPromise = (async () => {
      const mount = env.roadMount;
      if (typeof mount.configureCase === "function") mount.configureCase(selectedModelConfig());
      await mount.createSource();
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
      if (state.injectBlocker === "review_locked" || (state.injectBlocker === "run_inactive" && state.warmedUp)) {
        throw new Error(INJECT_BLOCK_REASONS[state.injectBlocker]);
      }
      if (Date.now() >= deadline) {
        throw new Error(INJECT_BLOCK_REASONS[state.injectBlocker] || "Die Kamera ist noch nicht bereit.");
      }
      await wait(200);
    }
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
  }

  async function triggerEvent(eventType) {
    if (eventBusy) return;
    if (!env || !env.roadMount) {
      setText("speech-alex", "Die Straßenkamera ist nicht verbunden. Ohne lokalen Server gibt es hier nichts zu analysieren.");
      return;
    }
    eventBusy = true;
    setEventButtonsDisabled(true);
    if (global.RoadSFX) global.RoadSFX.playEventTrigger();
    try {
      let state = cameraState();
      if (!state.available) {
        throw new Error((capabilities && capabilities.message) || "Die Straßenkamera ist nicht verfügbar (FFmpeg mit libx264 erforderlich).");
      }
      const current = state.caseId ? tickets.get(state.caseId) : null;
      const phase = phaseOf(current);
      if (state.sourceOpen && current && REVIEW_PHASES.has(phase)) {
        throw new Error(`Fall #${current.number} ist in Prüfung (${phaseLabel(phase)}). Entscheide in Raum 3 oder lass die Frist in Raum 4 ablaufen – erst dann startet ein neuer Fall.`);
      }
      if (!state.sourceOpen || !state.caseId || (phase && !ACTIVE_PHASES.has(phase))) {
        setText("speech-alex", "Ich starte eine neue Kameraquelle mit einem neuen Fall und warte, bis die Analyse drei echte Startframes gesehen hat …");
        await startNewCase();
      }
      state = await waitForInjectable(20000);
      const result = await env.roadMount.injectScenarioEvent(eventType);
      if (!result || !result.injected) {
        throw new Error(INJECT_BLOCK_REASONS[result && result.reason] || "Das Ereignis wurde nicht eingeblendet.");
      }
      const ticket = ticketFor(result.caseId || state.caseId);
      ticket.scenario.push({ type: eventType, at: new Date() });
      selectedCaseId = ticket.caseId;
      const label = EVENT_LABELS[eventType] || eventType;
      showBanner(`Szenario eingeblendet: ${label} · Fall #${ticket.number} · OpenCV analysiert jetzt die echten Frames`);
      setText("speech-alex", `${label} ist jetzt in der Live-Szene. Das ist noch kein Befund: Erst wenn die Bildanalyse auf den echten Frames anschlägt, schneidet der Server einen Clip für Fall #${ticket.number}.`);
      appendDatacenterLog(`[Fall #${ticket.number}] Szenario eingeblendet: ${label} (Spielaktion, kein Analysebefund)`);
      render();
    } catch (error) {
      setText("speech-alex", error && error.message ? error.message : "Das Ereignis konnte nicht eingeblendet werden.");
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
        const current = state.caseId ? tickets.get(state.caseId) : null;
        if (current && REVIEW_PHASES.has(phaseOf(current))) {
          throw new Error(`Fall #${current.number} ist in Prüfung. Stoppen würde die offene Prüfung abbrechen – entscheide zuerst in Raum 3.`);
        }
        await env.roadMount.stopSource();
        setText("speech-alex", "Kamera gestoppt. Die Aufnahme wurde als Clip gesichert.");
      } else {
        setText("speech-alex", "Ich starte die Kamera mit einem neuen Fall …");
        await startNewCase();
      }
    } catch (error) {
      setText("speech-alex", error && error.message ? error.message : "Die Kamera konnte nicht umgeschaltet werden.");
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
    if (modes.includes("fixture")) modeSelect.appendChild(h("option", { value: "fixture", textContent: "Offline-Testantwort (lokal, ohne Netz)" }));
    if (modes.includes("gemini_video")) modeSelect.appendChild(h("option", { value: "gemini_video", textContent: "Gemini-Video (extern, kostenpflichtig)" }));
    if (!modeSelect.options.length) modeSelect.appendChild(h("option", { value: "", textContent: "Kein Modellmodus verfügbar" }));
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

  function renderTransparency() {
    const nativeEnabled = Boolean(capabilities && capabilities.native_enabled);
    const ticket = focusTicket();
    const snapshot = ticket && ticket.snapshot;
    const pill = $("transparency-pill-text");
    const pillBox = $("transparency-pill");
    if (pill) {
      pill.textContent = snapshot && snapshot.mode === "gemini_video"
        ? "MODELL: GEMINI-VIDEO (EXTERN, KOSTENPFLICHTIG)"
        : "MODELL: OFFLINE-FIXTURE (FESTE TESTANTWORT)";
    }
    if (pillBox) pillBox.classList.toggle("fallback", !(snapshot && snapshot.mode === "gemini_video"));
    setText("transparency-profile-val", snapshot
      ? `${snapshot.mode}${snapshot.fixture_profile ? "/" + snapshot.fixture_profile : ""}`
      : "noch kein Fall");
    setText("transparency-native-val", nativeEnabled ? "freigegeben (Server mit Kostenfreigabe)" : "nicht freigegeben");
    setText("transparency-notice", nativeEnabled
      ? "Ehrlichkeitshinweis: Der Server erlaubt Gemini-Video. Fälle im Modus „gemini_video“ senden den Clip und Belegbilder an Google; Fixture-Fälle bleiben lokal und liefern feste Testantworten."
      : "Ehrlichkeitshinweis: Kein externes Modell freigegeben (Server ohne --allow-gemini-costs oder ohne GEMINI_API_KEY). Stufe 2 liefert deshalb feste Offline-Testantworten nach dem gewählten Profil – sie bewerten den Bildinhalt nicht. Echt sind die gerenderte Szene, die OpenCV-Analyse der Frames, Clip-Export mit SHA-256, Fristen und das simulierte Behördenpostfach.");
  }

  function renderDatacenter() {
    const ticket = focusTicket();
    const snapshot = ticket && ticket.snapshot;
    const phase = phaseOf(ticket);

    const chip1 = $("stage-chip-1");
    const chip2 = $("stage-chip-2");
    if (chip1) chip1.classList.toggle("active-stage", phase === "running" || phase === "starting");
    if (chip2) chip2.classList.toggle("active-stage", Boolean(phase) && phase !== "running" && phase !== "starting" && phase !== "normal");

    const listEl = $("datacenter-queue-list");
    if (listEl && listChanged("datacenter-queue-list", ticketSignature())) {
      listEl.replaceChildren();
      if (ticketOrder.length === 0) listEl.textContent = "Noch keine Fälle. Starte die Kamera in Raum 1.";
      for (const caseId of ticketOrder.slice(0, 6)) {
        const item = tickets.get(caseId);
        const clip = item.snapshot && item.snapshot.clip;
        const hazard = item.snapshot && item.snapshot.assessment && item.snapshot.assessment.hazard === true;
        listEl.appendChild(h("button", {
          type: "button",
          className: `queue-item ${hazard ? "high-risk" : ""} ${caseId === selectedCaseId ? "active-ticket" : ""}`,
          onclick: () => selectTicket(caseId),
        }, [
          h("strong", { textContent: `Fall #${item.number} · ${phaseLabel(phaseOf(item))}` }),
          h("br"),
          h("small", { textContent: clip ? `Clip ${String(clip.clip_id).slice(0, 8)} · SHA-256 ${shortHash(clip.sha256)}` : "noch kein Clip" }),
        ]));
      }
    }

    const verdictBox = $("datacenter-verdict-box");
    if (verdictBox) {
      verdictBox.replaceChildren();
      if (!ticket) {
        verdictBox.textContent = "Bereit – noch kein Fall.";
      } else {
        let stage1 = "Beobachtet die Szene (Bewegung + Richtung auf echten Frames) …";
        if (phase === "normal") stage1 = "Kein Befund im Beobachtungsfenster.";
        else if (phase === "source_ended" || phase === "cancelled") stage1 = "Analyse ohne Befund beendet.";
        else if (snapshot && snapshot.clip) stage1 = `Befund ausgelöst → Clip ${String(snapshot.clip.clip_id).slice(0, 8)} (SHA-256 ${shortHash(snapshot.clip.sha256)})`;
        else if (phase && phase !== "running" && phase !== "starting") stage1 = "Befund ausgelöst.";
        const assessment = assessmentText(snapshot && snapshot.assessment);
        let stage2 = "wartet auf einen Befund aus Stufe 1";
        if (assessment) stage2 = assessment;
        else if (snapshot && snapshot.model_error) stage2 = `Modellfehler: ${snapshot.model_error}`;
        else if (phase === "model_review") stage2 = "Modell prüft den Clip …";
        const hazard = snapshot && snapshot.assessment ? snapshot.assessment.hazard : null;
        const fixture = snapshot && snapshot.mode === "fixture";
        verdictBox.append(h("div", { className: "stage-breakdown" }, [
          h("span", { className: "stage-badge", textContent: `Fall #${ticket.number} · ${phaseLabel(phase)}` }),
          h("small", { className: "inference-interface-hint", textContent: `Eingeblendetes Szenario: ${scenarioText(ticket)}` }),
          h("span", { className: "stage-badge", textContent: `Stufe 1 (OpenCV): ${stage1}` }),
          h("span", {
            className: `verdict-badge ${hazard === true ? "danger" : hazard === false ? "safe" : ""}`,
            textContent: `Stufe 2 (Modell): ${stage2}`,
          }),
          h("small", { className: "inference-interface-hint", textContent: `Quelle: ${modelSourceText(ticket)}` }),
          fixture && assessment
            ? h("small", { className: "inference-interface-hint", textContent: "Hinweis: Fixture-Antworten sind fest vorgegeben und hängen nicht vom eingeblendeten Szenario ab." })
            : null,
        ].filter(Boolean)));
      }
    }

    const speech = $("speech-kira");
    if (speech && ticket && snapshot) {
      const assessment = snapshot.assessment;
      if (assessment && assessment.hazard === true) {
        speech.textContent = `Fall #${ticket.number}: Das Modell meldet Gefahr (Sicherheit ${formatDecimal(assessment.confidence)}). ${phase === "human_review" ? "Ein Mensch muss jetzt entscheiden – ab in die Leitstelle!" : phaseLabel(phase) + "."}`;
      } else if (assessment && assessment.hazard === false) {
        speech.textContent = `Fall #${ticket.number}: Das Modell meldet keine Gefahr (${OBJECT_LABELS[assessment.object_class] || assessment.object_class}). Kein Einsatz.`;
      } else if (phase === "model_review") {
        speech.textContent = `Fall #${ticket.number}: Der Clip liegt dem Modell vor …`;
      } else if (phase === "running" || phase === "starting") {
        speech.textContent = `Fall #${ticket.number}: Noch kein Befund – die Bildanalyse beobachtet die Szene.`;
      } else {
        speech.textContent = `Fall #${ticket.number}: ${phaseLabel(phase)}.`;
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
          textContent: ticket ? `Fall #${ticket.number}: noch kein Beweisclip (${phaseLabel(phaseOf(ticket))}).` : "Kein Fall ausgewählt.",
        }));
      }
      return;
    }
    if (clipView.caseId === ticket.caseId && clipView.clipId === clip.clip_id) return;
    revokeClipView();
    const generation = clipView.generation;
    clipView.caseId = ticket.caseId;
    clipView.clipId = clip.clip_id;
    box.replaceChildren(h("p", { className: "review-clip-meta", textContent: `Lade Beweisclip ${clip.clip_id.slice(0, 8)} vom Server …` }));
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
            h("span", { className: "proof-badge", textContent: `Beweisclip ${clip.clip_id.slice(0, 8)} · ${formatDecimal(clip.start_seconds, 1)}–${formatDecimal(clip.end_seconds, 1)} s` }),
            h("br"),
            h("small", { textContent: `SHA-256 ${shortHash(clip.sha256)} · ${verified ? "Prüfsumme vom Server bestätigt" : "Prüfsumme weicht ab!"}` }),
          ]),
          video,
        );
        video.play().catch(() => {});
      })
      .catch((error) => {
        if (generation !== clipView.generation) return;
        box.replaceChildren(h("p", { className: "review-clip-meta", textContent: `Clip konnte nicht geladen werden: ${error && error.message ? error.message : "unbekannter Fehler"}` }));
      });
  }

  function renderSchmidt() {
    const ticket = focusTicket();
    const inboxList = $("schmidt-ticket-list");
    if (inboxList && listChanged("schmidt-ticket-list", ticketSignature())) {
      inboxList.replaceChildren();
      if (ticketOrder.length === 0) {
        inboxList.appendChild(h("p", { className: "hint", textContent: "Noch keine Fälle. Starte in Raum 1 die Kamera und blende ein Ereignis ein." }));
      }
      for (const caseId of ticketOrder) {
        const item = tickets.get(caseId);
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
            h("strong", { textContent: `Fall #${item.number}` }),
            h("span", { className: `ticket-badge ${hazard === true ? "danger" : "safe"}`, textContent: badge }),
          ]),
          h("span", { className: "ticket-clip-title", textContent: `${phaseLabel(phaseOf(item))} · ${item.createdAt.toLocaleTimeString()} · Szenario: ${scenarioText(item)}` }),
        ]));
      }
    }

    renderReviewMonitor(ticket);

    const state = decisionState(ticket);
    const radio = $("btn-schmidt-radio");
    const reject = $("btn-schmidt-reject");
    const manual = $("btn-manual-dispatch");
    if (radio) radio.disabled = !state.canConfirm;
    if (manual) manual.disabled = !state.canConfirm;
    if (reject) {
      reject.disabled = !state.canReject;
      reject.textContent = state.canReject && /Entwarnung/.test(state.rejectLabel || "")
        ? "❌ Gefahr verneinen + Entwarnung senden"
        : t("schmidtFalseAlarm");
    }
    const status = $("schmidt-decision-status");
    if (status) {
      if (!ticket) status.textContent = "Kein Fall ausgewählt.";
      else if (!state.open) status.textContent = `Fall #${ticket.number} wird in der Prüfansicht geladen …`;
      else if (state.operationState === "in_flight") status.textContent = "Entscheidung wird an den Server gesendet …";
      else if (state.operationMessage) status.textContent = state.operationMessage;
      else if (state.canConfirm || state.canReject) status.textContent = `Fall #${ticket.number}: Entscheidung möglich (${phaseLabel(phaseOf(ticket))}).`;
      else status.textContent = `Fall #${ticket.number}: keine Entscheidung möglich (${phaseLabel(phaseOf(ticket))}).`;
    }
  }

  function decideSelected(decision) {
    const ticket = focusTicket();
    if (!ticket || !env || !env.workplace || typeof env.workplace.decide !== "function") return;
    const result = env.workplace.decide(ticket.caseId, decision);
    if (!result.started) {
      const reasons = {
        case_not_open: "Dieser Fall ist in der Prüfansicht noch nicht geladen. Einen Moment …",
        not_allowed: `Für Fall #${ticket.number} ist diese Entscheidung gerade nicht möglich (${phaseLabel(phaseOf(ticket))}).`,
      };
      setText("speech-schmidt", reasons[result.reason] || "Die Entscheidung konnte nicht gestartet werden.");
      if (result.reason === "case_not_open" && typeof env.openCase === "function") env.openCase(ticket.caseId);
      return;
    }
    if (global.RoadSFX) global.RoadSFX.playRadio();
    setText("speech-schmidt", `Entscheidung für Fall #${ticket.number} an den lokalen Server gesendet …`);
    render();
  }

  function onSchmidtCoffee() {
    if (global.RoadSFX) global.RoadSFX.playCoffee();
    setText("speech-schmidt", t("schmidtCoffeeAway"));
    setCablePulsing("cable-3-4", true);
    const followBtn3 = $("follow-btn-3");
    if (followBtn3) followBtn3.hidden = false;
  }

  // --- Room 4: real deadline of the open case ---------------------------------------
  function deadlineTicket() {
    const focus = focusTicket();
    if (focus && phaseOf(focus) === "human_review") return focus;
    for (const caseId of ticketOrder) {
      const item = tickets.get(caseId);
      if (phaseOf(item) === "human_review") return item;
    }
    return focus;
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
        ? `Fall #${ticket.number} · ${phaseLabel(phaseOf(ticket))}`
        : "Kein Fall mit offener Frist.";
    }
    if (alertBanner) {
      const receipt = snapshot && snapshot.first_receipt;
      if (snapshot && snapshot.phase === "no_response" && receipt) {
        alertBanner.textContent = `Frist abgelaufen ohne Entscheidung: Der Server hat Fall #${ticket.number} automatisch an das simulierte Behördenpostfach gemeldet (Beleg ${receipt.receipt_id || "–"}). Eine Entwarnung ist in Raum 3 weiterhin möglich.`;
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
      ? (state.runActive ? "[● LIVE · ANALYSE LÄUFT]" : "[● LIVE]")
      : "[● STANDBY]");
    renderDatacenter();
    renderSchmidt();
    renderBeta();
  }

  // --- Build Full Cartoon Interface ---
  function buildCartoonDOM(container) {
    const root = h("div", { className: "cartoon-stage road-cartoon-stage" });

    // 1. Header with Title & Toggles
    const header = h("header", { className: "cartoon-header" }, [
      h("div", { className: "cartoon-brand" }, [
        h("span", { className: "cartoon-brand__badge", textContent: "🚨" }),
        h("div", {}, [
          h("h2", { id: "cartoon-title", className: "cartoon-brand__title", textContent: t("title") }),
          h("p", { id: "cartoon-subtitle", className: "cartoon-brand__subtitle", textContent: t("subtitle") })
        ])
      ]),
      h("div", { className: "cartoon-top-controls" }, [
        h("button", {
          id: "cartoon-details-btn",
          className: "cartoon-pill-btn",
          type: "button",
          textContent: t("detailsShow"),
          onclick: () => {
            const open = container.classList.toggle("road-workspace--details");
            setText("cartoon-details-btn", open ? t("detailsHide") : t("detailsShow"));
          }
        }),
        h("button", {
          id: "cartoon-sound-btn",
          className: "cartoon-pill-btn",
          type: "button",
          textContent: global.RoadSFX && global.RoadSFX.getMuted() ? t("soundOff") : t("soundOn"),
          onclick: () => {
            if (!global.RoadSFX) return;
            const next = !global.RoadSFX.getMuted();
            global.RoadSFX.setMuted(next);
            setText("cartoon-sound-btn", next ? t("soundOff") : t("soundOn"));
          }
        }),
        h("button", {
          id: "cartoon-lang-btn",
          className: "cartoon-pill-btn",
          type: "button",
          textContent: t("langToggle"),
          onclick: () => {
            currentLang = currentLang === "de" ? "en" : "de";
            refreshI18n();
          }
        })
      ])
    ]);
    root.appendChild(header);

    // 2. Conduit Navigation Bar (Rooms + Cables)
    const navNode = (idx, icon) => h("button", {
      id: `nav-node-room-${idx}`,
      type: "button",
      className: `cartoon-station-node ${idx === 1 ? "active" : ""}`,
      onclick: () => switchRoom(idx)
    }, [
      h("span", { className: "cartoon-station-icon", textContent: icon }),
      h("span", { id: `nav-text-${idx}`, textContent: t(`room${idx}`) })
    ]);
    root.appendChild(h("nav", { className: "cartoon-conduit-nav", "aria-label": "Rettungszentrale Räume" }, [
      navNode(1, "🚦"),
      h("div", { id: "cable-1-2", className: "cartoon-cable-segment" }),
      navNode(2, "🏢"),
      h("div", { id: "cable-2-3", className: "cartoon-cable-segment" }),
      navNode(3, "🚓"),
      h("div", { id: "cable-3-4", className: "cartoon-cable-segment" }),
      navNode(4, "🚨")
    ]));

    // 3. Room 1: Verkehrsraumüberwachung (Roadside Station B4 Nord)
    root.appendChild(h("section", { id: "cartoon-room-1", className: "cartoon-room" }, [
      h("div", { className: "cartoon-room__stage" }, [
        h("div", { className: "cartoon-character-row" }, [
          h("div", { className: "cartoon-avatar", innerHTML: SVGS.technicianAlex }),
          h("div", { className: "cartoon-speech-bubble" }, [
            h("div", { className: "cartoon-speaker-name", textContent: "Alex · Techniker B4 Nord" }),
            h("p", { id: "speech-alex", className: "cartoon-speech-text", "aria-live": "polite", textContent: t("alexIntro") })
          ])
        ]),
        h("div", { className: "traffic-screen-wrap" }, [
          h("div", { className: "traffic-monitor" }, [
            h("div", { className: "traffic-monitor__header" }, [
              h("span", { textContent: "CAM-01 · B4 NORD · SYNTHETISCHE SZENE" }),
              h("span", { id: "cartoon-cam-status", textContent: "[● STANDBY]" })
            ]),
            h("div", { id: "cartoon-feed-mount", className: "traffic-feed-box" })
          ]),
          h("div", { className: "traffic-control-panel" }, [
            h("h3", { textContent: "Ereignis-Pult (Szenario einblenden)" }),
            h("label", { className: "cartoon-field" }, [
              h("span", { textContent: "Modell für den nächsten Fall" }),
              h("select", { id: "cartoon-model-mode", className: "cartoon-select" })
            ]),
            h("label", { className: "cartoon-field" }, [
              h("span", { textContent: "Feste Offline-Testantwort (Profil)" }),
              h("select", { id: "cartoon-fixture-profile", className: "cartoon-select" })
            ]),
            h("div", { className: "cartoon-btn-grid" }, [
              h("button", {
                id: "btn-toggle-cam",
                type: "button",
                className: "chunky-btn chunky-btn--power",
                textContent: "📷 Kamera AN / AUS",
                onclick: () => void toggleCamera()
              }),
              h("button", {
                id: "btn-sim-leaf",
                type: "button",
                className: "chunky-btn chunky-btn--leaf",
                textContent: "🍂 Blatt einblenden",
                onclick: () => void triggerEvent("leaf")
              }),
              h("button", {
                id: "btn-sim-bird",
                type: "button",
                className: "chunky-btn chunky-btn--bird",
                textContent: "🐦 Vogel einblenden",
                onclick: () => void triggerEvent("bird")
              }),
              h("button", {
                id: "btn-sim-wrongway",
                type: "button",
                className: "chunky-btn chunky-btn--wrongway",
                textContent: "⚠️ Falschfahrer einblenden!",
                onclick: () => void triggerEvent("wrong_way")
              })
            ]),
            h("button", {
              id: "follow-btn-1",
              type: "button",
              className: "cable-follow-btn",
              textContent: t("followCableTo2"),
              hidden: true,
              onclick: () => switchRoom(2)
            })
          ])
        ])
      ])
    ]));

    // 4. Room 2: AI-Rechenzentrum (Serverraum & Dr. Kira)
    root.appendChild(h("section", { id: "cartoon-room-2", className: "cartoon-room", hidden: true }, [
      h("div", { className: "cartoon-room__stage" }, [
        h("div", { className: "cartoon-character-row" }, [
          h("div", { className: "cartoon-avatar", innerHTML: SVGS.drKira }),
          h("div", { className: "cartoon-speech-bubble" }, [
            h("div", { className: "cartoon-speaker-name", textContent: "Dr. Kira · Vision-Analyse" }),
            h("p", { id: "speech-kira", className: "cartoon-speech-text", "aria-live": "polite", textContent: t("kiraIntro") })
          ])
        ]),
        h("div", { className: "pipeline-flow-banner" }, [
          h("div", { id: "stage-chip-1", className: "pipeline-stage-chip" }, [
            h("span", { className: "stage-badge", textContent: "STUFE 1 (OpenCV)" }),
            h("span", { textContent: "📹 Bewegung (Differenzbild) + Richtung (Graustufen-Patchabgleich) auf echten Frames" })
          ]),
          h("span", { className: "pipeline-arrow", textContent: "➔" }),
          h("div", { id: "stage-chip-2", className: "pipeline-stage-chip" }, [
            h("span", { className: "stage-badge", textContent: "STUFE 2 (Video-Modell)" }),
            h("span", { textContent: "🧠 Bewertung des geschnittenen Clips" })
          ])
        ]),
        h("div", { className: "inference-transparency-card" }, [
          h("div", { className: "transparency-header" }, [
            h("div", { id: "transparency-pill", className: "transparency-pill fallback" }, [
              h("span", { className: "dot" }),
              h("span", { id: "transparency-pill-text", textContent: "MODELL: OFFLINE-FIXTURE (FESTE TESTANTWORT)" })
            ]),
            h("span", { className: "transparency-sub", textContent: "Werte live vom lokalen Server" })
          ]),
          h("div", { className: "transparency-grid" }, [
            h("div", { className: "transparency-item" }, [
              h("span", { className: "label", textContent: "Modus / Profil des Falls" }),
              h("span", { className: "value", id: "transparency-profile-val", textContent: "noch kein Fall" })
            ]),
            h("div", { className: "transparency-item" }, [
              h("span", { className: "label", textContent: "Externes Modell" }),
              h("span", { className: "value", id: "transparency-native-val", textContent: "–" })
            ]),
            h("div", { className: "transparency-item" }, [
              h("span", { className: "label", textContent: "Ausführung" }),
              h("span", { className: "value", textContent: "Lokaler VisionFlow-Server (nur Loopback)" })
            ]),
            h("div", { className: "transparency-item" }, [
              h("span", { className: "label", textContent: "Beweisclip" }),
              h("span", { className: "value", textContent: "5 s Vorlauf + 10 s Nachlauf · H.264 (libx264) · SHA-256" })
            ])
          ]),
          h("p", { id: "transparency-notice", className: "transparency-notice", textContent: "" })
        ]),
        h("div", { className: "datacenter-layout" }, [
          h("div", { className: "datacenter-card" }, [
            h("h4", { className: "datacenter-card__title", textContent: "Fälle" }),
            h("div", { id: "datacenter-queue-list", className: "datacenter-queue-list", textContent: "Noch keine Fälle." })
          ]),
          h("div", { className: "datacenter-card" }, [
            h("h4", { className: "datacenter-card__title", textContent: "Ereignisprotokoll (Server-Zustände)" }),
            h("div", { id: "datacenter-console", className: "datacenter-console" })
          ]),
          h("div", { className: "datacenter-card" }, [
            h("h4", { className: "datacenter-card__title", textContent: "Modellantwort" }),
            h("div", { id: "datacenter-verdict-box", className: "datacenter-verdict", textContent: "Bereit – noch kein Fall." })
          ])
        ]),
        h("button", {
          id: "follow-btn-2",
          type: "button",
          className: "cable-follow-btn",
          textContent: t("followCableTo3"),
          hidden: true,
          onclick: () => switchRoom(3)
        })
      ])
    ]));

    // 5. Room 3: Leitstelle Alpha (Officer Schmidt)
    root.appendChild(h("section", { id: "cartoon-room-3", className: "cartoon-room", hidden: true }, [
      h("div", { className: "cartoon-room__stage" }, [
        h("div", { className: "cartoon-character-row" }, [
          h("div", { className: "cartoon-avatar", innerHTML: SVGS.officerSchmidt }),
          h("div", { className: "cartoon-speech-bubble" }, [
            h("div", { className: "cartoon-speaker-name", textContent: "Officer Schmidt · Leitstelle Alpha" }),
            h("p", { id: "speech-schmidt", className: "cartoon-speech-text", "aria-live": "polite", textContent: t("schmidtIntro") })
          ])
        ]),
        h("div", { className: "dispatch-grid" }, [
          h("div", { id: "schmidt-video-mount", className: "traffic-monitor" }, [
            h("div", { className: "traffic-monitor__header", textContent: "Beweisclip vom Server" }),
            h("div", { id: "cartoon-review-video-box", className: "traffic-feed-box" }),
            h("div", { className: "schmidt-ticket-inbox" }, [
              h("div", { className: "schmidt-ticket-inbox__title" }, [
                h("span", { textContent: "Fälle" }),
                h("small", { textContent: "Klick zum Prüfen" })
              ]),
              h("div", { id: "schmidt-ticket-list", className: "schmidt-ticket-list" })
            ])
          ]),
          h("div", { className: "dispatch-desk" }, [
            h("h3", { textContent: "Einsatzentscheidung" }),
            h("p", { id: "schmidt-decision-status", className: "decision-status", "aria-live": "polite", textContent: "Kein Fall ausgewählt." }),
            h("div", { className: "dispatch-actions" }, [
              h("button", {
                id: "btn-schmidt-radio",
                type: "button",
                className: "chunky-btn chunky-btn--radio",
                textContent: t("schmidtRadio"),
                disabled: true,
                onclick: () => decideSelected("confirm")
              }),
              h("button", {
                id: "btn-schmidt-reject",
                type: "button",
                className: "chunky-btn",
                textContent: t("schmidtFalseAlarm"),
                disabled: true,
                onclick: () => decideSelected("reject")
              }),
              h("button", {
                id: "btn-schmidt-coffee",
                type: "button",
                className: "chunky-btn chunky-btn--coffee",
                textContent: t("schmidtCoffee"),
                onclick: onSchmidtCoffee
              })
            ]),
            h("button", {
              id: "follow-btn-3",
              type: "button",
              className: "cable-follow-btn",
              textContent: t("followCableTo4"),
              hidden: true,
              onclick: () => switchRoom(4)
            })
          ])
        ])
      ])
    ]));

    // 6. Room 4: Leitstelle Beta (real deadline of the open case)
    root.appendChild(h("section", { id: "cartoon-room-4", className: "cartoon-room", hidden: true }, [
      h("div", { className: "cartoon-room__stage" }, [
        h("div", { className: "cartoon-character-row" }, [
          h("div", { className: "cartoon-avatar", innerHTML: SVGS.officerWeberEmpty }),
          h("div", { className: "cartoon-speech-bubble" }, [
            h("div", { className: "cartoon-speaker-name", textContent: "Officer Weber · Leitstelle Beta (Abwesend)" }),
            h("p", { id: "speech-weber", className: "cartoon-speech-text", textContent: t("weberAlert") })
          ])
        ]),
        h("div", { className: "beta-alert-box" }, [
          h("div", { id: "weber-sticky", className: "sticky-note", textContent: t("weberSticky") }),
          h("div", { id: "beta-countdown-clock", className: "beta-countdown-display", textContent: "–" }),
          h("p", { id: "beta-countdown-caption", className: "cartoon-countdown-caption", textContent: "Kein Fall mit offener Frist." }),
          h("button", {
            id: "btn-manual-dispatch",
            type: "button",
            className: "chunky-btn chunky-btn--wrongway",
            textContent: t("weberManual"),
            disabled: true,
            onclick: () => decideSelected("confirm")
          }),
          h("div", { id: "beta-alert-msg", className: "verdict-badge danger", textContent: "", hidden: true })
        ])
      ])
    ]));

    // 7. Bottom Patrol Car Track
    root.appendChild(h("div", { className: "patrol-car-stage" }, [
      h("div", { className: "patrol-car-road" }),
      h("div", { id: "cartoon-patrol-car", className: "patrol-car", innerHTML: SVGS.patrolCar })
    ]));

    return root;
  }

  function refreshI18n() {
    setText("cartoon-title", t("title"));
    setText("cartoon-subtitle", t("subtitle"));
    setText("cartoon-sound-btn", global.RoadSFX && global.RoadSFX.getMuted() ? t("soundOff") : t("soundOn"));
    setText("cartoon-lang-btn", t("langToggle"));
    const container = $("cartoon-details-btn") && $("cartoon-details-btn").closest(".road-workspace--game");
    setText("cartoon-details-btn", container && container.classList.contains("road-workspace--details") ? t("detailsHide") : t("detailsShow"));
    for (const idx of [1, 2, 3, 4]) setText(`nav-text-${idx}`, t(`room${idx}`));
    setText("follow-btn-1", t("followCableTo2"));
    setText("follow-btn-2", t("followCableTo3"));
    setText("follow-btn-3", t("followCableTo4"));
    setText("btn-schmidt-radio", t("schmidtRadio"));
    setText("btn-schmidt-coffee", t("schmidtCoffee"));
    setText("btn-manual-dispatch", t("weberManual"));
    setText("weber-sticky", t("weberSticky"));
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
