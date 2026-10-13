/* VisionFlow UI language: English by default, German on request.
 * Order: ?lang=de|en, then localStorage["visionflow.lang"], then "en".
 * Static HTML is written in English; German lives in data-de / data-de-<attr>. */
(function (global) {
  "use strict";
  const KEY = "visionflow.lang";
  const ok = (v) => (v === "de" || v === "en" ? v : null);
  let lang = "en";
  try {
    lang = ok(new URLSearchParams(global.location.search).get("lang")) ||
      ok(global.localStorage.getItem(KEY)) || "en";
  } catch (_err) {
    try { lang = ok(new URLSearchParams(global.location.search).get("lang")) || "en"; } catch (_e) { /* keep en */ }
  }
  document.documentElement.lang = lang;

  function setLang(code) {
    const next = ok(code) || "en";
    try { global.localStorage.setItem(KEY, next); } catch (_err) { /* private mode */ }
    const url = new URL(global.location.href);
    if (url.searchParams.has("lang")) url.searchParams.set("lang", next);
    global.location.href = url.toString();
  }

  function applyStatic() {
    if (lang !== "de") return;
    for (const el of document.querySelectorAll("[data-de]")) el.textContent = el.getAttribute("data-de");
    for (const attr of ["placeholder", "aria-label", "title", "alt", "value"]) {
      for (const el of document.querySelectorAll(`[data-de-${attr}]`)) {
        el.setAttribute(attr, el.getAttribute(`data-de-${attr}`));
      }
    }
    const title = document.querySelector("title[data-de]");
    if (title) document.title = title.getAttribute("data-de");
  }

  global.VisionFlowI18n = {
    lang,
    isDe: lang === "de",
    L: (de, en) => (lang === "de" ? de : en),
    setLang,
    applyStatic,
  };
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", applyStatic);
  else applyStatic();
})(window);
