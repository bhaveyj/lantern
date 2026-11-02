"""Browser handoff lifecycle, independent of an internet provider or real TV."""
import asyncio
from types import SimpleNamespace

import pytest

from lantern.controller import Controller, Device
from lantern.media import Probe


class Relay:
    def __init__(self):
        self.stream = None

    async def register(self, source, media_id=None):
        self.stream = SimpleNamespace(id="web-video", session_id=source["sessionId"], title=source["title"], mime_type="application/vnd.apple.mpegurl", live=True)
        return self.stream

    async def refresh(self, source):
        return self.stream

    def has(self, media_id):
        return self.stream is not None and self.stream.id == media_id

    def reset(self):
        self.stream = None

    async def close(self):
        self.reset()


class Socket:
    closed = False

    async def close(self):
        self.closed = True


def source():
    return {"url": "https://video.example/live.m3u8?secret=token", "title": "Live football", "sessionId": "browser-session", "headersByOrigin": {"https://video.example": {"Cookie": "session=private"}}}


async def connected_controller(tmp_path, monkeypatch):
    events, messages = [], []
    controller = Controller(tmp_path / "cache", lambda name, data: events.append((name, data)))
    controller.server.web = Relay()
    await controller.start()
    controller.device = Device("tv", "Living room", "127.0.0.1", 8765, "receiver")
    controller.ws = Socket()
    controller.web_stream_version = 1
    async def send(message):
        messages.append(message)
    monkeypatch.setattr(controller, "send", send)
    return controller, events, messages


async def test_browser_requires_connected_tv_and_does_not_persist_source(tmp_path):
    controller = Controller(tmp_path / "cache", lambda *args: None)
    try:
        assert not (await controller.browser_message({"action": "status"}))["connected"]
        with pytest.raises(ValueError, match="connect to your TV"):
            await controller.browser_message({"action": "play", "source": source()})
        with pytest.raises(ValueError, match="Unknown"):
            await controller.browser_message({"action": "fetch", "source": source()})
        assert controller.playlist == []
        assert controller.web_item is None
    finally:
        await controller.close()


async def test_browser_handoff_survives_library_sync_and_revokes_on_stop(tmp_path, monkeypatch):
    controller, events, messages = await connected_controller(tmp_path, monkeypatch)
    try:
        reply = await controller.browser_message({"action": "play", "source": source()})
        assert reply["ok"]
        assert controller.current_id == "web-video"
        item = next(m["item"] for m in messages if m["type"] == "playback.load")
        assert item["live"] and item["seekable"]  # The player determines DVR availability.
        assert item["mimeType"] == "application/vnd.apple.mpegurl"
        assert "video.example" not in str(item)
        assert "private" not in str(reply)
        assert not controller.playlist  # The file library never owns web sessions.
        await controller.sync([])
        assert controller.wire_playlist()[0]["id"] == "web-video"
        token = controller.server.token
        await controller.stop()
        assert controller.web_item is None
        assert controller.server.token != token
        assert not controller.server.web.has("web-video")
        assert controller.wire_playlist() == []
    finally:
        await controller.close()


async def test_old_browser_tab_cannot_refresh_or_stop_current_video(tmp_path, monkeypatch):
    controller, _, messages = await connected_controller(tmp_path, monkeypatch)
    try:
        await controller.browser_message({"action": "play", "source": source()})
        stale = {**source(), "sessionId": "other-tab"}
        reply = await controller.browser_message({"action": "stop", "source": stale})
        assert not reply["stopped"]
        with pytest.raises(ValueError, match="no longer"):
            await controller.browser_message({"action": "refresh", "source": stale})
        assert controller.current_id == "web-video"
        await controller.browser_message({"action": "refresh", "source": source()})
        messages.clear()
        controller.receiver_state({"id": "web-video", "playing": True, "live": True, "seekable": False})
        await controller.command("seek", 30)
        assert not messages  # No seek until the TV reports a seekable live window.
        controller.receiver_state({"id": "web-video", "playing": True, "live": True, "seekable": True})
        await controller.command("seek", 30)
        await controller.command("live")
        assert [m["action"] for m in messages] == ["seek", "live"]
        reply = await controller.browser_message({"action": "stop", "source": source()})
        assert reply["stopped"]
    finally:
        await controller.close()


