"use strict";
const Core = LanternCore;
const HOST = "app.lantern.tv";
const tabs = new Map();
const pending = new Map();
let active = null;
let nativeBusy = false;

function state(tabId) {
  if (tabId < 0) return null;
  if (!tabs.has(tabId)) {
    if (tabs.size >= 32) tabs.delete(tabs.keys().next().value);
    tabs.set(tabId, {candidates: new Map(), origins: new Map(), players: new Map(), formats: new Map(), updated: Date.now()});
  }
  const item = tabs.get(tabId);
  item.updated = Date.now();
  return item;
}
function rememberOrigin(item, url, headers, frameId) {
  const parsed = Core.safeUrl(url);
  if (!parsed) return;
  const key = `${frameId}:${parsed.origin}`;
  if (item.origins.size >= 32 && !item.origins.has(key)) item.origins.delete(item.origins.keys().next().value);
  item.origins.set(key, {origin: parsed.origin, frameId, headers});
}
function rememberCandidate(item, tabId, candidate) {
  const {url, frameId} = candidate;
  const key = `${frameId}:${url}`;
  const previous = item.candidates.get(key);
  // DOM/cache observations cannot erase finalized request credentials.
  const headers = candidate.headers ?? previous?.headers ?? {};
  for (const [oldKey, old] of item.candidates) {
    if (oldKey !== key && old.frameId === frameId && Core.identity(old.url) === Core.identity(url)) {
      if (!candidate.headers && old.hasRequestHeaders) return;
      item.candidates.delete(oldKey);
    }
  }
  if (item.candidates.size >= 32 && !previous) {
    const expendable = [...item.candidates.keys()].find(oldKey => {
      const old = item.candidates.get(oldKey);
      return !active || active.tabId !== tabId || old.frameId !== active.frameId || Core.identity(old.url) !== active.identity;
    });
    if (!expendable) return;
    item.candidates.delete(expendable);
  }
  const priority = {player: 80, iframe: 50, attribute: 20, resource: 5, network: 0};
  const kind = (priority[previous?.kind] || 0) > (priority[candidate.kind] || 0) ? previous.kind : candidate.kind;
  item.candidates.set(key, {...candidate, key, kind, headers, hasRequestHeaders: !!candidate.headers || !!previous?.hasRequestHeaders, seen: Date.now()});
}
function clearTab(tabId) {
  tabs.delete(tabId);
  if (active?.tabId === tabId) active = null;
  for (const [key, request] of pending) if (request.tabId === tabId) pending.delete(key);
}
function clearFrame(tabId, frameId) {
  for (const [key, request] of pending) if (request.tabId === tabId && request.frameId === frameId) pending.delete(key);
  const item = tabs.get(tabId);
  if (!item) return;
  item.players.delete(frameId);
  item.formats.delete(frameId);
  for (const [key, value] of item.candidates) if (value.frameId === frameId) item.candidates.delete(key);
  for (const [key, value] of item.origins) if (value.frameId === frameId) item.origins.delete(key);
  if (active?.tabId === tabId && active.frameId === frameId) active = null;
}
function snapshot(item, frameId) {
  const result = {};
  let size = 0;
  for (const record of [...item.origins.values()].reverse()) {
    if (record.frameId !== frameId) continue;
    size += JSON.stringify(record.headers).length + record.origin.length;
    if (size > 96 * 1024) break;
    result[record.origin] = record.headers;
  }
  return result;
}
async function native(message) {
  try {
    if (typeof LANTERN_MANAGED !== "undefined") return await managedRequest(message);
    return await browser.runtime.sendNativeMessage(HOST, message);
  } catch (_) {
    return {ok: false, error: "Install the Lantern Firefox bridge, then open Lantern and connect your TV."};
  }
}
function selectedSource(item, candidate, tab, sessionId) {
  const players = item.players.get(candidate.frameId) || [];
  const player = players.reduce((best, next) => !best || Core.score(candidate, [next]) > Core.score(candidate, [best]) ? next : best, null);
  return {url: candidate.url, title: String(tab.title || "Firefox video").slice(0, 240), pageUrl: tab.url,
    mimeType: candidate.mimeType, sessionId, rootHeaders: candidate.headers,
    headersByOrigin: snapshot(item, candidate.frameId), subtitles: player?.subtitles || []};
}
function sourceFingerprint(source) {
  return JSON.stringify({url: source.url, rootHeaders: source.rootHeaders, headers: source.headersByOrigin, subtitles: source.subtitles});
}
async function restoreMute(selection) {
  if (!selection.mutedByUs || selection.wasMuted) return;
  try {
    const tab = await browser.tabs.get(selection.tabId);
    if (tab.mutedInfo?.muted && tab.mutedInfo.reason === "extension" && tab.mutedInfo.extensionId === browser.runtime.id) {
      await browser.tabs.update(selection.tabId, {muted: false});
    }
  } catch (_) { /* The original tab may have closed. */ }
}
async function refreshActive() {
  if (!active || active.failures >= 3 || nativeBusy || Date.now() < active.nextRefreshAt) return;
  const item = tabs.get(active.tabId);
  if (!item) return;
  const candidate = [...item.candidates.values()].filter(c => c.frameId === active.frameId && Core.identity(c.url) === active.identity).sort((a, b) => b.seen - a.seen)[0];
  if (!candidate) return;
  const selection = active;
  nativeBusy = true;
  try {
    const tab = await browser.tabs.get(selection.tabId);
    if (active !== selection) return;
    const source = selectedSource(item, candidate, tab, active.sessionId);
    const fingerprint = sourceFingerprint(source);
    if (fingerprint === selection.lastFingerprint) return;
    selection.nextRefreshAt = Date.now() + 10000;
    const result = await native({action: "refresh", source});
    if (active === selection && result.ok === false) {
      selection.error = result.error || "Stream refresh failed. Reopen the Lantern popup.";
      selection.failures += 1;
      selection.nextRefreshAt = Date.now() + [10000, 30000, 60000][selection.failures - 1];
    } else if (active === selection) {
      selection.failures = 0;
      selection.lastFingerprint = fingerprint;
      delete selection.error;
    }
  } catch (_) { /* A closed tab is handled by the lifecycle listener. */ }
  finally { nativeBusy = false; }
}

