import asyncio
import gzip
import io
import socket
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest
from aiohttp import ClientSession
from multidict import CIMultiDict
from yarl import URL

from lantern.server import MediaServer
from lantern.web_stream import HLS_MIME, PublicResolver, WebRelay, WebStreamError, checked_headers, checked_url


class Body:
    def __init__(self, body=b"", *, started=None, release=None):
        self.body = body
        self.started, self.release = started, release

    async def iter_chunked(self, size):
        if self.started:
            self.started.set()
        if self.release:
            await self.release.wait()
        for offset in range(0, len(self.body), size):
            yield self.body[offset:offset + size]


class Response:
    def __init__(self, url, body=b"", status=200, headers=None, **kwargs):
        self.url = URL(url)
        self.status = status
        self.headers = CIMultiDict(headers or {})
        self.content = Body(body.encode() if isinstance(body, str) else body, **kwargs)
        self.closed = False

    def close(self):
        self.closed = True


class Upstream:
    def __init__(self, routes):
        self.routes, self.calls, self.responses = routes, [], []

    async def request(self, method, address, *, headers, allow_redirects):
        assert method == "GET" and not allow_redirects
        address = str(address)
        self.calls.append((address, headers))
        assert address in self.routes, address
        entry = self.routes[address]
        response = entry(address, headers) if callable(entry) else Response(address, **entry)
        self.responses.append(response)
        return response


def fixture_relay(monkeypatch, relay, routes):
    upstream = Upstream(routes)
    async def session():
        return upstream
    monkeypatch.setattr(relay, "_get_session", session)
    return upstream


def source(url="https://video.example/live.m3u8", **kwargs):
    return {"url": url, "title": "Football", "mimeType": HLS_MIME, "sessionId": "test-browser-session", **kwargs}


async def test_pasted_link_prefers_a_compatible_hls_variant(monkeypatch):
    root = "https://video.example/master.m3u8"
    compatible = "https://video.example/hd.m3u8"
    master = """#EXTM3U
#EXT-X-STREAM-INF:BANDWIDTH=23000000,RESOLUTION=3840x2160,VIDEO-RANGE=PQ,CODECS="hvc1.2.4.L153.90"
hdr.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=4500000,RESOLUTION=1920x1080,CODECS="avc1.640028,mp4a.40.2"
hd.m3u8
"""
    relay = WebRelay()
    upstream = fixture_relay(monkeypatch, relay, {
        root: {"body": master},
        compatible: {"body": "#EXTM3U\n#EXT-X-TARGETDURATION:6\n#EXTINF:6,\nsegment.ts\n#EXT-X-ENDLIST\n"},
    })
    try:
        stream = await relay.register(source(root, preferCompatible=True))
        rewritten, children, _ = relay._rewrite_manifest(stream, relay.resources[stream.id], master, root, lambda rid: "/" + rid)
        assert [child.url for child in children] == [compatible]
        assert "3840x2160" not in rewritten
        assert [address for address, _ in upstream.calls] == [root, compatible]
    finally:
        await relay.close()


async def test_web_subtitles_are_fetched_lazily_and_revoked_with_session(monkeypatch):
    root = "https://video.example/movie.mp4"
    captions = "https://captions.example/english?key=private"
    server = MediaServer()
    upstream = fixture_relay(monkeypatch, server.web, {
        root: {"headers": {"Content-Type": "video/mp4"}},
        captions: {"body": gzip.compress(b"1\r\n00:00:02,000 --> 00:00:04,000\r\nHello\r\n"), "headers": {"Content-Encoding": "gzip"}},
    })
    await server.start()
    try:
        data = source(root, mimeType="video/mp4", headersByOrigin={"https://video.example": {"Cookie": "video=secret"}},
                      subtitles=[{"url": "https://127.0.0.1/forbidden"}, {"url": captions, "label": "English"}])
        stream = await server.web.register(data)
        assert [address for address, _ in upstream.calls] == [root]
        (track,) = stream.subtitle_items()
        assert track["label"] == "English" and "private" not in str(track)
        await server.web.refresh(data)
        assert stream.subtitle_items() == [track]
        url = server.subtitle_url("127.0.0.1", track["id"])
        async with ClientSession() as client:
            async with client.get(url) as response:
                assert response.content_type == "text/vtt"
                assert await response.text() == "WEBVTT\n\n1\n00:00:02.000 --> 00:00:04.000\nHello\n"
            assert upstream.calls[-1] == (captions, {})
            server.reset()
            async with client.get(url) as response:
                assert response.status == 404
    finally:
        await server.stop()


