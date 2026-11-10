"""Session-scoped, bounded web media relay. The TV never receives upstream URLs.

Browser credentials are held only in memory and are applied to their exact HTTPS
origin. Every connection, including redirects and manifest children, uses public
addresses checked by the connector's resolver rather than a separate DNS probe.
"""
from __future__ import annotations

import asyncio
import ipaddress
import math
import re
import secrets
import socket
import zlib
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Callable
from urllib.parse import parse_qs, urljoin, urlsplit

import aiohttp
from aiohttp import web
from aiohttp.abc import AbstractResolver
from yarl import URL

from .stream_transport import MAX_WRAPPED_INPUT, TransportDecodeError, looks_wrapped_segment, unwrap_segment

HLS_MIME = "application/vnd.apple.mpegurl"
HLS_TYPES = {HLS_MIME, "application/x-mpegurl", "audio/mpegurl", "audio/x-mpegurl"}
MP4_TYPES = {"video/mp4", "audio/mp4"}
MAX_MANIFEST = 2 * 1024 * 1024
MAX_RESOURCES = 16384
MAX_SESSIONS = 4
MAX_SEGMENT = 128 * 1024 * 1024
MAX_PROGRESSIVE = 64 * 1024**3
MAX_URL = 8192
MAX_HEADERS = 128 * 1024
ALLOWED_HEADERS = {"authorization", "cookie", "referer", "origin", "user-agent", "accept", "accept-language"}
IDENTIFIER = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")
URI_ATTRIBUTE = re.compile(r'(?P<prefix>(?:^|,)(?:URI|SERVER-URI)=)"(?P<uri>[^"\r\n]*)"')
ATTRIBUTE = re.compile(r'(?:^|,)([A-Z0-9-]+)=("[^"\r\n]*"|[^,\r\n]*)')
VARIABLE = re.compile(r"\{\$([A-Za-z0-9_-]+)\}")


class WebStreamError(ValueError):
    """A user-safe error: never include URLs, headers, or upstream exception text."""


def public_address(value: str) -> bool:
    try:
        address = ipaddress.ip_address(value.split("%", 1)[0])
    except ValueError:
        return False
    return address.is_global and not address.is_multicast and not address.is_reserved


def checked_url(value: str) -> str:
    if not isinstance(value, str) or not value or len(value) > MAX_URL or any(ord(c) < 33 for c in value):
        raise WebStreamError("The video has an invalid media address.")
    try:
        parsed = URL(value)
        host = parsed.host
        if parsed.scheme != "https" or not host or parsed.user is not None or parsed.password is not None or parsed.fragment or parsed.port is None:
            raise ValueError
        if host.lower() in {"localhost", "localhost.localdomain"} or host.lower().endswith((".local", ".localhost", ".internal", ".home", ".lan")) or "%" in host:
            raise ValueError
        try:
            ipaddress.ip_address(host)
        except ValueError:
            # Single-label/integer/legacy IPv4 forms must not reach DNS resolution.
            if "." not in host or not re.search(r"[A-Za-z]", host):
                raise ValueError
        else:
            if not public_address(host):
                raise ValueError
    except (ValueError, TypeError, UnicodeError):
        raise WebStreamError("Only public HTTPS video sources are supported.") from None
    return value


def origin(value: str) -> str:
    return str(URL(checked_url(value)).origin())


class PublicResolver(AbstractResolver):
    def __init__(self):
        self._resolver = aiohttp.resolver.ThreadedResolver()

    async def resolve(self, host: str, port: int = 0, family: int = socket.AF_INET):
        records = await self._resolver.resolve(host, port, family)
        if not records or any(not public_address(record["host"]) for record in records):
            raise OSError("The media address is not on the public internet.")
        return records

    async def close(self):
        await self._resolver.close()


