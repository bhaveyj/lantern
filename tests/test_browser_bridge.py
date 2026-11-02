import asyncio
import io
import json
import struct

import aiohttp
import pytest

from lantern.browser_bridge import (
    BrowserBridge, DESCRIPTOR_NAME, MAX_MESSAGE, MAX_RESPONSE,
    forward_message, native_host_main, read_native_message, write_native_message,
)


def framed(value):
    encoded = json.dumps(value).encode()
    return struct.pack("<I", len(encoded)) + encoded


def test_native_framing_partial_reads_and_no_stdout_noise():
    class ShortReads(io.BytesIO):
        def read(self, size=-1):
            return super().read(min(size, 2))
    message = {"action": "play", "source": {"title": "A video ✓"}}
    assert read_native_message(ShortReads(framed(message))) == message
    assert read_native_message(io.BytesIO()) is None
    output = io.BytesIO()
    write_native_message(output, {"ok": True, "title": "✓"})
    content = output.getvalue()
    assert struct.unpack("<I", content[:4])[0] == len(content) - 4
    assert json.loads(content[4:]) == {"ok": True, "title": "✓"}


@pytest.mark.parametrize("content", [b"\x01", struct.pack("<I", MAX_MESSAGE + 1), framed([]), framed({"action": "unknown"}), framed({"action": "play"}), struct.pack("<I", 2) + b"{}"])
def test_invalid_native_frames_fail_closed(content):
    with pytest.raises((ValueError, EOFError)):
        read_native_message(io.BytesIO(content))


def test_native_host_rejects_bad_input_and_bounds_output(tmp_path):
    output = io.BytesIO()
    assert native_host_main(tmp_path, input_stream=io.BytesIO(struct.pack("<I", MAX_MESSAGE + 1)), output_stream=output) == 1
    assert json.loads(output.getvalue()[4:])["ok"] is False
    output = io.BytesIO()
    write_native_message(output, {"huge": "x" * (MAX_RESPONSE + 1)})
    assert len(output.getvalue()) < 1024


async def test_authenticated_native_handoff_encrypts_descriptor_and_revokes(tmp_path):
    seen = []
    async def handler(message):
        seen.append(message)
        return {"ok": True, "connected": True, "device": "Test TV"}
    bridge = BrowserBridge(tmp_path, handler)
    await bridge.start()
    try:
        assert bridge.port > 0
        result = await asyncio.to_thread(forward_message, tmp_path, {"action": "status"})
        assert result == {"ok": True, "connected": True, "device": "Test TV"}
        assert seen == [{"action": "status"}]
        import os
        if os.name == "nt":
            assert bridge.secret.encode() not in (tmp_path / DESCRIPTOR_NAME).read_bytes()
    finally:
        await bridge.close()
    assert not (tmp_path / DESCRIPTOR_NAME).exists()
    assert (await asyncio.to_thread(forward_message, tmp_path, {"action": "status"}))["ok"] is False


async def test_bridge_rejects_browser_origin_host_and_bad_auth(tmp_path):
    async def handler(message):
        raise AssertionError("Unauthorized requests must never reach the handler")
    bridge = BrowserBridge(tmp_path, handler)
    await bridge.start()
    try:
        async with aiohttp.ClientSession() as client:
            url = f"http://127.0.0.1:{bridge.port}/command"
            auth = {"X-Lantern-Bridge": bridge.secret}
            for extra in [{}, {"X-Lantern-Bridge": "wrong"}, {**auth, "Host": "evil.test"}, {**auth, "Origin": "null"}, {**auth, "Origin": "https://evil.test"}, {**auth, "Sec-Fetch-Site": "same-origin"}]:
                response = await client.post(url, json={"action": "status"}, headers=extra)
                assert response.status == 403
            assert (await client.get(url, headers=auth)).status == 405
            assert (await client.post(url, data="{}", headers=auth)).status == 415
            assert (await client.post(url, data='{"action":"play"}', headers={**auth, "Content-Type": "application/json"})).status == 400
    finally:
        await bridge.close()


async def test_bridge_limits_input_and_redacts_handler_exceptions(tmp_path):
    async def handler(message):
        raise ValueError("https://private.example?token=secret-cookie")
    bridge = BrowserBridge(tmp_path, handler)
    await bridge.start()
    try:
        async with aiohttp.ClientSession() as client:
            url = f"http://127.0.0.1:{bridge.port}/command"
            headers = {"X-Lantern-Bridge": bridge.secret, "Content-Type": "application/json"}
            response = await client.post(url, json={"action": "status"}, headers=headers)
            assert "secret-cookie" not in await response.text()
            assert (await response.json())["ok"] is False
            response = await client.post(url, data=b"x" * (MAX_MESSAGE + 1), headers=headers)
            assert response.status == 413
    finally:
        await bridge.close()


async def test_native_helper_process_framing_can_control_live_bridge(tmp_path):
    async def handler(message):
        return {"ok": True, "action": message["action"]}
    bridge = BrowserBridge(tmp_path, handler)
    await bridge.start()
    output = io.BytesIO()
    try:
        code = await asyncio.to_thread(native_host_main, tmp_path, input_stream=io.BytesIO(framed({"action": "status"})), output_stream=output)
        assert code == 0
        assert json.loads(output.getvalue()[4:]) == {"ok": True, "action": "status"}
    finally:
        await bridge.close()
