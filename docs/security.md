# Network and security model

[Back to Lantern TV](../README.md)

The custom receiver advertises `_lantern._tcp.local.` and listens on TCP port `8765`. The sender opens a WebSocket at `/control`, pairs with the six-digit TV PIN, and sends a fixed WebSocket PING every 10 seconds to keep the receiver alive during quiet playback. The sender's media server listens on an available TCP port and exposes only explicitly added files using random session tokens and byte-range support.

The custom receiver uses TLS 1.2 or newer and code-only SPAKE2+ pairing. The code is never sent to the other device: both sides prove knowledge using the pinned BoringSSL implementation of RFC 9383. The exchange binds both TLS certificate identities, the media port, the computer name, and fresh nonces. Windows remembers the verified TV automatically. Certificate fingerprints are available only in connection details; normal pairing does not ask users to compare them. Both apps must support protocol v3; earlier fingerprint-only pairings need one new code entry.

Remembered-device keys are protected by Windows DPAPI and AndroidKeyStore-backed AES-GCM storage on the TV. Reconnects use fresh, channel-bound proofs and need no code. Use the Windows connection menu's **Paired TVs** list to forget a TV, or **Paired computers** on the TV to revoke a computer. Each side retains at most 32 pairings. Clearing app data removes its remembered devices. The TV accepts HTTPS media only from the authenticated sender's address, port, and certificate.

Google Cast's default receiver requires a separate HTTP compatibility path. Windows warns before connecting, and Cast should be used only on a trusted LAN. Do not publish session URLs or enable router port forwarding. The media listener binds loopback while idle and the selected network interface while connected, accepts requests only from the selected TV's address, and rejects foreign Host and browser Origin headers. Cast CORS is limited to known receiver origins on that peer. The TV accepts only tokenized media/subtitle routes on the paired sender and refuses redirects. The protocol and message formats are documented in [`docs/protocol.md`](protocol.md).

## Connection troubleshooting

Keep the PC and TV on the same trusted home network. Guest Wi-Fi, VPN-only routes, or router client isolation can prevent discovery or playback. If discovery is unavailable, choose **Connect by IP** in Windows and enter the TV's private IPv4 address. Use port `8765` for the Lantern receiver or `8009` for Google Cast.

Allow Lantern through Windows Firewall on the **Private** profile. For packaged-app firewall rules, see the [Windows build guide](development.md#build-the-windows-sender). After a TV reset or reinstall, enter the new TV code to pair again.

## Media process restrictions

Media subprocesses accept a fixed set of file container formats and local file/pipe protocols; playlist, script, device, and network inputs are excluded. Probe output and captured error output are bounded, individual FFmpeg allocations are capped, and preparation has idle/overall deadlines. These restrictions reduce exposure to malicious files but do not constitute an operating-system sandbox. Keep native media tools updated.
