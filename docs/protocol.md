# Lantern receiver protocol v3

The TV advertises `_lantern._tcp.local.` on TCP port 8765. TXT fields are `fn` (friendly name) and `version=3`. Discovery is a hint, not proof of identity. Windows discovers that service or connects to a private IPv4 address. Cast devices use their separate discovery and compatibility transport.

The TV serves TLS 1.2 or newer with a persistent AndroidKeyStore identity. Windows retrieves its certificate without sending credentials, then pins that exact certificate on the WebSocket. On first use, TLS is authenticated by the subsequent channel-bound SPAKE2+ exchange. No PIN or password-derived verifier is transmitted. Normal setup asks only for the six-digit TV code; fingerprints live in advanced connection details.

Windows sends `pair.start` with `version=3`, `mode=code` or `resume`, a random 16-byte `clientId`, a fresh 32-byte `clientNonce`, `name`, `mediaPort`, and `mediaFingerprint`. The TV returns `pair.challenge` with a fresh 32-byte `nonce`. All binary fields use exact-length lowercase hexadecimal.

For code pairing, both sides call the checksum-pinned BoringSSL RFC 9383 P-256/SHA-256 implementation. Prover/verifier identities are `lantern-windows-v3` and `lantern-tv-v3`; upstream `Register` derives registration values locally from the code. Windows sends `pair.prove` with its 65-byte share. The TV returns `pair.proof` with its share and 32-byte verifier confirmation. Windows verifies it and sends `pair.confirm` with the prover confirmation. The TV checks this before saving any credentials or granting control. Codes expire after five minutes; a code expiring mid-exchange is rejected.

The SPAKE2+ context is a concatenation of unsigned two-byte big-endian length-prefixed fields, in this order: ASCII `Lantern Pairing v3 SPAKE2+-P256-SHA256`, TV certificate SHA-256 (32 bytes), media certificate SHA-256 (32 bytes), media port (two-byte big-endian), client ID (16 bytes), client nonce (32 bytes), TV nonce (32 bytes), and UTF-8 computer name (1–128 bytes). Each side uses its own observed TV identity. A relay between different TLS identities cannot complete the exchange.

After mutual confirmation, the reconnect key is `HMAC-SHA256(shared_secret, ASCII("Lantern v3 remembered key") || SHA256(context))`. Windows encrypts it with DPAPI; the TV encrypts paired-client records with an AndroidKeyStore AES-GCM key. Each side stores at most 32 devices. For resume, Windows sends `pair.resume` with `HMAC-SHA256(reconnect_key, ASCII("Lantern v3 client resume") || SHA256(context))`; the TV rechecks that the client is still registered. Fresh nonces and the current media identity/port bind every reconnect.

In both modes, success returns `pair.ok` with `version=3`, `audioMimes`, and `proof = HMAC-SHA256(reconnect_key, ASCII("Lantern v3 server accepted") || SHA256(context))`. Windows verifies this before persisting trust. A successful pairing replaces the previous sender. Unknown/revoked clients receive `PAIRING_REQUIRED`, leading to the same six-digit-code prompt. A changed TV certificate also requires this code-only re-pairing, with no old credential sent to it. There is no v2/PIN-message fallback.

An authenticated `pair.forget` removes the active computer's credential and closes its session. Either app also provides local pairing management. The TV pins media to the authenticated TCP peer address, port, and certificate; redirects remain disabled.

Messages after pairing:

Playlist/load items may include `subtitleRevision` (a nonnegative integer).
Subtitle commands carry the new counter as `revision`; receiver state echoes it
as `subtitleRevision`. Each side advances the counter for a local selection and
ignores selections with an older counter. Legacy peers may omit these fields.
Load items also accept optional `position` (seconds, default 0) and `autoplay`
(default true). Updating subtitle tracks in a snapshot preserves the active
item's position and paused state.

Windows sends a WebSocket PING at a fixed 10-second interval, independent of incoming state updates. The TV answers with PONG. This keeps the receiver's 30-second inbound socket timeout from expiring while the user watches or pauses without issuing commands. The sender cancels this task on disconnect; aiohttp's automatic heartbeat additionally detects an unresponsive peer when inbound traffic stops.

The receiver caps concurrent clients at eight. Unpaired WebSockets close after
30 seconds even if they send only keepalive frames. The patched NanoWSD parser
rejects frames over 1 MiB before allocating their payloads, bounds aggregate
fragmented messages to 1 MiB/256 fragments, and bounds JSON depth to 32. Pairing
messages are limited to 8 KiB; paired clients may send at most 30 messages per
second. Failed-PIN tracking is time-limited and bounded to 256 peers. A receiver-wide
limit of 25 code-pairing attempts per minute also applies, so rotating source IP
addresses cannot bypass the five-attempt per-peer limit. A lockout clears after
the one-minute window; it does not change the displayed PIN.

| Direction | Type | Fields and behavior |
| --- | --- | --- |
| Windows → TV | `playlist.snapshot` | `items`: ordered list of `id`, `title`, `url`, `selectedSubtitle`, and `subtitles` (`id`, `label`, `url`). Windows owns the playlist. |
| Windows → TV | `playback.load` | `item`: one resolved playlist item, with its direct or prepared URL. Starts playback. |
| Windows → TV | `playback.command` | `action`: `play`, `pause`, `stop`, `seek`, `volume`, `previous`, `next`, `subtitle`, or `fit`. `value` holds seconds, normalized volume, subtitle ID/null, or `fit`/`fill` as applicable. |
| TV → Windows | `request.play` | `id`: requests preparation and playback of a playlist item selected on TV. Used for remote selection, next/previous, and autoplay. |
| TV → Windows | `playback.state` | `id`, `playing`, `position` and `duration` in seconds, and `subtitleId` or null. Sent on changes and every second. |
| TV → Windows | `error` | Human-readable `message`, optional media `id` and decoder `code`. |

Windows hosts an HTTPS server on an available TCP port. `/stream/{session-token}/{opaque-media-id}` supports GET, HEAD, and byte ranges. `/subtitle/{session-token}/{opaque-subtitle-id}` returns WebVTT, converting external SRT as needed. Only explicitly added files are addressable. No request path is interpreted as a filesystem path. Tokens are random 256-bit bearer values, revoked on stop/disconnect. URLs must never be logged or published. The TV verifies the sender certificate and refuses redirects. The separate Google Cast compatibility path uses HTTP and CORS; there are no browser cookies or ambient credentials.

The version field is mandatory; incompatible protocol versions are rejected. Both applications must be updated together. Google Cast compatibility remains suitable only for a trusted private LAN.
