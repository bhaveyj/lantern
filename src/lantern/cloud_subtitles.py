"""Explicit, optional prerecorded transcription. No cloud call occurs on startup."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import aiohttp

from .captions import Word, write_srt

ENDPOINT = "https://api.deepgram.com/v1/listen"
RATES = {"single": .0043, "multi": .0052}  # USD/minute; estimates, checked 2026-10-25.


def estimated_cost(duration: float, language: str | None) -> float:
    return duration / 60 * RATES["single" if language else "multi"]


async def transcribe(audio: Path, output: Path, language: str | None, api_key: str, progress) -> dict:
    if not api_key.strip() or len(api_key) > 1024 or any(not 33 <= ord(char) <= 126 for char in api_key.strip()):
        raise ValueError("Enter a valid Deepgram API key to use cloud transcription.")
    params = [("model", "nova-3"), ("language", language or "multi"), ("punctuate", "true"), ("smart_format", "true")]
    if not language:
        params.extend([("language_hint", "en"), ("language_hint", "hi")])
    total = audio.stat().st_size

    async def upload():
        sent, previous_bucket = 0, -1
        with audio.open("rb") as source:
            while block := await asyncio.to_thread(source.read, 256 * 1024):
                sent += len(block)
                bucket = min(100, int(sent * 100 / max(total, 1)))
                if bucket != previous_bucket:
                    progress("Uploading the selected audio to Deepgram…", bucket)
                    previous_bucket = bucket
                yield block
        progress("Recognizing speech in the cloud…", None)

    timeout = aiohttp.ClientTimeout(total=3600, connect=20, sock_read=900)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(ENDPOINT, params=params, headers={"Authorization": f"Token {api_key.strip()}", "Content-Type": "audio/flac", "Content-Length": str(total)}, data=upload(), allow_redirects=False) as response:
                if response.status != 200:
                    explanation = {401: "The cloud API key was rejected.", 403: "This cloud account is not allowed to transcribe this audio.", 402: "The cloud account needs available credit.", 429: "The cloud account reached a usage or rate limit."}.get(response.status, "The cloud service could not complete this transcription.")
                    raise ValueError(f"{explanation} (HTTP {response.status})")
                blocks, size = [], 0
                async for block in response.content.iter_chunked(65536):
                    size += len(block)
                    if size > 32 * 1024**2:
                        raise ValueError("The cloud transcript exceeded the response size limit.")
                    blocks.append(block)
                document = json.loads(b"".join(blocks))
    except (aiohttp.ClientError, TimeoutError):
        # Never include a request object or provider response text: they can
        # contain authorization headers. Do not retry ambiguous paid requests.
        raise ValueError("The cloud connection failed or timed out. Check your connection and provider usage before retrying.") from None
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise ValueError("The cloud service returned an invalid transcript.") from None
    try:
        alternative = document["results"]["channels"][0]["alternatives"][0]
        raw_words = alternative["words"]
        if len(raw_words) > 300000:
            raise ValueError("The cloud transcript contains too many words.")
        words = [Word(float(word["start"]), float(word["end"]), word.get("punctuated_word", word["word"])) for word in raw_words]
        writing = asyncio.create_task(asyncio.to_thread(write_srt, words, output))
        try:
            count = await asyncio.shield(writing)
        except asyncio.CancelledError:
            await writing  # Finish closing the file before the job deletes temporary audio/output.
            raise
        languages = alternative.get("languages") or ([language] if language else [])
        detected = "/".join(str(value) for value in languages) or "auto"
        language_label = "/".join({"en": "English", "hi": "Hindi"}.get(value, value) for value in languages) or "Detected language"
        return {"language": detected, "language_label": language_label, "cues": count}
    except (KeyError, IndexError, TypeError, OverflowError):
        raise ValueError("The cloud service did not return usable timed words.") from None
