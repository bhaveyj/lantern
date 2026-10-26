# Lantern TV

Lantern streams personal video and audio from a Windows PC to an Android/Google TV receiver or a Google Cast device. The Windows sender remembers file references, serves media only across the local network, and keeps the playlist and playback controls synchronized. The Android TV receiver adds remote-control playback, captions, and picture-fit controls.

This repository contains a personal development build. The custom receiver uses TLS, verified TV certificate identities, and a short-lived screen PIN. Google Cast compatibility streams remain unencrypted and require a trusted private LAN. No router port forwarding is required.

## What Lantern does

- Discovers the custom Lantern receiver and Google Cast devices with mDNS.
- Falls back to a manually entered private IPv4 address when discovery is unavailable.
- Streams files directly from Windows; there is no Lantern account, cloud upload, analytics, or payment service.
- Maintains a persistent local playlist and supports play, pause, seek, volume, next/previous, captions, and fit/fill controls.
- Converts supported audio-only incompatibilities to a temporary MP4 with stereo AAC audio. It does not transcode unsupported video.
- Finds matching SRT/WebVTT captions beside a video or in `Subs`/`Subtitles` folders.
- Keeps a bounded preparation cache, never modifies the original media, and reports missing or moved files.

The receiver uses Android Media3 and the TV's available decoders, so a listed container does not guarantee that every codec, profile, subtitle format, or audio layout will play on every TV. DRM-protected media is not supported.

## Quick start for an existing build

If you already have the generated files, start the Windows sender with `run.ps1`, or launch `dist/Lantern/Lantern.exe`. Install `dist/Lantern-TV-0.1.0.apk` on the TV. These files are generated outputs and are intentionally ignored by Git; build them locally using the instructions below when cloning this repository.

1. Put the PC and TV on the same trusted home network. Avoid guest Wi-Fi, VPN-only routes, and router client isolation.
2. Open Lantern TV on the TV and leave its pairing screen visible.
3. Open Lantern on Windows. Select the discovered Lantern receiver, click **Connect**, and enter the six-digit code shown on the TV. On first pairing, compare the complete **VERIFY TV IDENTITY** fingerprint with the TV before approving. A changed identity requires verification again.
4. Add media on Windows and choose **Play on TV**. The playlist can be reordered from Windows or from the TV remote.
5. For Google Cast, select the separate **Google Cast** device. Cast does not use the Lantern PIN or custom receiver.

If discovery does not work, choose **Connect by IP**. Use the TV's private IPv4 address and port `8765` for the Lantern receiver, or port `8009` for Google Cast. Allow the sender through Windows Firewall on the **Private** profile.

## Development prerequisites

The checked-in build scripts target Windows PowerShell:

- Windows 10/11 x64 for the sender and packaging scripts.
- Python 3.12 or newer available as `python` on `PATH`.
- JDK 17 or newer for the Android build.
- Android SDK with platform 35 and platform-tools/ADB. Set `ANDROID_HOME` when the SDK is not at `%LOCALAPPDATA%\Android\Sdk`.
- An Android TV/Google TV device or emulator for receiver testing. ADB authorization is required for installation.
- Inno Setup 6 is optional and only needed for the Windows installer; the portable build and ZIP do not need it.

The bootstrap script creates `.venv` and downloads project-local build tools into `.tools`: Gradle 8.10.2 and FFmpeg/ffprobe. Existing Java and Android SDK installations are reused. Internet access is required for the first bootstrap. Node and the pinned JADX tool are optional; install them with `./scripts/bootstrap.ps1 -ResearchTools` when working on the research scripts.

## Clone and set up

```powershell
git clone https://github.com/bhaveyj/lantern.git
cd lantern

# Only needed if PowerShell blocks local scripts in this session.
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass

./scripts/bootstrap.ps1
```

`bootstrap.ps1` installs the tested Python versions from `requirements-lock.txt`, then creates the editable project installation using that environment. Windows packaging checks the installed versions against the lock before producing a distribution. Dependency upgrades should deliberately update the lock and pass the regression suite before packaging.

Start the sender from the repository root:

```powershell
./run.ps1
```

The equivalent command, useful for debugging, is:

```powershell
./.venv/Scripts/python.exe -m lantern.app
```

The default application data directory is `%LOCALAPPDATA%\LanternTV`. It contains the playlist database, logs, and temporary prepared media. Use a separate directory for diagnostics with `--data-dir PATH`.

## Build and install the Android receiver

Set `ANDROID_HOME` if needed, then build the debug APK and run the Android unit tests:

```powershell
$env:ANDROID_HOME = "$env:LOCALAPPDATA\Android\Sdk"
./scripts/build-android.ps1
```

The output is `dist/Lantern-TV-0.1.0.apk`. The build script writes the machine-specific `android/local.properties`; that file is ignored and must not be committed.