async def test_starting_local_file_closes_browser_session(tmp_path, monkeypatch):
    controller, _, _ = await connected_controller(tmp_path, monkeypatch)
    media = tmp_path / "movie.mp4"
    media.touch()
    async def probe(path):
        return Probe("h264", "aac", 60, "mp4")
    monkeypatch.setattr("lantern.controller.probe", probe)
    controller.audio_mimes = ["audio/mp4a-latm"]
    try:
        await controller.sync([{"id": "local", "title": "Movie", "path": str(media), "subtitles": []}])
        await controller.browser_message({"action": "play", "source": source()})
        await controller.play("local")
        assert controller.current_id == "local"
        assert controller.web_item is None
        assert not controller.server.web.has("web-video")
        assert controller.server.files["local"] == media
    finally:
        await controller.close()


async def test_stop_cancels_in_progress_browser_preparation(tmp_path, monkeypatch):
    controller, _, _ = await connected_controller(tmp_path, monkeypatch)
    entered = asyncio.Event()
    async def register(source, media_id=None):
        entered.set()
        await asyncio.Event().wait()
    monkeypatch.setattr(controller.server.web, "register", register)
    task = asyncio.create_task(controller.browser_message({"action": "play", "source": source()}))
    try:
        await asyncio.wait_for(entered.wait(), 1)
        result = await asyncio.wait_for(controller.browser_message({"action": "stop", "source": {"sessionId": "browser-session"}}), 2)
        assert result["stopped"]
        assert task.cancelled()
        assert not controller.web_item
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await controller.close()


async def test_browser_rejects_old_receiver_and_unfinished_pairing(tmp_path, monkeypatch):
    controller, _, _ = await connected_controller(tmp_path, monkeypatch)
    try:
        controller.web_stream_version = 0
        with pytest.raises(ValueError, match="updated Lantern TV APK"):
            await controller.browser_message({"action": "play", "source": source()})
        controller.ws.closed = True
        assert not (await controller.browser_message({"action": "status"}))["connected"]
        with pytest.raises(ValueError, match="connect to your TV"):
            await controller.browser_message({"action": "play", "source": source()})
    finally:
        await controller.close()


async def test_native_handoff_delivers_rewritten_media_and_revokes_it(tmp_path, monkeypatch):
    import aiohttp
    from lantern.browser_bridge import forward_message
    from lantern.web_stream import WebRelay, HLS_MIME
    from test_web_stream import fixture_relay

    controller, _, messages = await connected_controller(tmp_path, monkeypatch)
    controller.server.web = WebRelay()
    address = "https://video.example/live.m3u8?token=private"
    fixture_relay(monkeypatch, controller.server.web, {
        address: {"body": "#EXTM3U\n#EXT-X-TARGETDURATION:6\n#EXTINF:6,\nsegment.ts\n", "headers": {"Content-Type": HLS_MIME}},
        "https://video.example/segment.ts": {"body": b"media-segment", "headers": {"Content-Type": "video/mp2t", "Content-Length": "13"}},
    })
    selected = {**source(), "url": address, "mimeType": HLS_MIME}
    try:
        reply = await asyncio.to_thread(forward_message, tmp_path, {"action": "play", "source": selected})
        assert reply["ok"]
        load = next(m["item"] for m in messages if m["type"] == "playback.load")
        assert load["live"] and load["mimeType"] == HLS_MIME
        async with aiohttp.ClientSession() as client:
            response = await client.get(load["url"])
            playlist = await response.text()
            assert response.status == 200 and "private" not in playlist
            route = next(line for line in playlist.splitlines() if line.startswith("/stream/"))
            base = load["url"].split("/stream/", 1)[0]
            response = await client.get(base + route)
            assert await response.read() == b"media-segment"
            result = await asyncio.to_thread(forward_message, tmp_path, {"action": "stop", "source": {"sessionId": selected["sessionId"]}})
            assert result["stopped"]
            response = await client.get(base + route)
            assert response.status == 404
    finally:
        await controller.close()


async def test_token_renewal_reloads_root_without_losing_source_id(tmp_path, monkeypatch):
    controller, _, messages = await connected_controller(tmp_path, monkeypatch)
    try:
        await controller.browser_message({"action": "play", "source": source()})
        messages.clear()
        await controller.browser_message({"action": "refresh", "source": source()})
        assert not messages  # Credential refresh does not interrupt playback.
        renewed = {**source(), "url": "https://video.example/live.m3u8?secret=renewed"}
        await controller.browser_message({"action": "refresh", "source": renewed})
        load = next(m["item"] for m in messages if m["type"] == "playback.load")
        assert load["id"] == "web-video" and load["position"] == 0
        assert controller.current_id == "web-video"
    finally:
        await controller.close()
