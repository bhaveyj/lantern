"use strict";
const videos = document.getElementById("videos");
const play = document.getElementById("play");
const message = document.getElementById("message");
const connection = document.getElementById("connection");
let connected = false;
function show(text, error = false) { message.textContent = text || ""; message.classList.toggle("error", error); }
async function send(command) {
  try { return await browser.runtime.sendMessage(command); }
  catch (_) { return {ok: false, error: "Lantern could not respond. Reload the extension and try again."}; }
}
async function refresh() {
  play.disabled = true;
  show("Looking for video…");
  const result = await send({type: "list"});
  if (!result?.ok) { show(result?.error || "Could not inspect this tab.", true); return; }
  connected = result.status?.ok !== false && !!result.status?.connected;
  connection.textContent = connected ? `Connected to ${result.status.device || result.status.name || "your TV"}` : result.status?.error || "Open Lantern and connect your TV.";
  connection.classList.toggle("error", !connected);
  videos.replaceChildren();
  for (const [index, candidate] of result.candidates.entries()) {
    const url = new URL(candidate.url);
    const option = document.createElement("option");
    option.value = candidate.key;
    option.textContent = `${candidate.mimeType.includes("mpegurl") ? "HLS" : "MP4"} · ${url.hostname} · ${url.pathname.split("/").pop() || "stream"}`.slice(0, 110);
    if (index === 0) option.textContent = "Recommended · " + option.textContent;
    videos.append(option);
  }
  if (!result.candidates.length) videos.add(new Option("Start the video, then refresh", ""));
  videos.disabled = !result.candidates.length;
  play.disabled = !connected || !result.candidates.length;
  const info = result.diagnostics || {};
  const formats = (info.observedFormats || []).map(format => ({flv: "FLV", dash: "DASH", http: "unencrypted HTTP", websocket: "WebSocket", webrtc: "WebRTC"}[format])).filter(Boolean);
  document.getElementById("diagnostics").textContent = `Extension ${info.version || browser.runtime.getManifest().version} · ${info.players || 0} player(s) · ${result.candidates.length} source(s)${formats.length ? " · Observed: " + formats.join(", ") : ""}`;
  let noSource = "No HTTPS HLS or MP4 source found. Reload this page once, start playback, then refresh.";
  if (info.players) noSource = "Video player detected. Keep it playing, then refresh. If no source appears, reload this page once and start playback again.";
  if (formats.length) noSource = `Observed ${formats.join(", ")}, but no HTTPS HLS or MP4 source. Choose an HLS stream in the website's player if available, then refresh.`;
  show(result.error || (result.candidates.length ? "Ready when you are." : noSource), !!result.error);
}
play.addEventListener("click", async () => {
  play.disabled = true;
  show("Starting playback on your TV…");
  const result = await send({type: "play", candidateKey: videos.value, pauseLocal: document.getElementById("pause").checked, muteLocal: document.getElementById("mute").checked});
  show(result?.ok === false ? result.error : "Playing on your TV. Keep Lantern running.", result?.ok === false);
  play.disabled = !connected || !videos.value;
});
document.getElementById("refresh").addEventListener("click", refresh);
document.getElementById("stop").addEventListener("click", async () => {
  const result = await send({type: "stop"});
  show(result?.ok === false ? result.error : "TV playback stopped.", result?.ok === false);
});
void refresh();
