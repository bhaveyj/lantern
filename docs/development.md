# Building and developing Lantern

[Back to Lantern TV](../README.md)

Run the commands in this guide from the repository root unless stated otherwise. For playback, subtitles, and local data, see the [media guide](media.md). For pairing and network details, see the [security guide](security.md).

## Development prerequisites

The checked-in build scripts target Windows PowerShell:

- Windows 10/11 x64 for the sender and packaging scripts.
- Python 3.12 or newer available as `python` on `PATH`.
- JDK 17 or newer for the Android build.
- Visual Studio C++ build tools with the x64 C++ workload, CMake 3.22+, and Ninja for the shared pairing library. The build discovers the installed Visual Studio version; existing standalone 2019 tools also work. The Android SDK CMake package includes Ninja.
- Android SDK with platform 35 and platform-tools/ADB. Set `ANDROID_HOME` when the SDK is not at `%LOCALAPPDATA%\Android\Sdk`.
- Android NDK 26.3.11579264 and SDK CMake 3.22.1 for receiver native builds; accept the SDK licenses before building.
- An Android TV/Google TV device or emulator for receiver testing. ADB authorization is required for installation.
- Inno Setup 6 is optional and only needed for the Windows installer; the portable build and ZIP do not need it.

The bootstrap script creates `.venv`, restores pinned Gradle/FFmpeg/BoringSSL sources, and compiles the small SPAKE2+ pairing library. Existing compiler, Java, and Android SDK installations are reused. The native build runs an RFC 9383 known-answer test. Internet access is required for first setup. Node and JADX remain optional research tools.

## Clone and set up

```powershell
git clone https://github.com/bhaveyj/lantern.git
cd lantern

# Only needed if PowerShell blocks local scripts in this session.
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass

./scripts/bootstrap.ps1
```

`bootstrap.ps1` installs the tested Python versions from `requirements-lock.txt`, then creates the editable project installation using that environment. Windows packaging checks the installed versions against the lock before producing a distribution. Dependency upgrades should deliberately update the lock and pass the regression suite before packaging.

Native tool URLs, versions, archive hashes, and bundled media executable hashes are pinned in `scripts/toolchain-lock.json`. Bootstrap verifies downloads before extracting them and restores modified FFmpeg binaries; Windows packaging verifies those binaries again. Changing the lock requires deliberate upstream verification and regression testing. Existing Java/SDK/global Gradle installations remain part of the trusted developer environment.

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

The Android module uses application ID `app.lantern.tv`, min SDK 24, target/compile SDK 35, Kotlin, AndroidX Media3, and NanoHTTPD/NanoWSD. The debug APK is signed with the local development key. For a release APK, set `LANTERN_ANDROID_KEYSTORE` to an absolute keystore path outside the repository, plus `LANTERN_ANDROID_STORE_PASSWORD`, `LANTERN_ANDROID_KEY_ALIAS`, and `LANTERN_ANDROID_KEY_PASSWORD`, then run `./scripts/build-android.ps1 -Release`. Both Gradle and the script refuse unsigned release builds. Keep the same release key for future updates and back it up securely. A release key cannot update a debug-signed installation; changing signing identity requires a deliberate migration and may erase app data.

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

Normal Windows builds are unsigned development artifacts. For a signed build, install your valid code-signing certificate and private key in `CurrentUser\My`, set `LANTERN_WINDOWS_CERT_THUMBPRINT`, and run `./scripts/build-windows.ps1 -Release`. The script validates the certificate before packaging, signs the application before building the installer, then signs the installer and verifies both signatures and timestamps. Missing signing credentials or failed signing stops the release build. This does not purchase a certificate, publish binaries, or deploy the app.

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

GitHub CI runs the regression suites, verifies media tools, scans Git history with pinned Gitleaks, and audits exact Python plus resolved Android Maven dependencies against OSV on pushes and pull requests. An unavailable or incomplete dependency scan fails the check. Native FFmpeg/Qt/ML library advisories still require upstream review; a clean package scan is not a guarantee of safety. CI does not publish or deploy artifacts.

Optional checks:

```powershell
# Check an actual media file through the no-console preparation path.
./.venv/Scripts/python.exe scripts/check_windowed_media.py C:\media\movie.mkv .tmp\media-check.json

# Exercise a physical Lantern receiver. The video should be a compatible local file.
./.venv/Scripts/python.exe scripts/device_smoke.py TV_IP --pin 123456 --video C:\media\sample.mp4

# Exercise a Cast device instead of the custom receiver.
./.venv/Scripts/python.exe scripts/device_smoke.py CAST_IP --cast --video C:\media\sample.mp4
```

The device smoke test defaults to a 90-second playback hold and writes its local report under `artifacts/`, which is ignored because reports can contain private LAN addresses and device details. For the longer installed-executable receiver stability check, see [`docs/testing.md`](testing.md).

## Repository layout

```text
src/lantern/              Windows sender, controller, media server, library
android/app/src/          Android TV receiver and pairing/control server
tests/                    Python unit and integration tests
scripts/                  Bootstrap, build, firewall, smoke-test, and tooling scripts
docs/protocol.md          Receiver protocol v3
docs/testing.md           Physical-device stability test procedure
docs/development.md       Source setup, builds, packaging, and diagnostics
docs/media.md             Playback, subtitle tools, and local data
docs/security.md          Pairing, network model, and troubleshooting
docs/web-streaming.md     Firefox preview setup and web-source limits
pyproject.toml            Python package metadata and dependency ranges
requirements-lock.txt     Tested Python dependency resolution
THIRD_PARTY.md            External components and license/source notices
```

Generated directories such as `.venv`, `.tools`, `.tmp`, `build`, `dist`, Android build output, local SDK configuration, caches, and diagnostic reports are ignored by Git. Do not add personal media, pairing codes, private IP addresses, signing keys, or generated binaries to a public commit.

## Licensing and distribution

Lantern-authored source is MIT licensed; see [`LICENSE`](../LICENSE). External dependencies retain their own licenses. Read [`THIRD_PARTY.md`](../THIRD_PARTY.md) before redistributing an executable, especially for Qt/PySide6 and the selected FFmpeg build.

This development build uses a debug-signed APK and an unsigned Windows executable. A public release still needs a final Android package/signing strategy, backed-up release keys, Windows code signing, compatibility testing, and complete dependency/source notices.
