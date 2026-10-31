# Watch a Firefox video on Lantern TV

> **Preview documentation:** The Firefox companion and relay implementation are
> still in development and are not yet included in the published source. The
> commands below describe the development preview and require that implementation;
> they do not work with a documentation-only checkout.

This development feature sends an ordinary HLS live/on-demand stream or an MP4
video from a Firefox tab to Lantern. The Windows app relays media over the home
network; the TV decodes the video. Firefox continues to run the website with your
existing extensions. This is native media playback, not screen mirroring.

The source website must permit its media requests to work outside Firefox.
Successful browser playback alone does not establish that a source will work.
The supplied socolivexg.cc example has not been validated on a physical TV.

## Set up this development build

Run these commands from the repository root after the normal bootstrap:

```powershell
./scripts/build-firefox-extension.ps1 -NativeHelper
./scripts/install-firefox-bridge.ps1
```

The packaged Windows app already includes the native helper, unpacked extension,
and `scripts/install-firefox-bridge.ps1`. From its folder, run only the install
command, then load its `extension/firefox/manifest.json` in Firefox.

The build produces `dist/LanternFirefoxBridge.exe` and an unsigned
`dist/Lantern-Firefox-0.1.1.xpi`. The installation command registers the helper for
the current Windows account using Firefox's Native Messaging registry entry; it
does not install a browser extension or require administrator access. Keep the
helper executable at its registered location. Run the installation command again
if you move it, or supply `-HelperPath C:\path\LanternFirefoxBridge.exe`.

In Firefox, open `about:debugging#/runtime/this-firefox`, choose **Load Temporary
Add-on**, and select `extension/firefox/manifest.json`. Temporary add-ons are
removed when Firefox restarts. The unsigned XPI is a development package, not a
permanent Firefox Release installation; permanent distribution requires Mozilla
signing. No signing or publication is performed by these scripts.

Build the updated Android receiver with `./scripts/build-android.ps1` and install
the generated APK using the normal receiver setup. Start the Windows sender with
`./run.ps1` (or rebuild the Windows executable). An older running executable does
not include the new relay or browser connection.

## Watch a video

1. Open Lantern on the TV. Connect to it from Lantern on Windows as usual.
2. Open the video page in Firefox and start playback. If the page was already
   playing when you loaded the extension, reload the page once to discover its
   initial media requests.
3. Open the **Lantern TV** extension in the Firefox toolbar. Choose the main
   video from the list and click **Play on TV**. Several choices can appear when
   a page has advertisements or multiple players; Recommended is a heuristic.
4. Keep the laptop awake, Lantern running, and the Firefox tab open. Playback
   controls work in Lantern and with the TV remote. Live streams show **LIVE**
   and a **Go live** control. Seeking is available only when the player reports
   a seekable window.
5. Stop in Lantern or the extension to revoke the session. Playing a local file
   closes the previous web session. A stale tab cannot stop a newer web session
   or a local file.

Keeping the Firefox video playing silently is the most compatible option for
sites that renew tokens only while playing. If you choose to pause local
playback, some sites may stop renewing the session. Muting the tab instead avoids
duplicate audio while retaining its request activity.

The extension observes renewed requests for the selected source and updates
session headers in Lantern. A changed signed root URL reloads the TV source;
live playback returns to its default live position, while on-demand playback
keeps its last reported position. This may briefly interrupt playback. If a site
changes to a different stream path, chooses a new server, or requires another
browser interaction, select the new video and send it again.

Website popups and overlays are not sent to the TV. Advertisements inserted into
the video itself can remain. Ad blockers run in Firefox; their filters are not
automatically applied to the relay's media requests.

## Supported sources and current limits

- Public HTTPS HLS master/media playlists, adaptive variants, separate audio,
  ordinary subtitle renditions, initialization segments, byte ranges,
  discontinuities, and identity AES-128 keys available to the browser session.
- Public HTTPS progressive MP4, including byte-range seeking.
- Recognized PNG, WebP and gzip envelopes around clear HLS segments are unpacked
  on Windows with size, time and concurrency limits before reaching the TV.
  Keys, encrypted segments and initialization data retain their original bytes.
- Low-latency HLS sources with ordinary full segments play as regular HLS;
  low-latency controls are omitted. Delta-only or part-only streams are rejected.
- Media codecs, profiles, frame rates, and audio must be supported by the TV.
  Web streams are not transcoded.
- DASH, DRM/SAMPLE-AES, content steering, WebRTC, non-HTTPS sources, and browser
  capture are not implemented in this first build. A browser-bound challenge can
  prevent replay even when cookies and referring-page headers are available.
- Google Cast uses the existing separate compatibility connection and now receives
  the appropriate HLS MIME and LIVE/BUFFERED type. It still needs physical-device
  compatibility testing, including Cast receiver CORS and codec behavior.

## How the connection works

The Firefox extension observes HTTPS media requests and player metadata,
including embedded frames, to offer candidates. Host permissions are required
for both the page and its media servers. Media request URLs and a small allowlist
of request headers are bounded and kept temporarily in the extension's memory;
they are cleared on navigation, tab close, extension shutdown, or expiry. The
popup never receives cookies or authorization headers. Nothing is stored in
Firefox extension storage or uploaded to a Lantern service.

Only the extension popup can initiate playback. Firefox's registered native host
is restricted to the `lantern-tv@lantern.local` extension. It forwards bounded
control messages to an authenticated loopback-only endpoint in the running
sender. The per-launch connection secret is protected with Windows DPAPI and
removed on shutdown. The endpoint rejects browser origins and foreign Host
headers. Media bytes do not travel through Native Messaging.

The relay keeps browser session data in memory, scoped to each exact upstream
HTTPS origin. It verifies public IP addresses at connection time, including DNS
results and redirects, and does not use environment proxies or a shared cookie
jar. Playlists are rewritten so the receiver sees only opaque session media
routes. The custom TV receiver retains its pinned TLS sender identity and its
restriction to the paired sender's tokenized media routes. Stop/disconnect
revokes routes, cancels transfers, and clears credentials. File preparation's
FFmpeg restrictions remain unchanged.

## Validation and troubleshooting

Automated tests use controlled media responses and a simulated receiver. They
cover request scoping, private-address and redirect rejection, manifest rewriting,
ranges, resource bounds, cancellation, browser/native framing, popup access,
session ownership, local-file switching, and live playback controls. They cannot
establish compatibility with every provider or physical TV.

For a physical acceptance check, play an entire event, verify picture/audio sync
and remote controls, exercise stop/restart and a brief network interruption, and
watch through at least one source-token renewal. Record whether laptop playback
must continue for renewal. Use a stable source first to separate relay behavior
from provider outages. Do not publish signed URLs, cookies, or pairing details.

If the extension cannot connect, start the updated Windows app under the same
Windows account, connect the TV there, and rerun the bridge installer if its
executable moved. For a development sender using `--data-dir`, a separately
configured helper must target the same data directory. If no video is listed,
reload the tab after enabling the extension and start playback before refreshing
the popup. Unsupported transport or DRM requires a different playback path.

To remove the helper registration:

```powershell
./scripts/install-firefox-bridge.ps1 -Uninstall
```

Disable/remove the extension separately in Firefox. The uninstaller does not
delete your library or other Lantern data.
