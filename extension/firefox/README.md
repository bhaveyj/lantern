# Lantern Firefox companion

This development extension hands HLS and MP4 media requests from Firefox to the
running Lantern sender. The sender must use the normal data directory
(`%LOCALAPPDATA%\LanternTV`) for the installed helper to find it.

Version 0.1.1 discovers explicit HTTPS sources from video elements, embedded
player URL parameters, stream buttons, and recent resource timings as well as
network response headers. This covers players that expose a `blob:` video URL
while fetching HLS in an iframe. It observes frame creation and source changes
from document start, including matching `about:blank` frames. After updating,
reload the temporary add-on in Firefox, then reload the video page once. The
popup shows the installed version and source/player counts for verification.

1. From the repository root, run `./scripts/install-firefox-bridge.ps1`. This builds
   a separate console native helper if necessary and registers it for the current
   Windows account. It does not need administrator rights.
2. Open `about:debugging#/runtime/this-firefox`, choose **Load Temporary Add-on**,
   and select this folder's `manifest.json`.
3. Open Lantern and connect the TV. Reload the video page once, start the video,
   then open Lantern's extension button. Choose the main stream and **Play on TV**.

Temporary add-ons disappear when Firefox restarts. The unsigned XPI produced by
`./scripts/build-firefox-extension.ps1` is a development artifact; normal Firefox
requires Mozilla signing for permanent installation. No signing credentials or
publication are part of this build.

HTTPS host permissions are necessary to observe video requests inside embedded
players and on separate CDN origins. Only allowed media request headers are
retained, with bounded in-memory state. Cookies, authorization and detected signed
URLs are never saved by the extension. Source credentials stay on Windows, not the
TV. The native helper authenticates to a loopback-only server with a per-launch,
DPAPI-protected secret. It does not expose a website-callable casting endpoint.

Local playback keeps running by default, with the Firefox tab muted, so the
website can continue issuing session updates. This uses extra source bandwidth.
The pause checkbox can stop duplicate fetching after handoff, but sources with
short-lived browser credentials may then need local playback restarted to
discover a fresh request. Automatic refresh only
tracks the selected source's origin and path, never a newly detected unrelated
video. Full page or player-frame navigation clears that frame's stored state
and stops refresh; hash and History API changes retain it. Captured request
headers are scoped to the source frame and origin; DOM metadata cannot supply
credentials or initiate native commands. Already-started playback can continue
until stopped in Lantern or on the TV.

HLS manifests, segments and MP4 requests must be replayable outside Firefox. DRM,
browser-bound challenges, FLV, DASH, WebSocket video, WebRTC and screen capture are not handled by this
version. Video-inserted advertisements can remain. Session-dependent streams may
require the page to stay playing; uncheck the pause option in that case.
The updated Windows sender also recognizes bounded PNG, WebP and gzip envelopes
around clear HLS segments and unpacks them before sending media to the TV.

For a development sender with a separate data directory, invoke the helper
manually with `LanternFirefoxBridge.exe --data-dir PATH` while testing framed
Native Messaging input. The installed Firefox manifest uses the default directory.

Remove the registration with `./scripts/install-firefox-bridge.ps1 -Uninstall`.
The helper executable can be removed after Firefox and Lantern are closed.
