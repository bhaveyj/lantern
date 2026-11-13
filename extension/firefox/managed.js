"use strict";

async function managedRequest(message) {
  const response = await fetch(LANTERN_MANAGED.endpoint, {
    method: "POST",
    headers: {"Content-Type": "application/json", "X-Lantern-Link": LANTERN_MANAGED.token},
    body: JSON.stringify(message),
    cache: "no-store",
    signal: AbortSignal.timeout(45000)
  });
  if (!response.ok) throw new Error("Lantern video connection closed");
  return response.json();
}

const waitForPlayer = () => new Promise(resolve => setTimeout(resolve, 500));
const MAX_SOURCE_ATTEMPTS = 4;

async function openManagedVideo() {
  let url;
  const setupDeadline = Date.now() + 25000;
  while (!url && Date.now() < setupDeadline) {
    ({url} = await managedRequest({action: "open"}));
    if (!url) await waitForPlayer();
  }
  if (!url) return;

  const [tab] = await browser.tabs.query({active: true, currentWindow: true});
  if (!tab) {
    await managedRequest({action: "no-video"});
    return;
  }
  await browser.tabs.update(tab.id, {url, muted: true});
  const deadline = Date.now() + (LANTERN_MANAGED.interactive ? 180000 : 40000);
  const attempted = new Set();
  let previous;
  while (Date.now() < deadline && attempted.size < MAX_SOURCE_ATTEMPTS) {
    await waitForPlayer();
    const item = tabs.get(tab.id);
    const players = item ? [...item.players.values()].flat().filter(player => player.playing && player.width && player.height) : [];
    const playingFrames = new Set(players.map(player => player.frameId));
    const choice = item && [...item.candidates.values()]
      .filter(source => playingFrames.has(source.frameId))
      .map(source => {
        const identity = Core.identity(source.url);
        return {source, identity, key: `${source.frameId}:${identity}`};
      })
      .filter(entry => !attempted.has(entry.key))
      .sort((a, b) => Core.score(b.source, players) - Core.score(a.source, players) || b.source.seen - a.source.seen)[0];
    if (!choice || choice.key !== previous) {
      previous = choice?.key;
      continue;
    }
    const {source: candidate, identity, key} = choice;
    attempted.add(key);
    previous = undefined;
    const current = await browser.tabs.get(tab.id);
    const source = selectedSource(item, candidate, current, LANTERN_MANAGED.sessionId);
    const result = await managedRequest({action: "play", source});
    if (result.ok) {
      active = {tabId: tab.id, frameId: candidate.frameId, identity, sessionId: LANTERN_MANAGED.sessionId,
        nextRefreshAt: Date.now() + 10000, failures: 0, lastFingerprint: sourceFingerprint(source)};
      return;
    }
    if (!result.retryable) return;
  }
  await managedRequest({action: "no-video"});
}

void openManagedVideo().catch(async () => {
  await managedRequest({action: "no-video"}).catch(() => {});
});
