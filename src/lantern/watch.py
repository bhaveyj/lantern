from __future__ import annotations

import asyncio
import secrets
import weakref
from dataclasses import dataclass, field
from html import escape
from ipaddress import IPv4Address, IPv4Interface, IPv4Network
from pathlib import Path

from aiohttp import web

from .media import file_version, subtitle_vtt
from .presentation import describe_media
from .server import MediaFileResponse


LAN_NETWORKS = tuple(IPv4Network(network) for network in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"))
LINK_LIFETIME = 24 * 60 * 60


def lan_ipv4(address: IPv4Address) -> bool:
    return any(address in network for network in LAN_NETWORKS)


@dataclass
class WatchLink:
    item: dict
    source: Path
    version: tuple[int, int, int, int]
    video: Path
    expiry: asyncio.TimerHandle
    transports: weakref.WeakSet = field(default_factory=weakref.WeakSet)


class WatchServer:
    def __init__(self):
        self.links: dict[str, WatchLink] = {}
        self.interface: IPv4Interface | None = None
        self.authority = ""
        self.runner: web.AppRunner | None = None
        self.subtitle_lock = asyncio.Lock()
        self.app = web.Application(middlewares=[self.lan_only])
        self.app.router.add_get("/watch/{token}", self.page)
        self.app.router.add_get("/watch/{token}/video", self.video)
        self.app.router.add_get(r"/watch/{token}/captions/{index:[0-9]{1,6}}", self.captions)

    @web.middleware
    async def lan_only(self, request, handler):
        try:
            peer = IPv4Address(request.remote)
        except ValueError:
            raise web.HTTPForbidden() from None
        if (not self.interface or not lan_ipv4(peer) or peer not in self.interface.network
                or request.headers.get("Host") != self.authority
                or request.headers.get("Origin") not in {None, f"http://{self.authority}"}):
            raise web.HTTPForbidden()
        try:
            response = await handler(request)
        except web.HTTPException as error:
            response = error
        response.headers.update({
            "Cache-Control": "no-store",
            "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; media-src 'self'; frame-ancestors 'none'; base-uri 'none'",
            "Cross-Origin-Resource-Policy": "same-origin",
            "Referrer-Policy": "no-referrer",
            "X-Content-Type-Options": "nosniff",
            "X-Frame-Options": "DENY",
        })
        if isinstance(response, web.HTTPException):
            raise response
        return response

    async def start(self, interface: IPv4Interface):
        if not lan_ipv4(interface.ip) or not 0 < interface.network.prefixlen < 32:
            raise ValueError("Choose an IPv4 adapter on your home network.")
        if self.runner and self.interface == interface:
            return
        await self.close()
        runner = web.AppRunner(self.app, access_log=None, shutdown_timeout=2)
        try:
            await runner.setup()
            site = web.TCPSite(runner, str(interface.ip), 0)
            await site.start()
        except BaseException:
            await runner.cleanup()
            raise
        self.runner = runner
        self.interface = interface
        self.authority = f"{interface.ip}:{runner.addresses[0][1]}"

    def existing_url(self, item: dict, version: tuple[int, int, int, int]) -> str | None:
        now = asyncio.get_running_loop().time()
        for token, link in self.links.items():
            if now < link.expiry.when() and link.item == item and link.version == version and link.video.is_file():
                return f"http://{self.authority}/watch/{token}"
        return None

    def publish(self, item: dict, source: Path, version: tuple[int, int, int, int], video: Path) -> str:
        self.revoke(item["id"])
        token = secrets.token_urlsafe(24)
        expiry = asyncio.get_running_loop().call_later(LINK_LIFETIME, self.remove, token)
        self.links[token] = WatchLink(item, source, version, video, expiry)
        return f"http://{self.authority}/watch/{token}"

    def remove(self, token: str):
        link = self.links.pop(token, None)
        if link:
            link.expiry.cancel()
            for transport in list(link.transports):
                transport.close()

    def revoke(self, media_id: str) -> bool:
        token = next((token for token, link in self.links.items() if link.item["id"] == media_id), None)
        if token is None:
            return False
        self.remove(token)
        return True

    def sync(self, items: list[dict]):
        current = {item["id"]: item for item in items}
        for token, link in list(self.links.items()):
            item = current.get(link.item["id"])
            if not item or item["path"] != link.item["path"] or item["subtitles"] != link.item["subtitles"]:
                self.remove(token)

    async def close(self):
        for token in list(self.links):
            self.remove(token)
        if self.runner:
            await self.runner.cleanup()
            self.runner = None
        self.interface = None
        self.authority = ""

    def authorized(self, request: web.Request) -> WatchLink:
        token = request.match_info["token"]
        link = self.links.get(token)
        if link:
            if asyncio.get_running_loop().time() < link.expiry.when():
                try:
                    if file_version(link.source) == link.version and (link.video == link.source or link.video.is_file()):
                        return link
                except OSError:
                    pass
            self.remove(token)
        raise web.HTTPNotFound(text="This watch link is unavailable. Ask for a new link from Lantern.")

    async def page(self, request: web.Request):
        link = self.authorized(request)
        token = request.match_info["token"]
        root = f"/watch/{token}"
        title = escape(describe_media(str(link.source), link.item["title"]).title)
        selected = link.item.get("selected_subtitle")
        tracks = []
        for index, subtitle in enumerate(link.item["subtitles"]):
            default = " default" if subtitle["id"] == selected else ""
            label = escape(subtitle["label"])
            tracks.append(f'<track kind="subtitles" srclang="und" label="{label}" src="{root}/captions/{index}"{default}>')
        return web.Response(text=f"""<!doctype html>
<html lang="en">
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title} · Lantern</title>
<style>
:root {{ color-scheme: dark; font: 16px system-ui, sans-serif; background: #10141c; color: #edf2fc; }}
body {{ margin: 0; }}
main {{ max-width: 64rem; margin: auto; padding: clamp(1rem, 4vw, 3rem); }}
.brand {{ color: #a9c7ff; font-weight: 700; }}
h1 {{ font-size: clamp(1.5rem, 5vw, 2.5rem); overflow-wrap: anywhere; }}
video {{ display: block; width: 100%; max-height: 75vh; background: #000; border-radius: .75rem; }}
.hint {{ color: #b8c3d7; }}
</style>
<main>
  <div class="brand">Lantern</div>
  <h1>{title}</h1>
  <video controls playsinline preload="metadata" src="{root}/video">
    {"".join(tracks)}
    This browser does not support video playback.
  </video>
  <p class="hint">Keep Lantern running on the laptop while you watch.</p>
</main>
</html>""", content_type="text/html")

    async def video(self, request: web.Request):
        link = self.authorized(request)
        if request.transport:
            link.transports.add(request.transport)
        return MediaFileResponse(link.video, headers={"Content-Type": "video/mp4"})

    async def captions(self, request: web.Request):
        link = self.authorized(request)
        index = int(request.match_info["index"])
        if index >= len(link.item["subtitles"]):
            raise web.HTTPNotFound()
        path = Path(link.item["subtitles"][index]["path"])
        async with self.subtitle_lock:
            self.authorized(request)
            try:
                size = file_version(path)[2]
                if size > 8 * 1024**2:
                    raise web.HTTPRequestEntityTooLarge(max_size=8 * 1024**2, actual_size=size)
                text = await asyncio.to_thread(subtitle_vtt, path)
            except OSError:
                raise web.HTTPNotFound() from None
        self.authorized(request)
        return web.Response(text=text, content_type="text/vtt")
