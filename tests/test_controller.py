import asyncio
import threading
from dataclasses import asdict
from types import SimpleNamespace

import pytest
from aiohttp import web

from lantern.controller import Controller, Device, validate_host
from lantern.media import Probe


def test_only_local_ip_addresses_are_accepted():
    assert validate_host(" 192.168.1.2 ") == "192.168.1.2"
    for invalid in ["8.8.8.8", "https://192.168.1.2", "0.0.0.0", "224.0.0.1"]:
        with pytest.raises(ValueError):
            validate_host(invalid)


async def test_receiver_pair_playlist_state_and_stop(aiohttp_server, tmp_path):
    received = []
    async def receiver(request):
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        hello = await ws.receive_json()
        assert hello["type"] == "pair" and hello["version"] == 1
        if hello["pin"] != "123456":
            await ws.send_json({"type": "error", "message": "Wrong code"})
            await ws.close()
            return ws
        await ws.send_json({"type": "pair.ok", "audioMimes": ["audio/mp4a-latm"]})
        await ws.send_json({"type": "playback.state", "id": "test", "playing": True, "position": 5, "duration": 20})
        async for frame in ws:
            if frame.type == web.WSMsgType.TEXT:
                import json
                received.append(json.loads(frame.data))
        return ws
    app = web.Application()
    app.router.add_get("/control", receiver)
    tv = await aiohttp_server(app)
    events = []
    controller = Controller(tmp_path / "cache", lambda kind, data: events.append((kind, data)))
    await controller.start()
    media = tmp_path / "movie.mp4"
    media.touch()
    await controller.sync([{"id": "test", "title": "Movie", "path": str(media), "subtitles": [], "selected_subtitle": None}])
    device = asdict(Device("test-tv", "Living Room", "127.0.0.1", tv.port, "receiver"))
    try:
        with pytest.raises(ValueError, match="Wrong code"):
            await controller.connect(device, "000000")
        await controller.connect(device, "123456")
        await asyncio.sleep(0.05)
        assert any(kind == "connected" for kind, _ in events)
        assert controller.current_position == 5
        assert any(m["type"] == "playlist.snapshot" and m["items"][0]["title"] == "Movie" for m in received)
        token = controller.server.token
        await controller.command("pause")
        await controller.stop()
        assert controller.server.token != token
        await asyncio.sleep(0.05)
        assert any(m.get("action") == "pause" for m in received)
    finally:
        await controller.close()


async def test_cast_starts_without_active_session_and_polls(monkeypatch, tmp_path):
    class FakeMedia:
        def __init__(self):
            self.status = SimpleNamespace(media_session_id=None)
            self.session_active_event = threading.Event()
            self.stops = 0
            self.polls = 0
        def register_status_listener(self, listener): self.listener = listener
        def play_media(self, url, mime, **kwargs):
            self.url = url
            self.status.media_session_id = 1
            self.session_active_event.set()
        def block_until_active(self, timeout): assert self.session_active_event.is_set()
        def update_status(self): self.polls += 1
        def stop(self):
            assert self.status.media_session_id is not None
            self.stops += 1
            self.status.media_session_id = None
    media = FakeMedia()
    cast = SimpleNamespace(media_controller=media, wait=lambda timeout: None, disconnect=lambda: None)
    monkeypatch.setattr("lantern.controller.pychromecast.get_chromecast_from_host", lambda host: cast)
    async def fake_probe(path): return Probe("h264", "aac", 45, "mp4")
    monkeypatch.setattr("lantern.controller.probe", fake_probe)
    controller = Controller(tmp_path / "cache", lambda *args: None)
    async def fake_prepare(path, info, progress): return path
    monkeypatch.setattr(controller.preparation, "prepare", fake_prepare)
    source = tmp_path / "movie.mp4"
    source.touch()
    await controller.start()
    try:
        await controller.sync([{"id": "one", "title": "Movie", "path": str(source), "subtitles": [], "selected_subtitle": None}])
        await controller.connect(asdict(Device("cast", "TV", "127.0.0.1", 8009, "cast")))
        await controller.play("one")
        assert media.stops == 0
        await asyncio.sleep(2.1)
        assert media.polls >= 1
    finally:
        await controller.close()
    assert media.stops == 1


async def test_receiver_keepalive_survives_continuous_state_updates(aiohttp_server, monkeypatch, tmp_path):
    monkeypatch.setattr("lantern.controller.RECEIVER_PING_INTERVAL", 0.05, raising=False)
    pings = []
    timed_out = []

    async def receiver(request):
        ws = web.WebSocketResponse(autoping=False)
        await ws.prepare(request)
        await ws.receive_json()
        await ws.send_json({"type": "pair.ok", "audioMimes": []})

        async def state_updates():
            while not ws.closed:
                await ws.send_json({"type": "playback.state", "playing": True, "position": 5})
                await asyncio.sleep(0.01)

        ticker = asyncio.create_task(state_updates())
        try:
            while not ws.closed:
                # Model NanoWSD's inbound-only idle timeout, accelerated.
                try:
                    frame = await asyncio.wait_for(ws.receive(), 0.4)
                except TimeoutError:
                    timed_out.append(True)
                    await ws.close()
                    break
                if frame.type == web.WSMsgType.PING:
                    pings.append(frame.data)
                    await ws.pong(frame.data)
                elif frame.type in {web.WSMsgType.CLOSE, web.WSMsgType.CLOSING, web.WSMsgType.CLOSED}:
                    break
        finally:
            ticker.cancel()
            await asyncio.gather(ticker, return_exceptions=True)
        return ws

    app = web.Application()
    app.router.add_get("/control", receiver)
    tv = await aiohttp_server(app)
    events = []
    controller = Controller(tmp_path / "cache", lambda kind, data: events.append((kind, data)))
    await controller.start()
    try:
        await controller.connect(asdict(Device("test-tv", "TV", "127.0.0.1", tv.port, "receiver")), "123456")
        await asyncio.sleep(1.2)
        assert not timed_out, "Frequent TV state updates must not suppress outbound keepalive pings"
        assert len(pings) >= 3
        assert controller.ws is not None and not controller.ws.closed
        keepalive = controller.receiver_keepalive_task
    finally:
        await controller.close()
    assert keepalive.done()
    assert controller.receiver_keepalive_task is None
