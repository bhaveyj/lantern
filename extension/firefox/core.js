/* Pure candidate and header rules, shared by the background page and tests. */
(function (root) {
  "use strict";
  const HLS = new Set(["application/vnd.apple.mpegurl", "application/x-mpegurl", "audio/mpegurl", "audio/x-mpegurl"]);
  const ALLOWED_HEADERS = new Set(["cookie", "authorization", "referer", "origin", "user-agent", "accept"]);
  const AUTH_QUERY = new Set(["token", "expires", "exp", "signature", "sig", "auth", "hdnts", "hdnea", "policy", "key-pair-id",
    "x-amz-signature", "x-amz-date", "x-amz-expires", "x-amz-security-token", "x-amz-credential", "x-amz-algorithm", "x-amz-signedheaders"]);
  const languages = new Intl.DisplayNames(undefined, {type: "language", fallback: "none"});
  function safeUrl(value) {
    if (typeof value !== "string" || value.length > 8192) return null;
    try {
      const url = new URL(value);
      return url.protocol === "https:" && !url.username && !url.password && value.length <= 8192 ? url : null;
    } catch (_) { return null; }
  }
  function mediaType(value, contentType = "") {
    const url = safeUrl(value);
    if (!url) return null;
    const mime = contentType.split(";", 1)[0].trim().toLowerCase();
    if (HLS.has(mime) || /\.m3u8$/i.test(url.pathname)) return "application/vnd.apple.mpegurl";
    if (isFragment(value)) return null;
    if (mime === "video/mp4" || /\.(mp4|m4v)$/i.test(url.pathname)) return "video/mp4";
    return null;
  }
  function isSegment(value, contentType = "") {
    const url = safeUrl(value);
    return !!url && (/\.(ts|m4s|cmfv|cmfa|aac|vtt|key)$/i.test(url.pathname) || /(?:^|[/_.-])init(?:[_.-]|$)/i.test(url.pathname) || /^(video\/mp2t|audio\/aac|text\/vtt)(;|$)/i.test(contentType));
  }
  function isFragment(value) {
    const url = safeUrl(value);
    return !!url && (/\.(m4s|cmfv|cmfa|ts|aac|vtt|key)$/i.test(url.pathname) || /(?:^|[/_.-])(?:init|seg(?:ment)?|chunk|frag(?:ment)?)(?:[_.-]|\d|$)/i.test(url.pathname));
  }
  function unsupportedType(value, contentType = "") {
    try {
      const url = new URL(value);
      if (["ws:", "wss:"].includes(url.protocol)) return "websocket";
      if (url.protocol === "http:") return "http";
      const mime = String(contentType).split(";", 1)[0].trim().toLowerCase();
      if (/\.flv$/i.test(url.pathname) || ["video/x-flv", "video/flv"].includes(mime)) return "flv";
      if (/\.mpd$/i.test(url.pathname) || mime === "application/dash+xml") return "dash";
    } catch (_) { }
    return null;
  }
  function discoverMedia(value, type = "") {
    const candidates = [], formats = new Set(), seen = new Set();
    function inspect(address, hint, depth) {
      if (typeof address !== "string" || address.length > 8192 || seen.has(address)) return;
      seen.add(address);
      const unsupported = unsupportedType(address, hint);
      if (unsupported) formats.add(unsupported);
      const url = safeUrl(address);
      if (!url) return;
      const mimeType = mediaType(address, hint);
      if (mimeType && candidates.length < 16) candidates.push({url: address, mimeType});
      if (depth >= 2) return;
      for (const [name, nested] of url.searchParams) {
        if (["hls", "file", "url", "src"].includes(name.toLowerCase())) inspect(nested, "", depth + 1);
        else if (name.toLowerCase() === "flv" && nested) formats.add("flv");
      }
    }
    inspect(value, type, 0);
    return {candidates, formats: [...formats]};
  }
  function requestHeaders(headers = []) {
    const result = {};
    let size = 0;
    for (const header of headers) {
      const name = String(header.name).toLowerCase();
      const value = header.value;
      if (!ALLOWED_HEADERS.has(name) || typeof value !== "string" || /[\r\n\0]/.test(value)) continue;
      size += name.length + value.length;
      if (value.length > 16384 || size > 32768) continue;
      result[name] = value;
    }
    return result;
  }
  function subtitleTracks(tracks) {
    if (!Array.isArray(tracks)) return [];
    const result = new Map();
    let size = 0;
    for (const track of tracks.slice(0, 64)) {
      const url = safeUrl(track?.url);
      if (!url || url.hash || result.has(url.href)) continue;
      let label = typeof track.label === "string" ? track.label.slice(0, 120).trim() : "";
      const language = typeof track.language === "string" ? track.language : "";
      if (language.length <= 32 && /^[a-z]{2,3}(?:-[a-z0-9]{2,8})*$/i.test(language)) {
        try {
          const name = languages.of(language) || language;
          if (!label || /\.(srt|vtt)$/i.test(label)) label = label ? `${name} · ${label.replace(/\.(srt|vtt)$/i, "")}` : name;
        } catch (_) { /* Keep the original label for an invalid language tag. */ }
      }
      label = label.slice(0, 120);
      size += url.href.length + 4 * label.length + 64;
      if (size > 48 * 1024) break;
      result.set(url.href, {url: url.href, label});
      if (result.size === 32) break;
    }
    return [...result.values()];
  }
  function identity(value) {
    const url = safeUrl(value);
    if (!url) return "";
    const contentQuery = new URLSearchParams();
    for (const [name, value] of url.searchParams) if (!AUTH_QUERY.has(name.toLowerCase())) contentQuery.append(name, value);
    const query = contentQuery.toString();
    return url.origin + url.pathname + (query ? "?" + query : "");
  }
  function score(candidate, players) {
    let value = 0;
    for (const player of players) {
      if (player.frameId !== candidate.frameId) continue;
      const active = player.playing ? 100000 : 0;
      const area = Math.min(10000, Math.max(0, player.width * player.height) / 100);
      value = Math.max(value, active + area + (player.src === candidate.url ? 1000000 : 0));
    }
    const priority = {player: 80, iframe: 50, attribute: 20, resource: 5, network: 0}[candidate.kind] || 0;
    return value + priority + (candidate.mimeType.includes("mpegurl") ? 100 : 0);
  }
  const api = {safeUrl, mediaType, isSegment, isFragment, unsupportedType, discoverMedia, requestHeaders, subtitleTracks, identity, score};
  root.LanternCore = api;
  if (typeof module !== "undefined") module.exports = api;
})(globalThis);
