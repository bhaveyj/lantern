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
  const deadline = Date.now() + 40000;
  let previous;
  while (Date.now() < deadline) {
    await waitForPlayer();
    const item = tabs.get(tab.id);
    if (!item) continue;
    const players = [...item.players.values()].flat().filter(player => player.playing && player.width && player.height);
    const candidate = [...item.candidates.values()]
      .filter(source => players.some(player => player.frameId === source.frameId))
      .sort((a, b) => Core.score(b, players) - Core.score(a, players) || b.seen - a.seen)[0];
    if (!candidate) continue;
    const identity = Core.identity(candidate.url);
    if (identity !== previous) {
      previous = identity;
      continue;
    }
    const current = await browser.tabs.get(tab.id);
    active = {tabId: tab.id, frameId: candidate.frameId, identity, sessionId: LANTERN_MANAGED.sessionId,
      nextRefreshAt: Date.now() + 10000, failures: 0};
    const source = selectedSource(item, candidate, current);
    active.lastFingerprint = sourceFingerprint(source);
    await managedRequest({action: "play", source});
    return;
  }
  await managedRequest({action: "no-video"});
}

void openManagedVideo().catch(async () => {
  await managedRequest({action: "no-video"}).catch(() => {});
});