@pytest.mark.parametrize("address", [
    "http://video.example/live.m3u8", "file:///movie.mp4", "https://127.0.0.1/x", "https://10.0.0.1/x",
    "https://169.254.169.254/x", "https://[::1]/x", "https://[fc00::1]/x", "https://224.0.0.1/x",
    "https://2130706433/x", "https://0x7f000001/x", "https://127.1/x", "https://localhost/x",
    "https://router.lan/x", "https://host.local/x", "https://user:secret@video.example/x",
    "https://video.example/x#fragment", "https://video.example/\r\nInjected: x", "https://[fe80::1%25eth0]/x",
])
def test_media_urls_cannot_target_local_services(address):
    with pytest.raises(WebStreamError):
        checked_url(address)


async def test_dns_resolution_checks_the_addresses_given_to_the_connector(monkeypatch):
    resolver = PublicResolver()
    records = [{"hostname": "video.example", "host": "93.184.216.34", "port": 443, "family": socket.AF_INET, "proto": 0, "flags": 0}]
    async def resolve(*args):
        return records
    monkeypatch.setattr(resolver._resolver, "resolve", resolve)
    assert await resolver.resolve("video.example", 443) == records
    records.append({**records[0], "host": "192.168.1.1"})
    with pytest.raises(OSError, match="public internet"):
        await resolver.resolve("video.example", 443)
    await resolver.close()


def test_browser_credentials_are_validated_and_exact_origin_scoped():
    headers = checked_headers({"https://video.example:443": {"Cookie": "session=secret", "Host": "attacker.example", "Proxy-Authorization": "secret"}})
    assert headers == {"https://video.example": {"cookie": "session=secret"}}
    for invalid in ({"https://video.example/path": {}}, {"https://video.example": {"Cookie": "x\r\ny"}}, {"https://127.0.0.1": {}}):
        with pytest.raises(WebStreamError):
            checked_headers(invalid)


async def test_root_headers_do_not_overwrite_same_origin_segment_context(monkeypatch):
    relay = WebRelay()
    address = "https://video.example/live.m3u8"
    upstream = fixture_relay(monkeypatch, relay, {
        address: {"body": "#EXTM3U\n#EXT-X-TARGETDURATION:6\n#EXTINF:6,\nsegments/one.ts\n", "headers": {"Content-Type": HLS_MIME}},
        "https://video.example/segments/one.ts": {"body": b"segment"},
    })
    try:
        stream = await relay.register(source(address, rootHeaders={"Cookie": "path=root"}, headersByOrigin={"https://video.example": {"Cookie": "path=segment"}}))
        assert upstream.calls[0][1]["cookie"] == "path=root"
        response = await relay._fetch(stream, "https://video.example/segments/one.ts")
        response.close()
        assert upstream.calls[-1][1]["cookie"] == "path=segment"
        relay.reset()
        assert not stream.root_headers and not stream.headers
    finally:
        await relay.close()


