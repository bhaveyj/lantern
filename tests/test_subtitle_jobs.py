import asyncio
import sys
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


async def test_cancelling_local_worker_terminates_the_actual_process(monkeypatch, tmp_path):
    monkeypatch.setattr("lantern.subtitle_jobs.worker_command", lambda _: [sys.executable, "-c", "import time; time.sleep(60)"])
    jobs = SubtitleJobs(tmp_path, lambda *_: None)
    task = asyncio.create_task(jobs.local_worker(tmp_path, {}))
    for _ in range(100):
        if jobs.worker_process is not None:
            break
        await asyncio.sleep(.01)
    process = jobs.worker_process
    assert process is not None
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 3)
    assert process.returncode is not None
    assert jobs.worker_process is None


async def test_local_results_survive_cleanup_and_cache_is_source_and_audio_specific(monkeypatch, tmp_path):
    source = await make_embedded_fixture(tmp_path)
    jobs = SubtitleJobs(tmp_path / "subtitles", lambda *_: None)
    calls = []
    async def extract(source, output, *args):
        output.write_bytes(b"audio fixture")
    async def worker(folder, request):
        calls.append(request)
        Path(request["output"]).write_text("1\n00:00:00,100 --> 00:00:01,000\nनमस्ते\n", encoding="utf-8")
        return {"language": "hi", "cues": 1}
    monkeypatch.setattr(jobs, "extract_audio", extract)
    monkeypatch.setattr(jobs, "local_worker", worker)
    options = {"model": "small", "language": "hi", "audio_index": 0}
    first = await jobs.generate_local(source, options)
    second = await jobs.generate_local(source, {**options, "threads": 4})
    assert second["reused"] and len(calls) == 1
    assert not list((jobs.root / "jobs").iterdir())
    await jobs.close()
    assert Path(first["path"]).is_file()
    assert calls[0]["threads"] == 2
    assert not calls[0]["allow_download"]
    source.touch()
    other_jobs = SubtitleJobs(jobs.root, lambda *_: None)
    monkeypatch.setattr(other_jobs, "extract_audio", extract)
    monkeypatch.setattr(other_jobs, "local_worker", worker)
    third = await other_jobs.generate_local(source, options)
    assert third["path"] != first["path"] and len(calls) == 2
    Path(third["path"]).with_suffix(".json").write_text("{broken", encoding="utf-8")
    repaired = await other_jobs.generate_local(source, options)
    assert not repaired["reused"] and len(calls) == 3


async def test_restart_cleanup_removes_only_known_abandoned_job_files(tmp_path):
    jobs = SubtitleJobs(tmp_path, lambda *_: None)
    stale = tmp_path / "jobs" / ("a" * 32)
    stale.mkdir(parents=True)
    (stale / "audio.flac").write_bytes(b"temporary audio")
    (stale / "request.json").write_text("{}")
    unrelated = tmp_path / "jobs" / ("b" * 32)
    unrelated.mkdir()
    (unrelated / "personal.txt").write_text("keep")
    model = tmp_path / "models" / "model.bin"
    model.parent.mkdir()
    model.write_bytes(b"saved model")
    await jobs.cleanup_stale()
    assert not stale.exists()
    assert (unrelated / "personal.txt").read_text() == "keep"
    assert model.read_bytes() == b"saved model"