// A real document request resets discovery before its new player requests arrive.
// Hash changes and history.pushState do not make this request and retain discovery.
browser.webRequest.onBeforeRequest.addListener(details => {
  if (details.tabId < 0) return;
  if (details.type === "main_frame") clearTab(details.tabId);
  else if (details.type === "sub_frame") clearFrame(details.tabId, details.frameId);
}, {urls: ["https://*/*"], types: ["main_frame", "sub_frame"]});

browser.webRequest.onSendHeaders.addListener(details => {
  if (details.tabId < 0 || !Core.safeUrl(details.url)) return;
  if (pending.size >= 256) pending.delete(pending.keys().next().value);
  pending.set(details.requestId, {tabId: details.tabId, url: details.url, frameId: details.frameId,
    headers: Core.requestHeaders(details.requestHeaders), seen: Date.now()});
}, {urls: ["https://*/*"]}, ["requestHeaders"]);

browser.webRequest.onHeadersReceived.addListener(details => {
  const request = pending.get(details.requestId);
  pending.delete(details.requestId);
  if (details.tabId < 0 || !Core.safeUrl(details.url) || details.statusCode >= 400) return;
  const contentType = (details.responseHeaders || []).find(header => header.name.toLowerCase() === "content-type")?.value || "";
  let mimeType = Core.mediaType(details.url, contentType);
  const existing = tabs.get(details.tabId);
  const hlsFrame = existing && [...existing.candidates.values()].some(c => c.frameId === details.frameId && c.mimeType.includes("mpegurl"));
  const directVideo = existing && [...existing.players.values()].flat().some(p => p.frameId === details.frameId && p.src === details.url);
  const mp4Fragment = mimeType === "video/mp4" && hlsFrame && details.type !== "media" && !directVideo;
  if (mp4Fragment) mimeType = null;
  const unsupported = Core.unsupportedType(details.url, contentType);
  const subtitle = existing?.players.get(details.frameId)?.some(player => player.subtitles.some(track => track.url === details.url));
  if (!mimeType && !mp4Fragment && !Core.isSegment(details.url, contentType) && !unsupported && !subtitle) return;
  const item = state(details.tabId);
  if (!item) return;
  if (request) rememberOrigin(item, details.url, request.headers, details.frameId);
  if (unsupported) {
    const formats = new Set(item.formats.get(details.frameId) || []);
    formats.add(unsupported);
    if (item.formats.size < 32 || item.formats.has(details.frameId)) item.formats.set(details.frameId, [...formats]);
  }
  if (mimeType) {
    rememberCandidate(item, details.tabId, {url: details.url, mimeType, headers: request?.headers, frameId: details.frameId, kind: "network"});
  }
  void refreshActive();
}, {urls: ["https://*/*"]}, ["responseHeaders"]);