def checked_headers(value: object) -> dict[str, dict[str, str]]:
    if value is None:
        return {}
    if not isinstance(value, dict) or len(value) > 32:
        raise WebStreamError("The browser supplied invalid request information.")
    result: dict[str, dict[str, str]] = {}
    total = 0
    for address, headers in value.items():
        normalized = origin(address)
        url = URL(address)
        if url.path not in {"", "/"} or url.query_string or url.fragment or not isinstance(headers, dict) or len(headers) > 16:
            raise WebStreamError("The browser supplied invalid request information.")
        approved = {}
        for name, content in headers.items():
            if not isinstance(name, str) or not isinstance(content, str):
                raise WebStreamError("The browser supplied invalid request information.")
            name = name.lower()
            if name not in ALLOWED_HEADERS:
                continue
            if len(content) > 32768 or any(ord(c) < 32 or ord(c) == 127 for c in content):
                raise WebStreamError("The browser supplied invalid request information.")
            total += len(name.encode()) + len(content.encode())
            if total > MAX_HEADERS:
                raise WebStreamError("The browser request information is too large.")
            approved[name] = content
        result[normalized] = approved
    return result


@dataclass
class WebStream:
    id: str
    title: str
    mime_type: str
    live: bool
    session_id: str
    duration: float = 0.0
    headers: dict[str, dict[str, str]] = field(default_factory=dict, repr=False)
    root_headers: dict[str, str] | None = field(default=None, repr=False)
    prefer_compatible: bool = False


@dataclass
class Resource:
    id: str
    session_id: str
    url: str = field(repr=False)
    kind: str = "media"
    variables: dict[str, str] = field(default_factory=dict, repr=False)
    wrapped: bool | None = None


