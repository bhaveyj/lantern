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


async def test_replacement_play_waits_for_cancelled_job_cleanup(monkeypatch, tmp_path):
    events = []
    controller = Controller(tmp_path / "cache", lambda *args: None)
    started = asyncio.Event()
    cleaning = asyncio.Event()
    release_cleanup = asyncio.Event()

    async def play(media_id, position=0, **kwargs):
        events.append(media_id)
        if media_id == "first":
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cleaning.set()
                await release_cleanup.wait()
                events.append("cleaned")

    monkeypatch.setattr(controller, "_play", play)
    controller.queue_play("first")
    await asyncio.wait_for(started.wait(), 1)
    controller.queue_play("second")
    await asyncio.wait_for(cleaning.wait(), 1)
    controller.queue_play("third")
    await asyncio.sleep(0)
    assert events == ["first"]
    release_cleanup.set()
    await asyncio.wait_for(controller.play_task, 1)
    assert events == ["first", "cleaned", "third"]


@pytest.mark.parametrize("transport", ["receiver", "cast"])
async def test_failed_remote_stop_still_revokes_urls_and_cleans_cache(tmp_path, transport):
    controller = Controller(tmp_path / "cache", lambda *args: None)
    prepared = tmp_path / "cache" / "prepared.mp4"
    prepared.write_bytes(b"prepared media")
    controller.server.files["movie"] = prepared
    controller.current_id = "movie"
    controller.current_position = 42
    token = controller.server.token

    class FailedSocket:
        closed = False
        async def send_json(self, message):
            raise ConnectionResetError("TV disconnected")

    def failed_stop():
        raise ConnectionResetError("Cast disconnected")

    if transport == "receiver":
        controller.ws = FailedSocket()
    else:
        controller.cast = SimpleNamespace(media_controller=SimpleNamespace(
            status=SimpleNamespace(media_session_id=1), stop=failed_stop))
    await controller.stop()
    assert controller.server.token != token
    assert not controller.server.files
    assert not prepared.exists()
    assert controller.current_id is None
    assert controller.current_position == 0
    await controller.stop()  # Cleanup remains safe after repeated Stop actions.


async def test_connection_attempts_are_serialized(monkeypatch, tmp_path):
    controller = Controller(tmp_path / "cache", lambda *args: None)
    entered = asyncio.Event()
    release = asyncio.Event()
    order = []

    async def connect(device, pin):
        order.append(device["id"])
        if device["id"] == "first":
            entered.set()
            await release.wait()

    monkeypatch.setattr(controller, "_connect", connect)
    first = asyncio.create_task(controller.connect({"id": "first"}))
    await asyncio.wait_for(entered.wait(), 1)
    second = asyncio.create_task(controller.connect({"id": "second"}))
    await asyncio.sleep(0)
    assert order == ["first"]
    release.set()
    await asyncio.wait_for(asyncio.gather(first, second), 1)
    assert order == ["first", "second"]


async def test_old_receiver_disconnect_does_not_close_replacement(monkeypatch, tmp_path):
    controller = Controller(tmp_path / "cache", lambda *args: None)
    old = object()
    current = object()
    controller.ws = current
    async def unexpected_disconnect():
        pytest.fail("A stale receiver must not disconnect the current session")
    monkeypatch.setattr(controller, "_disconnect", unexpected_disconnect)
    await controller.disconnect(expected_ws=old)
    assert controller.ws is current


@pytest.fixture
def cast_playback(monkeypatch):
    class FakeMedia:
        def __init__(self):
            self.status = SimpleNamespace(media_session_id=None, content_id=None, player_state="UNKNOWN",
                                          current_time=0, duration=45, idle_reason=None)
            self.session_active_event = threading.Event()
            self.loads = []
        def register_status_listener(self, listener): self.listener = listener
        def play_media(self, url, mime, **kwargs):
            self.loads.append((url, kwargs))
            if not kwargs.get("enqueue"):
                self.status.media_session_id = 1
                self.status.content_id = url
                self.status.player_state = "PLAYING" if kwargs.get("autoplay", True) else "PAUSED"
                self.status.current_time = kwargs.get("current_time", 0)
                self.session_active_event.set()
                self.listener.new_media_status(self.status)
        def block_until_active(self, timeout): assert self.session_active_event.is_set()
        def update_status(self): self.listener.new_media_status(self.status)
        def stop(self): self.status.media_session_id = None
        def pause(self):
            self.status.player_state = "PAUSED"
            self.listener.new_media_status(self.status)
    media = FakeMedia()
    cast = SimpleNamespace(media_controller=media, wait=lambda timeout: None, disconnect=lambda: None)
    monkeypatch.setattr("lantern.controller.pychromecast.get_chromecast_from_host", lambda host: cast)
    async def fake_probe(path): return Probe("h264", "aac", 45, "mp4")
    monkeypatch.setattr("lantern.controller.probe", fake_probe)
    return media


async def cast_controller(tmp_path, monkeypatch, count):
    controller = Controller(tmp_path / "cache", lambda *args: None)
    async def prepare(path, info, progress): return path
    monkeypatch.setattr(controller.preparation, "prepare", prepare)
    items = []
    for index in range(count):
        source = tmp_path / f"{index}.mp4"
        source.touch()
        items.append({"id": str(index), "title": str(index), "path": str(source), "subtitles": [], "selected_subtitle": None})
    await controller.start()
    await controller.sync(items)
    await controller.connect(asdict(Device("cast", "TV", "127.0.0.1", 8009, "cast")))
    return controller, items


async def test_cast_reorder_preserves_pause_and_position(cast_playback, monkeypatch, tmp_path):
    controller, items = await cast_controller(tmp_path, monkeypatch, 3)
    try:
        await controller.play("0", 17)
        await controller.preload_task
        await controller.command("pause")
        await asyncio.sleep(0)
        assert not controller.playing
        await controller.sync([items[0], items[2], items[1]])
        await controller.play_task
        current_loads = [kwargs for url, kwargs in cast_playback.loads if url.endswith("/0")]
        assert len(current_loads) == 2
        assert current_loads[-1]["autoplay"] is False
        assert current_loads[-1]["current_time"] == 17
        assert not controller.playing
    finally:
        await controller.close()


async def test_cast_append_does_not_reload_current_item(cast_playback, monkeypatch, tmp_path):
    controller, items = await cast_controller(tmp_path, monkeypatch, 2)
    try:
        await controller.sync(items[:1])
        await controller.play("0")
        await controller.preload_task
        await controller.sync(items)
        await controller.preload_task
        assert sum(url.endswith("/0") for url, _ in cast_playback.loads) == 1
        assert any(url.endswith("/1") and kwargs["enqueue"] for url, kwargs in cast_playback.loads)
    finally:
        await controller.close()
