from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import shutil
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

GIB = 1024 ** 3
CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0


def tool_path(name: str) -> str:
    root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2]))
    executable = name + (".exe" if os.name == "nt" else "")
    for folder in (root / "tools", root / ".tools" / "ffmpeg", root / ".tools" / "ffmpeg" / "bin"):
        if (folder / executable).is_file():
            return str(folder / executable)
    found = shutil.which(name)
    if not found:
        raise RuntimeError(f"{name} is missing. Run scripts/bootstrap.ps1 to install the media tools.")
    return found


@dataclass
class Probe:
    video: str | None
    audio: str | None
    duration: float
    container: str
    channels: int | None = None
    video_index: int | None = None
    audio_index: int | None = None


async def probe(path: Path) -> Probe:
    process = await asyncio.create_subprocess_exec(tool_path("ffprobe"), "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, creationflags=CREATE_NO_WINDOW)
    try:
        output, error = await asyncio.wait_for(process.communicate(), timeout=30)
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()
    if process.returncode:
        raise RuntimeError(error.decode(errors="replace").strip() or "Could not inspect this media file.")
    data = json.loads(output)
    streams = data.get("streams", [])
    video = next((s for s in streams if s["codec_type"] == "video" and not s.get("disposition", {}).get("attached_pic")), {})
    audio = next((s for s in streams if s["codec_type"] == "audio"), {})
    return Probe(video.get("codec_name"), audio.get("codec_name"), float(data.get("format", {}).get("duration", 0)), data.get("format", {}).get("format_name", ""), audio.get("channels"), video.get("index"), audio.get("index"))


@dataclass(frozen=True)
class MediaPlan:
    mode: str  # direct, remux, or audio conversion
    mime_type: str


def playback_plan(info: Probe, cast: bool, audio_mimes: list[str], suffix: str) -> MediaPlan:
    suffix = suffix.lower()
    if cast:
        stereo_aac = info.audio is None or (info.audio == "aac" and (info.channels is None or info.channels <= 2))
        if suffix in {".mp4", ".m4v", ".m4a"} and info.video in {None, "h264"} and stereo_aac:
            return MediaPlan("direct", "video/mp4" if info.video else "audio/mp4")
        if info.video is None and info.audio == "mp3" and suffix == ".mp3":
            return MediaPlan("direct", "audio/mpeg")
        return MediaPlan("remux" if stereo_aac else "audio", "video/mp4" if info.video else "audio/mp4")
    audio_aliases = {"aac": "audio/mp4a-latm", "ac3": "audio/ac3", "eac3": "audio/eac3", "dts": "audio/vnd.dts", "truehd": "audio/true-hd", "mp3": "audio/mpeg", "flac": "audio/flac", "opus": "audio/opus", "vorbis": "audio/vorbis"}
    audio_mime = "audio/raw" if (info.audio or "").startswith("pcm_") else audio_aliases.get(info.audio, "")
    unsupported_audio = info.audio is not None and audio_mime not in audio_mimes
    if unsupported_audio:
        return MediaPlan("audio", "video/mp4" if info.video else "audio/mp4")
    if suffix == ".avi":
        return MediaPlan("remux" if info.audio in {None, "aac"} else "audio", "video/mp4")
    types = {".mkv": "video/x-matroska", ".webm": "video/webm", ".mp3": "audio/mpeg", ".wav": "audio/wav", ".flac": "audio/flac", ".ogg": "audio/ogg", ".aac": "audio/aac", ".m4a": "audio/mp4", ".ts": "video/mp2t", ".m2ts": "video/mp2t"}
    return MediaPlan("direct", types.get(suffix, "video/mp4" if info.video else "audio/mp4"))


def needs_preparation(info: Probe, cast: bool, audio_mimes: list[str], suffix: str) -> bool:
    return playback_plan(info, cast, audio_mimes, suffix).mode != "direct"


class Preparation:
    def __init__(self, cache: Path, quota: int = 10 * GIB):
        self.cache = cache
        self.quota = quota
        self.cache.mkdir(parents=True, exist_ok=True)
        self.process: asyncio.subprocess.Process | None = None
        self._lock = asyncio.Lock()
        self.pinned: set[Path] = set()
        self.cleanup()

    def cleanup(self) -> bool:
        complete = True
        for path in self.cache.glob("*"):
            if path.is_file() and path.suffix in {".mp4", ".part"}:
                try:
                    path.unlink(missing_ok=True)
                except PermissionError:
                    complete = False
        return complete

    async def cleanup_async(self) -> None:
        for delay in (0, 0.1, 0.3, 0.8, 1.5):
            if delay:
                await asyncio.sleep(delay)
            if self.cleanup():
                return

    def reserve(self, estimated: int) -> int:
        if estimated > self.quota:
            raise ValueError(f"Preparing this file would exceed the {self.quota / GIB:g} GiB session cache limit.")
        files = [(path, path.stat()) for path in self.cache.glob("*.mp4") if path.is_file()]
        used = sum(stat.st_size for _, stat in files)
        for path, stat in sorted(files, key=lambda entry: entry[1].st_mtime_ns):
            if used + estimated <= self.quota and shutil.disk_usage(self.cache).free >= estimated + GIB:
                break
            if path in self.pinned:
                continue
            try:
                path.unlink()
                used -= stat.st_size
            except OSError:
                continue  # A decoder may still hold the previous item open.
        if used + estimated > self.quota:
            raise ValueError("The session cache is full with files still in playback or queued. Stop the session to clear it.")
        if shutil.disk_usage(self.cache).free < estimated + GIB:
            raise ValueError("There is not enough free space to prepare this file and leave 1 GiB available.")
        return used

    async def prepare(self, path: Path, info: Probe, progress: Callable[[float], None], *, copy_audio: bool = False) -> Path:
        async with self._lock:
            return await self._prepare(path, info, progress, copy_audio=copy_audio)

    async def _prepare(self, path: Path, info: Probe, progress: Callable[[float], None], *, copy_audio: bool = False) -> Path:
        if info.video and info.video not in {"h264", "hevc"}:
            raise ValueError(f"This route cannot play {info.video.upper()} video. Use H.264 or HEVC; video conversion is not included.")
        stat = path.stat()
        key = hashlib.sha256(f"v2:{path}:{stat.st_size}:{stat.st_mtime_ns}:{copy_audio}".encode()).hexdigest()[:24]
        destination = self.cache / f"{key}.mp4"
        if destination.is_file():
            destination.touch()
            return destination
        partial = self.cache / f"{key}-{uuid.uuid4().hex}.part"
        estimated = stat.st_size + int(max(info.duration, 1) * 24000)
        used = self.reserve(estimated)
        video_map = f"0:{info.video_index}" if info.video_index is not None else "0:v:0?"
        audio_map = f"0:{info.audio_index}" if info.audio_index is not None else "0:a:0?"
        maps = (["-map", video_map] if info.video else []) + (["-map", audio_map] if info.audio else [])
        audio_args = ["-c:a", "copy"] if copy_audio else ["-c:a", "aac", "-ac", "2", "-b:a", "192k"]
        args = [tool_path("ffmpeg"), "-hide_banner", "-loglevel", "error", "-y", "-i", str(path), *maps, "-c:v", "copy", *audio_args, "-sn", "-movflags", "+faststart", "-progress", "pipe:1", "-f", "mp4", str(partial)]
        self.process = await asyncio.create_subprocess_exec(*args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, creationflags=CREATE_NO_WINDOW)
        process = self.process
        stderr = asyncio.create_task(process.stderr.read())
        try:
            async for raw in process.stdout:
                line = raw.decode(errors="replace").strip()
                if line.startswith("out_time_us="):
                    try:
                        progress(min(99.0, int(line.split("=", 1)[1]) / max(info.duration * 10000, 1)))
                    except ValueError:
                        pass
                if partial.exists() and (partial.stat().st_size + used > self.quota or shutil.disk_usage(self.cache).free < GIB):
                    raise ValueError("Preparation stopped because the temporary storage limit was reached.")
            await process.wait()
            error = await stderr
            if process.returncode:
                raise RuntimeError(error.decode(errors="replace")[-1600:] or "Media preparation failed.")
            partial.replace(destination)
            progress(100)
            return destination
        finally:
            if process.returncode is None:
                process.kill()
                await process.wait()
            if not stderr.done():
                await stderr
            self.process = None
            partial.unlink(missing_ok=True)


def subtitle_vtt(path: Path) -> str:
    data = path.read_bytes()
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = data.decode("utf-16") if data.startswith((b"\xff\xfe", b"\xfe\xff")) else data.decode("cp1252")
    text = text.replace("\r\n", "\n")
    if path.suffix.lower() == ".vtt":
        return text if text.lstrip().startswith("WEBVTT") else "WEBVTT\n\n" + text
    text = re.sub(r"(\d{2}:\d{2}:\d{2}),(\d{3})", r"\1.\2", text)
    return "WEBVTT\n\n" + text