for (const event of [browser.webRequest.onCompleted, browser.webRequest.onErrorOccurred]) event.addListener(details => pending.delete(details.requestId), {urls: ["https://*/*"]});
browser.tabs.onRemoved.addListener(clearTab);
browser.tabs.onUpdated.addListener((tabId, changeInfo) => {
  if (changeInfo.url && !Core.safeUrl(changeInfo.url)) clearTab(tabId);
});
setInterval(() => {
  const now = Date.now();
  for (const [key, item] of tabs) if (now - item.updated > 30 * 60 * 1000) tabs.delete(key);
  for (const [key, request] of pending) if (now - request.seen > 30000) pending.delete(key);
}, 30000);

browser.runtime.onMessage.addListener(async (message, sender) => {
  if (message?.type === "players" && sender.tab) {
    const item = state(sender.tab.id);
    if (!item || !Number.isInteger(sender.frameId) || sender.frameId < 0) return {};
    const dimension = value => typeof value === "number" && Number.isFinite(value) ? Math.min(8192, Math.max(0, value)) : 0;
    const players = Array.isArray(message.players) ? message.players.slice(0, 16).filter(player => player && typeof player === "object").map(player => ({
      src: typeof player.src === "string" ? player.src.slice(0, 8192) : "", playing: player.playing === true,
      width: dimension(player.width), height: dimension(player.height), frameId: sender.frameId,
      subtitles: Core.subtitleTracks(player.subtitles)})) : [];
    if (item.players.size >= 32 && !item.players.has(sender.frameId)) item.players.delete(item.players.keys().next().value);
    item.players.set(sender.frameId, players);
    const formats = Array.isArray(message.formats) ? message.formats.filter(value => ["flv", "dash", "http", "websocket", "webrtc"].includes(value)) : [];
    if (item.formats.size >= 32 && !item.formats.has(sender.frameId)) item.formats.delete(item.formats.keys().next().value);
    item.formats.set(sender.frameId, [...new Set(formats)]);
    const reported = Array.isArray(message.candidates) ? message.candidates.slice(0, 48) : [];
    const hlsFrame = reported.some(candidate => candidate && Core.mediaType(candidate.url)?.includes("mpegurl")) || [...item.candidates.values()].some(candidate => candidate.frameId === sender.frameId && candidate.mimeType.includes("mpegurl"));
    // Only explicit HTTPS media URLs survive validation. A page cannot supply
    // request headers, native commands, or another frame's captured credentials.
    for (const candidate of reported) {
      if (!candidate || !["player", "iframe", "attribute", "resource"].includes(candidate.kind)) continue;
      const mimeType = Core.mediaType(candidate.url);
      if (!mimeType || (mimeType === "video/mp4" && hlsFrame && candidate.kind === "resource")) continue;
      rememberCandidate(item, sender.tab.id, {url: candidate.url, mimeType, kind: candidate.kind, frameId: sender.frameId});
    }
    // The frame may advertise a compatible stream in its own player URL.
    // Attribute it to the frame playing the movie, with its request context.
    for (const candidate of Core.discoverMedia(sender.url).candidates) {
      rememberCandidate(item, sender.tab.id, {...candidate, kind: "iframe", frameId: sender.frameId});
    }
    void refreshActive();
    return {};
  }
  // Content scripts are allowed to report player metadata only. They cannot
  // initiate a cast or invoke the native host, even if a page spoofs a message.
  if (sender.tab || sender.url !== browser.runtime.getURL("popup.html")) return undefined;
  const [tab] = await browser.tabs.query({active: true, currentWindow: true});
  if (!tab) return {ok: false, error: "Select the Firefox tab containing your video."};
  if (message.type === "list") {
    await browser.tabs.sendMessage(tab.id, {type: "inspect"}).catch(() => {});
    const item = tabs.get(tab.id);
    const players = item ? [...item.players.values()].flat() : [];
    const candidates = item ? [...item.candidates.values()].sort((a, b) => Core.score(b, players) - Core.score(a, players) || b.seen - a.seen) : [];
    // The popup needs display information, never cookies or authorization.
    return {ok: true, title: tab.title, candidates: candidates.map(c => ({key: c.key, url: c.url, mimeType: c.mimeType})),
      diagnostics: {version: browser.runtime.getManifest().version, players: players.length, playingPlayers: players.filter(player => player.playing).length,
        candidates: candidates.length, observedFormats: item ? [...new Set([...item.formats.values()].flat())].sort() : []},
      active: active?.tabId === tab.id, error: active?.tabId === tab.id ? active.error : null, status: await native({action: "status"})};
  }
  if (message.type === "play") {
    const item = tabs.get(tab.id);
    const candidate = item?.candidates.get(message.candidateKey);
    if (!candidate) return {ok: false, error: "This video is no longer available. Start playback and refresh the list."};
    const previous = active;
    const inheritedMute = previous?.tabId === tab.id && previous.mutedByUs && !previous.wasMuted && tab.mutedInfo?.reason === "extension" && tab.mutedInfo.extensionId === browser.runtime.id;
    active = {tabId: tab.id, frameId: candidate.frameId, identity: Core.identity(candidate.url), sessionId: crypto.randomUUID(), nextRefreshAt: Date.now() + 10000,
      failures: 0, wasMuted: inheritedMute ? false : !!tab.mutedInfo?.muted, mutedByUs: !!inheritedMute};
    const selection = active;
    const source = selectedSource(item, candidate, tab, active.sessionId);
    const result = await native({action: "play", source});
    if (result.ok === false) {
      if (active === selection) { active = null; await restoreMute(selection); }
      if (previous && previous.tabId !== selection.tabId) await restoreMute(previous);
      return result;
    }
    selection.lastFingerprint = sourceFingerprint(source);
    if (previous && previous.tabId !== selection.tabId) await restoreMute(previous);
    if (message.muteLocal && !tab.mutedInfo?.muted) {
      try { await browser.tabs.update(tab.id, {muted: true}); selection.mutedByUs = true; } catch (_) { }
    }
    if (!message.muteLocal && selection.mutedByUs) { await restoreMute(selection); selection.mutedByUs = false; }
    if (message.pauseLocal) await browser.tabs.sendMessage(tab.id, {type: "pauseLocal"}).catch(() => {});
    return result;
  }
  if (message.type === "stop") {
    if (!active || active.tabId !== tab.id) return {ok: false, error: "This tab does not own the current TV stream."};
    const selection = active;
    const result = await native({action: "stop", source: {sessionId: selection.sessionId}});
    if (result.ok !== false && active === selection) { active = null; await restoreMute(selection); }
    return result;
  }
  return {ok: false, error: "Unknown extension command."};
});
