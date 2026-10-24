import asyncio
from pathlib import Path

import pytest

from lantern.media import tool_path
from lantern.subtitle_jobs import SubtitleJobs, inspect_media, run_tool


async def make_embedded_fixture(tmp_path):
    subtitle = tmp_path / "हिन्दी.srt"
    subtitle.write_text("1\n00:00:00,100 --> 00:00:01,800\nनमस्ते दुनिया।\n", encoding="utf-8")
    source = tmp_path / "movie.mkv"
    await run_tool(tool_path("ffmpeg"), "-v", "error", "-f", "lavfi", "-i", "sine=duration=2", "-i", str(subtitle), "-map", "0:a", "-map", "1:s", "-c:a", "pcm_s16le", "-c:s", "srt", "-metadata:s:s:0", "language=hin", str(source))
    return source


async def test_real_embedded_subtitle_extracts_unicode_and_reuses_saved_result(tmp_path):
    source = await make_embedded_fixture(tmp_path)
    original = source.read_bytes()
    inspection = await inspect_media(source)
    assert len(inspection.audio) == 1
    assert inspection.subtitles[0].text
    assert inspection.subtitles[0].language == "hin"
    jobs = SubtitleJobs(tmp_path / "subtitles", lambda *_: None)
    first = await jobs.extract(source, inspection.subtitles[0].index)
    output = Path(first["path"])
    assert "नमस्ते दुनिया।" in output.read_text(encoding="utf-8")
    timestamp = output.stat().st_mtime_ns
    second = await jobs.extract(source, inspection.subtitles[0].index)
    assert second["path"] == first["path"]
    assert second["reused"]
    assert output.stat().st_mtime_ns == timestamp
    assert source.read_bytes() == original
    assert not list(output.parent.glob("*.part"))


async def test_extraction_rejects_audio_track_and_missing_source(tmp_path):
    source = await make_embedded_fixture(tmp_path)
    jobs = SubtitleJobs(tmp_path / "subtitles", lambda *_: None)
    with pytest.raises(ValueError, match="text subtitle track"):
        await jobs.extract(source, 0)
    source.unlink()
    with pytest.raises(ValueError, match="no longer available"):
        await inspect_media(source)


async def test_cancellation_waits_for_job_cleanup_and_never_publishes_a_result(tmp_path):
    events, cleaned = [], asyncio.Event()
    jobs = SubtitleJobs(tmp_path / "subtitles", lambda name, data: events.append(name))
    async def operation(source):
        try:
            await asyncio.Event().wait()
        finally:
            await asyncio.sleep(.01)
            cleaned.set()
    jobs.start("media", "movie.mkv", operation)
    await asyncio.sleep(0)
    with pytest.raises(ValueError, match="already running"):
        jobs.start("other", "other.mkv", operation)
    await jobs.close()
    assert cleaned.is_set()
    assert not jobs.busy
    assert "subtitles.cancelled" in events
    assert "subtitles.ready" not in events
    assert events[-1] == "subtitles.finished"
    with pytest.raises(ValueError, match="closing"):
        jobs.start("other", "other.mkv", operation)
