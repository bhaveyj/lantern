# Receiver stability verification

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