For a network-connected TV with ADB enabled:

```powershell
adb connect TV_IP:5555
./scripts/build-android.ps1 -Install -Device TV_IP:5555
```

If the TV uses a different ADB transport, omit `-Device` when it is the only authorized device, or pass the exact serial shown by `adb devices`. Approve the debugging prompt on the TV before installing.

The Android module uses application ID `app.lantern.tv`, min SDK 24, target/compile SDK 35, Kotlin, AndroidX Media3, and NanoHTTPD/NanoWSD. The debug APK is signed with the local development key; a release build needs a deliberately managed signing key and release configuration.

## Build the Windows sender

The Windows build uses PyInstaller and bundles FFmpeg from `.tools/ffmpeg`:

```powershell
./scripts/build-windows.ps1
```

This creates:

- `dist/Lantern/Lantern.exe` — portable application.
- `dist/Lantern-Windows-0.1.0.zip` — portable archive.
- `dist/Lantern-Setup-0.1.0.exe` — installer, only when `.tools/inno/ISCC.exe` is available.

To produce the installer, install Inno Setup 6 and make its compiler available at `.tools/inno/ISCC.exe`, or adapt `scripts/build-windows.ps1` to your local installation. The script copies the README, license, and third-party notices into the portable distribution.

If Windows Firewall does not allow the packaged sender, run this command from an elevated PowerShell prompt:

```powershell
./scripts/configure-firewall.ps1 -Executable C:\path\to\Lantern.exe
```

The rules are limited to the Private profile, local-subnet TCP traffic, and mDNS discovery traffic. Review or remove the rules using Windows Defender Firewall after testing.

## Tests and diagnostics

Run the automated Python suite from the repository root:

```powershell
./.venv/Scripts/python.exe -m pytest -q
```

The suite covers library persistence, pairing/control integration, receiver keepalives, Cast startup and polling, media codec decisions, FFmpeg preparation, subtitles, authorized/ranged streaming, and large-file seeking. Android unit tests run as part of `scripts/build-android.ps1`.

Optional checks:

```powershell
# Check an actual media file through the no-console preparation path.
./.venv/Scripts/python.exe scripts/check_windowed_media.py C:\media\movie.mkv .tmp\media-check.json

# Exercise a physical Lantern receiver. The video should be a compatible local file.
./.venv/Scripts/python.exe scripts/device_smoke.py TV_IP --pin 123456 --fingerprint FULL_SHA256_FROM_TV --video C:\media\sample.mp4

# Exercise a Cast device instead of the custom receiver.
./.venv/Scripts/python.exe scripts/device_smoke.py CAST_IP --cast --video C:\media\sample.mp4
```

The device smoke test defaults to a 90-second playback hold and writes its local report under `artifacts/`, which is ignored because reports can contain private LAN addresses and device details. For the longer installed-executable receiver stability check, see [`docs/testing.md`](docs/testing.md).

## Media behavior

The UI accepts common video containers including MP4, M4V, MOV, MKV, WebM, AVI, MPEG, MPG, TS, and M2TS, plus audio formats including MP3, M4A, AAC, FLAC, WAV, and OGG. Actual playback is determined by the receiver's decoders and the codecs inside the container.

Lantern probes media with FFmpeg/ffprobe and caches inspection results until the file changes. Compatible H.264/stereo-AAC MP4 files and MP3 audio stream directly to Cast. Files that only need a container change are remuxed with compatible AAC audio preserved; unsupported audio is converted to stereo AAC while H.264 or HEVC video is copied. Unsupported video is reported instead of being transcoded. Prepared files use a 10 GiB session quota and leave at least 1 GiB free on the volume. The cache evicts the oldest unused prepared files when space is needed, protecting current and queued playback. Stopping, disconnecting, or quitting removes prepared session files.

SRT and WebVTT files are discovered when they share a base name with the video or live in a neighboring `Subs` or `Subtitles` directory. A subtitle can also be selected manually. The custom receiver can switch external tracks during playback; Cast applies the selected subtitle when the item is played again.

Choose **Create subtitles…** to inspect a file for embedded subtitle tracks. Text tracks can be extracted to a persistent SRT file and attached automatically without modifying the original media. Image subtitles require OCR or speech transcription. Extraction runs in the background, supports cancellation, and reuses saved results until the source file changes. Saved subtitles are separate from the temporary playback cache.

The same dialog can generate subtitles locally from an audio track using free multilingual Whisper models. Choose Auto, English, or Hindi, and Base, Small, or Medium. Eco mode uses two CPU inference threads; Balanced uses four. Inference runs in a separate, below-normal-priority process, one job at a time, and exits after completion or cancellation. Thread settings are not a fixed CPU-percentage cap. The first-use model download is optional and resumable; public model revisions are pinned and downloads are verified before loading. After download, local transcription works offline and does not upload audio. The original media is never changed. Completed AI subtitles and models persist under the data directory's `subtitles` folder; temporary audio is deleted when the job finishes. Generated captions transcribe speech in its original language and can contain recognition or timing errors; translation and rolling live captions are not included.