async def test_nested_adaptive_hls_routes_all_resources_through_paired_server(aiohttp_client, monkeypatch):
    server = MediaServer()
    root = "https://video.example/master.m3u8?token=secret"
    master = """#EXTM3U
#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="audio",NAME="English",URI="https://audio.example/audio.m3u8"
#EXT-X-STREAM-INF:BANDWIDTH=1000000,AUDIO="audio"
low/index.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=4000000,AUDIO="audio"
high/index.m3u8
"""
    audio = "#EXTM3U\n#EXT-X-TARGETDURATION:6\n#EXTINF:6,\naudio.aac\n"
    video = """#EXTM3U
#EXT-X-TARGETDURATION:6
#EXT-X-KEY:METHOD=AES-128,URI="https://keys.example/key.bin",IV=0x00000000000000000000000000000001
#EXT-X-MAP:URI="init.mp4",BYTERANGE="20@0"
#EXTINF:6,
#EXT-X-BYTERANGE:100@20
segment.mp4
#EXT-X-DISCONTINUITY
#EXTINF:6,
segment-2.ts
"""
    upstream = fixture_relay(monkeypatch, server.web, {
        root: {"body": master, "headers": {"Content-Type": HLS_MIME}},
        "https://audio.example/audio.m3u8": {"body": audio, "headers": {"Content-Type": HLS_MIME}},
        "https://video.example/high/index.m3u8": {"body": video, "headers": {"Content-Type": HLS_MIME}},
        "https://video.example/high/init.mp4": {"body": b"init", "headers": {"Content-Type": "video/mp4", "Content-Length": "4"}},
    })
    stream = await server.web.register(source(root, headersByOrigin={"https://video.example": {"Cookie": "session=secret"}, "https://audio.example": {"Authorization": "Bearer audio"}}))
    assert stream.live and stream.mime_type == HLS_MIME
    assert upstream.calls[0][1]["cookie"] == "session=secret"
    assert upstream.calls[1][1] == {"authorization": "Bearer audio"}
    client = await aiohttp_client(server.app)
    response = await client.get(f"/stream/{server.token}/{stream.id}")
    rewritten = await response.text()
    assert response.content_type == HLS_MIME
    assert "https://" not in rewritten and "secret" not in rewritten
    assert "BANDWIDTH=1000000" in rewritten and "BANDWIDTH=4000000" in rewritten
    high = next(resource for resource in server.web.resources.values() if resource.url.endswith("high/index.m3u8"))
    response = await client.get(f"/stream/{server.token}/{high.id}")
    rewritten = await response.text()
    assert "https://" not in rewritten and "#EXT-X-DISCONTINUITY" in rewritten
    assert 'BYTERANGE="20@0"' in rewritten and "#EXT-X-BYTERANGE:100@20" in rewritten
    assert len([resource for resource in server.web.resources.values() if resource.url == "https://keys.example/key.bin"]) == 1
    init = next(resource for resource in server.web.resources.values() if resource.url.endswith("high/init.mp4"))
    server.cast_compatibility = True
    response = await client.get(f"/stream/{server.token}/{init.id}", headers={"Origin": "https://www.gstatic.com"})
    assert await response.read() == b"init"
    assert response.headers["Access-Control-Allow-Origin"] == "https://www.gstatic.com"
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert "cookie" in upstream.calls[-1][1]
    await server.web.close()


async def test_progressive_range_streaming_preserves_headers_and_never_sends_browser_cookies_to_tv(aiohttp_client, monkeypatch):
    server = MediaServer()
    root = "https://video.example/movie.mp4"
    body = bytes(range(100))
    def movie(address, headers):
        value = headers.get("Range", "bytes=0-")
        first, _, last = value.removeprefix("bytes=").partition("-")
        start, stop = int(first), int(last) + 1 if last else len(body)
        return Response(address, body[start:stop], status=206, headers={"Content-Type": "video/mp4", "Content-Length": str(len(body[start:stop])), "Content-Range": f"bytes {start}-{min(stop, len(body)) - 1}/100", "Accept-Ranges": "bytes", "Set-Cookie": "source-secret=1"})
    fixture_relay(monkeypatch, server.web, {root: movie})
    stream = await server.web.register(source(root, mimeType="video/mp4", headersByOrigin={"https://video.example": {"Cookie": "browser-secret=1"}}))
    assert not stream.live
    client = await aiohttp_client(server.app)
    route = f"/stream/{server.token}/{stream.id}"
    response = await client.get(route, headers={"Range": "bytes=10-19"})
    assert response.status == 206 and await response.read() == body[10:20]
    assert response.headers["Content-Range"] == "bytes 10-19/100"
    assert "Set-Cookie" not in response.headers and "Cookie" not in response.headers
    response = await client.head(route, headers={"Range": "bytes=10-19"})
    assert response.status == 206 and response.headers["Content-Length"] == "10"
    assert await response.read() == b""
    assert (await client.get(route, headers={"Range": "bytes=1-2,3-4"})).status == 416
    await server.web.close()


