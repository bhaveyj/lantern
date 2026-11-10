"""Firefox Native Messaging control bridge; media and credentials never go to disk.

The descriptor contains only a per-process IPC secret, protected with Windows
DPAPI. The browser extension cannot call the HTTP endpoint itself: only the
registered native helper reads the descriptor and authenticates requests.
"""
from __future__ import annotations

import asyncio
import http.client
import json
import os
import secrets
import struct
import sys
from pathlib import Path
from typing import Awaitable, Callable

from aiohttp import web

from .credentials import crypt
from .web_stream import WebStreamError

HOST_NAME = "app.lantern.tv"
EXTENSION_ID = "lantern-tv@lantern.local"
MAX_MESSAGE = 256 * 1024
MAX_RESPONSE = 64 * 1024
DESCRIPTOR_NAME = "browser-bridge.bin"


class BrowserCommandError(ValueError):
    """A deliberate user-facing message containing no private request context."""


def default_data_dir() -> Path:
    return Path(os.environ.get("LOCALAPPDATA", Path.home() / ".local" / "share")) / "LanternTV"


def _protect(value: bytes, *, decrypt: bool = False) -> bytes:
    return crypt(value, decrypt=decrypt) if os.name == "nt" else value


def validate_message(message: object) -> dict:
    if not isinstance(message, dict) or message.get("action") not in {"status", "play", "refresh", "stop"}:
        raise ValueError("Unknown browser command.")
    if message["action"] in {"play", "refresh"} and not isinstance(message.get("source"), dict):
        raise ValueError("Select a detected video first.")
    return message


class BrowserBridge:
    def __init__(self, data_dir: Path, handler: Callable[[dict], Awaitable[dict]]):
        self.path = data_dir / DESCRIPTOR_NAME
        self.handler = handler
        self.secret = secrets.token_urlsafe(32)
        self.port = 0
        self.runner: web.AppRunner | None = None
        self.descriptor: bytes | None = None
        self.pending: set[asyncio.Task] = set()
        self.app = web.Application(client_max_size=MAX_MESSAGE)
        self.app.router.add_post("/command", self.command)

    async def start(self) -> None:
        self.runner = web.AppRunner(self.app, access_log=None)
        await self.runner.setup()
        site = web.TCPSite(self.runner, "127.0.0.1", 0)
        try:
            await site.start()
            self.port = site._server.sockets[0].getsockname()[1]
            encoded = json.dumps({"version": 1, "port": self.port, "secret": self.secret}).encode("utf-8")
            self.descriptor = _protect(encoded)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            pending = self.path.with_suffix(".tmp")
            pending.write_bytes(self.descriptor)
            if os.name != "nt":
                pending.chmod(0o600)
            pending.replace(self.path)
        except BaseException:
            await self.runner.cleanup()
            self.runner = None
            raise

    async def command(self, request: web.Request) -> web.Response:
        # Never trust forwarded addresses, browser origins, or DNS Host names.
        if (request.remote != "127.0.0.1" or request.headers.get("Host") != f"127.0.0.1:{self.port}"
                or "Origin" in request.headers or "Sec-Fetch-Site" in request.headers
                or not secrets.compare_digest(request.headers.get("X-Lantern-Bridge", "").encode("utf-8"), self.secret.encode("utf-8"))):
            raise web.HTTPForbidden()
        if request.content_type != "application/json":
            raise web.HTTPUnsupportedMediaType()
        if len(self.pending) >= 4:
            raise web.HTTPTooManyRequests()
        task = asyncio.current_task()
        self.pending.add(task)
        try:
            try:
                message = validate_message(await request.json())
            except (ValueError, UnicodeError, TypeError, RecursionError):
                return web.json_response({"ok": False, "error": "Invalid browser command."}, status=400)
            try:
                async with asyncio.timeout(30):
                    result = await self.handler(message)
                payload = json.dumps(result, ensure_ascii=False).encode("utf-8")
                if len(payload) > MAX_RESPONSE:
                    raise ValueError("Response too large")
            except asyncio.CancelledError:
                raise
            except (BrowserCommandError, WebStreamError) as error:
                return web.json_response({"ok": False, "error": str(error)}, headers={"Cache-Control": "no-store"})
            except TimeoutError:
                return web.json_response({"ok": False, "error": "The stream took too long to start. Restart the video in Firefox, then send it again."})
            except Exception:
                # Exception text may contain upstream tokens, URLs or headers.
                return web.json_response({"ok": False, "error": "Lantern could not complete this browser command. Check the Lantern window."})
            return web.Response(body=payload, content_type="application/json", headers={"Cache-Control": "no-store"})
        finally:
            self.pending.discard(task)

    async def close(self) -> None:
        self.secret = secrets.token_urlsafe(32)
        for task in list(self.pending):
            if task is not asyncio.current_task():
                task.cancel()
        if self.pending:
            await asyncio.gather(*[task for task in self.pending if task is not asyncio.current_task()], return_exceptions=True)
        if self.runner:
            await self.runner.cleanup()
            self.runner = None
        try:
            if self.descriptor is not None and self.path.read_bytes() == self.descriptor:
                self.path.unlink()
        except FileNotFoundError:
            pass


