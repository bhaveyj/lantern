import asyncio
from ipaddress import IPv4Interface
from pathlib import Path

import pytest
from aiohttp import ClientSession

from lantern import watch
from lantern.controller import Controller
from lantern.media import GIB, Probe, browser_encoding_budget, browser_video_mode, file_version, probe, tool_path


LOOPBACK = IPv4Interface("127.0.0.1/24")


def playlist_item(path: Path, subtitles=None):
    return {"id": path.stem, "path": str(path), "title": "Movie <test>", "subtitles": subtitles or []}


async def test_watch_link_serves_only_its_movie_and_captions(tmp_path, monkeypatch):
    monkeypatch.setattr(watch, "lan_ipv4", lambda address: address.is_loopback)
    movie = tmp_path / "movie.mp4"
    movie.write_bytes(bytes(range(100)))
    captions = tmp_path / "movie.srt"
    captions.write_text("1\n00:00:01,000 --> 00:00:02,000\nHello!\n", encoding="utf-8")
    item = playlist_item(movie, [{"id": "en", "label": 'English "CC"', "path": str(captions)}])
    server = watch.WatchServer()
    await server.start(LOOPBACK)
    try:
        url = server.publish(item, movie, file_version(movie), movie)
        assert server.existing_url(item, file_version(movie)) == url
        async with ClientSession() as client:
            async with client.get(url) as response:
                assert response.status == 200
                assert response.headers["Referrer-Policy"] == "no-referrer"
                page = await response.text()
                assert "Movie &lt;test&gt;" in page and "English &quot;CC&quot;" in page
                assert str(tmp_path) not in page
            async with client.get(url + "/video", headers={"Range": "bytes=30-39"}) as response:
                assert response.status == 206
                assert response.headers["Content-Range"] == "bytes 30-39/100"
                assert response.content_type == "video/mp4"
                assert await response.read() == bytes(range(30, 40))
            async with client.get(url + "/captions/0") as response:
                assert response.content_type == "text/vtt"
                assert "Hello!" in await response.text()
            assert (await client.get(url + "/captions/1")).status == 404
            assert (await client.get(url + "invalid/video")).status == 404
            assert (await client.get(url, headers={"Host": "foreign.example"})).status == 403
            assert (await client.get(url, headers={"Origin": "https://foreign.example"})).status == 403
            server.interface = IPv4Interface("127.1.0.1/24")
            assert (await client.get(url)).status == 403
            server.interface = LOOPBACK
            server.sync([playlist_item(movie)])
            assert (await client.get(url + "/video")).status == 404
            assert (await client.get(url + "/captions/0")).status == 404
    finally:
        await server.close()


async def test_watch_expiry_and_replaced_source_invalidate_links(tmp_path, monkeypatch):
    monkeypatch.setattr(watch, "lan_ipv4", lambda address: address.is_loopback)
    monkeypatch.setattr(watch, "LINK_LIFETIME", 0.03)
    movie = tmp_path / "movie.mp4"
    movie.write_bytes(b"original")
    server = watch.WatchServer()
    await server.start(LOOPBACK)
    try:
        server.publish(playlist_item(movie), movie, file_version(movie), movie)
        await asyncio.sleep(0.06)
        assert not server.links
        monkeypatch.setattr(watch, "LINK_LIFETIME", 24 * 60 * 60)
        url = server.publish(playlist_item(movie), movie, file_version(movie), movie)
        movie.write_bytes(b"replacement")
        async with ClientSession() as client:
            assert (await client.get(url + "/video")).status == 404
        assert not server.links
    finally:
        await server.close()


@pytest.mark.parametrize(("codec", "suffix", "mode", "transfer"), [
    ("libx264", ".mp4", "direct", None),
    ("libx264", ".mkv", "remux", None),
    ("libvpx-vp9", ".webm", "video", None),
    ("libx265", ".mkv", "video", "smpte2084"),
    ("libx265", ".mkv", "video", "arib-std-b67"),
])
async def test_copy_watch_link_with_real_media(tmp_path, monkeypatch, codec, suffix, mode, transfer):
    monkeypatch.setattr(watch, "lan_ipv4", lambda address: address.is_loopback)
    try:
        ffmpeg = tool_path("ffmpeg")
    except RuntimeError:
        pytest.skip("FFmpeg tools are unavailable")
    source = tmp_path / f"sample{suffix}"
    color_args = ["-pix_fmt", "yuv420p"]
    if transfer:
        color_args = [
            "-vf", f"format=yuv420p10le,setparams=range=limited:color_primaries=bt2020:color_trc={transfer}:colorspace=bt2020nc",
            "-x265-params", f"pools=1:frame-threads=1:log-level=error:colorprim=bt2020:transfer={transfer}:colormatrix=bt2020nc",
        ]
    process = await asyncio.create_subprocess_exec(
        ffmpeg, "-v", "error", "-f", "lavfi", "-i", "color=c=blue:s=320x180:r=24:d=1",
        "-c:v", codec, *color_args, str(source), stderr=asyncio.subprocess.PIPE,
    )
    _, error = await process.communicate()
    assert process.returncode == 0, error.decode()
    events = []
    controller = Controller(tmp_path / "cache", lambda event, data: events.append((event, data)))
    await controller.start()
    try:
        item = playlist_item(source)
        await controller.sync([item])
        await controller.copy_watch_link(item["id"], str(LOOPBACK))
        link = next((data for event, data in events if event == "watch.ready"), None)
        assert link is not None, events
        assert link["mode"] == mode
        prepared = next(iter(controller.watch.links.values())).video
        output = await probe(prepared)
        assert output.video == "h264"
        if transfer:
            assert (await probe(source)).is_hdr
            assert output.color_transfer == "bt709"
            assert output.pixel_format == "yuv420p"
        async with ClientSession() as client:
            await controller.stop()
            async with client.get(link["url"] + "/video") as response:
                assert response.status == 200
                assert await response.read() == prepared.read_bytes()
            if mode == "direct":
                assert prepared == source
            await controller.revoke_watch_link(item["id"])
            assert (await client.get(link["url"] + "/video")).status == 404
    finally:
        await controller.close()


def test_browser_video_plan_preserves_h264_and_detects_hdr():
    assert browser_video_mode(Probe("h264", "aac", 30, "mov,mp4", 2, pixel_format="yuv420p"), ".mp4") == "direct"
    assert browser_video_mode(Probe("h264", "dts", 30, "matroska", 6, pixel_format="yuv420p"), ".mkv") == "audio"
    assert browser_video_mode(Probe("hevc", "aac", 30, "matroska", 2, color_transfer="smpte2084"), ".mkv") == "video"


def test_large_hdr_movie_budget_is_based_on_output_rate():
    source_size = 86_000_000_000
    quota = 10 * GIB
    info = Probe("hevc", "eac3", 8700, "matroska", 6, color_transfer="smpte2084")
    bitrate, estimate = browser_encoding_budget(info, source_size, quota, copy_audio=False)
    assert bitrate == 8_000_000
    assert 8 * GIB < estimate < quota < source_size