async def test_redirects_validate_each_target_and_do_not_forward_credentials(monkeypatch):
    relay = WebRelay()
    upstream = fixture_relay(monkeypatch, relay, {
        "https://video.example/movie.mp4": {"status": 302, "headers": {"Location": "https://cdn.example/movie.mp4"}},
        "https://cdn.example/movie.mp4": {"body": b"movie", "headers": {"Content-Type": "video/mp4"}},
    })
    await relay.register(source("https://video.example/movie.mp4", mimeType="video/mp4", headersByOrigin={"https://video.example": {"Cookie": "private=1", "Authorization": "Bearer secret"}}))
    assert upstream.calls[0][1]["cookie"] == "private=1"
    assert "cookie" not in upstream.calls[1][1] and "authorization" not in upstream.calls[1][1]
    relay.reset()
    upstream.routes["https://video.example/movie.mp4"] = {"status": 302, "headers": {"Location": "https://169.254.169.254/metadata"}}
    with pytest.raises(WebStreamError, match="public HTTPS"):
        await relay.register(source("https://video.example/movie.mp4", mimeType="video/mp4"))
    assert not relay.streams and not relay.resources
    await relay.close()


async def test_live_reload_reuses_ids_and_keeps_only_complete_segments(aiohttp_client, monkeypatch):
    server = MediaServer()
    root = "https://video.example/live.m3u8"
    playlist = """#EXTM3U
#EXT-X-TARGETDURATION:6
#EXT-X-SERVER-CONTROL:CAN-BLOCK-RELOAD=YES,CAN-SKIP-UNTIL=36
#EXT-X-PART-INF:PART-TARGET=1
#EXTINF:6,
segment.ts
#EXT-X-PART:DURATION=1,URI="part.mp4"
#EXT-X-PRELOAD-HINT:TYPE=PART,URI="next-part.mp4"
#EXT-X-RENDITION-REPORT:URI="other.m3u8",LAST-MSN=1
"""
    fixture_relay(monkeypatch, server.web, {root: {"body": playlist, "headers": {"Content-Type": HLS_MIME}}})
    stream = await server.web.register(source())
    client = await aiohttp_client(server.app)
    route = f"/stream/{server.token}/{stream.id}"
    for _ in range(3):
        response = await client.get(route)
        body = await response.text()
        assert "SERVER-CONTROL" not in body and "PART" not in body and "RENDITION-REPORT" not in body
        assert "#EXTINF:6," in body
        assert len(server.web.resources) == 2
    await server.web.close()


async def test_refresh_keeps_local_id_and_changes_only_current_session_context(aiohttp_client, monkeypatch):
    server = MediaServer()
    old, new = "https://video.example/live.m3u8?token=old", "https://video.example/live.m3u8?token=new"
    playlist = "#EXTM3U\n#EXT-X-TARGETDURATION:6\n#EXTINF:6,\nsegment.ts\n"
    upstream = fixture_relay(monkeypatch, server.web, {url: {"body": playlist, "headers": {"Content-Type": HLS_MIME}} for url in (old, new)})
    stream = await server.web.register(source(old, headersByOrigin={"https://video.example": {"Cookie": "old-cookie"}}))
    client = await aiohttp_client(server.app)
    refreshed = await server.web.refresh(source(new, headersByOrigin={"https://video.example": {"Cookie": "new-cookie"}}))
    assert refreshed is stream
    response = await client.get(f"/stream/{server.token}/{stream.id}")
    assert response.status == 200
    assert upstream.calls[-1] == (new, {"cookie": "new-cookie"})
    server.reset()
    assert not stream.headers
    with pytest.raises(WebStreamError, match="ended"):
        await server.web.refresh(source(new))
    async with ClientSession() as fresh:
        assert (await fresh.get(client.make_url(f"/stream/{server.token}/{stream.id}"))).status == 404
    await server.web.close()


async def test_stop_cancels_pending_upstream_and_clears_credentials(monkeypatch):
    relay = WebRelay()
    started, release = asyncio.Event(), asyncio.Event()
    root = "https://video.example/live.m3u8"
    upstream = fixture_relay(monkeypatch, relay, {root: {"body": "#EXTM3U\n", "headers": {"Content-Type": HLS_MIME}, "started": started, "release": release}})
    task = asyncio.create_task(relay.register(source(headersByOrigin={"https://video.example": {"Cookie": "secret"}})))
    await asyncio.wait_for(started.wait(), 1)
    stream = next(iter(relay.streams.values()))
    relay.reset()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not relay.resources and not relay.streams and not stream.headers
    assert all(response.closed for response in upstream.responses)
    await relay.close()


