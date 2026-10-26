"""Constrain media tool inputs and bound captured subprocess output."""
from __future__ import annotations

import asyncio

# Deliberately exclude playlists, device inputs, scripts, and network protocols.
# These are input options and must appear before each input filename.
LOCAL_INPUT = ("-protocol_whitelist", "file,pipe", "-format_whitelist", "mov,matroska,webm,avi,mpeg,mpegvideo,mpegts,mp3,aac,flac,wav,ogg", "-max_alloc", "268435456")
PROBE_FIELDS = "stream=index,codec_name,codec_type,channels:stream_disposition=attached_pic:stream_tags=language,title:format=duration,format_name"


async def read_bounded(stream, limit: int) -> bytes:
    result = bytearray()
    while chunk := await stream.read(min(65536, limit + 1 - len(result))):
        result.extend(chunk)
        if len(result) > limit:
            raise ValueError("Media tool output exceeded the safety limit.")
    return bytes(result)


async def read_tail(stream, limit: int = 65536) -> bytes:
    result = bytearray()
    while chunk := await stream.read(65536):
        result.extend(chunk)
        if len(result) > limit:
            del result[:-limit]
    return bytes(result)


async def communicate_bounded(process, timeout: float) -> tuple[bytes, bytes]:
    tasks = [asyncio.create_task(read_bounded(process.stdout, 4 * 1024**2)),
             asyncio.create_task(read_bounded(process.stderr, 65536)),
             asyncio.create_task(process.wait())]
    try:
        output, error, _ = await asyncio.wait_for(asyncio.gather(*tasks), timeout)
        return output, error
    finally:
        if process.returncode is None:
            process.kill()
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        # Draining after termination prevents a full pipe from blocking wait.
        await asyncio.gather(read_tail(process.stdout, 1), read_tail(process.stderr, 1), process.wait())
