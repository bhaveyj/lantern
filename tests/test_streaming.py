import os
import subprocess
import gc
import threading
import weakref

from aiohttp import ClientSession

from lantern.server import MediaServer
from lantern.media import subtitle_vtt


async def test_media_rejects_other_peers_rebinding_and_browser_origins(aiohttp_client, tmp_path):
    path = tmp_path / "movie.mp4"
    path.write_bytes(b"media")
    server = MediaServer()
    server.files["movie"] = path
    client = await aiohttp_client(server.app)
    route = f"/stream/{server.token}/movie"
    assert (await client.get(route, headers={"Host": "attacker.example"})).status == 403
    assert (await client.get(route, headers={"Origin": "https://attacker.example"})).status == 403
    assert (await client.get(route, headers={"Origin": "null"})).status == 403
    server.allowed_peer = "192.168.1.10"
    assert (await client.get(route)).status == 403  # A stolen token is insufficient.
    server.allowed_peer = "127.0.0.1"
    server.cast_compatibility = True
    response = await client.get(route, headers={"Origin": "https://www.gstatic.com"})
    assert response.status == 200 and await response.read() == b"media"
    assert response.headers["Access-Control-Allow-Origin"] == "https://www.gstatic.com"
    assert (await client.options(route, headers={"Origin": "https://www.gstatic.com", "Access-Control-Request-Headers": "range"})).status == 204
    assert (await client.get(route, headers={"Origin": "https://attacker.example"})).status == 403


async def test_stream_requires_token_supports_range_and_rotates(aiohttp_client, tmp_path):
    path = tmp_path / "movie.mp4"
    path.write_bytes(bytes(range(100)))
    server = MediaServer()
    server.files["movie"] = path
    client = await aiohttp_client(server.app)
    response = await client.get("/stream/wrong/movie")
    assert response.status == 404
    route = f"/stream/{server.token}/movie"
    response = await client.get(route, headers={"Range": "bytes=10-19"})
    assert response.status == 206
    assert response.headers["Content-Range"] == "bytes 10-19/100"
    assert await response.read() == bytes(range(10, 20))
    assert "Access-Control-Allow-Origin" not in response.headers
    response = await client.head(route)
    assert response.status == 200
    assert response.headers["Content-Length"] == "100"
    server.reset()
    # Revocation deliberately closes prior streaming connections; use a new peer.
    async with ClientSession() as fresh:
        assert (await fresh.get(client.make_url(route))).status == 404


async def test_external_subtitle_conversion_and_missing_file(aiohttp_client, tmp_path):
    subtitle = tmp_path / "movie.srt"
    subtitle.write_text("1\n00:00:01,000 --> 00:00:02,500\nHello\n", encoding="utf-8-sig")
    server = MediaServer()
    server.subtitles["english"] = subtitle
    client = await aiohttp_client(server.app)
    response = await client.get(f"/subtitle/{server.token}/english")
    assert response.content_type == "text/vtt"
    assert "00:00:01.000 --> 00:00:02.500" in await response.text()
    assert (await client.get(f"/stream/{server.token}/../../Windows" )).status == 404


async def test_large_file_open_ranges_and_seek_beyond_four_gib(aiohttp_client, tmp_path):
    path = tmp_path / "large.mkv"
    path.touch()
    if os.name == "nt":
        # Avoid allocating or zero-filling gigabytes just for this regression.
        subprocess.run(["fsutil", "sparse", "setflag", str(path)], check=True, capture_output=True)
    offset = 5 * 1024**3
    start = b"Lantern large-file header"
    end = b"Lantern seek marker"
    with path.open("r+b") as file:
        file.write(start)
        file.seek(offset)
        file.write(end)
    size = offset + len(end)
    server = MediaServer()
    server.files["large"] = path
    client = await aiohttp_client(server.app)
    route = f"/stream/{server.token}/large"
    response = await client.head(route)
    assert response.headers["Content-Length"] == str(size)
    # These requests used to send a >2 GiB count to Windows TransmitFile and
    # close the connection before delivering even the first media header.
    for headers in ({}, {"Range": "bytes=0-"}):
        async with client.get(route, headers=headers) as response:
            assert response.status == (206 if headers else 200)
            assert response.headers["Content-Length"] == str(size)
            assert await response.content.readexactly(len(start)) == start
    async with client.get(route, headers={"Range": f"bytes={offset}-"}) as response:
        assert response.status == 206
        assert response.headers["Content-Range"] == f"bytes {offset}-{size - 1}/{size}"
        assert await response.read() == end


async def test_subtitle_conversion_is_cached_off_loop_and_invalidated(aiohttp_client, tmp_path, monkeypatch):
    source = tmp_path / "movie.srt"
    source.write_text("1\n00:00:01,000 --> 00:00:02,000\nOriginal\n", encoding="utf-8")
    calls = []
    event_loop_thread = threading.get_ident()
    def convert(path):
        calls.append(threading.get_ident())
        return subtitle_vtt(path)
    monkeypatch.setattr("lantern.server.subtitle_vtt", convert)
    server = MediaServer()
    server.subtitles["english"] = source
    client = await aiohttp_client(server.app)
    route = f"/subtitle/{server.token}/english"
    for _ in range(2):
        response = await client.get(route)
        assert "Original" in await response.text()
    assert len(calls) == 1
    assert calls[0] != event_loop_thread
    source.write_text("1\n00:00:01,000 --> 00:00:02,000\nUpdated captions\n", encoding="utf-8")
    response = await client.get(route)
    assert "Updated captions" in await response.text()
    assert len(calls) == 2


def test_finished_streaming_transports_are_not_retained():
    class Transport:
        def close(self): pass
    server = MediaServer()
    transport = Transport()
    reference = weakref.ref(transport)
    server.transports.add(transport)
    del transport
    gc.collect()
    assert reference() is None


async def test_stop_revokes_subtitle_request_during_conversion(aiohttp_client, tmp_path, monkeypatch):
    source = tmp_path / "movie.srt"
    source.write_text("captions", encoding="utf-8")
    server = MediaServer()
    server.subtitles["english"] = source
    started = threading.Event()
    release = threading.Event()
    def convert(path):
        started.set()
        release.wait(2)
        return "WEBVTT\n\nold session"
    monkeypatch.setattr("lantern.server.subtitle_vtt", convert)
    client = await aiohttp_client(server.app)
    import asyncio
    request = asyncio.create_task(client.get(f"/subtitle/{server.token}/english"))
    try:
        assert await asyncio.to_thread(started.wait, 1)
        server.reset()
        release.set()
        response = await request
        assert response.status == 404
        assert not server._subtitle_cache
    finally:
        release.set()
