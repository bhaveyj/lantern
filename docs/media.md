# Media, subtitles, and local data

[Back to Lantern TV](../README.md)

## Playback compatibility and preparation

The UI accepts common video containers including MP4, M4V, MOV, MKV, WebM, AVI, MPEG, MPG, TS, and M2TS, plus audio formats including MP3, M4A, AAC, FLAC, WAV, and OGG. Actual playback is determined by the receiver's decoders and the codecs inside the container.

Lantern probes media with FFmpeg/ffprobe and caches inspection results until the file changes. Compatible H.264/stereo-AAC MP4 files and MP3 audio stream directly to Cast. Files that only need a container change are remuxed with compatible AAC audio preserved; unsupported audio is converted to stereo AAC while H.264 or HEVC video is copied. Unsupported video is reported instead of being transcoded. Prepared files use a 10 GiB session quota and leave at least 1 GiB free on the volume. The cache evicts the oldest unused prepared files when space is needed, protecting current and queued playback. Stopping, disconnecting, or quitting removes prepared session files.

## Finding and extracting subtitles

SRT and WebVTT files are discovered when they share a base name with the video or live in a neighboring `Subs` or `Subtitles` directory. A subtitle can also be selected manually. The custom receiver can switch external tracks during playback; Cast applies the selected subtitle when the item is played again.

Choose **Create subtitles…** to inspect a file for embedded subtitle tracks. Text tracks can be extracted to a persistent SRT file and attached automatically without modifying the original media. Image subtitles require OCR or speech transcription. Extraction runs in the background, supports cancellation, and reuses saved results until the source file changes. Saved subtitles are separate from the temporary playback cache.

## Generating subtitles locally

The same dialog can generate subtitles locally from an audio track using free multilingual Whisper models. Choose Auto, English, or Hindi, and Base, Small, or Medium. Eco mode uses two CPU inference threads; Balanced uses four. Inference runs in a separate, below-normal-priority process, one job at a time, and exits after completion or cancellation. Thread settings are not a fixed CPU-percentage cap. The first-use model download is optional and resumable; public model revisions are pinned and downloads are verified before loading. After download, local transcription works offline and does not upload audio. The original media is never changed. Completed AI subtitles and models persist under the data directory's `subtitles` folder; temporary audio is deleted when the job finishes. Generated captions transcribe speech in its original language and can contain recognition or timing errors; translation and rolling live captions are not included.

## Optional cloud generation

An optional **Generate with Deepgram** method uploads only the selected audio track using your own API key. The dialog shows an estimated USD cost and requires an explicit **Upload audio & generate** action. Auto uses Nova-3 multilingual with English/Hindi hints; selecting English or Hindi uses that language. The optional remembered key is protected with Windows DPAPI for the current user, never stored in plaintext or sent to the TV. Leaving Remember unchecked forgets any saved key when starting a cloud job. Cached results can be reused without another upload. Cancelling stops the local request but does not undo charges already incurred by the provider. No cloud credentials or paid requests are needed for local generation.

Cloud generation excludes uploads from Deepgram model improvement by default (`mip_opt_out=true`). The dialog explains the privacy choice and starts with a US$1 maximum estimated cost per job; raise it deliberately for longer audio. This checks Lantern's estimate, not a provider-enforced billing cap. Deepgram's published Pay As You Go/Growth rates do not change for opt-out as of 26 Oct 2026. Cached results remain reusable and the choice does not change retention of earlier uploads.

## Data locations and cleanup

The sender stores its library and logs under `%LOCALAPPDATA%\LanternTV`; prepared media is under its `cache` subdirectory. Removing that directory resets the local library and clears logs/cache after Lantern is closed. Original videos and subtitle files are never moved or rewritten.

To stop a connected session cleanly, use **Stop**, disconnect, or quit from the tray menu. Closing the main window while connected keeps the sender in the Windows tray so playback can continue.
