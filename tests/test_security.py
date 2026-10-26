import asyncio
import hashlib
from pathlib import Path

import aiohttp
from aiohttp import web
import pytest

from lantern.controller import Controller, Device
from lantern.security import LocalIdentity, ReceiverTrust, receiver_fingerprint
from dataclasses import asdict


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


async def test_unknown_or_changed_tv_never_receives_pin_before_approval(aiohttp_server, tmp_path):
    received = []
    async def receiver(request):
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        received.append(await ws.receive_json())
        await ws.close()
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
        await controller.connect(device, "123456")
        assert not received
        notice = next(data for kind, data in events if kind == "receiver.trust_required")
        assert notice["fingerprint"] == identity.fingerprint and not notice["changed"]
        assert "123456" not in str(events)
        await controller.trust_receiver(identity.fingerprint, False)
        controller.receiver_trust.save("127.0.0.1", tv.port, "a" * 64)
        events.clear()
        await controller.connect(device, "123456")
        assert not received
        assert next(data for kind, data in events if kind == "receiver.trust_required")["changed"]
    finally:
        await controller.close()


def test_receiver_trust_rejects_incomplete_fingerprints(tmp_path):
    trust = ReceiverTrust(tmp_path / "receivers.json")
    with pytest.raises(ValueError):
        trust.save("192.168.1.10", 8765, "123456")
    trust.save("192.168.1.10", 8765, "ab" * 32)
    assert trust.get("192.168.1.10", 8765) == "ab" * 32