@pytest.mark.parametrize("playlist, message", [
    ("#EXTM3U\n#EXT-X-TARGETDURATION:6\n#EXT-X-PART:DURATION=1,URI=\"part.mp4\"\n", "no complete"),
    ("#EXTM3U\n#EXT-X-SKIP:SKIPPED-SEGMENTS=4\n#EXTINF:6,\nsegment.ts\n", "Delta"),
    ("#EXTM3U\n#EXT-X-KEY:METHOD=SAMPLE-AES,URI=\"key\"\n#EXTINF:6,\nsegment.ts\n", "content protection"),
    ("#EXTM3U\n#EXTINF:6,\nhttps://127.0.0.1/private.ts\n", "public HTTPS"),
    ("<html>Advertisement</html>", "valid HLS"),
])
async def test_invalid_or_unsupported_playlists_fail_without_retaining_session(monkeypatch, playlist, message):
    relay = WebRelay()
    fixture_relay(monkeypatch, relay, {"https://video.example/live.m3u8": {"body": playlist, "headers": {"Content-Type": HLS_MIME}}})
    with pytest.raises(WebStreamError, match=message):
        await relay.register(source())
    assert not relay.resources and not relay.streams
    await relay.close()


async def test_manifest_and_resource_limits_are_bounded(monkeypatch):
    import lantern.web_stream as module
    relay = WebRelay()
    monkeypatch.setattr(module, "MAX_MANIFEST", 100)
    fixture_relay(monkeypatch, relay, {"https://video.example/live.m3u8": {"body": "#EXTM3U\n" + "# large\n" * 100, "headers": {"Content-Type": HLS_MIME}}})
    with pytest.raises(WebStreamError, match="too large"):
        await relay.register(source())
    assert not relay.resources
    monkeypatch.setattr(module, "MAX_MANIFEST", 2048)
    monkeypatch.setattr(module, "MAX_RESOURCES", 3)
    fixture_relay(monkeypatch, relay, {"https://video.example/live.m3u8": {"body": "#EXTM3U\n#EXT-X-TARGETDURATION:6\n#EXTINF:6,\n1.ts\n#EXTINF:6,\n2.ts\n#EXTINF:6,\n3.ts\n", "headers": {"Content-Type": HLS_MIME}}})
    with pytest.raises(WebStreamError, match="too many resources"):
        await relay.register(source())
    assert not relay.resources
    await relay.close()


async def test_variables_expand_relative_children_without_exposing_token(monkeypatch):
    relay = WebRelay()
    master = '#EXTM3U\n#EXT-X-DEFINE:NAME="token",VALUE="secret"\n#EXT-X-STREAM-INF:BANDWIDTH=1000\nmedia.m3u8?token={$token}\n'
    child = '#EXTM3U\n#EXT-X-DEFINE:IMPORT="token"\n#EXT-X-TARGETDURATION:6\n#EXTINF:6,\nsegment.ts?token={$token}\n#EXT-X-ENDLIST\n'
    fixture_relay(monkeypatch, relay, {
        "https://video.example/live.m3u8": {"body": master, "headers": {"Content-Type": HLS_MIME}},
        "https://video.example/media.m3u8?token=secret": {"body": child, "headers": {"Content-Type": HLS_MIME}},
    })
    stream = await relay.register(source())
    assert not stream.live and stream.duration == 6
    assert any(resource.url == "https://video.example/segment.ts?token=secret" for resource in relay.resources.values())
    rewritten, _, _ = relay._rewrite_manifest(stream, relay.resources[stream.id], master, "https://video.example/live.m3u8", lambda resource_id: "/stream/token/" + resource_id)
    assert "secret" not in rewritten and "EXT-X-DEFINE" not in rewritten
    await relay.close()