class WebRelay:
    def __init__(self):
        self.streams: dict[str, WebStream] = {}
        self.resources: OrderedDict[str, Resource] = OrderedDict()
        self._resource_ids: dict[tuple, str] = {}
        self._client: aiohttp.ClientSession | None = None
        self._tasks: set[asyncio.Task] = set()
        self._cleanup_tasks: set[asyncio.Task] = set()
        self._semaphore = asyncio.Semaphore(12)
        self._transport_semaphore = asyncio.Semaphore(2)
        self._generation = 0

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._client is None or self._client.closed:
            self._client = aiohttp.ClientSession(
                connector=aiohttp.TCPConnector(resolver=PublicResolver(), use_dns_cache=False, limit=12, limit_per_host=8),
                timeout=aiohttp.ClientTimeout(total=None, connect=10, sock_read=25),
                cookie_jar=aiohttp.DummyCookieJar(), trust_env=False, auto_decompress=False,
                headers={"Accept-Encoding": "identity"},
            )
        return self._client

    def has(self, resource_id: str) -> bool:
        return resource_id in self.resources

    def _active(self, stream: WebStream) -> None:
        if self.streams.get(stream.session_id) is not stream:
            raise WebStreamError("This browser playback session has ended.")

    def _source(self, source: dict) -> tuple[str, str, str, str, dict]:
        if not isinstance(source, dict):
            raise WebStreamError("The browser supplied an invalid video.")
        address = checked_url(source.get("url"))
        session_id = source.get("sessionId") or secrets.token_urlsafe(24)
        if not isinstance(session_id, str) or not IDENTIFIER.fullmatch(session_id):
            raise WebStreamError("The browser supplied an invalid session.")
        title = source.get("title") or "Browser video"
        if not isinstance(title, str):
            raise WebStreamError("The browser supplied an invalid video title.")
        title = "".join(c for c in title if ord(c) >= 32 and ord(c) != 127)[:300] or "Browser video"
        mime = source.get("mimeType", "")
        if not isinstance(mime, str):
            raise WebStreamError("The browser supplied an invalid video format.")
        mime = mime.split(";", 1)[0].strip().lower()
        suffix = URL(address).path.lower()
        if mime == "application/dash+xml" or suffix.endswith(".mpd"):
            raise WebStreamError("DASH video is not supported yet. Choose an HLS or MP4 source.")
        if mime in HLS_TYPES or suffix.endswith(".m3u8"):
            mime = HLS_MIME
        elif mime in MP4_TYPES or suffix.endswith((".mp4", ".m4v", ".m4a")):
            mime = "audio/mp4" if mime == "audio/mp4" or suffix.endswith(".m4a") else "video/mp4"
        else:
            mime = ""  # Probe the response type; do not mistake a webpage for media.
        return address, session_id, title, mime, checked_headers(source.get("headersByOrigin"))

    async def register(self, source: dict, media_id: str | None = None) -> WebStream:
        address, session_id, title, mime, headers = self._source(source)
        if len(self.streams) >= MAX_SESSIONS:
            raise WebStreamError("Stop a browser video before starting another one.")
        if session_id in self.streams:
            raise WebStreamError("This browser video is already available. Send a session refresh instead.")
        media_id = media_id or secrets.token_urlsafe(24)
        if not isinstance(media_id, str) or not IDENTIFIER.fullmatch(media_id) or media_id in self.resources:
            raise WebStreamError("The browser supplied an invalid video identifier.")
        stream = WebStream(media_id, title, mime, False, session_id, headers=headers,
                           prefer_compatible=source.get("preferCompatible") is True)
        if "rootHeaders" in source:
            stream.root_headers = checked_headers({origin(address): source["rootHeaders"]})[origin(address)]
        self.streams[session_id] = stream
        self._add_resource(stream, address, "manifest" if mime == HLS_MIME else "progressive", resource_id=media_id)
        task = asyncio.current_task()
        self._tasks.add(task)
        try:
            async with asyncio.timeout(45):
                await self._probe(stream, self.resources[media_id])
            self._active(stream)
            return stream
        except (aiohttp.ClientError, OSError, TimeoutError):
            self._remove_stream(stream)
            raise WebStreamError("The video source stopped responding. Keep Firefox open and try sending the video again.") from None
        except BaseException:
            self._remove_stream(stream)
            raise
        finally:
            self._tasks.discard(task)

    async def refresh(self, source: dict) -> WebStream:
        address, session_id, title, mime, headers = self._source(source)
        root_headers = checked_headers({origin(address): source["rootHeaders"]})[origin(address)] if "rootHeaders" in source else None
        stream = self.streams.get(session_id)
        if stream is None:
            raise WebStreamError("This browser playback session has ended.")
        # A refresh may renew a signed root URL and exact-origin credentials.
        # Existing segment URLs remain valid until the receiver reloads a playlist.
        if mime and mime != stream.mime_type:
            raise WebStreamError("The browser video format changed. Send it to the TV again.")
        old = self.resources[stream.id]
        old_key = self._resource_key(old)
        self._resource_ids.pop(old_key, None)
        old.url = address
        self._resource_ids[self._resource_key(old)] = old.id
        stream.headers = headers
        stream.root_headers = root_headers
        stream.title = title
        return stream

    async def _fetch(self, stream: WebStream, address: str, extra_headers: dict | None = None):
        for _ in range(6):
            self._active(stream)
            address = checked_url(address)
            headers = dict(stream.headers.get(origin(address), {}))
            root = self.resources.get(stream.id)
            if root is not None and root.url == address and stream.root_headers is not None:
                headers = dict(stream.root_headers)
            headers.update(extra_headers or {})
            try:
                response = await (await self._get_session()).request("GET", URL(address, encoded=True), headers=headers, allow_redirects=False)
            except (aiohttp.ClientError, OSError, TimeoutError, ValueError):
                raise WebStreamError("The video source could not be reached. Keep Firefox open and try sending the video again.") from None
            try:
                self._active(stream)
                if response.status in {301, 302, 303, 307, 308}:
                    location = response.headers.get("Location")
                    if not location:
                        raise WebStreamError("The video source returned an invalid redirect.")
                    address = checked_url(urljoin(address, location))
                    response.close()
                    continue
                if response.status in {401, 403}:
                    raise WebStreamError("The website session expired or requires Firefox playback. Keep the tab open and send the video again.")
                if response.status not in {200, 206, 416}:
                    raise WebStreamError("The video source is temporarily unavailable. Try sending the video again.")
                return response
            except BaseException:
                response.close()
                raise
        raise WebStreamError("The video source redirected too many times.")

    async def _read_manifest(self, response) -> str:
        encoding = response.headers.get("Content-Encoding", "identity").lower()
        if encoding not in {"identity", "gzip", "deflate"}:
            raise WebStreamError("The video playlist uses an unsupported encoding.")
        decoder = zlib.decompressobj(16 + zlib.MAX_WBITS if encoding == "gzip" else zlib.MAX_WBITS) if encoding != "identity" else None
        data = bytearray()
        received = 0
        try:
            async for chunk in response.content.iter_chunked(65536):
                received += len(chunk)
                if received > MAX_MANIFEST:
                    raise WebStreamError("The video playlist is too large.")
                data.extend(decoder.decompress(chunk, MAX_MANIFEST + 1 - len(data)) if decoder else chunk)
                if len(data) > MAX_MANIFEST or (decoder and decoder.unconsumed_tail):
                    raise WebStreamError("The video playlist is too large.")
            if decoder:
                data.extend(decoder.flush(MAX_MANIFEST + 1 - len(data)))
                if not decoder.eof or len(data) > MAX_MANIFEST:
                    raise WebStreamError("The video playlist is invalid or too large.")
            text = data.decode("utf-8-sig")
        except (UnicodeError, zlib.error):
            raise WebStreamError("The video source did not return a valid HLS playlist.") from None
        if text.splitlines()[:1] != ["#EXTM3U"] or "\x00" in text:
            raise WebStreamError("The video source did not return a valid HLS playlist.")
        return text

    async def _probe(self, stream: WebStream, resource: Resource, depth: int = 0):
        if depth > 4:
            raise WebStreamError("The video playlist is nested too deeply.")
        # MP4 probing reads only its HTTP headers. Never download an entire movie.
        async with self._semaphore:
            response = await self._fetch(stream, resource.url, {"Range": "bytes=0-1023"} if stream.mime_type != HLS_MIME else None)
            try:
                response_type = response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
                if resource.kind == "manifest" or response_type in HLS_TYPES:
                    resource.kind = "manifest"
                    stream.mime_type = HLS_MIME
                    # A range used for an unknown source might truncate a playlist.
                    if response.status == 206:
                        response.close()
                        response = await self._fetch(stream, resource.url)
                    text = await self._read_manifest(response)
                    base = str(response.url)
                elif response_type in MP4_TYPES or stream.mime_type in MP4_TYPES and response_type in {"application/octet-stream", "binary/octet-stream", ""}:
                    stream.mime_type = response_type if response_type in MP4_TYPES else stream.mime_type
                    resource.kind = "progressive"
                    return
                else:
                    raise WebStreamError("No playable HLS or MP4 video was found. Start the video in Firefox and choose its media source.")
            finally:
                response.close()
        _, children, media = self._rewrite_manifest(stream, resource, text, base, lambda rid: "/" + rid)
        if media:
            stream.live = "#EXT-X-ENDLIST" not in text
            durations = re.findall(r"^#EXTINF:([0-9]+(?:\.[0-9]+)?)(?:,|$)", text, re.MULTILINE)
            duration = sum(float(value) for value in durations)
            if not math.isfinite(duration) or duration > 365 * 86400:
                raise WebStreamError("The video playlist contains an invalid duration.")
            stream.duration = duration if not stream.live else 0.0
        else:
            child = next((r for r in children if r.kind == "manifest"), None)
            if child is None:
                raise WebStreamError("The HLS playlist has no playable video.")
            await self._probe(stream, child, depth + 1)

    def _resource_key(self, resource: Resource) -> tuple:
        return resource.session_id, resource.url, tuple(sorted(resource.variables.items()))

    def _add_resource(self, stream: WebStream, address: str, kind: str, *, variables: dict | None = None, resource_id: str | None = None, protected: set[str] | None = None) -> Resource:
        self._active(stream)
        address = checked_url(address)
        resource = Resource(resource_id or secrets.token_urlsafe(24), stream.session_id, address, kind, dict(variables or {}))
        key = self._resource_key(resource)
        existing = self._resource_ids.get(key)
        if existing in self.resources:
            self.resources.move_to_end(existing)
            # A URL used as a key, initialization data, or ciphertext must never
            # be interpreted as an image envelope, even if another playlist uses it.
            if kind in {"key", "init", "encrypted-segment"}:
                self.resources[existing].kind = kind
            return self.resources[existing]
        while len(self.resources) >= MAX_RESOURCES:
            # Keep entry playlists, but allow old sliding-window segments to expire.
            old_id = next((rid for rid, old in self.resources.items() if old.kind not in {"manifest", "progressive"} and rid not in (protected or ())), None)
            if old_id is None:
                raise WebStreamError("The video playlist references too many resources.")
            old = self.resources.pop(old_id)
            self._resource_ids.pop(self._resource_key(old), None)
        self.resources[resource.id] = resource
        self._resource_ids[key] = resource.id
        return resource

    @staticmethod
    def _attributes(line: str) -> dict[str, str]:
        return {key: value[1:-1] if value.startswith('"') else value for key, value in ATTRIBUTE.findall(line.partition(":")[2])}

    def _variables(self, resource: Resource, lines: list[str], base: str) -> dict[str, str]:
        variables = {}
        for line in lines:
            if not line.startswith("#EXT-X-DEFINE:"):
                continue
            values = self._attributes(line)
            if "IMPORT" in values:
                name = values["IMPORT"]
                if name not in resource.variables:
                    raise WebStreamError("The HLS playlist references an unavailable variable.")
                variables[name] = resource.variables[name]
            elif "QUERYPARAM" in values:
                name = values["QUERYPARAM"]
                entries = parse_qs(urlsplit(base).query).get(name, [])
                if len(entries) != 1:
                    raise WebStreamError("The HLS playlist references an unavailable query variable.")
                variables[name] = entries[0]
            elif "NAME" in values and "VALUE" in values:
                variables[values["NAME"]] = values["VALUE"]
            else:
                raise WebStreamError("The HLS playlist contains an invalid variable.")
            if len(variables) > 64 or any(len(value) > MAX_URL for value in variables.values()):
                raise WebStreamError("The HLS playlist contains too many variables.")
        return variables

    @staticmethod
    def _expand(value: str, variables: dict[str, str]) -> str:
        for _ in range(8):
            if not VARIABLE.search(value):
                return value
            try:
                value = VARIABLE.sub(lambda match: variables[match[1]], value)
            except KeyError:
                break
            if len(value) > MAX_URL:
                break
        raise WebStreamError("The HLS playlist contains an unresolved variable.")

    def _rewrite_manifest(self, stream: WebStream, resource: Resource, text: str, base: str, local_url: Callable[[str], str]) -> tuple[str, list[Resource], bool]:
        lines = text.splitlines()
        if stream.prefer_compatible:
            lines = self._compatible_variants(lines)
        variables = self._variables(resource, lines, base)
        output = []
        children = []
        protected = set()
        next_is_playlist = False
        encrypted = False
        media = any(line.startswith(("#EXTINF:", "#EXT-X-TARGETDURATION:")) for line in lines)
        if any(line.startswith("#EXT-X-SKIP:") for line in lines):
            raise WebStreamError("Delta HLS playlists are not supported yet. Choose the full HLS playlist in Firefox.")
        if media and not any(line.strip() and not line.startswith("#") for line in lines):
            raise WebStreamError("The HLS stream has no complete video segments yet. Wait for playback to start in Firefox and try again.")
        for line in lines:
            if len(line) > 32768:
                raise WebStreamError("A video playlist entry is too large.")
            if line.startswith("#EXT-X-DEFINE:"):
                continue  # Variables are already expanded; do not expose token values.
            if line.startswith(("#EXT-X-SERVER-CONTROL:", "#EXT-X-PART-INF:", "#EXT-X-PART:", "#EXT-X-PRELOAD-HINT:", "#EXT-X-RENDITION-REPORT:")):
                # Use complete ordinary HLS segments. The paired receiver accepts
                # opaque resource paths, never LL-HLS query requests or URLs.
                continue
            if line.startswith("#EXT-X-CONTENT-STEERING:"):
                raise WebStreamError("HLS content steering is not supported yet.")
            if line.startswith(("#EXT-X-KEY:", "#EXT-X-SESSION-KEY:")):
                attributes = self._attributes(line)
                if attributes.get("METHOD") not in {"NONE", "AES-128"} or attributes.get("KEYFORMAT", "identity") != "identity":
                    raise WebStreamError("This video uses content protection that Lantern does not support.")
                if line.startswith("#EXT-X-KEY:"):
                    encrypted = attributes.get("METHOD") != "NONE"
            if line.startswith("#"):
                tag, separator, attributes = line.partition(":")
                kind = "manifest" if tag in {"#EXT-X-MEDIA", "#EXT-X-I-FRAME-STREAM-INF", "#EXT-X-RENDITION-REPORT"} else "media"
                if tag in {"#EXT-X-KEY", "#EXT-X-SESSION-KEY"}:
                    kind = "key"
                elif tag == "#EXT-X-MAP":
                    kind = "init"
                def replace(match):
                    address = urljoin(base, self._expand(match["uri"], variables))
                    child = self._add_resource(stream, address, kind, variables=variables, protected=protected)
                    protected.add(child.id)
                    children.append(child)
                    return match["prefix"] + '"' + local_url(child.id) + '"'
                line = tag + separator + URI_ATTRIBUTE.sub(replace, attributes) if separator else line
                if tag == "#EXT-X-STREAM-INF":
                    next_is_playlist = True
            elif line.strip():
                address = urljoin(base, self._expand(line.strip(), variables))
                kind = "manifest" if next_is_playlist or not media else "encrypted-segment" if encrypted else "segment"
                child = self._add_resource(stream, address, kind, variables=variables, protected=protected)
                protected.add(child.id)
                children.append(child)
                line = local_url(child.id)
                next_is_playlist = False
            output.append(line)
        return "\n".join(output) + "\n", children, media

    def _compatible_variants(self, lines: list[str]) -> list[str]:
        variants = []
        pending = None
        for index, line in enumerate(lines):
            if line.startswith("#EXT-X-STREAM-INF:"):
                pending = (index, self._attributes(line))
            elif pending and line.strip() and not line.startswith("#"):
                first, attributes = pending
                pending = None
                resolution = re.fullmatch(r"(\d{1,5})x(\d{1,5})", attributes.get("RESOLUTION", ""))
                if resolution:
                    width, height = (int(value) for value in resolution.groups())
                    fits = width <= 1920 and height <= 1080
                else:
                    fits = False
                sdr = attributes.get("VIDEO-RANGE", "SDR").upper() == "SDR"
                avc = any(codec.strip().lower().startswith(("avc1.", "avc3.")) for codec in attributes.get("CODECS", "").split(","))
                variants.append((first, index, fits and sdr, avc))
        compatible = [variant for variant in variants if variant[2]]
        if not compatible:
            return lines
        kept = {variant[0] for variant in compatible if variant[3]} or {variant[0] for variant in compatible}
        removed = {i for first, last, _, _ in variants if first not in kept for i in range(first, last + 1)}
        return [line for index, line in enumerate(lines) if index not in removed]

    def _playlist_address(self, request: web.Request, resource: Resource) -> str:
        if request.query:
            raise WebStreamError("The media request contains unsupported parameters.")
        return resource.url

    async def _unwrap_response(self, request, stream, resource, upstream, prefix, chunks):
        # Acquire before buffering a whole envelope. Ordinary media stays streamed.
        async with self._transport_semaphore:
            length = upstream.headers.get("Content-Length")
            if length and (not length.isdecimal() or int(length) > MAX_WRAPPED_INPUT):
                raise WebStreamError("The wrapped video segment is too large.")
            body = bytearray(prefix)
            if len(body) > MAX_WRAPPED_INPUT:
                raise WebStreamError("The wrapped video segment is too large.")
            async for chunk in chunks:
                self._active(stream)
                if len(body) + len(chunk) > MAX_WRAPPED_INPUT:
                    raise WebStreamError("The wrapped video segment is too large.")
                body.extend(chunk)
            self._active(stream)
            decoding = asyncio.create_task(asyncio.to_thread(unwrap_segment, bytes(body), upstream.headers.get("Content-Type", "")))
            try:
                decoded = await asyncio.shield(decoding)
            except asyncio.CancelledError:
                # Keep the slot until the bounded worker exits after Stop.
                await asyncio.gather(decoding, return_exceptions=True)
                raise
            except TransportDecodeError as error:
                raise WebStreamError(str(error)) from None
            self._active(stream)
        resource.wrapped = decoded.changed
        data = decoded.data
        headers = {"Cache-Control": "no-store", "Content-Type": decoded.content_type or "application/octet-stream", "Accept-Ranges": "bytes"}
        status = 200
        value = request.headers.get("Range")
        if value:
            first, _, last = value.removeprefix("bytes=").partition("-")
            start = int(first) if first else max(0, len(data) - int(last))
            stop = min(len(data), int(last) + 1) if first and last else len(data)
            if start >= stop:
                raise web.HTTPRequestRangeNotSatisfiable(headers={"Content-Range": f"bytes */{len(data)}"})
            headers["Content-Range"] = f"bytes {start}-{stop - 1}/{len(data)}"
            data = data[start:stop]
            status = 206
        return web.Response(body=data, status=status, headers=headers)

    async def serve(self, request: web.Request, resource_id: str, local_url: Callable[[str], str]):
        if len(self._tasks) >= 64:
            raise web.HTTPServiceUnavailable(text="Too many simultaneous media requests. Try playback again.")
        resource = self.resources.get(resource_id)
        if resource is None:
            raise web.HTTPNotFound()
        stream = self.streams.get(resource.session_id)
        if stream is None:
            raise web.HTTPNotFound()
        self.resources.move_to_end(resource_id)
        task = asyncio.current_task()
        self._tasks.add(task)
        upstream = None
        response = None
        try:
            address = self._playlist_address(request, resource)
            extra = {}
            range_value = None
            if "Range" in request.headers and resource.kind != "manifest":
                value = request.headers["Range"]
                if not re.fullmatch(r"bytes=(?:[0-9]{1,19}-[0-9]{0,19}|-[0-9]{1,19})", value):
                    raise web.HTTPRequestRangeNotSatisfiable()
                range_value = value
                # Ranges refer to decoded media, never offsets inside a PNG/gzip
                # envelope. Inspect the start once before classifying a segment.
                if resource.kind != "segment" or resource.wrapped is False:
                    extra["Range"] = value
            async with asyncio.timeout(6 * 3600 if resource.kind == "progressive" else 60):
                async with self._semaphore:
                    upstream = await self._fetch(stream, address, extra)
                    content_type = upstream.headers.get("Content-Type", "").split(";", 1)[0].lower().strip()
                    if resource.kind == "manifest" or content_type in HLS_TYPES:
                        text = await self._read_manifest(upstream)
                        self._active(stream)
                        body, _, media = self._rewrite_manifest(stream, resource, text, str(upstream.url), local_url)
                        if media:
                            stream.live = "#EXT-X-ENDLIST" not in text
                        return web.Response(text=body, content_type=HLS_MIME, headers={"Cache-Control": "no-store"})
                    chunks = upstream.content.iter_chunked(65536).__aiter__()
                    prefix = bytearray()
                    if resource.kind == "segment" and resource.wrapped is not False and upstream.status != 416:
                        while len(prefix) < 512:
                            chunk = await anext(chunks, b"")
                            if not chunk:
                                break
                            prefix.extend(chunk)
                        if resource.wrapped or looks_wrapped_segment(bytes(prefix[:65536]), content_type):
                            return await self._unwrap_response(request, stream, resource, upstream, prefix, chunks)
                        resource.wrapped = False
                        if range_value:
                            upstream.close()
                            upstream = await self._fetch(stream, address, {"Range": range_value})
                            content_type = upstream.headers.get("Content-Type", "").split(";", 1)[0].lower().strip()
                            chunks = upstream.content.iter_chunked(65536).__aiter__()
                            prefix.clear()
                    if upstream.headers.get("Content-Encoding", "identity").lower() != "identity":
                        raise WebStreamError("The video source uses an unsupported media encoding.")
                    headers = {"Cache-Control": "no-store", "Content-Type": content_type or "application/octet-stream"}
                    for name in ("Content-Length", "Content-Range", "Accept-Ranges"):
                        if name in upstream.headers:
                            headers[name] = upstream.headers[name]
                    size_limit = MAX_PROGRESSIVE if resource.kind == "progressive" else MAX_SEGMENT
                    length = upstream.headers.get("Content-Length")
                    if length and (not length.isdecimal() or int(length) > size_limit):
                        raise WebStreamError("The video resource is too large.")
                    self._active(stream)
                    response = web.StreamResponse(status=upstream.status, headers=headers)
                    await response.prepare(request)
                    if request.method != "HEAD":
                        size = len(prefix)
                        if prefix:
                            await response.write(prefix)
                        async for chunk in chunks:
                            self._active(stream)
                            size += len(chunk)
                            if size > size_limit:
                                raise WebStreamError("The video resource is too large.")
                            await response.write(chunk)
                    await response.write_eof()
                    return response
        except (WebStreamError, aiohttp.ClientError, OSError, TimeoutError) as error:
            if response is not None and response.prepared:
                if request.transport:
                    request.transport.close()
                return response
            message = str(error) if isinstance(error, WebStreamError) else "The video source stopped responding. Try sending it from Firefox again."
            raise web.HTTPBadGateway(text=message) from None
        finally:
            if upstream is not None:
                upstream.close()
            self._tasks.discard(task)

    def _remove_stream(self, stream: WebStream):
        stream.headers.clear()
        if stream.root_headers is not None:
            stream.root_headers.clear()
        if self.streams.get(stream.session_id) is not stream:
            return
        self.streams.pop(stream.session_id, None)
        for resource_id, resource in list(self.resources.items()):
            if resource.session_id == stream.session_id:
                self.resources.pop(resource_id, None)
                self._resource_ids.pop(self._resource_key(resource), None)

    def reset(self):
        self._generation += 1
        for stream in self.streams.values():
            stream.headers.clear()
            if stream.root_headers is not None:
                stream.root_headers.clear()
        self.streams.clear()
        self.resources.clear()
        self._resource_ids.clear()
        try:
            current = asyncio.current_task()
        except RuntimeError:
            current = None
        for task in self._tasks:
            if task is not current and not task.done():
                task.cancel()
        if self._client is not None:
            client, self._client = self._client, None
            cleanup = asyncio.create_task(client.close())
            self._cleanup_tasks.add(cleanup)
            cleanup.add_done_callback(self._cleanup_tasks.discard)

    async def close(self):
        self.reset()
        current = asyncio.current_task()
        await asyncio.gather(*(task for task in self._tasks | self._cleanup_tasks if task is not current), return_exceptions=True)
