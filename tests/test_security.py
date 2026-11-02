import asyncio
import hashlib
from pathlib import Path

import aiohttp
from aiohttp import web
import pytest

from lantern.controller import Controller, Device
from lantern.security import LocalIdentity, ReceiverConnectionError, ReceiverTrust, receiver_fingerprint
from dataclasses import asdict
from pairing_peer import accept


async def test_refused_receiver_reports_tcp_service_failure(unused_tcp_port):
    with pytest.raises(ReceiverConnectionError) as result:
        await receiver_fingerprint("127.0.0.1", unused_tcp_port)
    assert result.value.phase == "tcp" and result.value.reason == "refused"
    assert "Open Lantern TV" in str(result.value)
    assert "up to date" not in str(result.value)


async def test_unreachable_receiver_does_not_start_tls_or_leak_socket(monkeypatch):
    loop = asyncio.get_running_loop()
    sockets = []
    async def unreachable(connection, address):
        sockets.append(connection)
        raise OSError("network route unavailable")
    async def unexpected_tls(**kwargs):
        raise AssertionError("TLS must not start without a TCP connection")
    monkeypatch.setattr(loop, "sock_connect", unreachable)
    monkeypatch.setattr("lantern.security.asyncio.open_connection", unexpected_tls)
    with pytest.raises(ReceiverConnectionError) as result:
        await receiver_fingerprint("127.0.0.1", 8765)
    assert result.value.phase == "tcp" and result.value.reason == "unreachable"
    assert "same home network" in str(result.value)
    assert "up to date" not in str(result.value)
    assert sockets[0].fileno() == -1


async def test_receiver_tcp_timeout_is_not_reported_as_tls_failure(monkeypatch):
    async def stalled(connection, address):
        await asyncio.Event().wait()
    monkeypatch.setattr(asyncio.get_running_loop(), "sock_connect", stalled)
    monkeypatch.setattr("lantern.security.RECEIVER_CONNECT_TIMEOUT", .02)
    with pytest.raises(ReceiverConnectionError) as result:
        await receiver_fingerprint("127.0.0.1", 8765)
    assert result.value.phase == "tcp" and result.value.reason == "timeout"
    assert "did not respond" in str(result.value)
    assert "up to date" not in str(result.value)


async def test_plaintext_peer_reports_tls_failure_without_sending_pairing_data():
    received = []
    finished = asyncio.Event()
    async def plaintext(reader, writer):
        try:
            received.append(await reader.read(4096))
            writer.write(b"HTTP/1.1 400 Bad Request\r\nContent-Length: 0\r\n\r\n")
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()
            finished.set()
    server = await asyncio.start_server(plaintext, "127.0.0.1", 0)
    try:
        port = server.sockets[0].getsockname()[1]
        with pytest.raises(ReceiverConnectionError) as result:
            await receiver_fingerprint("127.0.0.1", port)
        await asyncio.wait_for(finished.wait(), 2)
        assert result.value.phase == "tls"
        assert "accepted a connection" in str(result.value)
        assert received[0][0] == 22  # TLS handshake record, not HTTP or JSON.
        assert b'"pin"' not in received[0] and b'"type"' not in received[0]
    finally:
        server.close()
        await server.wait_closed()


async def test_accepted_but_stalled_receiver_reports_tls_failure(monkeypatch):
    monkeypatch.setattr("lantern.security.RECEIVER_TLS_TIMEOUT", .05)
    finished = asyncio.Event()
    async def stalled(reader, writer):
        try:
            await reader.read()
        finally:
            writer.close()
            await writer.wait_closed()
            finished.set()
    server = await asyncio.start_server(stalled, "127.0.0.1", 0)
    try:
        port = server.sockets[0].getsockname()[1]
        with pytest.raises(ReceiverConnectionError) as result:
            await receiver_fingerprint("127.0.0.1", port)
        await asyncio.wait_for(finished.wait(), 2)
        assert result.value.phase == "tls"
        assert "encrypted connection failed" in str(result.value)
    finally:
        server.close()
        await server.wait_closed()


async def test_tls_identity_persists_and_media_uses_pinned_https(tmp_path):
    identity = LocalIdentity(tmp_path / "identity")
    assert LocalIdentity(tmp_path / "identity").fingerprint == identity.fingerprint
    assert b"ENCRYPTED PRIVATE KEY" in identity.key.read_bytes()
    controller = Controller(tmp_path / "cache", lambda *_: None)
    await controller.start()
    try:
        await controller.server.configure("127.0.0.1", identity.context)
        media = tmp_path / "media.mp4"
        media.write_bytes(b"test media bytes")
        controller.server.files["one"] = media
        url = controller.server.url("127.0.0.1", "one")
        assert url.startswith("https://")
        async with aiohttp.ClientSession() as session:
            async with session.get(url, ssl=aiohttp.Fingerprint(bytes.fromhex(identity.fingerprint)), headers={"Range": "bytes=5-9"}) as response:
                assert response.status == 206
                assert await response.read() == b"media"
            with pytest.raises(aiohttp.ServerFingerprintMismatch):
                await session.get(url, ssl=aiohttp.Fingerprint(bytes(32)))
    finally:
        await controller.close()


