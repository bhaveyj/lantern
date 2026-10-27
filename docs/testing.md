# Receiver stability verification

The Android device test `TlsIdentityDeviceTest` exercises a real AndroidKeyStore
identity and a pinned TLS/WebSocket pairing handshake. Desktop JVM tests use a
different security provider and cannot catch Android's prehashed ECDSA permission
requirements. To preserve an existing TV installation, build
`assembleDebugAndroidTest`, install the receiver and test APK with `adb install -r`,
then run `adb shell am instrument -w app.lantern.tv.test/androidx.test.runner.AndroidJUnitRunner`.
Remove only `app.lantern.tv.test` afterward. Gradle `connectedDebugAndroidTest`
can uninstall its target application at teardown; reserve it for disposable devices.
Protocol v3 also exercises the same native SPAKE2+ library on both platforms.
The native Windows build checks RFC 9383 Appendix C's known-answer values.
Pairing integration tests cover code-only setup, remembered reconnects, two-TLS
identity relays, wrong codes, expiry, revocation, and replay rejection.

Run the Python suite with `./.venv/Scripts/python.exe -m pytest -q`.
The receiver keepalive regression models an inbound-only timeout while the TV
continuously sends state updates. It checks that fixed-schedule PINGs keep the
connection alive and that their task is cancelled on disconnect.

The optional `--check-receiver CONFIG_JSON` development entry point exercises
the installed executable, including its actual LAN media server and firewall
identity. Use a compatible local video at least five minutes long, open Lantern
on the TV, and create a local configuration containing its current pairing code:

```json
{
  "host": "192.168.1.10",
  "pin": "CURRENT_SIX_DIGIT_CODE",
  "video": "C:/Videos/example.mkv",
  "report": "C:/path/to/project/artifacts/receiver-wifi-stability.json",
  "playSeconds": 180,
  "pauseSeconds": 65,
  "resumeSeconds": 35
}
```

Run `Lantern.exe --check-receiver CONFIG_JSON --data-dir CHECK_DATA_DIRECTORY`
using the installed executable's absolute path. The data directory is separate
from the normal playlist. Logs and temporary preparation files go there; the
JSON report updates during the check. It never writes the pairing code or media
URLs to that report. Remove the temporary configuration when finished.

The check does not issue playback commands during each hold. It requires live
TV state updates and advancing playback, verifies the paused position remains
stable, then checks resume. It stops its test playback and exits afterward. Keep
the TV receiver foreground during the run. The ordinary `device_smoke.py` test
now holds playback for 90 seconds by default to cross multiple idle-timeout
intervals.

For the TV pairing-screen lifecycle regression, install the APK and read its code.
Within the code's five-minute lifetime, press Back to return to the TV launcher,
then reopen Lantern; the code must remain unchanged. Repeat with Home, and open
the playlist using the Menu key: Back should close the playlist without leaving
Lantern. A launcher-style restart must also retain the code and one receiver
activity:

```powershell
adb -s TV_IP:5555 shell am start -W -f 0x14000000 -n app.lantern.tv/.MainActivity
```

If a Windows sender is paired, its connection should remain alive through these
warm launches. Normal five-minute expiry and an actual app-process restart still
issue a fresh code. Never include pairing codes in saved test reports.

Subtitle generation checks cover real FFmpeg text extraction, preservation of the
original media, Hindi/English caption timing, worker-process cancellation,
source-specific cache invalidation, resumable model downloads and corrupt-model
rejection. Cloud tests use a local HTTP server to verify audio-only uploads,
word timestamps, key redaction, redirect refusal and cancellation cleanup. Windows
DPAPI tests use only a dummy credential. These tests do not make paid API calls.

For an end-to-end local check, generate subtitles from a short known speech file
in Small/Eco mode, then repeat with model downloads disabled. Check the packaged
Windows executable as well as source execution: its hidden worker must finish,
release its process and attach a saved subtitle. Review recognition accuracy
separately on representative English and Hindi dialogue, including music and
overlapping voices. Synthetic speech checks are not movie-accuracy benchmarks.

The physical receiver smoke test also starts without an external subtitle track,
then attaches one while paused. It verifies that the receiver fetches the WebVTT
file and preserves position and pause state. This catches Media3 merged-track ID
prefixes and source updates that otherwise report a selected track without
loading captions. Visually check both English and Devanagari text on the TV,
including switching captions off and back on and seeking within a cue.

The pinned Media3 1.6.1 receiver uses its supported legacy subtitle decoder to
retain in-progress cues after seeks. Recheck this compatibility path when
upgrading Media3, since the legacy APIs are deprecated upstream. Resume playback
after a paused seek so the decoder can publish the active cue.