An optional **Generate with Deepgram** method uploads only the selected audio track using your own API key. The dialog shows an estimated USD cost and requires an explicit **Upload audio & generate** action. Auto uses Nova-3 multilingual with English/Hindi hints; selecting English or Hindi uses that language. The optional remembered key is protected with Windows DPAPI for the current user, never stored in plaintext or sent to the TV. Leaving Remember unchecked forgets any saved key when starting a cloud job. Cached results can be reused without another upload. Cancelling stops the local request but does not undo charges already incurred by the provider. No cloud credentials or paid requests are needed for local generation.

Cloud generation excludes uploads from Deepgram model improvement by default (`mip_opt_out=true`). The dialog explains the privacy choice and starts with a US$1 maximum estimated cost per job; raise it deliberately for longer audio. This checks Lantern's estimate, not a provider-enforced billing cap. Deepgram's published Pay As You Go/Growth rates do not change for opt-out as of 26 Oct 2026. Cached results remain reusable and the choice does not change retention of earlier uploads.

Media subprocesses accept a fixed set of file container formats and local file/pipe protocols; playlist, script, device, and network inputs are excluded. Probe output and captured error output are bounded, individual FFmpeg allocations are capped, and preparation has idle/overall deadlines. These restrictions reduce exposure to malicious files but do not constitute an operating-system sandbox. Keep native media tools updated.

## Network and security model

The custom receiver advertises `_lantern._tcp.local.` and listens on TCP port `8765`. The sender opens a WebSocket at `/control`, pairs with the six-digit TV PIN, and sends a fixed WebSocket PING every 10 seconds to keep the receiver alive during quiet playback. The sender's media server listens on an available TCP port and exposes only explicitly added files using random session tokens and byte-range support.

The custom receiver uses TLS 1.2 or newer. Windows pins the TV's SHA-256 certificate fingerprint after the user compares it on the TV, before sending the PIN. The authenticated pairing sends the Windows media server's certificate fingerprint, and the TV accepts HTTPS media only from that pinned sender. There is no automatic plaintext fallback; both apps must support protocol v2. The TV key lives in AndroidKeyStore; Windows encrypts its media key and protects its password with the current user's DPAPI identity. Clearing app data removes these identities.

Google Cast's default receiver requires a separate HTTP compatibility path. Windows warns before connecting, and Cast should be used only on a trusted LAN. Do not publish session URLs or enable router port forwarding. The media listener binds loopback while idle and the selected network interface while connected, accepts requests only from the selected TV's address, and rejects foreign Host and browser Origin headers. Cast CORS is limited to known receiver origins on that peer. The TV accepts only tokenized media/subtitle routes on the paired sender and refuses redirects. The protocol and message formats are documented in [`docs/protocol.md`](docs/protocol.md).

## Repository layout

```text
src/lantern/              Windows sender, controller, media server, library
android/app/src/          Android TV receiver and pairing/control server
tests/                    Python unit and integration tests
scripts/                  Bootstrap, build, firewall, smoke-test, and tooling scripts
docs/protocol.md          Receiver protocol v2
docs/testing.md           Physical-device stability test procedure
pyproject.toml            Python package metadata and dependency ranges
requirements-lock.txt     Tested Python dependency resolution
THIRD_PARTY.md            External components and license/source notices
```

Generated directories such as `.venv`, `.tools`, `.tmp`, `build`, `dist`, Android build output, local SDK configuration, caches, and diagnostic reports are ignored by Git. Do not add personal media, pairing codes, private IP addresses, signing keys, or generated binaries to a public commit.

## Data locations and cleanup

The sender stores its library and logs under `%LOCALAPPDATA%\LanternTV`; prepared media is under its `cache` subdirectory. Removing that directory resets the local library and clears logs/cache after Lantern is closed. Original videos and subtitle files are never moved or rewritten.

To stop a connected session cleanly, use **Stop**, disconnect, or quit from the tray menu. Closing the main window while connected keeps the sender in the Windows tray so playback can continue.

## Licensing and distribution

Lantern-authored source is MIT licensed; see [`LICENSE`](LICENSE). External dependencies retain their own licenses. Read [`THIRD_PARTY.md`](THIRD_PARTY.md) before redistributing an executable, especially for Qt/PySide6 and the selected FFmpeg build.

This development build uses a debug-signed APK and an unsigned Windows executable. A public release still needs a final Android package/signing strategy, backed-up release keys, Windows code signing, compatibility testing, and complete dependency/source notices.