def _read_exact(stream, size: int) -> bytes:
    chunks = bytearray()
    while len(chunks) < size:
        chunk = stream.read(size - len(chunks))
        if not chunk:
            raise EOFError()
        chunks.extend(chunk)
    return bytes(chunks)


def read_native_message(stream) -> dict | None:
    first = stream.read(1)
    if not first:
        return None
    length = struct.unpack("<I", first + _read_exact(stream, 3))[0]
    if not 1 <= length <= MAX_MESSAGE:
        raise ValueError("Invalid native message length.")
    return validate_message(json.loads(_read_exact(stream, length).decode("utf-8")))


def write_native_message(stream, message: dict) -> None:
    encoded = json.dumps(message, ensure_ascii=False).encode("utf-8")
    if len(encoded) > MAX_RESPONSE:
        encoded = b'{"ok":false,"error":"Lantern response exceeded the message limit."}'
    stream.write(struct.pack("<I", len(encoded)))
    stream.write(encoded)
    stream.flush()


def forward_message(data_dir: Path, message: dict) -> dict:
    try:
        path = data_dir / DESCRIPTOR_NAME
        if path.stat().st_size > 8192:
            raise ValueError()
        descriptor = json.loads(_protect(path.read_bytes(), decrypt=True).decode("utf-8"))
        port, secret = descriptor["port"], descriptor["secret"]
        if descriptor.get("version") != 1 or type(port) is not int or not 1 <= port <= 65535 or not isinstance(secret, str) or not 32 <= len(secret) <= 128:
            raise ValueError()
        payload = json.dumps(validate_message(message)).encode("utf-8")
        if len(payload) > MAX_MESSAGE:
            raise ValueError()
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=35)
        try:
            connection.request("POST", "/command", body=payload, headers={"Content-Type": "application/json", "X-Lantern-Bridge": secret})
            response = connection.getresponse()
            encoded = response.read(MAX_RESPONSE + 1)
            if response.status != 200 or len(encoded) > MAX_RESPONSE:
                raise ValueError()
            result = json.loads(encoded.decode("utf-8"))
            if not isinstance(result, dict):
                raise ValueError()
            return result
        finally:
            connection.close()
    except Exception:
        return {"ok": False, "error": "Open Lantern on this Windows account, connect your TV, then try again."}


def native_host_main(data_dir: Path | None = None, *, input_stream=None, output_stream=None) -> int:
    if input_stream is None or output_stream is None:
        if os.name == "nt":
            import msvcrt
            msvcrt.setmode(sys.stdin.fileno(), os.O_BINARY)
            msvcrt.setmode(sys.stdout.fileno(), os.O_BINARY)
        input_stream = sys.stdin.buffer
        output_stream = sys.stdout.buffer
    while True:
        try:
            message = read_native_message(input_stream)
            if message is None:
                return 0
        except (ValueError, EOFError, UnicodeError, TypeError, RecursionError):
            write_native_message(output_stream, {"ok": False, "error": "Invalid native message."})
            return 1
        write_native_message(output_stream, forward_message(data_dir or default_data_dir(), message))


if __name__ == "__main__":
    raise SystemExit(native_host_main())
