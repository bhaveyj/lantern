import asyncio
import os
from pathlib import Path

import pytest
from aiohttp import web

from lantern.cloud_subtitles import estimated_cost
from lantern.credentials import CredentialStore
from lantern.subtitle_jobs import SubtitleJobs
from test_subtitle_jobs import make_embedded_fixture


async def test_cloud_uploads_only_selected_audio_and_reuses_saved_subtitles(monkeypatch, aiohttp_server, tmp_path):
    uploads = []
    async def handle(request):
        body = await request.read()
        uploads.append(body)
        assert body.startswith(b"fLaC")
        assert request.headers["Authorization"] == "Token test-only-secret"
        assert request.query["language"] == "multi"
        assert request.query.getall("language_hint") == ["en", "hi"]
        return web.json_response({"results": {"channels": [{"alternatives": [{"languages": ["hi", "en"], "words": [
            {"word": "नमस्ते", "start": .1, "end": .6},
            {"word": "world", "punctuated_word": "world.", "start": .7, "end": 1.5},
        ]}]}]}})
    app = web.Application()
    app.router.add_post("/listen", handle)
    server = await aiohttp_server(app)
    monkeypatch.setattr("lantern.cloud_subtitles.ENDPOINT", str(server.make_url("/listen")))
    source = await make_embedded_fixture(tmp_path)
    original = source.read_bytes()
    jobs = SubtitleJobs(tmp_path / "subtitles", lambda *_: None)
    options = {"audio_index": 0, "language": None, "api_key": "test-only-secret"}
    result = await jobs.generate_cloud(source, options)
    text = Path(result["path"]).read_text(encoding="utf-8")
    assert "नमस्ते world." in text
    assert "00:00:00,100 --> 00:00:01,500" in text
    assert "Hindi/English" in result["label"]
    reused = await jobs.generate_cloud(source, {**options, "api_key": "rotated-test-key"})
    assert reused["reused"] and len(uploads) == 1
    assert source.read_bytes() == original
    assert not list((jobs.root / "jobs").iterdir())
    assert "test-only-secret" not in next((jobs.root / "generated").glob("*.json")).read_text()


@pytest.mark.parametrize("status", [401, 429, 302])
async def test_cloud_errors_do_not_leak_keys_and_clean_temporary_audio(monkeypatch, aiohttp_server, tmp_path, status):
    async def handle(request):
        await request.read()
        return web.Response(status=status, text="Token test-only-secret", headers={"Location": "http://127.0.0.1/not-followed"})
    app = web.Application()
    app.router.add_post("/listen", handle)
    server = await aiohttp_server(app)
    monkeypatch.setattr("lantern.cloud_subtitles.ENDPOINT", str(server.make_url("/listen")))
    source = await make_embedded_fixture(tmp_path)
    jobs = SubtitleJobs(tmp_path / "subtitles", lambda *_: None)
    with pytest.raises(ValueError) as error:
        await jobs.generate_cloud(source, {"audio_index": 0, "language": "en", "api_key": "test-only-secret"})
    assert "test-only-secret" not in str(error.value)
    assert not list((jobs.root / "jobs").iterdir())
    assert not list((jobs.root / "generated").glob("*.srt"))


@pytest.mark.skipif(os.name != "nt", reason="Windows user encryption")
def test_optional_cloud_key_is_encrypted_and_can_be_forgotten(tmp_path):
    store = CredentialStore(tmp_path / "key.bin")
    assert not store.load()
    store.save("private-test-key")
    assert b"private-test-key" not in store.path.read_bytes()
    assert store.load() == "private-test-key"
    store.forget()
    assert not store.load()


def test_cloud_estimate_uses_audio_minutes_and_multilingual_rate():
    assert estimated_cost(7200, "en") == pytest.approx(.516)
    assert estimated_cost(7200, None) == pytest.approx(.624)


async def test_cancelling_cloud_job_leaves_no_audio_or_result(monkeypatch, aiohttp_server, tmp_path):
    started, release = asyncio.Event(), asyncio.Event()
    async def handle(request):
        await request.read()
        started.set()
        await release.wait()
        return web.json_response({})
    app = web.Application()
    app.router.add_post("/listen", handle)
    server = await aiohttp_server(app)
    monkeypatch.setattr("lantern.cloud_subtitles.ENDPOINT", str(server.make_url("/listen")))
    source = await make_embedded_fixture(tmp_path)
    events = []
    jobs = SubtitleJobs(tmp_path / "subtitles", lambda name, data: events.append(name))
    jobs.start_cloud("media", str(source), {"audio_index": 0, "language": "en", "api_key": "test-only-secret"})
    try:
        await asyncio.wait_for(started.wait(), 4)
        await jobs.cancel()
        assert not jobs.busy
        assert "subtitles.cancelled" in events and "subtitles.ready" not in events
        assert not list((jobs.root / "jobs").iterdir())
        assert not list((jobs.root / "generated").glob("*.srt"))
    finally:
        release.set()
        await jobs.close()
