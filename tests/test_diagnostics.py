import asyncio
import json

import aiohttp
from aiohttp import web
import pytest

from lantern.diagnostics import check_receiver
from lantern.media import Probe
from lantern.security import LocalIdentity
from pairing_peer import accept


async def test_diagnostic_entry_point_uses_verified_tls_and_pinned_media(aiohttp_server, monkeypatch, tmp_path):
    fetched = []
    async def probe(path):
        return Probe("h264", "aac", 60, "mp4", 2)
    monkeypatch.setattr("lantern.controller.probe", probe)

    async def receiver(request):
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        pair = await accept(ws, identity.fingerprint)
        async for frame in ws:
            if frame.type != aiohttp.WSMsgType.TEXT:
                continue
            message = json.loads(frame.data)
            if message["type"] == "playback.load":
                async with aiohttp.ClientSession() as session:
                    async with session.get(message["item"]["url"], ssl=aiohttp.Fingerprint(bytes.fromhex(pair["mediaFingerprint"]))) as response:
                        fetched.append(await response.read())
                await ws.send_json({"type": "playback.state", "id": "stability-check", "playing": True, "position": 3, "duration": 60})
            elif message["type"] == "playback.command" and message["action"] in {"play", "pause"}:
                await ws.send_json({"type": "playback.state", "id": "stability-check", "playing": message["action"] == "play", "position": 3, "duration": 60})
        return ws

    app = web.Application()
    app.router.add_get("/control", receiver)
    identity = LocalIdentity(tmp_path / "tv")
    server = await aiohttp_server(app, ssl=identity.context)
    source = tmp_path / "movie.mp4"
    source.write_bytes(b"diagnostic media bytes")
    report = tmp_path / "report.json"
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"host": "127.0.0.1", "port": server.port, "pin": "123456", "video": str(source), "report": str(report), "playSeconds": 0, "pauseSeconds": 0, "resumeSeconds": 0}))
    data = tmp_path / "data"
    data.mkdir()
    await asyncio.wait_for(asyncio.to_thread(check_receiver, config, data), 15)
    assert fetched == [source.read_bytes()]
    assert json.loads(report.read_text())["result"] == "passed"
    assert "123456" not in report.read_text() and "/stream/" not in report.read_text()
