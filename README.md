# Lantern TV

**Your videos. Your TV. No cloud required.**

Stream videos and music from your Windows PC to Android TV, Google TV, or a Google Cast device. Build a playlist, bring your subtitles, and put your media on the big screen.

Local playback needs no Lantern account or cloud upload. With the Lantern receiver, you can control playback, captions, and picture fit from your TV remote.

**Windows 10/11 x64 | Android TV / Google TV / Google Cast | MIT licensed source**

[Get started](#getting-started) | [Subtitle tools](#subtitles-without-the-search) | [Firefox preview](docs/web-streaming.md) | [Build from source](docs/development.md)

> **Development preview:** Apps are built locally; installable binaries are not included in this repository. Start with the [build guide](docs/development.md) for prerequisites and commands.

## Why Lantern?

- **Watch what you already own.** Stream files across your home network. Original media stays untouched.
- **Keep the next video ready.** Save and reorder a playlist that stays synchronized between Windows and the Lantern receiver.
- **Spend less time finding subtitles.** Load nearby captions, extract embedded text tracks, or generate subtitles locally.
- **Use your TV remote.** Play, pause, seek, switch captions, and adjust picture fit with the Lantern Android TV receiver.
- **Pair once, reconnect later.** Enter the code shown on your TV once; Lantern remembers the verified receiver.

## How it works

Your Windows PC sends media over your home network to your TV. Choose a connection:

| TV connection | Setup | What you get |
| --- | --- | --- |
| **Lantern receiver** | Install the APK on Android TV / Google TV and pair with its six-digit code. | Encrypted playback, synchronized playlists, TV remote controls, captions, and picture-fit options. |
| **Google Cast** | Select a compatible Cast device in Lantern. | Uses Cast's default receiver without the Lantern APK. Media uses unencrypted HTTP, so use a trusted private LAN. |

Keep your PC awake and Lantern running while you watch. Closing the main window while connected keeps playback running from the system tray.

## Getting started

[Build the apps](docs/development.md) to generate `dist/Lantern/Lantern.exe` and, for the custom receiver, `dist/Lantern-TV-0.1.0.apk`.

Once you have a build:

1. Put your PC and TV on the same trusted home network.
2. For the custom receiver, install and open Lantern TV on the TV. Leave its pairing screen visible. Google Cast does not need this app.
3. Launch `Lantern.exe` on Windows, or run `./run.ps1` from a bootstrapped source checkout.
4. Select your TV and click **Connect**. Enter the TV code for a new Lantern receiver; later connections only need **Connect**. For Cast, select the separate **Google Cast** device.
5. Add media and choose **Play on TV**. With the Lantern receiver, you can also control playback and reorder the playlist from the TV remote.

If discovery fails, use **Connect by IP**. See [connection troubleshooting](docs/security.md#connection-troubleshooting) for ports, Wi-Fi, and Windows Firewall settings.

## Subtitles without the search

Lantern finds matching SRT/WebVTT files beside your video or in nearby `Subs` and `Subtitles` folders. You can also choose a subtitle manually.

Choose **Create subtitles...** to extract embedded text captions or generate subtitles locally with multilingual Whisper models, including English and Hindi. After the first model download, local generation works offline without uploading audio.

Optional Deepgram generation uses your own API key and uploads the selected audio track after you confirm the upload and estimated cost. Provider charges apply.

Generated captions can contain errors. Translation, rolling live captions, and automatic OCR are not included. See the [subtitle guide](docs/media.md) for models, caption switching, and cloud settings.

## Firefox streaming preview

An optional companion for sending HLS live/on-demand streams and MP4 video from Firefox to your TV is in development. Its companion and relay implementation are not yet included in the published source.

[Preview setup and limitations](docs/web-streaming.md)

## Compatibility

Common containers such as MP4, MKV, and WebM, plus audio formats such as MP3 and FLAC, can be added. Playback depends on your TV's codecs; a supported container does not guarantee playback.

Lantern can remux supported files and convert incompatible audio to stereo AAC. It does not transcode unsupported video. DRM-protected media is not supported.

See [media compatibility](docs/media.md#playback-compatibility-and-preparation) and [web-source limits](docs/web-streaming.md#supported-sources-and-current-limits).

## Privacy and your files

Local file playback stays on your home network. Lantern has no account service or analytics. Local subtitle generation does not upload audio; optional Deepgram generation sends audio to that provider after an explicit upload action.

The Lantern receiver uses encrypted connections and verified pairing. Cast uses a separate unencrypted compatibility path. No router port forwarding is needed.

Original media is never moved or rewritten. See the [security model](docs/security.md) and [local data guide](docs/media.md#data-locations-and-cleanup) for details.

## Development and documentation

- [Build and develop](docs/development.md): prerequisites, installation, packaging, release signing, and automated tests.
- [Media and subtitles](docs/media.md): formats, preparation, caption tools, and local storage.
- [Firefox streaming preview](docs/web-streaming.md): planned companion setup, supported sources, and current limitations; implementation is pending publication.
- [Network and security](docs/security.md): pairing, encryption, Cast, and media process restrictions.
- [Receiver protocol](docs/protocol.md) and [device testing](docs/testing.md).

## Help improve Lantern

[Report a bug or share compatibility feedback](https://github.com/bhaveyj/lantern/issues). Include your Windows version, TV or Cast model, connection type, and steps to reproduce. For playback issues, include the container/codecs if known. Keep credentials, pairing codes, and private media out of public reports.

## License

Lantern-authored source is [MIT licensed](LICENSE). Dependencies retain their own licenses; read the [third-party notices](THIRD_PARTY.md) before redistributing packaged builds, especially for Qt/PySide6 and FFmpeg.
