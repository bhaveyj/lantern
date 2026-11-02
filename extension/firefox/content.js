"use strict";
(() => {
  const Core = LanternCore;
  let lastReport = 0, scheduled = false;
  function report(force = false) {
    if (!force && Date.now() - lastReport < 1500) return;
    lastReport = Date.now();
    const players = [], candidates = new Map(), formats = new Set();
    function collect(raw, type, kind, contextUrl) {
      if (typeof raw !== "string" || !raw || raw.length > 8192) return;
      let address;
      try { address = new URL(raw, document.baseURI).href; } catch (_) { return; }
      const discovered = Core.discoverMedia(address, type || "");
      for (const format of discovered.formats) formats.add(format);
      for (const candidate of discovered.candidates) {
        if (candidates.size >= 48 && !candidates.has(candidate.url)) break;
        if (!candidates.has(candidate.url)) candidates.set(candidate.url, {...candidate, kind, contextUrl});
      }
    }
    for (const video of Array.from(document.querySelectorAll("video")).slice(0, 16)) {
      const rect = video.getBoundingClientRect();
      const src = String(video.currentSrc || video.src || "").slice(0, 8192);
      players.push({src, playing: !video.paused && !video.ended, width: Math.min(8192, Math.max(0, rect.width)), height: Math.min(8192, Math.max(0, rect.height))});
      collect(src, video.getAttribute("type"), "player");
      for (const source of Array.from(video.querySelectorAll("source[src]")).slice(0, 8)) collect(source.src, source.type, "player");
      if (video.srcObject) formats.add("webrtc");
    }
    let count = 0;
    for (const frame of document.querySelectorAll("iframe[src]")) {
      if (++count > 32) break;
      collect(frame.src, "", "iframe", frame.src);
    }
    count = 0;
    for (const element of document.querySelectorAll("[data-stream-url],[data-embed]")) {
      if (++count > 48) break;
      collect(element.getAttribute("data-stream-url"), "", "attribute");
      const embed = element.getAttribute("data-embed");
      collect(embed, "", "iframe", embed);
      if (element.getAttribute("data-stream-url-flv")) formats.add("flv");
    }
    for (const entry of performance.getEntriesByType("resource").slice(-192)) collect(entry.name, "", "resource");
    return browser.runtime.sendMessage({type: "players", players, candidates: [...candidates.values()], formats: [...formats]}).catch(() => {});
  }
  function schedule() {
    if (scheduled) return;
    scheduled = true;
    setTimeout(() => { scheduled = false; report(true); }, 250);
  }
  for (const name of ["play", "pause", "loadedmetadata", "timeupdate", "emptied"]) document.addEventListener(name, () => report(), true);
  document.addEventListener("DOMContentLoaded", schedule, {once: true});
  new MutationObserver(schedule).observe(document, {childList: true, subtree: true, attributes: true, attributeFilter: ["src", "type", "data-stream-url", "data-embed"]});
  try { new PerformanceObserver(schedule).observe({type: "resource", buffered: true}); } catch (_) { }
  browser.runtime.onMessage.addListener(message => {
    if (message.type === "inspect") return report(true);
    if (message.type === "pauseLocal") {
      for (const video of document.querySelectorAll("video")) video.pause();
      report(true);
    }
  });
  report(true);
})();
