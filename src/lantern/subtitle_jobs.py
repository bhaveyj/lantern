"""On-demand subtitle jobs; playback and its temporary cache remain independent."""
from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import re
import shutil
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .media import CREATE_NO_WINDOW, tool_path
from .speech_models import MODELS, MODEL_REVISIONS
from .cloud_subtitles import transcribe as cloud_transcribe, estimated_cost

MAX_SUBTITLE_BYTES = 8 * 1024**2
TEXT_CODECS = {"subrip", "srt", "ass", "ssa", "webvtt", "mov_text", "text"}
LANGUAGES = {"eng": "English", "en": "English", "hin": "Hindi", "hi": "Hindi", "und": "Unknown language"}
JOB_FLAGS = CREATE_NO_WINDOW | (0x00004000 if os.name == "nt" else 0)  # Below normal priority.
MAX_AUDIO_BYTES = 1024**3


@dataclass(frozen=True)
class Track:
    index: int
    codec: str
    language: str
    label: str
    text: bool = False


@dataclass(frozen=True)
class Inspection:
    duration: float
    audio: list[Track]
    subtitles: list[Track]


async def run_tool(*args: str, timeout: float = 120) -> bytes:
    process = await asyncio.create_subprocess_exec(
        *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        creationflags=JOB_FLAGS,
    )
    try:
        output, error = await asyncio.wait_for(process.communicate(), timeout)
        if process.returncode:
            raise ValueError(error.decode(errors="replace")[-1200:] or "The media tool could not complete this job.")
        return output
    except TimeoutError:
        raise ValueError("Media inspection or extraction timed out. Check that the file is available and try again.") from None
    finally:
        if process.returncode is None:
            process.kill()
            await process.communicate()


