import os
import subprocess

from aiohttp import ClientSession

from lantern.server import MediaServer


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
    assert response.headers["Access-Control-Allow-Origin"] == "*"
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
