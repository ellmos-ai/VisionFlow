/**
 * VisionFlow Emergency Leitstelle – Cartoon Sound Effects
 * 100% self-contained Web Audio API synthesizer. Zero external files or CDNs.
 */
(function (global) {
  let audioCtx = null;
  let isMuted = false;

  function getAudioContext() {
    if (!audioCtx) {
      const AudioContext = window.AudioContext || window.webkitAudioContext;
      if (AudioContext) {
        audioCtx = new AudioContext();
      }
    }
    if (audioCtx && audioCtx.state === "suspended") {
      audioCtx.resume();
    }
    return audioCtx;
  }

  function setMuted(muted) {
    isMuted = Boolean(muted);
    try {
      localStorage.setItem("visionflow_sfx_muted", isMuted ? "1" : "0");
    } catch (_) {}
  }

  function getMuted() {
    try {
      const stored = localStorage.getItem("visionflow_sfx_muted");
      if (stored !== null) {
        isMuted = stored === "1";
      }
    } catch (_) {}
    return isMuted;
  }

  // Friendly cartoon button click (pleasant pop)
  function playClick() {
    if (getMuted()) return;
    const ctx = getAudioContext();
    if (!ctx) return;

    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.type = "sine";
    osc.frequency.setValueAtTime(440, ctx.currentTime);
    osc.frequency.exponentialRampToValueAtTime(880, ctx.currentTime + 0.08);

    gain.gain.setValueAtTime(0.15, ctx.currentTime);
    gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + 0.08);

    osc.connect(gain);
    gain.connect(ctx.destination);
    osc.start();
    osc.stop(ctx.currentTime + 0.09);
  }

  // Cable data transfer whoosh (bubbly futuristic sweep)
  function playCablePulse() {
    if (getMuted()) return;
    const ctx = getAudioContext();
    if (!ctx) return;

    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.type = "triangle";
    osc.frequency.setValueAtTime(300, ctx.currentTime);
    osc.frequency.linearRampToValueAtTime(700, ctx.currentTime + 0.15);
    osc.frequency.exponentialRampToValueAtTime(200, ctx.currentTime + 0.35);

    gain.gain.setValueAtTime(0.01, ctx.currentTime);
    gain.gain.linearRampToValueAtTime(0.12, ctx.currentTime + 0.1);
    gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + 0.35);

    osc.connect(gain);
    gain.connect(ctx.destination);
    osc.start();
    osc.stop(ctx.currentTime + 0.36);
  }

  // Event triggered fanfare / alert blip
  function playEventTrigger() {
    if (getMuted()) return;
    const ctx = getAudioContext();
    if (!ctx) return;

    [523.25, 659.25, 783.99].forEach((freq, idx) => {
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();
      osc.type = "sine";
      osc.frequency.value = freq;
      const start = ctx.currentTime + idx * 0.06;
      gain.gain.setValueAtTime(0.1, start);
      gain.gain.exponentialRampToValueAtTime(0.001, start + 0.14);

      osc.connect(gain);
      gain.connect(ctx.destination);
      osc.start(start);
      osc.stop(start + 0.15);
    });
  }

  // Radio walkie-talkie click and beep
  function playRadio() {
    if (getMuted()) return;
    const ctx = getAudioContext();
    if (!ctx) return;

    // Beep 1 & Beep 2
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.type = "square";
    osc.frequency.setValueAtTime(950, ctx.currentTime);
    osc.frequency.setValueAtTime(1200, ctx.currentTime + 0.08);

    gain.gain.setValueAtTime(0.08, ctx.currentTime);
    gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + 0.18);

    osc.connect(gain);
    gain.connect(ctx.destination);
    osc.start();
    osc.stop(ctx.currentTime + 0.19);
  }

  // Friendly cartoon emergency siren (Tatü-Tata)
  function playSiren() {
    if (getMuted()) return;
    const ctx = getAudioContext();
    if (!ctx) return;

    const notes = [
      { f: 587.33, t: 0.22 }, // D5
      { f: 440.00, t: 0.22 }, // A4
      { f: 587.33, t: 0.22 }, // D5
      { f: 440.00, t: 0.22 }, // A4
    ];

    let offset = ctx.currentTime;
    notes.forEach((note) => {
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();
      osc.type = "triangle";
      osc.frequency.setValueAtTime(note.f, offset);

      gain.gain.setValueAtTime(0.12, offset);
      gain.gain.linearRampToValueAtTime(0.12, offset + note.t - 0.02);
      gain.gain.exponentialRampToValueAtTime(0.001, offset + note.t);

      osc.connect(gain);
      gain.connect(ctx.destination);
      osc.start(offset);
      osc.stop(offset + note.t);
      offset += note.t;
    });
  }

  // Coffee sip / relax chime
  function playCoffee() {
    if (getMuted()) return;
    const ctx = getAudioContext();
    if (!ctx) return;

    [659.25, 587.33, 523.25].forEach((freq, idx) => {
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();
      osc.type = "sine";
      osc.frequency.value = freq;
      const start = ctx.currentTime + idx * 0.09;
      gain.gain.setValueAtTime(0.1, start);
      gain.gain.exponentialRampToValueAtTime(0.001, start + 0.2);

      osc.connect(gain);
      gain.connect(ctx.destination);
      osc.start(start);
      osc.stop(start + 0.22);
    });
  }

  global.RoadSFX = {
    playClick,
    playCablePulse,
    playEventTrigger,
    playRadio,
    playSiren,
    playCoffee,
    setMuted,
    getMuted,
  };
})(window);
