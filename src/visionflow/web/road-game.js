/**
 * FlowVision Emergency Leitstelle – Interactive Browser Game Engine
 * Inspired by Emergency / Bibi Blocksberg / Benjamin Blümchen Rettungszentrale
 * 
 * Manages 4 Rooms:
 *   Room 1: 🚦 Verkehrsraumüberwachung (B4 Nord & Techniker Alex)
 *   Room 2: 🏢 AI-Rechenzentrum (Serverraum & Dr. Kira)
 *   Room 3: 🚓 Polizeileitstelle Alpha (Officer Schmidt & Funk)
 *   Room 4: 🚨 Polizeileitstelle Beta (Officer Weber Abwesend & Auto-Eskalation)
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

  // State
  let currentRoom = 1;
  let currentLang = "de";
  let activeCaseId = null;
  let activeClipUrl = null;
  let cameraRunning = false;
  let countdownSeconds = 20;
  let countdownTimer = null;
  let caseQueue = [];

  // Translation table
  const I18N = {
    de: {
      title: "FlowVision – Emergency Einsatzleitstelle",
      subtitle: "Autonome Video-KI · Leitwarten-Workflow",
      soundOn: "🔊 Ton: An",
      soundOff: "🔇 Ton: Aus",
      langToggle: "🌐 EN",
      room1: "🚦 1. Verkehrsraum",
      room2: "🏢 2. Rechenzentrum",
      room3: "🚓 3. Leitstelle Alpha",
      room4: "🚨 4. Leitstelle Beta (Fallback)",
      followCableTo2: "Dem Datenkabel ins Rechenzentrum folgen ➔",
      followCableTo3: "Dem Alarmkabel zur Leitstelle folgen ➔",
      followCableTo4: "Dem Kabel zu Tisch 2 folgen ➔",
      alexIntro: "Hallo! Ich bin Techniker Alex am B4-Nord Kontrollposten. Der OpenCV 5 Vektor-Kernel überwacht kontinuierlich alle Fahrspuren und puffert die letzten 15 Sekunden. Teste das System mit den Event-Buttons!",
      alexEventFired: "Ereignis erfasst! Der 15s-H.264 Buffer wurde geschnitten, gehasht und über das Datenkabel übertragen. Folge dem Kabel!",
      kiraIntro: "Willkommen im KI-Rechenzentrum! Hier trifft der H.264-Videoclip ein. Unser Vision-Modell analysiert Vektoren und semantische Gefahren.",
      kiraSafe: "Entwarnung! Das Modell stuft das Ereignis als harmlos ein (Score < 0.1). Kein Eingriff nötig.",
      kiraDanger: "Achtung! Akute Gefahr durch Falschfahrer erkannt (Score 0.98)! Der Fall wird mit höchster Priorität an die Leitstelle übergeben!",
      schmidtIntro: "Polizeileitstelle Alpha. Officer Schmidt am Apparat. Ich prüfe den eingehenden Beweisclip...",
      schmidtRadio: "📻 Funkgerät: Einsatzwagen alarmieren!",
      schmidtFalseAlarm: "❌ Fehlalarm verwerfen",
      schmidtCoffee: "☕ Kaffeepause (Logout & Weiterleiten)",
      schmidtDispatched: "Funkspruch an Streife 4 abgesetzt! Einsatzwagen rückt mit Blaulicht zur B4 Nord aus!",
      schmidtRejected: "Fehlalarm bestätigt. Ticket geschlossen, kein Einsatz nötig.",
      schmidtCoffeeAway: "Ich hol mir schnell einen Kaffee! Übergebe den Fall an Kollege Weber an Tisch 2.",
      weberSticky: "☕ Bin kurz Kaffee holen – Weber",
      weberAlert: "Officer Weber ist abwesend! Sicherheits-Frist läuft!",
      weberManual: "🚨 Manuell Freigeben",
      weberTimeout: "🚨 SICHERHEITS-AUTOMATISMUS EINGEGRIFFEN! Keine Reaktion innerhalb der 20s Frist – Notfall-Einsatz automatisch ausgelöst, um Menschenleben zu retten!"
    },
    en: {
      title: "FlowVision – Emergency Dispatch Mission Control",
      subtitle: "Autonomous Video AI · Traffic Control Center",
      soundOn: "🔊 Sound: ON",
      soundOff: "🔇 Sound: OFF",
      langToggle: "🌐 DE",
      room1: "🚦 1. Traffic Outpost",
      room2: "🏢 2. Datacenter",
      room3: "🚓 3. Dispatch Alpha",
      room4: "🚨 4. Dispatch Beta (Fallback)",
      followCableTo2: "Follow Data Cable to Datacenter ➔",
      followCableTo3: "Follow Alert Cable to Dispatch ➔",
      followCableTo4: "Follow Cable to Desk 2 ➔",
      alexIntro: "Hello! I am Field Technician Alex at the B4 North station. The OpenCV 5 vector kernel monitors traffic lanes and keeps a rolling 15s buffer. Test the system using the event buttons!",
      alexEventFired: "Event captured! The 15s H.264 buffer was clipped, hashed, and sent over the optic cable. Follow the cable!",
      kiraIntro: "Welcome to the AI Datacenter! The H.264 clip arrives here. Our Vision Model inspects vector consistency and semantic hazard.",
      kiraSafe: "All clear! The model evaluated this as a harmless environmental event (Score < 0.1). No dispatch required.",
      kiraDanger: "Warning! Acute wrong-way driver hazard detected (Score 0.98)! High priority dispatch forwarded to Police Command!",
      schmidtIntro: "Police Dispatch Alpha. Officer Schmidt on duty. Reviewing the incoming video proof clip...",
      schmidtRadio: "📻 Radio: Dispatch Patrol Car!",
      schmidtFalseAlarm: "❌ Dismiss False Alarm",
      schmidtCoffee: "☕ Coffee Break (Logout & Forward)",
      schmidtDispatched: "Radio call sent to Patrol 4! Emergency vehicle dispatched to B4 North with sirens blaring!",
      schmidtRejected: "False alarm confirmed. Ticket resolved, no unit dispatched.",
      schmidtCoffeeAway: "Stepping out for coffee! Forwarding case to Officer Weber at Desk 2.",
      weberSticky: "☕ Grabbing coffee, be right back – Weber",
      weberAlert: "Officer Weber is away! Emergency fallback countdown running!",
      weberManual: "🚨 Manual Override Dispatch",
      weberTimeout: "🚨 FAIL-SAFE AUTOMATION ACTIVATED! No human response within 20s deadline – Patrol car dispatched automatically to protect lives!"
    }
  };

  function t(key) {
    return I18N[currentLang][key] || key;
  }

  // DOM Builder helper
  function h(tag, attrs, children) {
    const el = document.createElement(tag);
    if (attrs) {
      for (const [k, v] of Object.entries(attrs)) {
        if (k === "className") el.className = v;
        else if (k === "textContent") el.textContent = v;
        else if (k === "innerHTML") el.innerHTML = v;
        else if (k === "onclick") el.addEventListener("click", v);
        else if (k === "hidden") el.hidden = Boolean(v);
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

  // --- Dispatch Patrol Car Animation ---
  function dispatchPatrolCar() {
    if (global.RoadSFX) global.RoadSFX.playSiren();
    const car = document.getElementById("cartoon-patrol-car");
    if (!car) return;
    car.classList.remove("dispatching");
    void car.offsetWidth; // trigger reflow
    car.classList.add("dispatching");
  }

  // --- Room Switching ---
  function switchRoom(roomNum) {
    if (global.RoadSFX) global.RoadSFX.playClick();
    currentRoom = roomNum;

    // Update Nav buttons
    [1, 2, 3, 4].forEach((idx) => {
      const navBtn = document.getElementById(`nav-node-room-${idx}`);
      const roomEl = document.getElementById(`cartoon-room-${idx}`);
      if (navBtn) navBtn.classList.toggle("active", idx === currentRoom);
      if (roomEl) roomEl.hidden = idx !== currentRoom;
    });
  }

  // --- Update Cable States ---
  function setCablePulsing(cableId, pulsing) {
    const cable = document.getElementById(cableId);
    if (cable) {
      cable.classList.toggle("pulsing", Boolean(pulsing));
      if (pulsing && global.RoadSFX) global.RoadSFX.playCablePulse();
    }
  }

  // --- Handle Real Event Trigger ---
  async function triggerEvent(eventType) {
    if (global.RoadSFX) global.RoadSFX.playEventTrigger();

    // 1. Ensure camera is started in backend if not running
    const startBtn = document.getElementById("road-camera-create");
    if (startBtn && !startBtn.disabled) {
      startBtn.click();
    }

    // 2. Update Alex's Speech Bubble
    const speechAlex = document.getElementById("speech-alex");
    if (speechAlex) speechAlex.textContent = t("alexEventFired");

    // 3. Inject event to backend
    try {
      const select = document.getElementById("road-camera-event");
      const injectBtn = document.getElementById("road-camera-inject");
      if (select && injectBtn) {
        select.value = eventType;
        injectBtn.click();
      }
    } catch (_) {}

    // 4. Light up Cable 1 -> 2
    setCablePulsing("cable-1-2", true);
    const followBtn1 = document.getElementById("follow-btn-1");
    if (followBtn1) followBtn1.hidden = false;

    // 5. Add ticket to queue in Datacenter (Room 2)
    const ticketId = "T-" + Math.floor(1000 + Math.random() * 9000);
    const hash = Array.from({ length: 8 }, () => Math.floor(Math.random() * 16).toString(16)).join("");
    const isDanger = eventType === "wrong_way";
    const score = isDanger ? 0.98 : 0.04;

    const ticket = {
      id: ticketId,
      type: eventType,
      hash: "3a" + hash + "...",
      isDanger,
      score,
      time: new Date().toLocaleTimeString()
    };
    caseQueue.unshift(ticket);
    renderDatacenterQueue();

    // 6. Log to Room 2 Terminal
    appendDatacenterLog(`[INGEST] Clip received: event_${eventType}_${ticketId}.mp4`);
    appendDatacenterLog(`[HASH] SHA-256: ${ticket.hash}`);
    appendDatacenterLog(`[MODEL] Running RoadModelExchange inference...`);
    appendDatacenterLog(`[RESULT] Hazard score: ${score.toFixed(2)} (${isDanger ? "CRITICAL RISK" : "CLEARED"})`);

    // 7. Update Room 2 Verdict
    const verdictBox = document.getElementById("datacenter-verdict-box");
    const speechKira = document.getElementById("speech-kira");
    if (verdictBox) {
      if (isDanger) {
        verdictBox.innerHTML = `<span class="verdict-badge danger">⚠️ ${t("kiraDanger")}</span>`;
        if (speechKira) speechKira.textContent = t("kiraDanger");
        setCablePulsing("cable-2-3", true);
        const followBtn2 = document.getElementById("follow-btn-2");
        if (followBtn2) followBtn2.hidden = false;
      } else {
        verdictBox.innerHTML = `<span class="verdict-badge safe">✅ ${t("kiraSafe")}</span>`;
        if (speechKira) speechKira.textContent = t("kiraSafe");
      }
    }
  }

  function appendDatacenterLog(msg) {
    const consoleEl = document.getElementById("datacenter-console");
    if (consoleEl) {
      const line = document.createElement("div");
      line.textContent = `[${new Date().toLocaleTimeString()}] ${msg}`;
      consoleEl.appendChild(line);
      consoleEl.scrollTop = consoleEl.scrollHeight;
    }
  }

  function renderDatacenterQueue() {
    const listEl = document.getElementById("datacenter-queue-list");
    if (!listEl) return;
    listEl.replaceChildren();
    caseQueue.slice(0, 5).forEach((item) => {
      const div = h("div", {
        className: `queue-item ${item.isDanger ? "high-risk" : ""}`,
        innerHTML: `<strong>${item.id}</strong> · ${item.type.toUpperCase()} · ${item.time}<br><small>SHA256: ${item.hash}</small>`
      });
      listEl.appendChild(div);
    });
  }

  // --- Room 3 Actions ---
  function onSchmidtRadio() {
    if (global.RoadSFX) global.RoadSFX.playRadio();
    dispatchPatrolCar();

    const speech = document.getElementById("speech-schmidt");
    if (speech) speech.textContent = t("schmidtDispatched");

    // Click confirm in real backend workplace if present
    const confirmBtn = document.getElementById("btn-operator-confirm");
    if (confirmBtn) confirmBtn.click();
  }

  function onSchmidtReject() {
    if (global.RoadSFX) global.RoadSFX.playClick();
    const speech = document.getElementById("speech-schmidt");
    if (speech) speech.textContent = t("schmidtRejected");

    const denyBtn = document.getElementById("btn-operator-deny");
    if (denyBtn) denyBtn.click();
  }

  function onSchmidtCoffee() {
    if (global.RoadSFX) global.RoadSFX.playCoffee();
    const speech = document.getElementById("speech-schmidt");
    if (speech) speech.textContent = t("schmidtCoffeeAway");

    setCablePulsing("cable-3-4", true);
    const followBtn3 = document.getElementById("follow-btn-3");
    if (followBtn3) followBtn3.hidden = false;

    // Start 20-second countdown in Room 4
    startBetaCountdown();
  }

  // --- Room 4 Countdown & Fail-Safe ---
  function startBetaCountdown() {
    if (countdownTimer) clearInterval(countdownTimer);
    countdownSeconds = 20;

    const display = document.getElementById("beta-countdown-clock");
    if (display) display.textContent = `${countdownSeconds}s`;

    countdownTimer = setInterval(() => {
      countdownSeconds -= 1;
      if (display) display.textContent = `${countdownSeconds}s`;

      if (countdownSeconds <= 0) {
        clearInterval(countdownTimer);
        countdownTimer = null;
        triggerBetaTimeout();
      }
    }, 1000);
  }

  function triggerBetaTimeout() {
    dispatchPatrolCar();
    const alertBanner = document.getElementById("beta-alert-msg");
    if (alertBanner) {
      alertBanner.textContent = t("weberTimeout");
      alertBanner.hidden = false;
    }
  }

  function onBetaManualDispatch() {
    if (countdownTimer) {
      clearInterval(countdownTimer);
      countdownTimer = null;
    }
    dispatchPatrolCar();
    const alertBanner = document.getElementById("beta-alert-msg");
    if (alertBanner) {
      alertBanner.textContent = "🚨 Notfalleinsatz manuell freigegeben – Streifenwagen rückt aus!";
      alertBanner.hidden = false;
    }
  }

  // --- Build Full Cartoon Interface ---
  function buildCartoonDOM() {
    const root = h("div", { className: "cartoon-stage" });

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
          id: "cartoon-sound-btn",
          className: "cartoon-pill-btn",
          textContent: global.RoadSFX && global.RoadSFX.getMuted() ? t("soundOff") : t("soundOn"),
          onclick: () => {
            const next = !global.RoadSFX.getMuted();
            global.RoadSFX.setMuted(next);
            document.getElementById("cartoon-sound-btn").textContent = next ? t("soundOff") : t("soundOn");
          }
        }),
        h("button", {
          id: "cartoon-lang-btn",
          className: "cartoon-pill-btn",
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
    const conduitNav = h("nav", { className: "cartoon-conduit-nav", "aria-label": "Rettungszentrale Räume" }, [
      h("button", {
        id: "nav-node-room-1",
        className: "cartoon-station-node active",
        onclick: () => switchRoom(1)
      }, [
        h("span", { className: "cartoon-station-icon", textContent: "🚦" }),
        h("span", { id: "nav-text-1", textContent: t("room1") })
      ]),
      h("div", { id: "cable-1-2", className: "cartoon-cable-segment" }),
      h("button", {
        id: "nav-node-room-2",
        className: "cartoon-station-node",
        onclick: () => switchRoom(2)
      }, [
        h("span", { className: "cartoon-station-icon", textContent: "🏢" }),
        h("span", { id: "nav-text-2", textContent: t("room2") })
      ]),
      h("div", { id: "cable-2-3", className: "cartoon-cable-segment" }),
      h("button", {
        id: "nav-node-room-3",
        className: "cartoon-station-node",
        onclick: () => switchRoom(3)
      }, [
        h("span", { className: "cartoon-station-icon", textContent: "🚓" }),
        h("span", { id: "nav-text-3", textContent: t("room3") })
      ]),
      h("div", { id: "cable-3-4", className: "cartoon-cable-segment" }),
      h("button", {
        id: "nav-node-room-4",
        className: "cartoon-station-node",
        onclick: () => switchRoom(4)
      }, [
        h("span", { className: "cartoon-station-icon", textContent: "🚨" }),
        h("span", { id: "nav-text-4", textContent: t("room4") })
      ])
    ]);
    root.appendChild(conduitNav);

    // 3. Room 1: Verkehrsraumüberwachung (Roadside Station B4 Nord)
    const room1 = h("section", { id: "cartoon-room-1", className: "cartoon-room" }, [
      h("div", { className: "cartoon-room__stage" }, [
        // Character row
        h("div", { className: "cartoon-character-row" }, [
          h("div", { className: "cartoon-avatar", innerHTML: SVGS.technicianAlex }),
          h("div", { className: "cartoon-speech-bubble" }, [
            h("div", { className: "cartoon-speaker-name", textContent: "Alex · Techniker B4 Nord" }),
            h("p", { id: "speech-alex", className: "cartoon-speech-text", textContent: t("alexIntro") })
          ])
        ]),
        // Traffic Screen & Trigger Pad
        h("div", { className: "traffic-screen-wrap" }, [
          h("div", { className: "traffic-monitor" }, [
            h("div", { className: "traffic-monitor__header" }, [
              h("span", { textContent: "CAM-01 · B4 NORD LIVE FEED" }),
              h("span", { id: "cartoon-cam-status", textContent: "[● STANDBY]" })
            ]),
            h("div", { id: "cartoon-feed-mount", className: "traffic-feed-box" })
          ]),
          h("div", { className: "traffic-control-panel" }, [
            h("h3", { textContent: "Ereignis-Pult (Echtzeit-Simulation)" }),
            h("div", { className: "cartoon-btn-grid" }, [
              h("button", {
                id: "btn-toggle-cam",
                className: "chunky-btn chunky-btn--power",
                textContent: "📷 Kamera AN / AUS",
                onclick: () => {
                  if (global.RoadSFX) global.RoadSFX.playClick();
                  const startBtn = document.getElementById("road-camera-create");
                  const stopBtn = document.getElementById("road-camera-stop");
                  if (!cameraRunning && startBtn && !startBtn.disabled) {
                    startBtn.click();
                    cameraRunning = true;
                    document.getElementById("cartoon-cam-status").textContent = "[● LIVE STREAM]";
                  } else if (cameraRunning && stopBtn && !stopBtn.disabled) {
                    stopBtn.click();
                    cameraRunning = false;
                    document.getElementById("cartoon-cam-status").textContent = "[● STANDBY]";
                  }
                }
              }),
              h("button", {
                id: "btn-sim-leaf",
                className: "chunky-btn chunky-btn--leaf",
                textContent: "🍂 Blatt simulieren",
                onclick: () => triggerEvent("leaf")
              }),
              h("button", {
                id: "btn-sim-bird",
                className: "chunky-btn chunky-btn--bird",
                textContent: "🐦 Vogel simulieren",
                onclick: () => triggerEvent("bird")
              }),
              h("button", {
                id: "btn-sim-wrongway",
                className: "chunky-btn chunky-btn--wrongway",
                textContent: "⚠️ Falschfahrer simulieren!",
                onclick: () => triggerEvent("wrong_way")
              })
            ]),
            h("button", {
              id: "follow-btn-1",
              className: "cable-follow-btn",
              textContent: t("followCableTo2"),
              hidden: true,
              onclick: () => switchRoom(2)
            })
          ])
        ])
      ])
    ]);
    root.appendChild(room1);

    // 4. Room 2: AI-Rechenzentrum (Serverraum & Dr. Kira)
    const room2 = h("section", { id: "cartoon-room-2", className: "cartoon-room", hidden: true }, [
      h("div", { className: "cartoon-room__stage" }, [
        h("div", { className: "cartoon-character-row" }, [
          h("div", { className: "cartoon-avatar", innerHTML: SVGS.drKira }),
          h("div", { className: "cartoon-speech-bubble" }, [
            h("div", { className: "cartoon-speaker-name", textContent: "Dr. Kira · Lead Vision AI" }),
            h("p", { id: "speech-kira", className: "cartoon-speech-text", textContent: t("kiraIntro") })
          ])
        ]),
        h("div", { className: "datacenter-layout" }, [
          h("div", { className: "datacenter-card" }, [
            h("h4", { className: "datacenter-card__title", textContent: "Eingehende Videoclips" }),
            h("div", { id: "datacenter-queue-list", className: "datacenter-queue-list", textContent: "Warte auf Events..." })
          ]),
          h("div", { className: "datacenter-card" }, [
            h("h4", { className: "datacenter-card__title", textContent: "Live Inferenz Terminal" }),
            h("div", { id: "datacenter-console", className: "datacenter-console" })
          ]),
          h("div", { className: "datacenter-card" }, [
            h("h4", { className: "datacenter-card__title", textContent: "Modell-Interpretation" }),
            h("div", { id: "datacenter-verdict-box", className: "datacenter-verdict", textContent: "Bereit für Inferenz" })
          ])
        ]),
        h("button", {
          id: "follow-btn-2",
          className: "cable-follow-btn",
          textContent: t("followCableTo3"),
          hidden: true,
          onclick: () => switchRoom(3)
        })
      ])
    ]);
    root.appendChild(room2);

    // 5. Room 3: Leitstelle Alpha (Officer Schmidt)
    const room3 = h("section", { id: "cartoon-room-3", className: "cartoon-room", hidden: true }, [
      h("div", { className: "cartoon-room__stage" }, [
        h("div", { className: "cartoon-character-row" }, [
          h("div", { className: "cartoon-avatar", innerHTML: SVGS.officerSchmidt }),
          h("div", { className: "cartoon-speech-bubble" }, [
            h("div", { className: "cartoon-speaker-name", textContent: "Officer Schmidt · Leitstelle Alpha" }),
            h("p", { id: "speech-schmidt", className: "cartoon-speech-text", textContent: t("schmidtIntro") })
          ])
        ]),
        h("div", { className: "dispatch-grid" }, [
          h("div", { id: "schmidt-video-mount", className: "traffic-monitor" }, [
            h("div", { className: "traffic-monitor__header", textContent: "Beweis-Videoauswertung" }),
            h("div", { id: "cartoon-review-video-box", className: "traffic-feed-box" })
          ]),
          h("div", { className: "dispatch-desk" }, [
            h("h3", { textContent: "Einsatzentscheidung" }),
            h("div", { className: "dispatch-actions" }, [
              h("button", {
                id: "btn-schmidt-radio",
                className: "chunky-btn chunky-btn--radio",
                textContent: t("schmidtRadio"),
                onclick: onSchmidtRadio
              }),
              h("button", {
                id: "btn-schmidt-reject",
                className: "chunky-btn",
                textContent: t("schmidtFalseAlarm"),
                onclick: onSchmidtReject
              }),
              h("button", {
                id: "btn-schmidt-coffee",
                className: "chunky-btn chunky-btn--coffee",
                textContent: t("schmidtCoffee"),
                onclick: onSchmidtCoffee
              })
            ]),
            h("button", {
              id: "follow-btn-3",
              className: "cable-follow-btn",
              textContent: t("followCableTo4"),
              hidden: true,
              onclick: () => switchRoom(4)
            })
          ])
        ])
      ])
    ]);
    root.appendChild(room3);

    // 6. Room 4: Leitstelle Beta (Fallback Station & Frist-Eskalation)
    const room4 = h("section", { id: "cartoon-room-4", className: "cartoon-room", hidden: true }, [
      h("div", { className: "cartoon-room__stage" }, [
        h("div", { className: "cartoon-character-row" }, [
          h("div", { className: "cartoon-avatar", innerHTML: SVGS.officerWeberEmpty }),
          h("div", { className: "cartoon-speech-bubble" }, [
            h("div", { className: "cartoon-speaker-name", textContent: "Officer Weber · Leitstelle Beta (Abwesend)" }),
            h("p", { className: "cartoon-speech-text", textContent: t("weberAlert") })
          ])
        ]),
        h("div", { className: "beta-alert-box" }, [
          h("div", { className: "sticky-note", textContent: t("weberSticky") }),
          h("div", { id: "beta-countdown-clock", className: "beta-countdown-display", textContent: "20s" }),
          h("button", {
            id: "btn-manual-dispatch",
            className: "chunky-btn chunky-btn--wrongway",
            textContent: t("weberManual"),
            onclick: onBetaManualDispatch
          }),
          h("div", { id: "beta-alert-msg", className: "verdict-badge danger", textContent: "", hidden: true })
        ])
      ])
    ]);
    root.appendChild(room4);

    // 7. Bottom Patrol Car Track
    const patrolStage = h("div", { className: "patrol-car-stage" }, [
      h("div", { className: "patrol-car-road" }),
      h("div", { id: "cartoon-patrol-car", className: "patrol-car", innerHTML: SVGS.patrolCar })
    ]);
    root.appendChild(patrolStage);

    return root;
  }

  function refreshI18n() {
    document.getElementById("cartoon-title").textContent = t("title");
    document.getElementById("cartoon-subtitle").textContent = t("subtitle");
    document.getElementById("cartoon-sound-btn").textContent = global.RoadSFX && global.RoadSFX.getMuted() ? t("soundOff") : t("soundOn");
    document.getElementById("cartoon-lang-btn").textContent = t("langToggle");
    document.getElementById("nav-text-1").textContent = t("room1");
    document.getElementById("nav-text-2").textContent = t("room2");
    document.getElementById("nav-text-3").textContent = t("room3");
    document.getElementById("nav-text-4").textContent = t("room4");
    document.getElementById("follow-btn-1").textContent = t("followCableTo2");
    document.getElementById("follow-btn-2").textContent = t("followCableTo3");
    document.getElementById("follow-btn-3").textContent = t("followCableTo4");
  }

  // --- Public Mount Function ---
  function mountCartoonGame(container) {
    if (!container) return;
    const gameDom = buildCartoonDOM();
    container.prepend(gameDom);

    // Reparent live feed elements into cartoon room viewport if available
    window.setTimeout(() => {
      const feedBox = document.getElementById("cartoon-feed-mount");
      const realViewport = document.querySelector(".road-camera__viewport");
      if (feedBox && realViewport) {
        feedBox.appendChild(realViewport);
      }
    }, 100);

    return {
      destroy: () => {
        if (countdownTimer) clearInterval(countdownTimer);
        gameDom.remove();
      }
    };
  }

  global.RoadGame = {
    mountCartoonGame,
    switchRoom,
    triggerEvent,
    dispatchPatrolCar
  };
})(window);
