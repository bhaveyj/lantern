# Lantern receiver protocol v1

The TV advertises `_lantern._tcp.local.` on TCP port 8765. TXT fields are `fn` (friendly name) and `version=1`. Windows discovers that service or connects to a user-entered private IPv4 address. Cast devices use their separate `_googlecast._tcp.local.` service and Cast protocol.

Windows opens a WebSocket at `ws://TV_IP:8765/control`. Its first text message, within 15 seconds, is `{"type":"pair","version":1,"pin":"123456","name":"Windows PC"}`. The TV accepts its current six-digit screen PIN, expires the code after five minutes, and permits five failed attempts per peer per minute. A successful pairing replaces the previous sender connection. The response is `{"type":"pair.ok","version":1,"audioMimes":["audio/mp4a-latm"]}`. Other outcomes return an `error` message and close the socket.

Messages after pairing:

Windows sends a WebSocket PING at a fixed 10-second interval, independent of incoming state updates. The TV answers with PONG. This keeps the receiver's 30-second inbound socket timeout from expiring while the user watches or pauses without issuing commands. The sender cancels this task on disconnect; aiohttp's automatic heartbeat additionally detects an unresponsive peer when inbound traffic stops.

| Direction | Type | Fields and behavior |
| --- | --- | --- |
| Windows → TV | `playlist.snapshot` | `items`: ordered list of `id`, `title`, `url`, `selectedSubtitle`, and `subtitles` (`id`, `label`, `url`). Windows owns the playlist. |
| Windows → TV | `playback.load` | `item`: one resolved playlist item, with its direct or prepared URL. Starts playback. |
| Windows → TV | `playback.command` | `action`: `play`, `pause`, `stop`, `seek`, `volume`, `previous`, `next`, `subtitle`, or `fit`. `value` holds seconds, normalized volume, subtitle ID/null, or `fit`/`fill` as applicable. |
| TV → Windows | `request.play` | `id`: requests preparation and playback of a playlist item selected on TV. Used for remote selection, next/previous, and autoplay. |
| TV → Windows | `playback.state` | `id`, `playing`, `position` and `duration` in seconds, and `subtitleId` or null. Sent on changes and every second. |
| TV → Windows | `error` | Human-readable `message`, optional media `id` and decoder `code`. |

Windows hosts an HTTP server on an available TCP port. `/stream/{session-token}/{opaque-media-id}` supports GET, HEAD, and byte ranges. `/subtitle/{session-token}/{opaque-subtitle-id}` returns WebVTT, converting external SRT as needed. Only explicitly added files are addressable. No request path is interpreted as a filesystem path. Tokens are random 256-bit bearer values, revoked on stop/disconnect. URLs must never be logged or published. CORS is enabled for the Cast receiver; there are no browser cookies or ambient credentials.

The personal receiver uses unencrypted local HTTP/WebSocket transport and a screen PIN. Use a trusted private LAN. A public release should review authenticated encrypted pairing before extending this beyond that environment. The version field is mandatory; incompatible protocol versions are rejected.