@pytest.mark.parametrize("content_type", ["image/png", "application/octet-stream"])
async def test_wrapped_hls_ranges_and_head_use_decoded_bytes(aiohttp_client, monkeypatch, content_type):
    server = MediaServer()
    root, segment = "https://video.example/live.m3u8", "https://video.example/segment.png"
    transport = (b"\x47" + bytes(187)) * 5
    encoded = gzip.compress(transport)
    upstream = fixture_relay(monkeypatch, server.web, {
        root: {"body": "#EXTM3U\n#EXT-X-TARGETDURATION:6\n#EXTINF:6,\nsegment.png\n", "headers": {"Content-Type": HLS_MIME}},
        segment: {"body": encoded, "headers": {"Content-Type": content_type, "Content-Length": str(len(encoded)), "Set-Cookie": "secret=1"}},
    })
    try:
        await server.web.register(source())
        resource = next(r for r in server.web.resources.values() if r.url == segment)
        client = await aiohttp_client(server.app)
        route = f"/stream/{server.token}/{resource.id}"
        # Even the first request may begin inside the image envelope. Fetch its
        # start rather than sending the receiver's decoded offset upstream.
        response = await client.get(route, headers={"Range": "bytes=188-375"})
        assert response.status == 206 and await response.read() == transport[188:376]
        assert response.headers["Content-Type"] == "video/mp2t"
        assert response.headers["Content-Length"] == "188"
        assert response.headers["Content-Range"] == f"bytes 188-375/{len(transport)}"
        assert "Set-Cookie" not in response.headers
        assert resource.wrapped is True and "Range" not in upstream.calls[-1][1]
        response = await client.head(route, headers={"Range": "bytes=-188"})
        assert response.status == 206 and response.headers["Content-Length"] == "188"
        assert await response.read() == b""
        response = await client.get(route)
        assert await response.read() == transport
        assert response.headers["Content-Length"] == str(len(transport))
        response = await client.get(route, headers={"Range": "bytes=99999-"})
        assert response.status == 416 and response.headers["Content-Range"] == f"bytes */{len(transport)}"
        assert all("Range" not in headers for address, headers in upstream.calls if address == segment)
    finally:
        await server.web.close()


async def test_normal_hls_segment_inspection_preserves_ranges_and_streaming(aiohttp_client, monkeypatch):
    server = MediaServer()
    root, segment = "https://video.example/live.m3u8", "https://video.example/segment.ts"
    transport = (b"\x47" + bytes(187)) * 1000
    def media(address, headers):
        if "Range" in headers:
            assert headers["Range"] == "bytes=188-375"
            return Response(address, transport[188:376], status=206, headers={"Content-Type": "video/mp2t", "Content-Length": "188", "Content-Range": f"bytes 188-375/{len(transport)}"})
        return Response(address, transport, headers={"Content-Type": "video/mp2t"})
    upstream = fixture_relay(monkeypatch, server.web, {
        root: {"body": "#EXTM3U\n#EXT-X-TARGETDURATION:6\n#EXTINF:6,\nsegment.ts\n", "headers": {"Content-Type": HLS_MIME}}, segment: media,
    })
    try:
        await server.web.register(source())
        resource = next(r for r in server.web.resources.values() if r.url == segment)
        client = await aiohttp_client(server.app)
        route = f"/stream/{server.token}/{resource.id}"
        response = await client.get(route, headers={"Range": "bytes=188-375"})
        assert response.status == 206 and await response.read() == transport[188:376]
        assert resource.wrapped is False
        assert [headers for address, headers in upstream.calls if address == segment] == [{}, {"Range": "bytes=188-375"}]
        response = await client.get(route)
        assert response.status == 200 and await response.read() == transport
    finally:
        await server.web.close()


async def test_keys_init_and_encrypted_segments_are_never_unwrapped(aiohttp_client, monkeypatch):
    server = MediaServer()
    root = "https://video.example/live.m3u8"
    opaque = b"\x89PNG\r\n\x1a\nnot-an-image-or-media"
    playlist = '#EXTM3U\n#EXT-X-TARGETDURATION:6\n#EXT-X-KEY:METHOD=AES-128,URI="key.bin"\n#EXT-X-MAP:URI="init.mp4"\n#EXTINF:6,\nencrypted.ts\n#EXT-X-KEY:METHOD=NONE\n#EXTINF:6,\nclear.ts\n'
    fixture_relay(monkeypatch, server.web, {
        root: {"body": playlist, "headers": {"Content-Type": HLS_MIME}},
        **{f"https://video.example/{path}": {"body": opaque, "headers": {"Content-Type": "image/png"}} for path in ["key.bin", "init.mp4", "encrypted.ts"]},
    })
    try:
        await server.web.register(source())
        client = await aiohttp_client(server.app)
        for path, kind in [("key.bin", "key"), ("init.mp4", "init"), ("encrypted.ts", "encrypted-segment")]:
            resource = next(r for r in server.web.resources.values() if r.url.endswith(path))
            assert resource.kind == kind
            response = await client.get(f"/stream/{server.token}/{resource.id}")
            assert response.status == 200 and await response.read() == opaque
        assert next(r for r in server.web.resources.values() if r.url.endswith("clear.ts")).kind == "segment"
    finally:
        await server.web.close()


