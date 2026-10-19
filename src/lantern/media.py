from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import shutil
import sys
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


async def probe(path: Path) -> Probe:
    process = await asyncio.create_subprocess_exec(tool_path("ffprobe"), "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, creationflags=CREATE_NO_WINDOW)
    try:
        output, error = await process.communicate()
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()
    if process.returncode:
        raise RuntimeError(error.decode(errors="replace").strip() or "Could not inspect this media file.")
    data = json.loads(output)
    streams = data.get("streams", [])
    return Probe(next((s["codec_name"] for s in streams if s["codec_type"] == "video" and not s.get("disposition", {}).get("attached_pic")), None), next((s["codec_name"] for s in streams if s["codec_type"] == "audio"), None), float(data.get("format", {}).get("duration", 0)), data.get("format", {}).get("format_name", ""))


def needs_preparation(info: Probe, cast: bool, audio_mimes: list[str], suffix: str) -> bool:
    if cast:
        return True
    audio_aliases = {"aac": "audio/mp4a-latm", "ac3": "audio/ac3", "eac3": "audio/eac3", "dts": "audio/vnd.dts", "truehd": "audio/true-hd", "mp3": "audio/mpeg", "flac": "audio/flac", "opus": "audio/opus", "vorbis": "audio/vorbis"}
    unsupported_audio = info.audio is not None and audio_aliases.get(info.audio, "") not in audio_mimes
    # AVI is not a Media3 progressive container. Normalize compatible streams.
    return unsupported_audio or suffix.lower() == ".avi"


class Preparation:
    def __init__(self, cache: Path, quota: int = 10 * GIB):
        self.cache = cache
        self.quota = quota
        self.cache.mkdir(parents=True, exist_ok=True)
        self.process: asyncio.subprocess.Process | None = None
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
        for delay in (0.1, 0.3, 0.8, 1.5):
            await asyncio.sleep(delay)
            if self.cleanup():
                return

    async def prepare(self, path: Path, info: Probe, progress: Callable[[float], None]) -> Path:
        if info.video and info.video not in {"h264", "hevc"}:
            raise ValueError(f"This route cannot play {info.video.upper()} video. Use H.264 or HEVC; video conversion is not included.")
        stat = path.stat()
        key = hashlib.sha256(f"{path}:{stat.st_size}:{stat.st_mtime_ns}".encode()).hexdigest()[:24]
        destination = self.cache / f"{key}.mp4"
        if destination.is_file():
            return destination
        partial = self.cache / f"{key}.part"
        estimated = stat.st_size + int(max(info.duration, 1) * 24000)
        used = sum(p.stat().st_size for p in self.cache.glob("*.mp4"))
        if estimated > self.quota or used + estimated > self.quota:
            raise ValueError("Preparing this file would exceed the 10 GiB session cache. Stop the session to clear it, or use the TV receiver directly.")
        if shutil.disk_usage(self.cache).free < estimated + GIB:
            raise ValueError("There is not enough free space to prepare this file and leave 1 GiB available.")
        args = [tool_path("ffmpeg"), "-hide_banner", "-loglevel", "error", "-y", "-i", str(path), "-map", "0:v:0?", "-map", "0:a:0?", "-c:v", "copy", "-c:a", "aac", "-ac", "2", "-b:a", "192k", "-sn", "-movflags", "+faststart", "-progress", "pipe:1", "-f", "mp4", str(partial)]
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