async def test_code_only_pairing_remembers_tv_and_reconnects_without_prompt(aiohttp_server, tmp_path):
    received = []
    records = {}
    async def receiver(request):
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        hello = await accept(ws, identity.fingerprint, records=records)
        if hello:
            received.append(hello)
            async for _ in ws: pass
        return ws
    identity = LocalIdentity(tmp_path / "tv")
    app = web.Application()
    app.router.add_get("/control", receiver)
    tv = await aiohttp_server(app, ssl=identity.context)
    events = []
    controller = Controller(tmp_path / "cache", lambda kind, data: events.append((kind, data)))
    await controller.start()
    try:
        device = asdict(Device("tv", "TV", "127.0.0.1", tv.port, "receiver"))
        await controller.connect(device)
        assert not received
        notice = next(data for kind, data in events if kind == "pairing.code_required")
        assert not notice["again"]
        assert "123456" not in str(events)
        await controller.connect(device, "123456")
        assert controller.ws and not controller.ws.closed
        assert controller.pairings.get(identity.fingerprint)
        assert received[-1]["mode"] == "code" and "pin" not in received[-1]
        await controller.disconnect()
        events.clear()
        await controller.connect(device)
        assert received[-1]["mode"] == "resume"
        assert not [kind for kind, _ in events if kind == "pairing.code_required"]
        assert not [kind for kind, _ in events if kind == "receiver.trust_required"]
        records.clear()
        await controller.connect(device)
        assert next(data for kind, data in events if kind == "pairing.code_required")["again"]
    finally:
        await controller.close()


async def test_changed_certificate_requires_code_and_replaces_old_record(aiohttp_server, tmp_path):
    events = []
    controller = Controller(tmp_path / "cache", lambda kind, data: events.append((kind, data)))
    await controller.start()
    try:
        for version in ("first", "reset"):
            identity = LocalIdentity(tmp_path / version)
            async def receiver(request):
                ws = web.WebSocketResponse()
                await ws.prepare(request)
                await accept(ws, identity.fingerprint)
                async for _ in ws: pass
                return ws
            app = web.Application()
            app.router.add_get("/control", receiver)
            tv = await aiohttp_server(app, ssl=identity.context)
            device = asdict(Device("same-tv", "TV", "127.0.0.1", tv.port, "receiver"))
            events.clear()
            await controller.connect(device)
            notice = next(data for kind, data in events if kind == "pairing.code_required")
            assert notice["again"] == (version == "reset")
            await controller.connect(device, "123456")
            assert len(controller.pairings.read()) == 1
    finally:
        await controller.close()


async def test_relay_between_different_tls_identities_cannot_finish_pairing(aiohttp_server, tmp_path):
    legitimate = LocalIdentity(tmp_path / "real-tv")
    impostor = LocalIdentity(tmp_path / "impostor")
    records = {}
    async def real_receiver(request):
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        await accept(ws, legitimate.fingerprint, records=records)
        await ws.close()
        return ws
    app = web.Application()
    app.router.add_get("/control", real_receiver)
    real_tv = await aiohttp_server(app, ssl=legitimate.context)
    async def relay(request):
        local = web.WebSocketResponse()
        await local.prepare(request)
        async with aiohttp.ClientSession() as session:
            async with session.ws_connect(f"https://127.0.0.1:{real_tv.port}/control", ssl=aiohttp.Fingerprint(bytes.fromhex(legitimate.fingerprint))) as upstream:
                try:
                    for _ in range(3):
                        await upstream.send_json(await local.receive_json())
                        await local.send_json(await upstream.receive_json())
                except (TypeError, aiohttp.ClientError): pass
        return local
    proxy = web.Application()
    proxy.router.add_get("/control", relay)
    fake_tv = await aiohttp_server(proxy, ssl=impostor.context)
    controller = Controller(tmp_path / "cache", lambda *_: None)
    await controller.start()
    try:
        with pytest.raises(ValueError, match="code"):
            await controller.connect(asdict(Device("fake", "TV", "127.0.0.1", fake_tv.port, "receiver")), "123456")
        assert not controller.pairings.read() and not records
        assert controller.ws is None
    finally:
        await controller.close()


def test_receiver_trust_rejects_incomplete_fingerprints(tmp_path):
    trust = ReceiverTrust(tmp_path / "receivers.json")
    with pytest.raises(ValueError):
        trust.save("192.168.1.10", 8765, "123456")
    trust.save("192.168.1.10", 8765, "ab" * 32)
    assert trust.get("192.168.1.10", 8765) == "ab" * 32
