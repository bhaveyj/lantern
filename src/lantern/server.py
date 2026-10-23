from __future__ import annotations

import asyncio
import secrets
import weakref
from collections import OrderedDict
from pathlib import Path

from aiohttp import web

from .media import subtitle_vtt


class MediaFileResponse(web.FileResponse):
    async def _sendfile(self, request, fobj, offset, count):
        # Windows TransmitFile rejects transfers above 2 GiB. Keep aiohttp's
        # range/HEAD handling, but send the body in bounded 256 KiB buffers.
        writer = await web.StreamResponse.prepare(self, request)
        assert writer is not None
        return await self._sendfile_fallback(writer, fobj, offset, count)


class MediaServer:
    def __init__(self):
        self.token = secrets.token_urlsafe(32)
        self.files: dict[str, Path] = {}
        self.subtitles: dict[str, Path] = {}
        self.runner: web.AppRunner | None = None
        self.port = 0
        self.transports = weakref.WeakSet()
        self._subtitle_cache = OrderedDict()
        self._subtitle_cache_bytes = 0
        self._subtitle_lock = asyncio.Lock()
        self.app = web.Application(middlewares=[self.cors])
        self.app.router.add_get("/stream/{token}/{id}", self.stream)
        self.app.router.add_get("/subtitle/{token}/{id}", self.subtitle)
        self.app.router.add_route("OPTIONS", "/{tail:.*}", self.options)
        self.app.router.add_route("*", "/{tail:.*}", self.not_found)

    @web.middleware
    async def cors(self, request: web.Request, handler):
        try:
            response = await handler(request)
        except web.HTTPException as error:
            response = error
        response.headers.update({"Access-Control-Allow-Origin": "*", "Access-Control-Allow-Headers": "Range, Content-Type", "Access-Control-Allow-Methods": "GET, HEAD, OPTIONS", "Access-Control-Expose-Headers": "Content-Length, Content-Range, Accept-Ranges", "Referrer-Policy": "no-referrer", "X-Content-Type-Options": "nosniff"})
        if isinstance(response, web.HTTPException):
            raise response
        return response

    async def options(self, request):
        return web.Response(status=204)

    async def not_found(self, request):
        raise web.HTTPNotFound()

    def authorized(self, request: web.Request) -> None:
        if not secrets.compare_digest(request.match_info["token"], self.token):
            raise web.HTTPNotFound()

    async def stream(self, request: web.Request):
        self.authorized(request)
        path = self.files.get(request.match_info["id"])
        if path is None or not path.is_file():
            raise web.HTTPNotFound()
        if request.transport:
            for transport in list(self.transports):
                if transport.is_closing():
                    self.transports.discard(transport)
            self.transports.add(request.transport)
        types = {".mp4": "video/mp4", ".m4v": "video/mp4", ".mkv": "video/x-matroska", ".ts": "video/mp2t", ".m2ts": "video/mp2t", ".aac": "audio/aac", ".m4a": "audio/mp4"}
        headers = {"Cache-Control": "no-store"}
        if path.suffix.lower() in types:
            headers["Content-Type"] = types[path.suffix.lower()]
        return MediaFileResponse(path, headers=headers)

    async def subtitle(self, request: web.Request):
        self.authorized(request)
        path = self.subtitles.get(request.match_info["id"])
        if path is None or not path.is_file():
            raise web.HTTPNotFound()
        async with self._subtitle_lock:
            converted = await self.converted_subtitle(request, path)
        return web.Response(text=converted, content_type="text/vtt", headers={"Cache-Control": "no-store"})

    async def converted_subtitle(self, request: web.Request, path: Path):
        try:
            stat = await asyncio.to_thread(path.stat)
            self.authorized(request)
            if stat.st_size > 8 * 1024**2:
                raise web.HTTPRequestEntityTooLarge(max_size=8 * 1024**2, actual_size=stat.st_size)
            key = (str(path), stat.st_size, stat.st_mtime_ns)
            converted = self._subtitle_cache.get(key)
            if converted is None:
                converted = await asyncio.to_thread(subtitle_vtt, path)
                self.authorized(request)
                size = len(converted.encode("utf-8"))
                if size <= 8 * 1024**2:
                    while self._subtitle_cache and (len(self._subtitle_cache) >= 32 or self._subtitle_cache_bytes + size > 8 * 1024**2):
                        _, old = self._subtitle_cache.popitem(last=False)
                        self._subtitle_cache_bytes -= len(old.encode("utf-8"))
                    self._subtitle_cache[key] = converted
                    self._subtitle_cache_bytes += size
            else:
                self._subtitle_cache.move_to_end(key)
        except (FileNotFoundError, PermissionError):
            raise web.HTTPNotFound() from None
        self.authorized(request)  # Stop may revoke the token during conversion.
        return converted

    async def start(self) -> None:
        self.runner = web.AppRunner(self.app, access_log=None)
        await self.runner.setup()
        site = web.TCPSite(self.runner, "0.0.0.0", 0)
        await site.start()
        self.port = site._server.sockets[0].getsockname()[1]

    async def stop(self) -> None:
        if self.runner:
            await self.runner.cleanup()

    def reset(self) -> None:
        for transport in list(self.transports):
            transport.close()
        self.transports.clear()
        self.token = secrets.token_urlsafe(32)
        self.files.clear()
        self.subtitles.clear()
        self._subtitle_cache.clear()
        self._subtitle_cache_bytes = 0

    def url(self, host: str, media_id: str) -> str:
        return f"http://{host}:{self.port}/stream/{self.token}/{media_id}"

    def subtitle_url(self, host: str, subtitle_id: str) -> str:
        return f"http://{host}:{self.port}/subtitle/{self.token}/{subtitle_id}"
