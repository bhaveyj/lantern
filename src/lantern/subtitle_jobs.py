"""On-demand subtitle jobs; playback and its temporary cache remain independent."""
from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .media import CREATE_NO_WINDOW, tool_path

MAX_SUBTITLE_BYTES = 8 * 1024**2
TEXT_CODECS = {"subrip", "srt", "ass", "ssa", "webvtt", "mov_text", "text"}
LANGUAGES = {"eng": "English", "en": "English", "hin": "Hindi", "hi": "Hindi", "und": "Unknown language"}
JOB_FLAGS = CREATE_NO_WINDOW | (0x00004000 if os.name == "nt" else 0)  # Below normal priority.


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


class SubtitleJobs:
    def __init__(self, root: Path, emit: Callable[[str, dict], None]):
        self.root = root
        self.emit = emit
        self.task: asyncio.Task | None = None
        self.closed = False

    @property
    def busy(self):
        return self.task is not None and not self.task.done()

    def start_extraction(self, media_id: str, source: str, track_index: int):
        self.start(media_id, source, self.extract, track_index)

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
            await asyncio.to_thread(validate_subtitle, destination)
            return {"path": str(destination), "label": label, "reused": True}
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