@pytest.mark.parametrize("body,limit,message", [(b"\x89PNG\r\n\x1a\ninvalid", 1024, "invalid or unsupported"), (b"\x1f\x8b" + bytes(150), 100, "too large")])
async def test_invalid_or_oversized_wrapper_returns_safe_error(aiohttp_client, monkeypatch, body, limit, message):
    import lantern.web_stream as module
    monkeypatch.setattr(module, "MAX_WRAPPED_INPUT", limit)
    server = MediaServer()
    root, segment = "https://video.example/live.m3u8", "https://video.example/segment.png?private=secret"
    fixture_relay(monkeypatch, server.web, {
        root: {"body": f"#EXTM3U\n#EXT-X-TARGETDURATION:6\n#EXTINF:6,\n{segment}\n", "headers": {"Content-Type": HLS_MIME}},
        segment: {"body": body, "headers": {"Content-Type": "image/png"}},
    })
    try:
        await server.web.register(source())
        resource = next(r for r in server.web.resources.values() if r.url == segment)
        client = await aiohttp_client(server.app)
        response = await client.get(f"/stream/{server.token}/{resource.id}")
        text = await response.text()
        assert response.status == 502 and message in text
        assert "secret" not in text and "https://" not in text
    finally:
        await server.web.close()


async def test_cancelled_decode_holds_worker_slot_until_bounded_worker_exits(monkeypatch):
    import lantern.web_stream as module
    from lantern.stream_transport import DecodedSegment
    relay = WebRelay()
    fixture_relay(monkeypatch, relay, {"https://video.example/live.m3u8": {"body": "#EXTM3U\n#EXTINF:6,\nsegment.ts\n", "headers": {"Content-Type": HLS_MIME}}})
    stream = await relay.register(source())
    resource = relay.resources[stream.id]
    started, release = threading.Event(), threading.Event()
    def decode(*args):
        started.set()
        release.wait(5)
        return DecodedSegment(b"decoded", "video/mp2t", True)
    monkeypatch.setattr(module, "unwrap_segment", decode)
    upstream = Response("https://video.example/segment.png", headers={"Content-Type": "image/png"})
    task = asyncio.create_task(relay._unwrap_response(SimpleNamespace(headers={}), stream, resource, upstream, b"wrapped", upstream.content.iter_chunked(65536)))
    try:
        async with asyncio.timeout(2):
            while not started.is_set():
                await asyncio.sleep(.01)
        relay.reset()
        task.cancel()
        await asyncio.sleep(.02)
        assert not task.done() and relay._transport_semaphore._value == 1
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert relay._transport_semaphore._value == 2
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
        await relay.close()


async def test_captured_png_segment_relays_decodable_h264_and_aac(aiohttp_client, monkeypatch):
    import av
    fixture = Path(__file__).resolve().parents[1] / ".tmp/ripple-wrapped-segment.bin"
    if not fixture.exists():
        pytest.skip("Live diagnostic fixture is intentionally not committed.")
    server = MediaServer()
    root, segment = "https://video.example/live.m3u8", "https://video.example/captured.image"
    encoded = fixture.read_bytes()
    fixture_relay(monkeypatch, server.web, {
        root: {"body": "#EXTM3U\n#EXT-X-TARGETDURATION:6\n#EXTINF:6,\ncaptured.image\n", "headers": {"Content-Type": HLS_MIME}},
        segment: {"body": encoded, "headers": {"Content-Type": "image/png", "Content-Length": str(len(encoded))}},
    })
    try:
        await server.web.register(source())
        resource = next(r for r in server.web.resources.values() if r.url == segment)
        client = await aiohttp_client(server.app)
        response = await client.get(f"/stream/{server.token}/{resource.id}")
        data = await response.read()
        assert response.status == 200 and response.headers["Content-Type"] == "video/mp2t"
        assert response.headers["Content-Length"] == str(len(data))
        with av.open(io.BytesIO(data)) as container:
            assert {s.codec_context.name for s in container.streams} == {"h264", "aac"}
    finally:
        await server.web.close()