async def inspect_media(path: Path) -> Inspection:
    if not await asyncio.to_thread(path.is_file):
        raise ValueError("The original media file is no longer available.")
    output = await run_tool(tool_path("ffprobe"), "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path), timeout=30)
    document = json.loads(output)
    duration = float(document.get("format", {}).get("duration", 0))
    duration = duration if math.isfinite(duration) and duration > 0 else 0
    audio, subtitles = [], []
    for stream in document.get("streams", []):
        kind = stream.get("codec_type")
        if kind not in {"audio", "subtitle"}:
            continue
        tags = stream.get("tags", {})
        language = tags.get("language", "und")
        codec = stream.get("codec_name", "unknown")
        title = tags.get("title", "")
        label = " · ".join(filter(None, (LANGUAGES.get(language, language), title, codec)))
        track = Track(int(stream["index"]), codec, language, label, codec in TEXT_CODECS)
        (audio if kind == "audio" else subtitles).append(track)
    return Inspection(duration, audio, subtitles)


def source_stamp(path: Path) -> tuple[str, int, int]:
    resolved = path.resolve(strict=True)
    stat = resolved.stat()
    return os.path.normcase(str(resolved)), stat.st_size, stat.st_mtime_ns


def cache_key(stamp: tuple, *settings) -> str:
    return hashlib.sha256(json.dumps([stamp, *settings], ensure_ascii=False).encode()).hexdigest()


def validate_subtitle(path: Path) -> None:
    size = path.stat().st_size
    if not 0 < size <= MAX_SUBTITLE_BYTES:
        raise ValueError("No usable captions were produced, or the subtitle exceeded the 8 MiB limit.")
    text = path.read_text(encoding="utf-8-sig")
    if not re.search(r"\d{2}:\d{2}:\d{2},\d{3}\s+-->\s+\d{2}:\d{2}:\d{2},\d{3}", text):
        raise ValueError("No timed captions were found in this subtitle track.")


def worker_command(request_path: Path) -> list[str]:
    if getattr(sys, "frozen", False):
        return [sys.executable, "--subtitle-worker", str(request_path)]
    return [sys.executable, "-m", "lantern.subtitle_worker", str(request_path)]


class SubtitleJobs:
    def __init__(self, root: Path, emit: Callable[[str, dict], None]):
        self.root = root
        self.emit = emit
        self.task: asyncio.Task | None = None
        self.closed = False
        self.worker_process: asyncio.subprocess.Process | None = None

    @property
    def busy(self):
        return self.task is not None and not self.task.done()

    async def cleanup_stale(self):
        def cleanup():
            jobs = self.root / "jobs"
            if not jobs.is_dir():
                return
            expected = {"audio.flac", "request.json", "state.json", "state.tmp", "result.srt", "metadata.json"}
            for folder in jobs.iterdir():
                if not re.fullmatch(r"[0-9a-f]{32}", folder.name) or not folder.is_dir() or folder.resolve().parent != jobs.resolve():
                    continue
                try:
                    files = list(folder.iterdir())
                    if any(file.name not in expected or not file.is_file() for file in files):
                        continue
                    for file in files:
                        file.unlink(missing_ok=True)
                    folder.rmdir()
                except OSError:
                    continue  # An antivirus scanner or exiting worker may still hold a file.
        await asyncio.to_thread(cleanup)

    def start_extraction(self, media_id: str, source: str, track_index: int):
        self.start(media_id, source, self.extract, track_index)

    def start_local(self, media_id: str, source: str, options: dict):
        self.start(media_id, source, self.generate_local, options)

    def start_cloud(self, media_id: str, source: str, options: dict):
        self.start(media_id, source, self.generate_cloud, options)

    def start(self, media_id: str, source: str, operation, *args):
        if self.closed:
            raise ValueError("Subtitle processing is closing.")
        if self.busy:
            raise ValueError("A subtitle job is already running. Cancel it or wait for it to finish.")
        context = {"media_id": media_id, "source": source}

        async def execute():
            self.emit("subtitles.started", context)
            try:
                result = await operation(Path(source), *args)
                self.emit("subtitles.ready", {**context, **result})
            except asyncio.CancelledError:
                self.emit("subtitles.cancelled", context)
                raise
            except Exception as error:
                self.emit("subtitles.failed", {**context, "message": str(error)})
            finally:
                self.emit("subtitles.finished", context)

        self.task = asyncio.create_task(execute())

    async def cancel(self):
        task = self.task
        if task and not task.done():
            if not task.cancelling():
                task.cancel()
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                if not task.done():
                    raise

    async def close(self):
        self.closed = True
        await self.cancel()

    async def extract(self, source: Path, track_index: int) -> dict:
        stamp = await asyncio.to_thread(source_stamp, source)
        info = await inspect_media(source)
        track = next((track for track in info.subtitles if track.index == track_index), None)
        if track is None or not track.text:
            raise ValueError("Choose a text subtitle track. Image subtitles need OCR or audio transcription.")
        folder = self.root / "extracted"
        await asyncio.to_thread(folder.mkdir, parents=True, exist_ok=True)
        destination = folder / f"{cache_key(stamp, 'embedded-v1', track_index)}.srt"
        label = f"{track.label} · Embedded"
        if await asyncio.to_thread(destination.is_file):
            try:
                await asyncio.to_thread(validate_subtitle, destination)
                return {"path": str(destination), "label": label, "reused": True}
            except (OSError, ValueError):
                pass  # Recover an incomplete/damaged cached artifact by extracting again.
        partial = folder / f"{uuid.uuid4().hex}.part"
        self.emit("subtitles.progress", {"message": "Extracting embedded subtitles…", "percent": None})
        try:
            await run_tool(tool_path("ffmpeg"), "-nostdin", "-v", "error", "-y", "-i", str(source), "-map", f"0:{track.index}", "-c:s", "srt", "-f", "srt", str(partial))
            await asyncio.to_thread(validate_subtitle, partial)
            if stamp != await asyncio.to_thread(source_stamp, source):
                raise ValueError("The media file changed during extraction. Try again.")
            await asyncio.to_thread(partial.replace, destination)
            return {"path": str(destination), "label": label, "reused": False}
        finally:
            partial.unlink(missing_ok=True)

    async def extract_audio(self, source: Path, destination: Path, track: int, duration: float, threads: int):
        estimated = int(max(duration, 60) * 32000 * 1.05)
        if estimated > MAX_AUDIO_BYTES or shutil.disk_usage(destination.parent).free < estimated + 1024**3:
            raise ValueError("Not enough temporary space for this audio track while leaving 1 GiB free, or the track exceeds the 1 GiB audio limit.")
        process = await asyncio.create_subprocess_exec(
            tool_path("ffmpeg"), "-nostdin", "-v", "error", "-y", "-threads", str(threads), "-i", str(source),
            "-map", f"0:{track}", "-vn", "-sn", "-ac", "1", "-ar", "16000", "-c:a", "flac",
            "-threads", str(threads), "-progress", "pipe:1", str(destination),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, creationflags=JOB_FLAGS,
        )
        error_task = asyncio.create_task(process.stderr.read())
        try:
            while raw := await asyncio.wait_for(process.stdout.readline(), 90):
                line = raw.decode(errors="replace").strip()
                if line.startswith("out_time_us="):
                    percent = min(99, int(line.split("=", 1)[1]) / max(duration * 10000, 1))
                    self.emit("subtitles.progress", {"message": "Preparing the selected audio track…", "percent": percent})
                    if destination.stat().st_size > MAX_AUDIO_BYTES or shutil.disk_usage(destination.parent).free < 1024**3:
                        raise ValueError("Subtitle generation stopped because the temporary audio storage limit was reached.")
            await process.wait()
            error = await error_task
            if process.returncode:
                raise ValueError(error.decode(errors="replace")[-1200:] or "The selected audio track could not be decoded.")
        finally:
            if process.returncode is None:
                process.kill()
                await process.wait()
            if not error_task.done():
                await error_task

    async def local_worker(self, folder: Path, request: dict) -> dict:
        request_path = folder / "request.json"
        await asyncio.to_thread(request_path.write_text, json.dumps(request), encoding="utf-8")
        process = await asyncio.create_subprocess_exec(*worker_command(request_path), stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL, creationflags=JOB_FLAGS)
        self.worker_process = process
        wait_task = asyncio.create_task(process.wait())
        previous = None

        def read_state():
            try:
                return json.loads((folder / "state.json").read_text(encoding="utf-8"))
            except (OSError, ValueError):
                return {}

        try:
            while not wait_task.done():
                state = await asyncio.to_thread(read_state)
                if state != previous and state.get("phase"):
                    self.emit("subtitles.progress", {"message": state["phase"], "percent": state.get("percent")})
                previous = state
                try:
                    await asyncio.wait_for(asyncio.shield(wait_task), .25)
                except TimeoutError:
                    pass
            state = await asyncio.to_thread(read_state)
            if process.returncode or not state.get("complete"):
                raise ValueError(state.get("error", "The local speech worker could not finish. Check that the speech runtime is installed."))
            return state
        finally:
            if process.returncode is None:
                process.kill()
            await wait_task
            self.worker_process = None

    async def generate_local(self, source: Path, options: dict) -> dict:
        return await self.generate(source, options, cloud=False)

    async def generate_cloud(self, source: Path, options: dict) -> dict:
        return await self.generate(source, options, cloud=True)

    async def generate(self, source: Path, options: dict, *, cloud: bool) -> dict:
        model = "Deepgram Nova-3" if cloud else options.get("model", "small")
        language = options.get("language")
        threads = 2 if cloud else options.get("threads", 2)
        if (not cloud and model not in MODELS) or language not in (None, "en", "hi") or threads not in (2, 4):
            raise ValueError("Choose a supported speech model, language, and CPU mode.")
        stamp = await asyncio.to_thread(source_stamp, source)
        inspection = await inspect_media(source)
        if cloud and inspection.duration <= 0:
            raise ValueError("The audio duration is unknown, so a cloud cost estimate cannot be provided. Use local generation for this file.")
        if cloud and "approved_duration" in options and abs(inspection.duration - options["approved_duration"]) > 1:
            raise ValueError("The media duration changed. Reopen Create subtitles to review a fresh cloud cost estimate.")
        if cloud:
            budget = float(options.get("max_estimated_cost", 1.0))
            if not math.isfinite(budget) or budget <= 0 or estimated_cost(inspection.duration, language) > budget:
                raise ValueError("The estimated cloud cost exceeds the allowed amount. Review the cost limit in Create subtitles.")
            if not isinstance(options.get("mip_opt_out", True), bool):
                raise ValueError("Choose a valid cloud privacy setting.")
        audio_index = options.get("audio_index")
        if not any(track.index == audio_index for track in inspection.audio):
            raise ValueError("The selected audio track is no longer available.")
        folder = self.root / "generated"
        await asyncio.to_thread(folder.mkdir, parents=True, exist_ok=True)
        key = (cache_key(stamp, "deepgram-nova3-v1", language, audio_index) if cloud else
               cache_key(stamp, "local-v1-faster-whisper-1.2.1", model, MODEL_REVISIONS[model], language, audio_index))
        destination = folder / f"{key}.srt"
        metadata = folder / f"{key}.json"
        if await asyncio.to_thread(destination.is_file) and await asyncio.to_thread(metadata.is_file):
            try:
                await asyncio.to_thread(validate_subtitle, destination)
                record = json.loads(await asyncio.to_thread(metadata.read_text, encoding="utf-8"))
                if isinstance(record["label"], str):
                    return {"path": str(destination), "label": record["label"], "reused": True}
            except (OSError, ValueError, KeyError, TypeError):
                pass
        job = self.root / "jobs" / uuid.uuid4().hex
        await asyncio.to_thread(job.mkdir, parents=True, exist_ok=True)
        try:
            audio = job / "audio.flac"
            self.emit("subtitles.progress", {"message": "Preparing the selected audio track…", "percent": 0})
            await self.extract_audio(source, audio, audio_index, inspection.duration, threads)
            if cloud:
                if stamp != await asyncio.to_thread(source_stamp, source):
                    raise ValueError("The media changed during audio preparation. Review the file before uploading it.")
                result = await cloud_transcribe(audio, job / "result.srt", language, options.get("api_key", ""),
                    lambda message, percent: self.emit("subtitles.progress", {"message": message, "percent": percent}), mip_opt_out=options.get("mip_opt_out", True))
            else:
                result = await self.local_worker(job, {
                    "model": model, "threads": threads, "language": language,
                    "audio": str(audio), "duration": inspection.duration,
                    "models": str(self.root / "models"), "output": str(job / "result.srt"),
                    "allow_download": bool(options.get("allow_download", False)), "parent_pid": os.getpid(),
                })
            output = job / "result.srt"
            await asyncio.to_thread(validate_subtitle, output)
            if stamp != await asyncio.to_thread(source_stamp, source):
                raise ValueError("The media file changed during transcription. Try again.")
            label = f"{result.get('language_label', LANGUAGES.get(result['language'], result['language']))} · AI generated ({model})"
            record = {"label": label, "model": model, "language": result["language"], "audio_index": audio_index, "cues": result["cues"]}
            if cloud:
                record["mip_opt_out"] = options.get("mip_opt_out", True)
            await asyncio.to_thread((job / "metadata.json").write_text, json.dumps(record), encoding="utf-8")
            await asyncio.to_thread(output.replace, destination)
            await asyncio.to_thread((job / "metadata.json").replace, metadata)
            return {"path": str(destination), "label": label, "reused": False}
        finally:
            # Job folders contain only our direct temporary files; never walk or
            # recursively remove a computed path, and never remove saved models.
            for temporary in job.iterdir():
                if temporary.is_file():
                    temporary.unlink(missing_ok=True)
            job.rmdir()
