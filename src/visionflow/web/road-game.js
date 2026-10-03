/**
 * VisionFlow four-room controller view. The cartoon room illustrations are
 * presentational; case, queue, model, clip, and deadline data come from mounts.
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

  // The game shell owns navigation and illustration only. Operational data
  // arrives through update() and the explicit callbacks below.
  let currentRoom = 1;
  let currentLang = "de";
  let currentState = { source: null, cases: [], jobs: [], selectedCase: null, exchanges: [], inbox: [] };
  let callbacks = {};
  let gameRoot = null;
  let requestedSelectedCaseId = null;

  const COPY = {
    de: {
      title: "VisionFlow · Verkehrsleitstelle",
      subtitle: "Lokale Controlleransicht · vier Räume, echte Falldaten",
      soundOn: "🔊 Ton: An", soundOff: "🔇 Ton: Aus", langToggle: "🌐 EN",
      navLabel: "Controller-Räume",
      room1: "🚦 1. Verkehrsraum", room2: "🏢 2. Rechenzentrum",
      room3: "🚓 3. Leitstelle Alpha", room4: "🚨 4. Leitstelle Beta",
      alexName: "Alex · Techniker B4 Nord",
      alexIntro: "Die Kameraquelle und Aufzeichnung erscheinen hier. Ein Ereignis startet einen Prüfauftrag; es ist noch kein Befund.",
      cameraTitle: "Aktuelle Kameraquelle", eventTitle: "Prüfauftrag starten",
      eventHelp: "Wähle Ereignis, Modus und die sichtbare Offline-Testantwort. Das Ereignis sagt nichts über das Prüfergebnis aus.",
      wrongWay: "⚠️ Falschfahrer-Szenario", bird: "🐦 Vogel-Szenario", leaf: "🍂 Blatt-Szenario",
      noSource: "Starte zuerst eine Kameraquelle.", sourceReady: "Quelle läuft",
      profileMissing: "Die ausgewählte Testantwort konnte nicht gelesen werden.",
      sourceEnded: "Quelle beendet", noSourceData: "Noch keine Quelle ausgewählt.",
      followTo2: "Dem Datenkabel ins Rechenzentrum folgen ➔",
      kiraName: "Dr. Kira · Modellprüfung",
      kiraIntro: "Hier erscheinen tatsächliche Jobs, Modellprüfungen, Prompts, Belege und Antworten.",
      jobQueue: "Prüfaufträge und Fälle", noJobs: "Noch keine Prüfaufträge oder Fälle vorhanden.",
      resultTitle: "Befund des ausgewählten Falls",
      noAssessment: "Noch kein Ergebnis verfügbar. Ein Befund erscheint erst nach der tatsächlichen Bewertung.",
      noCase: "Wähle einen Fall aus der Warteschlange.",
      followTo3: "Dem Kabel zur Leitstelle folgen ➔",
      schmidtName: "Officer Schmidt · Menschliche Prüfung",
      schmidtIntro: "Wähle einen tatsächlichen Fall und prüfe dessen gespeicherten Clip und Befund.",
      historyTitle: "Fallauswahl", noHistory: "Noch keine Fälle vorhanden.",
      workplaceTitle: "Menschlicher Arbeitsplatz",
      coffee: "☕ Kaffeepause · Fallübersicht öffnen",
      coffeeStatus: "Kaffeepause. Fall, Frist und Entscheidung bleiben unverändert.",
      followTo4: "Dem Kabel zur Fallchronik folgen ➔",
      weberName: "Officer Weber · Serverstatus",
      room4Intro: "Dieser Raum zeigt ausschließlich Phase, Frist und Belege aus dem ausgewählten Serverfall.",
      selectedCase: "Ausgewählter Fall", noSelection: "Kein Fall ausgewählt",
      remaining: "Vom Server gemeldete Restzeit", receiptTitle: "Serverbelege",
      noReceipt: "Für diesen Fall ist kein Serverbeleg gespeichert.",
      firstReceipt: "Erstmeldung", correctionReceipt: "Korrekturmeldung",
      timelineTitle: "Fallchronik",
      noTimeline: "Für den ausgewählten Fall liegen noch keine Chronikeinträge vor.",
      sourceId: "Quellenkennung", frames: "Aufgezeichnete Quellbilder", sourceStatus: "Quellenstatus",
      phase: "Serverphase", mode: "Modus", profile: "Testantwort",
      job: "Prüfauftrag", event: "Szenario", error: "Fehler",
      clipId: "Ereignisclip-ID", clipHash: "Ereignisclip-Prüfsumme",
      noClip: "Für diesen Fall ist kein Ereignisclip im Snapshot vorhanden.",
      coffeeAway: "Kollegin oder Kollege ist in der Kaffeepause. Der Server überwacht die Frist weiter.",
      patrol: "Simulierte Streife · keine echte Entsendung",
      roadTab: "🚦 VisionFlow Verkehrsleitstelle",
    },
    en: {
      title: "VisionFlow · Traffic Control",
      subtitle: "Local controller view · four rooms, real case data",
      soundOn: "🔊 Sound: on", soundOff: "🔇 Sound: off", langToggle: "🌐 DE",
      navLabel: "Controller rooms",
      room1: "🚦 1. Traffic room", room2: "🏢 2. Datacenter",
      room3: "🚓 3. Dispatch Alpha", room4: "🚨 4. Dispatch Beta",
      alexName: "Alex · B4 North technician",
      alexIntro: "The camera source and recording appear here. An event starts an analysis job; it is not a finding.",
      cameraTitle: "Current camera source", eventTitle: "Start an analysis job",
      eventHelp: "Choose an event, mode, and the visible offline test response. The event does not determine the result.",
      wrongWay: "⚠️ Wrong-way scenario", bird: "🐦 Bird scenario", leaf: "🍂 Leaf scenario",
      noSource: "Start a camera source first.", sourceReady: "Source running",
      profileMissing: "The selected test response could not be read.",
      sourceEnded: "Source ended", noSourceData: "No source selected yet.",
      followTo2: "Follow the data cable to the datacenter ➔",
      kiraName: "Dr. Kira · Model review",
      kiraIntro: "Actual jobs, model reviews, prompts, evidence, and responses appear here.",
      jobQueue: "Jobs and cases", noJobs: "There are no jobs or cases yet.",
      resultTitle: "Selected case assessment",
      noAssessment: "No result is available yet. A finding appears only after an actual assessment.",
      noCase: "Select a case from the queue.",
      followTo3: "Follow the cable to dispatch ➔",
      schmidtName: "Officer Schmidt · Human review",
      schmidtIntro: "Select an actual case to review its saved clip and assessment.",
      historyTitle: "Case selection", noHistory: "There are no cases yet.",
      workplaceTitle: "Human review workspace",
      coffee: "☕ Coffee break · open case overview",
      coffeeStatus: "Coffee break. The case, deadline, and decision remain unchanged.",
      followTo4: "Follow the cable to the case timeline ➔",
      weberName: "Officer Weber · Server status",
      room4Intro: "This room shows only the phase, deadline, and receipts from the selected server case.",
      selectedCase: "Selected case", noSelection: "No case selected",
      remaining: "Server-reported time remaining", receiptTitle: "Server receipts",
      noReceipt: "No server receipt is stored for this case.",
      firstReceipt: "Initial report", correctionReceipt: "Correction report",
      timelineTitle: "Case timeline",
      noTimeline: "There are no timeline entries for the selected case yet.",
      sourceId: "Source ID", frames: "Recorded source frames", sourceStatus: "Source status",
      phase: "Server phase", mode: "Mode", profile: "Test response",
      job: "Job", event: "Scenario", error: "Error",
      clipId: "Event clip ID", clipHash: "Event clip checksum",
      noClip: "No event clip is present in this case snapshot.",
      coffeeAway: "A colleague is on a coffee break. The server continues to enforce the deadline.",
      patrol: "Simulated patrol · no real dispatch",
      roadTab: "🚦 VisionFlow Controller",
    },
  };

  const MODULE_COPY = [
    ["Straßenkamera-Demo", "Road camera"],
    ["Eine lokale synthetische Verkehrsszene liefert echte Bildpixel für die freigegebenen OpenCV-Werkzeuge.", "A local synthetic traffic scene provides real image pixels to the enabled OpenCV tools."],
    ["Modellmodus", "Model mode"], ["Offline-Testantwort", "Offline test response"],
    ["Gemini Video (nicht freigeschaltet)", "Gemini Video (not enabled)"],
    ["CAM-01 · B4 NORD", "CAM-01 · B4 NORTH"],
    ["Kamera im Bereitschaftsmodus", "Camera ready"],
    ["Sitzung starten oder ⚡ 1-Klick Demo aktivieren", "Start a session to begin"],
    ["Aktuelles Bild der synthetischen Straßenkamera", "Current synthetic road camera image"],
    ["Vollständiger Ereignisclip zur menschlichen Fallprüfung", "Full event clip for human case review"],
    ["Lokale synthetische Straßenkamera und H.264-Aufzeichnung sind bereit.", "The local synthetic road camera and H.264 recording are ready."],
    ["Die Kamerafunktion konnte nicht geprüft werden. Prüfe den lokalen FFmpeg-Server.", "The camera feature could not be checked. Check the local FFmpeg server."],
    ["Für die Straßenkamera-Aufzeichnung muss FFmpeg mit libx264 verfügbar sein.", "FFmpeg with libx264 must be available for road camera recording."],
    ["Neue synthetische Quelle gestartet.", "New synthetic source started."],
    ["Neue Quelle gestartet; frühere Clips bleiben erhalten.", "New source started; earlier clips remain available."],
    ["Quelle aktiv; noch kein Prüfauftrag gestartet.", "Source is active; no analysis job has been started yet."],
    ["Quelle wiederhergestellt; Prüfaufträge und Fälle werden vom Server geladen.", "Source restored; jobs and cases are loading from the server."],
    ["Wiederhergestellte Quelle; warte auf das aktuelle Kamerabild.", "Restored source; waiting for the current camera image."],
    ["Beendete Quelle; historische Fälle und Clips bleiben verfügbar.", "Ended source; historical cases and clips remain available."],
    ["Warte auf das erste Kamerabild.", "Waiting for the first camera image."],
    ["Quelle mit einem technischen Fehler beendet.", "Source ended with a technical error."],
    ["Quelle beendet; das letzte echte Kamerabild bleibt sichtbar.", "Source ended; the last actual camera image remains visible."],
    ["Quelle beendet; die vollständige Aufnahme wird angezeigt.", "Source ended; the complete recording is available."],
    ["Die Quelle war bereits beendet; ihre gespeicherte Aufnahme ist nun fertiggestellt.", "The source had already ended; its saved recording is now complete."],
    ["Aufnahmedaten konnten nicht geladen werden.", "Recording data could not be loaded."],
    ["Das aktuelle Kamerabild konnte nicht geladen werden.", "The current camera image could not be loaded."],
    ["Starte zuerst eine Quelle.", "Start a source first."],
    ["Ereignisse werden erst freigegeben, wenn drei echte Startframes analysiert wurden.", "Events are enabled after three actual startup frames have been analyzed."],
    ["Das Analyseereignis verändert nur die Szene. Ein positiver Befund entsteht ausschließlich aus analysierten Frames.", "The event changes only the scene. A positive finding comes only from analyzed frames."],
    ["Das Ereignis verändert nur die Szene. Ein positiver Befund entsteht ausschließlich aus analysierten Frames.", "The event changes only the scene. A positive finding comes only from analyzed frames."],
    ["Während einer Prüfung sind neue Szenarioereignisse gesperrt. Stoppen und Zurücksetzen bleiben verfügbar.", "New scenario events are locked during a review. Stop and reset remain available."],
    ["Die Straßenkamera ist nicht verfügbar. Prüfe die FFmpeg-Voraussetzung.", "The road camera is unavailable. Check the FFmpeg requirement."],
    ["Der Analyse-Lauf ist beendet. Starte eine neue Sitzung für eine weitere Prüfung.", "The analysis run is complete. Start a new session for another review."],
    ["Ereignis in die Szene eingeblendet. Das ist noch kein Analysebefund.", "Event added to the scene. This is not an analysis finding."],
    ["Analyse abgebrochen; die Kameraquelle zeichnet weiter auf.", "Analysis cancelled; the camera source continues recording."],
    ["Die Quelle läuft, aber der Analyse-Lauf ist fehlgeschlagen.", "The source is running, but the analysis run failed."],
    ["Die Quelle ist beendet; der letzte Laufstatus ist nicht verfügbar.", "The source has ended; the last run status is unavailable."],
    ["Der Kameraablauf konnte nicht gestartet werden.", "The camera workflow could not be started."],
    ["Der Controllerstatus konnte nicht geladen werden.", "Controller status could not be loaded."],
    ["Der Server lieferte keinen abspielbaren Clip.", "The server did not return a playable clip."],
    ["Die Clip-Prüfsumme passt nicht zu den gespeicherten Metadaten.", "The clip checksum does not match the stored metadata."],
    ["Der gespeicherte Clip konnte nicht geöffnet werden.", "The saved clip could not be opened."],
    ["Der Server lieferte keinen gültigen Fall- und Laufbezug.", "The server did not return a valid case and run reference."],
    ["Der Server lieferte keinen gültigen Prüfauftrag zurück.", "The server did not return a valid analysis job."],
    ["Starte zuerst eine Kameraquelle.", "Start a camera source first."],
    ["Wähle Ereignis und Modellmodus explizit aus.", "Choose an event and model mode explicitly."],
    ["Wähle die sichtbare Offline-Testantwort explizit aus.", "Choose the visible offline test response explicitly."],
    ["Die aufgezeichnete Quelle passt nicht zur gespeicherten Quellenkennung.", "The recording does not match the stored source ID."],
    ["Die ausgewählte Fallansicht konnte nicht vollständig geladen werden.", "The selected case view could not be fully loaded."],
    ["Quellen-, Fall- oder Postfachdaten konnten nicht geladen werden.", "Source, case, or inbox data could not be loaded."],
    ["Der gespeicherte Kamerastatus enthält keine Quellenkennung.", "The stored camera status has no source ID."],
    ["Der Server lieferte keine neue Quellenkennung.", "The server did not return a new source ID."],
    ["Straßenkamera-Demo", "Road camera"],
    ["Offline-Testantwort · menschliche Prüfung (Sicherheit 0,80)", "Offline test response · human review (confidence 0.80)"],
    ["Offline-Testantwort · hohe Sicherheit (0,99)", "Offline test response · high confidence (0.99)"],
    ["Offline-Testantwort · Vogel, keine Gefahr (0,99)", "Offline test response · bird, no hazard (0.99)"],
    ["Offline-Testantwort · Blatt, keine Gefahr (0,99)", "Offline test response · leaf, no hazard (0.99)"],
    ["Offline-Testantwort · Folgebeobachtung", "Offline test response · follow-up observation"],
    ["Offline-Testantwort · unklar", "Offline test response · unclear"],
    ["Offline-Testantwort · Modellfehler", "Offline test response · model error"],
    ["Wartet", "Queued"],
    ["Gemini Video (gemini_video)", "Gemini Video (gemini_video)"],
    ["Neue Sitzung starten", "Start new session"], ["Neue Straßenkamera-Sitzung starten", "Start a new road camera session"],
    ["Aufnahme beenden", "Stop recording"], ["Aufnahme speichern", "Save recording"], ["Zurücksetzen", "Reset"],
    ["Analyse abbrechen", "Cancel analysis"], ["Noch keine Sitzung gestartet.", "No session started yet."],
    ["Quelle: –", "Source: –"], ["Analyse und Controller-Trace", "Analysis and controller trace"],
    ["Noch kein Analyse-Lauf gestartet.", "No analysis run started yet."], ["Noch kein Analyse-Lauf gestartet.", "No analysis run started yet."],
    ["Noch keine Analysebefunde.", "No analysis findings yet."], ["Noch kein Trace vorhanden.", "No trace available yet."],
    ["Gespeicherte Controller-Ereignisse", "Stored controller events"], ["Livebild", "Live image"],
    ["Bereit. Starte eine Sitzung oder nutze die ⚡ 1-Klick Demo.", "Ready. Start a session to begin."],
    ["Szenarioereignis", "Scenario event"], ["Ereignis für die Szene", "Event for the scene"],
    ["Ereignis einblenden", "Add event to the scene"], ["Aufzeichnung", "Recording"],
    ["Warte auf Aufnahmedaten.", "Waiting for recording data."], ["Gespeicherter Clip", "Saved clip"],
    ["Noch kein gespeicherter Clip.", "No saved clip yet."],
    ["Noch kein Clip geladen. Wähle nach Beenden der Aufnahme einen Clip aus der Liste.", "No clip loaded. Select a clip after recording ends."],
    ["Wähle einen gespeicherten Clip aus.", "Select a saved clip."], ["Gespeicherte Clips", "Saved clips"],
    ["Gespeicherte Straßenkamera-Aufzeichnung", "Saved road camera recording"],
    ["Noch kein Clip geladen.", "No clip loaded yet."], ["Der Ereignisclip konnte nicht geöffnet werden.", "The event clip could not be opened."],
    ["Der authentifizierte Ereignisclip konnte nicht geladen werden.", "The authenticated event clip could not be loaded."],
    ["Clip geladen. Native Videosteuerung ist verfügbar.", "Clip loaded. Native video controls are available."],
    ["Authentifizierter Ereignisclip wird geladen.", "Loading authenticated event clip."],
    ["Kein Clip im Snapshot vorhanden.", "No clip is present in the snapshot."],
    ["Kein Clip in der Meldung vorhanden.", "No clip is attached to this report."],
    ["Für diese Korrektur gibt es keinen eigenen Clip.", "This correction has no separate clip."],
    ["Clip erneut laden", "Reload clip"], ["Clip-Hash", "Clip hash"], ["SHA-256", "SHA-256"],
    ["Modellprüfung und Ereignisclips", "Model reviews and event clips"],
    ["Modellprüfungen", "Model reviews"], ["Warten auf Modellprüfungen", "Waiting for model reviews"],
    ["Keine Modellprüfungen vorhanden", "No model reviews available"],
    ["Noch liegt keine Modellprüfung vor. Sobald ein Ereignisclip geprüft wird, erscheinen hier der tatsächliche Prompt, die Belege und die Modellantwort.", "No model review is available yet. The actual prompt, evidence, and model response will appear here when an event clip is reviewed."],
    ["Tatsächlicher Modell-Prompt", "Actual model prompt"], ["Unveränderte Modellantwort", "Unmodified model response"],
    ["Echte Belegreferenzen und Frame-IDs", "Actual evidence references and frame IDs"],
    ["Übergebene Bildbelege", "Submitted image evidence"], ["Bildbelege aus dem Modelllauf", "Image evidence from the model run"],
    ["Keine Bildbelege im Snapshot vorhanden.", "No image evidence is present in the snapshot."],
    ["Bildbelege können ohne Lauf-ID nicht geladen werden.", "Image evidence cannot be loaded without a run ID."],
    ["Authentifizierter Bildbeleg wird geladen.", "Loading authenticated image evidence."],
    ["Bildbeleg geladen.", "Image evidence loaded."], ["Bildbeleg konnte nicht dargestellt werden. Bitte erneut laden.", "Image evidence could not be displayed. Please reload it."],
    ["Authentifizierter Bildbeleg konnte nicht geladen werden. Bitte erneut laden.", "Authenticated image evidence could not be loaded. Please reload it."],
    ["Bildbelegformat nicht unterstützt. Erlaubt sind PNG und JPEG.", "Image evidence format is not supported. PNG and JPEG are allowed."],
    ["Bild erneut laden", "Reload image"], ["Antwort-Deltas", "Response deltas"], ["Validierte Bewertung", "Validated assessment"],
    ["Keine Belegreferenzen im Snapshot vorhanden.", "No evidence references are present in the snapshot."],
    ["Kein Prompt im Snapshot vorhanden.", "No prompt is present in the snapshot."],
    ["Keine Antwort-Deltas im Snapshot vorhanden.", "No response deltas are present in the snapshot."],
    ["Keine Rohantwort im Snapshot vorhanden.", "No raw response is present in the snapshot."],
    ["Noch liegt keine validierte Bewertung vor.", "No validated assessment is available yet."],
    ["Status nicht angegeben", "Status not provided"], ["Wird geprüft", "Under review"],
    ["Abgeschlossen", "Completed"], ["Fehlgeschlagen", "Failed"], ["Abgebrochen", "Cancelled"],
    ["Die Modellanfrage hat das Zeitlimit erreicht.", "The model request timed out."],
    ["Die Modellantwort ließ sich nicht als gültige Bewertung lesen.", "The model response could not be read as a valid assessment."],
    ["Die Modellantwort verwies auf ungültige Belege.", "The model response referenced invalid evidence."],
    ["Die Modellprüfung wurde abgebrochen.", "The model review was cancelled."],
    ["Modus", "Mode"], ["Clip-ID", "Clip ID"], ["Clipversion", "Clip version"],
    ["Aufnahmebildrate", "Recording frame rate"], ["Verarbeitungsbildrate", "Processing frame rate"], ["Clipdauer", "Clip duration"],
    ["Modell-ID", "Model ID"], ["Fall-ID", "Case ID"], ["Lauf-ID", "Run ID"], ["Austausch-ID", "Exchange ID"],
    ["Objektklasse", "Object class"], ["Gefahrenstatus", "Hazard status"], ["Vom Modell angegebene Sicherheit", "Model-reported confidence"],
    ["Sicherheit", "Confidence"], ["Zusammenfassung", "Summary"], ["Validierte Folgeaktion", "Validated next action"],
    ["Ereignisclip-ID", "Event clip ID"], ["Ereignisclip-Prüfsumme", "Event clip checksum"],
    ["Fallinhaltshash nicht angegeben.", "Case content hash not provided."],
    ["Vollständiger Ereignisclip zur Modellprüfung", "Full event clip for model review"],
    ["Menschlicher Arbeitsplatz und Demo-Postfach", "Human review workspace and demo inbox"],
    ["Kein offener Fall", "No open case"], ["Warten auf einen Fall zur menschlichen Prüfung.", "Waiting for a case for human review."],
    ["Fallzusammenfassung", "Case summary"], ["Fallstatus nicht angegeben", "Case status not provided"],
    ["Serverfrist für die menschliche Prüfung", "Server deadline for human review"],
    ["Frist: im Snapshot nicht angegeben", "Deadline not provided in snapshot"],
    ["Serverfrist: im Snapshot nicht angegeben", "Server deadline not provided in snapshot"],
    ["Noch keine Benachrichtigung für eine menschliche Prüfung.", "No human review notification yet."],
    ["Büroarbeitsplatz mit Laptop", "Office workspace with laptop"],
    ["Einsatz-Arbeitsplatz auf dem Laptop", "Operations workspace on laptop"],
    ["Schreibtisch – menschliche Fallprüfung", "Desk · human case review"],
    ["Simulierter Behördeneingang", "Simulated agency inbox"], ["Demo-Postfach des Behördenempfängers", "Demo inbox for the agency recipient"],
    ["Erstmeldungen und Entwarnungen", "Initial reports and corrections"],
    ["Im Demo-Postfach ist noch keine Meldung eingegangen.", "No reports have arrived in the demo inbox yet."],
    ["Noch keine Benachrichtigung für eine menschliche Prüfung.", "No human review notification yet."],
    ["Fall öffnen", "Open case"], ["Fall aktualisieren", "Refresh case"],
    ["Gleiche Entscheidung erneut senden", "Resend the same decision"],
    ["Clip-ID:", "Clip ID:"], ["Fall-ID:", "Case ID:"], ["Fallversion:", "Case version:"],
    ["Inhaltshash:", "Content hash:"], ["Fallphase:", "Case phase:"], ["Gefahrenstatus:", "Hazard status:"],
    ["Belegte Frame-IDs:", "Evidence frame IDs:"], ["Modellprüfung:", "Model review:"],
    ["Erstmeldung:", "Initial report:"], ["Meldungsgrund:", "Report reason:"], ["Entwarnung:", "Correction:"],
    ["Verknüpft mit Erstmeldung:", "Linked to initial report:"],
    ["Clipversion:", "Clip version:"], ["Clip-Hash:", "Clip hash:"],
    ["Fallstatus:", "Case status:"], ["Objektklasse:", "Object class:"], ["Zusammenfassung:", "Summary:"],
    ["Meldungsgrund nicht übersetzt", "Report reason is not translated"],
    ["Meldungsgrund im Snapshot nicht übersetzt", "Report reason is not translated in the snapshot"],
    ["Keine Frame-IDs im Snapshot vorhanden", "No frame IDs are present in the snapshot"],
    ["Nicht angegeben", "Not provided"], ["Unklar", "Unclear"], ["Ja", "Yes"], ["Nein", "No"],
    ["Wartet", "Queued"], ["Läuft", "Running"], ["Beobachtet", "Observing"],
    ["Eingabe erforderlich", "Input required"], ["Freigabe erforderlich", "Approval required"],
    ["Fall wird vorbereitet", "Preparing case"], ["Fallstart läuft", "Case is starting"],
    ["Verkehr wird ausgewertet", "Traffic is being analyzed"], ["Modellprüfung läuft", "Model review in progress"],
    ["Keine Auffälligkeit festgestellt", "No issue detected"], ["Quelle beendet", "Source ended"],
    ["Menschliche Prüfung erforderlich", "Human review required"], ["Keine Antwort bis zum Fristende", "No response before the deadline"],
    ["Erstmeldung gesendet", "Initial report sent"], ["Entwarnung gesendet", "Correction sent"],
    ["Gefahr verneint", "Hazard rejected"], ["Kein Gefahrverdacht", "No suspected hazard"], ["Fall abgebrochen", "Case cancelled"],
    ["Grund: Der Fall wurde nach der Erstmeldung menschlich korrigiert.", "Reason: A human corrected the case after the initial report."],
    ["Automatische Erstmeldung nach bestätigter hoher Sicherheit und Belegen", "Automatic initial report after high confidence and evidence were confirmed"],
    ["Erstmeldung nach menschlicher Bestätigung", "Initial report after human confirmation"],
    ["Erstmeldung nach Ablauf der serverseitigen Prüfzeit ohne Antwort", "Initial report after the server review deadline expired without a response"],
    ["Grund:", "Reason:"], ["Meldung", "Report"], ["Bestätigen", "Confirm"], ["Ablehnen", "Reject"],
    ["Gefahr bestätigen · simulierte Erstmeldung", "Confirm hazard · simulated initial report"],
    ["Gefahr verneinen", "Reject hazard"], ["Gefahr verneinen · simulierte Korrekturmeldung", "Reject hazard · simulated correction report"],
    ["Die Entscheidung wird übertragen. Weitere Entscheidungen bleiben bis zur Antwort gesperrt.", "Sending the decision. Further decisions are locked until a response arrives."],
    ["Die Antwort ist nicht angekommen. Es ist unklar, ob der Server den Antrag gespeichert hat. Du kannst exakt denselben Antrag mit derselben Kennung erneut senden.", "The response did not arrive. It is unclear whether the server stored the request. You can resend the exact same request with the same identifier."],
    ["Der Fall wurde inzwischen geändert. Bitte aktualisiere ihn und prüfe die Entscheidung erneut.", "The case has changed. Refresh it and review the decision again."],
    ["Der Server hat die Entscheidung abgelehnt. Bitte aktualisiere den Fall und prüfe ihn erneut.", "The server rejected the decision. Refresh the case and review it again."],
    ["Entscheidung vom Server angenommen.", "Decision accepted by the server."],
    ["Für diese Entscheidung konnte keine sichere Kennung erstellt werden. Bitte lade den Fall erneut.", "A safe identifier could not be created for this decision. Reload the case."],
    ["Der aktuelle Fall wird vom Server neu geladen.", "Reloading the current case from the server."],
    ["Der Fall konnte nicht aktualisiert werden. Bitte versuche es erneut.", "The case could not be refreshed. Please try again."],
    ["Authentifizierter Ereignisclip wird geladen.", "Loading authenticated event clip."],
    ["Keine Bildbelege im Snapshot vorhanden.", "No image evidence is present in the snapshot."],
    ["Modellfehler", "Model error"],
    ["Keine offenen Prüfungen", "No open reviews"],
  ];

  function t(key) {
    return COPY[currentLang][key] || key;
  }

  function h(tag, attrs, children) {
    const node = document.createElement(tag);
    if (attrs) {
      for (const [key, value] of Object.entries(attrs)) {
        if (key === "className") node.className = value;
        else if (key === "textContent") node.textContent = value;
        else if (key === "innerHTML") node.innerHTML = value;
        else if (key === "onclick") node.addEventListener("click", value);
        else if (key === "hidden") node.hidden = Boolean(value);
        else if (key === "disabled") node.disabled = Boolean(value);
        else node.setAttribute(key, value);
      }
    }
    if (children) {
      for (const child of Array.isArray(children) ? children : [children]) {
        if (typeof child === "string") node.appendChild(document.createTextNode(child));
        else if (child instanceof Node) node.appendChild(child);
      }
    }
    return node;
  }

  function tx(tag, key, className, extra = {}) {
    return h(tag, { ...extra, className, textContent: t(key), "data-i18n": key });
  }

  function navigate(room) {
    currentRoom = room;
    if (global.RoadSFX) global.RoadSFX.playClick();
    [1, 2, 3, 4].forEach((index) => {
      const button = document.getElementById("nav-node-room-" + index);
      const section = document.getElementById("cartoon-room-" + index);
      if (button) {
        button.classList.toggle("active", index === room);
        button.setAttribute("aria-current", index === room ? "page" : "false");
      }
      if (section) section.hidden = index !== room;
    });
  }

  function appendRoomNavigation(root, room) {
    root.appendChild(tx("button", "followTo" + room, "cable-follow-btn", {
      id: "follow-btn-" + room,
      type: "button",
      onclick: () => navigate(room),
    }));
    return root;
  }

  function characterRow(svg, nameKey, speechKey, id) {
    return h("div", { className: "cartoon-character-row" }, [
      h("div", { className: "cartoon-avatar", innerHTML: svg }),
      h("div", { className: "cartoon-speech-bubble" }, [
        tx("div", nameKey, "cartoon-speaker-name"),
        tx("p", speechKey, "cartoon-speech-text", { id }),
      ]),
    ]);
  }

  function eventPayload(event) {
    const mode = document.getElementById("road-model-mode")?.value || "fixture";
    const fixtureProfile = document.getElementById("road-fixture-profile")?.value || "";
    return mode === "fixture"
      ? { event, mode, fixture_profile: fixtureProfile }
      : { event, mode };
  }

  function enqueue(event) {
    const source = currentState.source;
    if (!source || source.closed === true || source.status === "stopped" || source.status === "ended") {
      setStatus(t("noSource"), true);
      return;
    }
    const payload = eventPayload(event);
    if (payload.mode === "fixture" && !payload.fixture_profile) {
      setStatus(t("profileMissing"), true);
      return;
    }
    if (global.RoadSFX) global.RoadSFX.playEventTrigger();
    setStatus("");
    Promise.resolve(callbacks.onEnqueue?.(payload)).catch((error) => {
      setStatus(error?.message || t("error"), true);
    });
  }

  function setStatus(message, isError = false) {
    const status = document.getElementById("cartoon-job-status");
    if (!status) return;
    status.textContent = message || "";
    status.classList.toggle("error-message", Boolean(isError));
    localizeModules();
  }

  function formatValue(value) {
    if (value === null || value === undefined || value === "") return "–";
    if (typeof value === "number" && Number.isFinite(value)) return String(value);
    return String(value);
  }

  function phaseText(snapshot) {
    const value = snapshot && typeof snapshot.phase === "string" ? snapshot.phase : null;
    if (!value) return "–";
    const phases = {
      queued: ["Warteschlange", "Queued"],
      starting: ["Startet", "Starting"],
      running: ["Läuft", "Running"],
      observing: ["Beobachtet", "Observing"],
      model_review: ["Modellprüfung", "Model review"],
      human_review: ["Menschliche Prüfung", "Human review"],
      no_response: ["Keine Antwort", "No response"],
      reported: ["Gemeldet", "Reported"],
      rejected: ["Abgelehnt", "Rejected"],
      completed: ["Abgeschlossen", "Completed"],
      cancelled: ["Abgebrochen", "Cancelled"],
      failed: ["Fehlgeschlagen", "Failed"],
    };
    const pair = phases[value];
    return pair ? pair[currentLang === "de" ? 0 : 1] : value;
  }

  function eventText(value) {
    const labels = {
      wrong_way: ["Falschfahrer-Szenario", "Wrong-way scenario"],
      bird: ["Vogel-Szenario", "Bird scenario"],
      leaf: ["Blatt-Szenario", "Leaf scenario"],
    };
    const pair = labels[value];
    return pair ? pair[currentLang === "de" ? 0 : 1] : formatValue(value);
  }

  function modeText(value) {
    if (value === "fixture") return currentLang === "de" ? "Offline-Testantwort" : "Offline test response";
    if (value === "gemini_video") return "Gemini Video";
    return formatValue(value);
  }

  function selectCase(caseId) {
    if (typeof caseId !== "string" || !caseId) return;
    requestedSelectedCaseId = caseId;
    if (gameRoot) gameRoot.dataset.selectedCaseId = caseId;
    gameRoot?.querySelectorAll("button[data-testid='case-ticket']").forEach((button) => {
      button.setAttribute("aria-pressed", String(button.dataset.caseId === caseId));
    });
    callbacks.onSelectCase?.(caseId);
  }

  function ticketButton(record, selectedId, jobs) {
    const isJob = record.kind === "job";
    const caseId = isJob ? record.job.case_id : record.snapshot.case_id;
    const id = isJob ? record.job.job_id : record.snapshot.case_id;
    const button = h("button", {
      type: "button",
      className: "queue-item",
      "data-testid": "case-ticket",
      "aria-pressed": String(Boolean(caseId && caseId === selectedId)),
      disabled: !caseId,
      onclick: () => selectCase(caseId),
    });
    if (caseId) button.dataset.caseId = caseId;
    if (isJob) button.dataset.jobId = id;
    const title = document.createElement("strong");
    title.textContent = isJob ? t("job") + " " + formatValue(id) : t("selectedCase") + " " + formatValue(caseId);
    const details = document.createElement("small");
    if (isJob) {
      details.textContent = [
        t("event") + ": " + eventText(record.job.event),
        t("mode") + ": " + modeText(record.job.mode),
        t("profile") + ": " + formatValue(record.job.fixture_profile),
        phaseText({ phase: record.job.state }),
      ].join(" · ");
    } else {
      const job = jobs.find((item) => item && item.case_id === caseId);
      const parts = [t("phase") + ": " + phaseText(record.snapshot), t("mode") + ": " + modeText(record.snapshot.mode)];
      if (record.snapshot.fixture_profile) parts.push(t("profile") + ": " + String(record.snapshot.fixture_profile));
      if (job) {
        parts.push(t("job") + ": " + formatValue(job.job_id));
        parts.push(phaseText({ phase: job.state }));
        if (job.event) parts.push(t("event") + ": " + eventText(job.event));
      }
      details.textContent = parts.join(" · ");
    }
    button.append(title, details);
    return button;
  }

  function renderTickets(target, selectedId) {
    if (!target) return;
    const cases = Array.isArray(currentState.cases) ? currentState.cases : [];
    const jobs = Array.isArray(currentState.jobs) ? currentState.jobs : [];
    const records = [];
    const caseIds = new Set();
    for (const snapshot of cases) {
      if (!snapshot || typeof snapshot.case_id !== "string") continue;
      caseIds.add(snapshot.case_id);
      records.push({ kind: "case", snapshot });
    }
    for (const job of jobs) {
      if (!job || typeof job.job_id !== "string") continue;
      if (job.case_id && caseIds.has(job.case_id)) continue;
      records.push({ kind: "job", job });
    }
    records.sort((left, right) => {
      const leftTime = String(left.kind === "job" ? left.job.created_at || "" : left.snapshot.created_at || "");
      const rightTime = String(right.kind === "job" ? right.job.created_at || "" : right.snapshot.created_at || "");
      return rightTime.localeCompare(leftTime);
    });
    const focused = document.activeElement;
    const focusId = focused && target.contains(focused)
      ? (focused.dataset.caseId || focused.dataset.jobId || null)
      : null;
    target.replaceChildren();
    if (!records.length) {
      const empty = document.createElement("p");
      empty.className = "road-empty-state";
      empty.textContent = t("noJobs");
      target.appendChild(empty);
      return;
    }
    for (const record of records) target.appendChild(ticketButton(record, selectedId, jobs));
    if (focusId) {
      const restored = [...target.querySelectorAll("button[data-case-id],button[data-job-id]")]
        .find((item) => item.dataset.caseId === focusId || item.dataset.jobId === focusId);
      if (restored) restored.focus({ preventScroll: true });
    }
  }

  function appendActualValue(target, label, value) {
    const row = document.createElement("p");
    const strong = document.createElement("strong");
    strong.textContent = label + ": ";
    const text = document.createElement("span");
    text.className = "road-case-field-value";
    text.textContent = formatValue(value);
    row.append(strong, text);
    target.appendChild(row);
  }

  function renderSource(state) {
    const target = document.getElementById("cartoon-source-facts");
    if (!target) return;
    target.replaceChildren();
    const source = state.source;
    if (!source || typeof source !== "object") {
      const empty = document.createElement("p");
      empty.className = "road-empty-state";
      empty.textContent = t("noSourceData");
      target.appendChild(empty);
      return;
    }
    appendActualValue(target, t("sourceId"), source.source_id);
    if (typeof source.frame_count === "number") appendActualValue(target, t("frames"), source.frame_count);
    appendActualValue(target, t("sourceStatus"), source.status);
    const status = document.createElement("p");
    status.textContent = source.closed === true || source.status === "stopped" || source.status === "ended"
      ? t("sourceEnded")
      : t("sourceReady");
    target.appendChild(status);
  }

  function renderAssessment(state) {
    const target = document.getElementById("datacenter-verdict-box");
    if (!target) return;
    target.replaceChildren();
    const snapshot = state.selectedCase;
    if (!snapshot) {
      const empty = document.createElement("p");
      empty.className = "road-empty-state";
      empty.textContent = t("noCase");
      target.appendChild(empty);
      return;
    }
    appendActualValue(target, t("selectedCase"), snapshot.case_id);
    appendActualValue(target, t("phase"), phaseText(snapshot));
    if (snapshot.mode) appendActualValue(target, t("mode"), modeText(snapshot.mode));
    if (snapshot.fixture_profile) appendActualValue(target, t("profile"), snapshot.fixture_profile);
    const clip = snapshot.clip && typeof snapshot.clip === "object" ? snapshot.clip : null;
    if (clip) {
      appendActualValue(target, t("clipId"), clip.clip_id);
      appendActualValue(target, t("clipHash"), clip.sha256);
    } else {
      const noClip = document.createElement("p");
      noClip.className = "road-empty-state";
      noClip.textContent = t("noClip");
      target.appendChild(noClip);
    }
    if (snapshot.model_error) appendActualValue(target, t("error"), snapshot.model_error);
    if (snapshot.assessment && typeof snapshot.assessment === "object") {
      const assessment = snapshot.assessment;
      const labels = currentLang === "de"
        ? { object_class: "Objektklasse", hazard: "Gefahrenstatus", confidence: "Sicherheit", summary: "Zusammenfassung" }
        : { object_class: "Object class", hazard: "Hazard status", confidence: "Confidence", summary: "Summary" };
      for (const key of ["object_class", "hazard", "confidence", "summary"]) {
        if (assessment[key] !== null && assessment[key] !== undefined && assessment[key] !== "") {
          appendActualValue(target, labels[key], assessment[key]);
        }
      }
    } else {
      const pending = document.createElement("p");
      pending.className = "road-empty-state";
      pending.textContent = t("noAssessment");
      target.appendChild(pending);
    }
  }  function receiptSummary(receipt) {
    if (!receipt || typeof receipt !== "object") return null;
    return {
      caseId: receipt.case_id,
      receiptId: receipt.receipt_id || receipt.message_id || receipt.id,
      decision: receipt.decision || receipt.action || receipt.status,
    };
  }

  function renderRoom4(state) {
    const snapshot = state.selectedCase;
    const selected = document.getElementById("beta-selected-case");
    const remaining = document.getElementById("beta-countdown-clock");
    const message = document.getElementById("beta-alert-msg");
    const receipts = document.getElementById("beta-receipts");
    if (!selected || !remaining || !message || !receipts) return;
    selected.textContent = snapshot
      ? t("selectedCase") + ": " + formatValue(snapshot.case_id)
      : t("noSelection");
    remaining.textContent = snapshot && typeof snapshot.remaining_seconds === "number"
      ? String(Math.max(0, Math.ceil(snapshot.remaining_seconds))) + " s"
      : "–";
    message.textContent = snapshot ? t("phase") + ": " + phaseText(snapshot) : "";
    receipts.replaceChildren();
    const initial = receiptSummary(snapshot?.first_receipt);
    const correction = receiptSummary(snapshot?.correction_receipt);
    let count = 0;
    for (const [label, receipt] of [[t("firstReceipt"), initial], [t("correctionReceipt"), correction]]) {
      if (!receipt) continue;
      const row = document.createElement("p");
      row.dataset.receiptCaseId = receipt.caseId || (snapshot && snapshot.case_id) || "";
      const parts = [label];
      if (receipt.receiptId) parts.push(formatValue(receipt.receiptId));
      if (receipt.decision) parts.push(formatValue(receipt.decision));
      row.textContent = parts.join(" · ");
      receipts.appendChild(row);
      count += 1;
    }
    if (count === 0) {
      const empty = document.createElement("p");
      empty.className = "road-empty-state";
      empty.textContent = t("noReceipt");
      receipts.appendChild(empty);
    }
    const character = document.getElementById("speech-weber");
    if (character) character.textContent = t("room4Intro");
  }

  function localizeModules() {
    if (!gameRoot) return;
    const forward = new Map(MODULE_COPY);
    const reverse = new Map(MODULE_COPY.map(([de, en]) => [en, de]));
    const translations = currentLang === "en" ? forward : reverse;
    const dynamicTranslations = currentLang === "en" ? [
      [/^Quelle: (.*)$/, (_match, value) => `Source: ${value}`],
      [/^Quelle beendet · (.+) Frames · (.+) \(max\. 20 FPS\) · (.+) Sek\. · (.+) Byte$/, (_match, frames, rate, seconds, bytes) => `Source ended · ${frames} recorded frames · ${rate} (max. 20 FPS) · ${seconds} s · ${bytes} bytes`],
      [/^Aufzeichnung läuft · (.+) Frames · (.+) \(max\. 20 FPS\) · (.+) Sek\. · (.+) Byte$/, (_match, frames, rate, seconds, bytes) => `Recording · ${frames} recorded frames · ${rate} (max. 20 FPS) · ${seconds} s · ${bytes} bytes`],
      [/^FPS noch nicht gemessen$/, () => "FPS not measured yet"],
      [/^([0-9.,]+) FPS gemessen$/, (_match, value) => `${value} FPS measured`],
      [/^Frame (.+) · Quellenzeit (.+) Sek\.$/, (_match, frame, seconds) => `Frame ${frame} · source time ${seconds} s`],
      [/^(.+) in die Szene eingeblendet\. Das ist noch kein Analysebefund\.$/, (_match, event) => `${event === "Falschfahrer" ? "Wrong-way event" : event === "Vogel" ? "Bird event" : event === "Blatt" ? "Leaf event" : event} added to the scene. This is not an analysis finding.`],
      [/^Quelle (.+) · Version (.+) · (.+)–(.+) Sek\.$/, (_match, source, version, start, end) => `Source ${source} · Version ${version} · ${start}–${end} s`],
      [/^Fall ([^ ·]+) · (.+)$/, (_match, caseId, phase) => `Case ${caseId} · ${translations.get(phase) || phase}`],
      [/^Fallphase: (.+)$/, (_match, phase) => `Case phase: ${translations.get(phase) || phase}`],
      [/^Fall ([^ ·]+) · ([A-Za-z0-9_-]+)$/, (_match, caseId, phase) => `Case ${caseId} · ${phase}`],
      [/^Serverfrist endet: (.+)$/, (_match, value) => `Server deadline ends: ${value}`],
      [/^Serverfrist: (.+)$/, (_match, value) => `Server deadline: ${value}`],
      [/^Frist: (.+)$/, (_match, value) => `Deadline: ${value}`],
      [/^Meldungs-ID: (.+)$/, (_match, value) => `Report ID: ${value}`],
      [/^Gebundener Fallinhalt: (.+)$/, (_match, value) => `Bound case content: ${value}`],
      [/^Fallinhaltshash nicht angegeben\.$/, () => "Case content hash not provided."],
      [/^Vollständiger Ereignisclip (.+) zur Modellprüfung$/, (_match, id) => `Full event clip ${id} for model review`],
      [/^Vollständiger Ereignisclip (.+) zur menschlichen Prüfung$/, (_match, id) => `Full event clip ${id} for human review`],
      [/^Vollständiger Ereignisclip zur Modellprüfung (.+)$/, (_match, id) => `Full event clip for model review ${id}`],
      [/^Entwarnung zu Fall (.+)$/, (_match, value) => `Correction for case ${value}`],
      [/^Erstmeldung zu Fall (.+)$/, (_match, value) => `Initial report for case ${value}`],
      [/^Verknüpft mit ursprünglicher Erstmeldung (.+)\.$/, (_match, value) => `Linked to original initial report ${value}.`],
      [/^Grund: (.+)$/, (_match, value) => `Reason: ${translations.get(value) || value}`],
      [/^Fallphase: (.+)$/, (_match, value) => `Case phase: ${translations.get(value) || value}`],
      [/^Modellprüfung (.+)$/, (_match, id) => `Model review ${id}`],
      [/^(\d+) Modellprüfung(?:en)?$/, (_match, count) => `${count} model review${count === "1" ? "" : "s"}`],
      [/^(Wartet|Läuft|Beobachtet|Eingabe erforderlich|Freigabe erforderlich|Abgeschlossen|Fehlgeschlagen|Abgebrochen) · Lauf (.+) · Startanalyse mit drei Frames abgeschlossen$/, (_match, status, id) => `${translations.get(status) || status} · Run ${id} · Startup analysis with three frames completed`],
      [/^(Wartet|Läuft|Beobachtet|Eingabe erforderlich|Freigabe erforderlich|Abgeschlossen|Fehlgeschlagen|Abgebrochen) · Lauf (.+) · Startframes (\d+\/3)$/, (_match, status, id, frames) => `${translations.get(status) || status} · Run ${id} · Startup frames ${frames}`],
    ] : [
      [/^Source: (.*)$/, (_match, value) => `Quelle: ${value}`],
      [/^Source ended · (.+) recorded frames · (.+) \(max\. 20 FPS\) · (.+) s · (.+) bytes$/, (_match, frames, rate, seconds, bytes) => `Quelle beendet · ${frames} Frames · ${rate} (max. 20 FPS) · ${seconds} Sek. · ${bytes} Byte`],
      [/^Recording · (.+) recorded frames · (.+) \(max\. 20 FPS\) · (.+) s · (.+) bytes$/, (_match, frames, rate, seconds, bytes) => `Aufzeichnung läuft · ${frames} Frames · ${rate} (max. 20 FPS) · ${seconds} Sek. · ${bytes} Byte`],
      [/^FPS not measured yet$/, () => "FPS noch nicht gemessen"],
      [/^([0-9.,]+) FPS measured$/, (_match, value) => `${value} FPS gemessen`],
      [/^Frame (.+) · source time (.+) s$/, (_match, frame, seconds) => `Frame ${frame} · Quellenzeit ${seconds} Sek.`],
      [/^(.+) added to the scene\. This is not an analysis finding\.$/, (_match, event) => `${event === "Wrong-way event" ? "Falschfahrer" : event === "Bird event" ? "Vogel" : event === "Leaf event" ? "Blatt" : event} in die Szene eingeblendet. Das ist noch kein Analysebefund.`],
      [/^Source (.+) · Version (.+) · (.+)–(.+) s$/, (_match, source, version, start, end) => `Quelle ${source} · Version ${version} · ${start}–${end} Sek.`],
      [/^Case ([^ ·]+) · (.+)$/, (_match, caseId, phase) => `Fall ${caseId} · ${translations.get(phase) || phase}`],
      [/^Case phase: (.+)$/, (_match, phase) => `Fallphase: ${translations.get(phase) || phase}`],
      [/^Server deadline ends: (.+)$/, (_match, value) => `Serverfrist endet: ${value}`],
      [/^Server deadline: (.+)$/, (_match, value) => `Serverfrist: ${value}`],
      [/^Deadline: (.+)$/, (_match, value) => `Frist: ${value}`],
      [/^Report ID: (.+)$/, (_match, value) => `Meldungs-ID: ${value}`],
      [/^Bound case content: (.+)$/, (_match, value) => `Gebundener Fallinhalt: ${value}`],
      [/^Case content hash not provided\.$/, () => "Fallinhaltshash nicht angegeben."],
      [/^Full event clip (.+) for model review$/, (_match, id) => `Vollständiger Ereignisclip ${id} zur Modellprüfung`],
      [/^Full event clip (.+) for human review$/, (_match, id) => `Vollständiger Ereignisclip ${id} zur menschlichen Prüfung`],
      [/^Full event clip for model review (.+)$/, (_match, id) => `Vollständiger Ereignisclip zur Modellprüfung ${id}`],
      [/^Correction for case (.+)$/, (_match, value) => `Entwarnung zu Fall ${value}`],
      [/^Initial report for case (.+)$/, (_match, value) => `Erstmeldung zu Fall ${value}`],
      [/^Linked to original initial report (.+)\.$/, (_match, value) => `Verknüpft mit ursprünglicher Erstmeldung ${value}.`],
      [/^Reason: (.+)$/, (_match, value) => `Grund: ${translations.get(value) || value}`],
      [/^Model review (.+)$/, (_match, id) => `Modellprüfung ${id}`],
      [/^(\d+) model review(?:s)?$/, (_match, count) => `${count} Modellprüfung${count === "1" ? "" : "en"}`],
      [/^(Queued|Running|Observing|Input required|Approval required|Completed|Failed|Cancelled) · Run (.+) · Startup analysis with three frames completed$/, (_match, status, id) => `${translations.get(status) || status} · Lauf ${id} · Startanalyse mit drei Frames abgeschlossen`],
      [/^(Queued|Running|Observing|Input required|Approval required|Completed|Failed|Cancelled) · Run (.+) · Startup frames (\d+\/3)$/, (_match, status, id, frames) => `${translations.get(status) || status} · Lauf ${id} · Startframes ${frames}`],
    ];
    const translate = (original) => {
      const exact = translations.get(original);
      if (exact) return exact;
      for (const [pattern, render] of dynamicTranslations) {
        const match = original.match(pattern);
        if (match) return render(...match);
      }
      return null;
    };
    const walker = document.createTreeWalker(gameRoot, NodeFilter.SHOW_TEXT);
    const nodes = [];
    while (walker.nextNode()) nodes.push(walker.currentNode);
    for (const node of nodes) {
      if (node.parentElement?.closest("pre,code,video,audio,.road-model-field-value,.road-case-field-value,#road-controller-trace,#road-timeline-content")) continue;
      const original = node.nodeValue.trim();
      const translated = translate(original);
      if (translated) node.nodeValue = node.nodeValue.replace(original, translated);
    }
    const attributeNodes = gameRoot.querySelectorAll("[aria-label],[title]");
    for (const node of attributeNodes) {
      for (const attribute of ["aria-label", "title"]) {
        const original = node.getAttribute(attribute);
        if (!original) continue;
        const translated = translate(original);
        if (translated) node.setAttribute(attribute, translated);
      }
    }
  }

  function refreshLanguage() {
    if (!gameRoot) return;
    gameRoot.querySelectorAll("[data-i18n]").forEach((node) => {
      node.textContent = t(node.dataset.i18n);
    });
    document.documentElement.lang = currentLang;
    localizeModules();
    const sound = document.getElementById("cartoon-sound-btn");
    if (sound && global.RoadSFX) {
      sound.textContent = global.RoadSFX.getMuted() ? t("soundOff") : t("soundOn");
    }
    const roadTab = document.getElementById("road-tab");
    if (roadTab) roadTab.textContent = t("roadTab");
    update(currentState);
    gameRoot.dispatchEvent(new CustomEvent("road-language-changed", {
      bubbles: true,
      detail: { locale: currentLang },
    }));
  }

  function buildCartoonDOM() {
    const root = h("div", { className: "cartoon-stage road-cartoon-stage" });
    root.dataset.selectedCaseId = "";

    root.appendChild(h("header", { className: "cartoon-header" }, [
      h("div", { className: "cartoon-brand" }, [
        h("span", { className: "cartoon-brand__badge", textContent: "🚦" }),
        h("div", {}, [
          tx("h2", "title", "cartoon-brand__title", { id: "cartoon-title" }),
          tx("p", "subtitle", "cartoon-brand__subtitle", { id: "cartoon-subtitle" }),
        ]),
      ]),
      h("div", { className: "cartoon-top-controls" }, [
        h("button", {
          id: "cartoon-sound-btn",
          className: "cartoon-pill-btn",
          type: "button",
          textContent: global.RoadSFX && global.RoadSFX.getMuted() ? t("soundOff") : t("soundOn"),
          onclick: () => {
            if (!global.RoadSFX) return;
            global.RoadSFX.setMuted(!global.RoadSFX.getMuted());
            refreshLanguage();
          },
        }),
        tx("button", "langToggle", "cartoon-pill-btn", {
          id: "cartoon-lang-btn",
          type: "button",
          onclick: () => {
            currentLang = currentLang === "de" ? "en" : "de";
            refreshLanguage();
          },
        }),
      ]),
    ]));

    const nav = h("nav", { className: "cartoon-conduit-nav", "aria-label": t("navLabel") });
    for (let room = 1; room <= 4; room += 1) {
      if (room > 1) nav.appendChild(h("div", {
        id: "cable-" + (room - 1) + "-" + room,
        className: "cartoon-cable-segment",
        "aria-hidden": "true",
      }));
      nav.appendChild(h("button", {
        id: "nav-node-room-" + room,
        className: "cartoon-station-node" + (room === 1 ? " active" : ""),
        type: "button",
        "aria-current": room === 1 ? "page" : "false",
        onclick: () => navigate(room),
      }, [
        h("span", { className: "cartoon-station-icon", textContent: ["🚦", "🏢", "🚓", "🚨"][room - 1], "aria-hidden": "true" }),
        tx("span", "room" + room, "", { id: "nav-text-" + room }),
      ]));
    }
    root.appendChild(nav);
    root.appendChild(h("p", {
      id: "road-workspace-status",
      className: "road-workspace-status",
      role: "status",
      "aria-live": "polite",
    }));

    const room1 = h("section", { id: "cartoon-room-1", className: "cartoon-room", "aria-label": t("room1") }, [
      h("div", { className: "cartoon-room__stage" }, [
        characterRow(SVGS.technicianAlex, "alexName", "alexIntro", "speech-alex"),
        h("div", { className: "traffic-screen-wrap" }, [
          h("section", { className: "traffic-monitor" }, [
            h("div", { className: "traffic-monitor__header" }, [
              tx("span", "cameraTitle", "", {}),
              h("span", { id: "cartoon-cam-status", textContent: t("noSourceData") }),
            ]),
            h("div", { id: "road-camera-live-slot", className: "road-camera-live-slot" }),
          ]),
          h("section", { className: "traffic-control-panel" }, [
            tx("h3", "eventTitle", "", {}),
            tx("p", "eventHelp", "hint", {}),
            h("div", { className: "cartoon-btn-grid" }, [
              tx("button", "wrongWay", "chunky-btn chunky-btn--wrongway", {
                id: "btn-sim-wrongway", type: "button", disabled: true, onclick: () => enqueue("wrong_way"),
              }),
              tx("button", "bird", "chunky-btn chunky-btn--bird", {
                id: "btn-sim-bird", type: "button", disabled: true, onclick: () => enqueue("bird"),
              }),
              tx("button", "leaf", "chunky-btn chunky-btn--leaf", {
                id: "btn-sim-leaf", type: "button", disabled: true, onclick: () => enqueue("leaf"),
              }),
            ]),
            h("p", { id: "cartoon-job-status", className: "road-workspace-status", role: "status", "aria-live": "polite" }),
            h("div", { id: "cartoon-source-facts", className: "cartoon-source-facts" }),
          ]),
        ]),
        h("div", { id: "road-camera-root", className: "road-camera-root" }),
        appendRoomNavigation(h("div", { className: "cartoon-follow-row" }), 2),
      ]),
    ]);
    root.appendChild(room1);

    const room2 = h("section", { id: "cartoon-room-2", className: "cartoon-room", "aria-label": t("room2"), hidden: true }, [
      h("div", { className: "cartoon-room__stage" }, [
        characterRow(SVGS.drKira, "kiraName", "kiraIntro", "speech-kira"),
        h("section", { className: "datacenter-card" }, [
          tx("h3", "jobQueue", "datacenter-card__title", {}),
          h("div", { id: "datacenter-queue-list", className: "datacenter-queue-list" }),
        ]),
        h("section", { className: "datacenter-card" }, [
          tx("h3", "resultTitle", "datacenter-card__title", {}),
          h("div", { id: "datacenter-verdict-box", className: "datacenter-verdict" }),
        ]),
        h("section", { id: "road-model-root", className: "road-workspace-panel", "aria-label": t("jobQueue") }),
        appendRoomNavigation(h("div", { className: "cartoon-follow-row" }), 3),
      ]),
    ]);
    root.appendChild(room2);    const coffeeButton = tx("button", "coffee", "chunky-btn chunky-btn--coffee", {
      id: "btn-schmidt-coffee",
      type: "button",
      onclick: () => {
        if (global.RoadSFX) global.RoadSFX.playCoffee();
        callbacks.onCoffeeNavigation?.();
        const speech = document.getElementById("speech-schmidt");
        if (speech) speech.textContent = t("coffeeStatus");
        navigate(4);
      },
    });
    const room3 = h("section", { id: "cartoon-room-3", className: "cartoon-room", "aria-label": t("room3"), hidden: true }, [
      h("div", { className: "cartoon-room__stage" }, [
        characterRow(SVGS.officerSchmidt, "schmidtName", "schmidtIntro", "speech-schmidt"),
        h("section", { className: "datacenter-card" }, [
          tx("h3", "historyTitle", "datacenter-card__title", {}),
          h("div", { id: "schmidt-ticket-list", className: "schmidt-ticket-list" }),
        ]),
        h("section", { id: "road-workplace-root", className: "road-workspace-panel", "aria-label": t("workplaceTitle") }),
        coffeeButton,
        appendRoomNavigation(h("div", { className: "cartoon-follow-row" }), 4),
      ]),
    ]);
    root.appendChild(room3);

    const room4 = h("section", { id: "cartoon-room-4", className: "cartoon-room", "aria-label": t("room4"), hidden: true }, [
      h("div", { className: "cartoon-room__stage" }, [
        characterRow(SVGS.officerWeberEmpty, "weberName", "room4Intro", "speech-weber"),
        h("section", { className: "beta-alert-box" }, [
          tx("h3", "selectedCase", "", {}),
          h("p", { id: "beta-selected-case", textContent: t("noSelection") }),
          h("p", { className: "beta-alert-label", textContent: t("remaining") }),
          h("div", { id: "beta-countdown-clock", className: "beta-countdown-display", textContent: "–" }),
          h("div", { id: "beta-alert-msg", className: "road-server-phase", role: "status", "aria-live": "polite" }),
          tx("h3", "receiptTitle", "", {}),
          h("div", { id: "beta-receipts" }),
        ]),
        h("section", { id: "road-timeline-root", className: "road-workspace-panel road-timeline" }, [
          tx("h3", "timelineTitle", "road-panel-title", {}),
          h("div", { id: "road-controller-trace" }),
          h("div", { id: "road-timeline-content" }, [
            tx("p", "noTimeline", "road-empty-state", {}),
          ]),
        ]),
      ]),
    ]);
    root.appendChild(room4);

    root.appendChild(h("div", { className: "patrol-car-stage", "aria-label": t("patrol") }, [
      h("div", { className: "patrol-car-road" }),
      h("div", { id: "cartoon-patrol-car", className: "patrol-car", innerHTML: SVGS.patrolCar, "aria-hidden": "true" }),
    ]));
    return root;
  }

  function update(state = {}) {
    if (!gameRoot) return;
    currentState = {
      ...currentState,
      ...state,
      cases: Array.isArray(state.cases) ? state.cases : currentState.cases,
      jobs: Array.isArray(state.jobs) ? state.jobs : currentState.jobs,
    };
    const selectedCase = currentState.selectedCase || null;
    const selectedId = selectedCase && typeof selectedCase.case_id === "string" ? selectedCase.case_id : "";
    if (selectedId) requestedSelectedCaseId = selectedId;
    else if (requestedSelectedCaseId && !currentState.cases.some((item) => item?.case_id === requestedSelectedCaseId)) {
      requestedSelectedCaseId = null;
    }
    gameRoot.dataset.selectedCaseId = selectedId || requestedSelectedCaseId || "";
    renderSource(currentState);
    const displayedSelectedId = selectedId || requestedSelectedCaseId;
    renderTickets(document.getElementById("datacenter-queue-list"), displayedSelectedId);
    renderTickets(document.getElementById("schmidt-ticket-list"), displayedSelectedId);
    renderAssessment(currentState);
    renderRoom4(currentState);
    const source = currentState.source;
    const camStatus = document.getElementById("cartoon-cam-status");
    const eventButtons = ["btn-sim-wrongway", "btn-sim-bird", "btn-sim-leaf"];
    const sourceOpen = Boolean(source && source.closed !== true && source.status !== "stopped" && source.status !== "ended");
    const modeSelect = document.getElementById("road-model-mode");
    const profileSelect = document.getElementById("road-fixture-profile");
    const nativeOption = modeSelect?.selectedOptions?.[0];
    const modeReady = Boolean(modeSelect?.value) && !(modeSelect.value === "gemini_video" && nativeOption?.disabled);
    const profileReady = modeSelect?.value !== "fixture" || Boolean(profileSelect?.value);
    if (camStatus) camStatus.textContent = !source
      ? t("noSourceData")
      : sourceOpen ? t("sourceReady") : t("sourceEnded");
    for (const id of eventButtons) {
      const button = document.getElementById(id);
      if (button) button.disabled = !sourceOpen || !modeReady || !profileReady;
    }
    localizeModules();
  }

  function mountCartoonGame(container, options = {}) {
    if (!container) return null;
    const existing = container.querySelector(".road-cartoon-stage");
    if (existing) existing.remove();
    callbacks = options || {};
    currentRoom = 1;
    requestedSelectedCaseId = null;
    gameRoot = buildCartoonDOM();
    container.prepend(gameRoot);
    update(currentState);
    return {
      update,
      destroy: () => {
        if (!gameRoot) return;
        gameRoot.remove();
        gameRoot = null;
      },
    };
  }

  global.RoadGame = { mountCartoonGame };
})(window);
