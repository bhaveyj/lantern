from __future__ import annotations

import asyncio
import copy
import ipaddress
import logging
import socket
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse

import aiohttp
import pychromecast
from zeroconf import ServiceBrowser, ServiceListener, Zeroconf

from .media import Preparation, needs_preparation, probe
from .server import MediaServer

RECEIVER_PING_INTERVAL = 10.0
CAST_LOAD_TIMEOUT = 15.0
logger = logging.getLogger(__name__)


@dataclass
class Device:
    id: str
    name: str
    host: str
    port: int
    kind: str
    uuid: str = ""
    model: str = ""


def local_address(destination: str) -> str:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as connection:
        connection.connect((destination, 9))
        return connection.getsockname()[0]


def validate_host(value: str) -> str:
    try:
        address = ipaddress.ip_address(value.strip())
    except ValueError:
        raise ValueError("Enter the TV's IPv4 address, such as 192.168.1.25.") from None
    if address.version != 4 or not address.is_private or address.is_unspecified or address.is_multicast:
        raise ValueError("Use an IPv4 address on your home network.")
    return str(address)


class Discovery(ServiceListener):
    def __init__(self, callback: Callable[[str, dict], None]):
        self.callback = callback
        self.zeroconf = Zeroconf()
        self.browser = ServiceBrowser(self.zeroconf, ["_lantern._tcp.local.", "_googlecast._tcp.local."], self)

    def add_service(self, zc, service_type, name):
        self.update_service(zc, service_type, name)

    def update_service(self, zc, service_type, name):
        info = zc.get_service_info(service_type, name, timeout=2000)
        if info and info.parsed_addresses():
            addresses = [a for a in info.parsed_addresses() if ":" not in a]
            if not addresses:
                return
            props = {k.decode(errors="replace"): v.decode(errors="replace") for k, v in info.properties.items() if v}
            device = Device(name, props.get("fn", name.split("._")[0]), addresses[0], info.port, "cast" if "_googlecast" in service_type else "receiver", props.get("id", ""), props.get("md", ""))
            self.callback("device", vars(device))

    def remove_service(self, zc, service_type, name):
        self.callback("device.removed", {"id": name})

    def close(self):
        self.browser.cancel()
        self.zeroconf.close()


class CastListener:
    def __init__(self, controller, generation: int):
        self.controller = controller
        self.generation = generation

    def new_media_status(self, status):
        self.controller.new_media_status(status, self.generation)

    def load_media_failed(self, queue_item_id, error_code):
        self.controller.load_media_failed(queue_item_id, error_code, self.generation)


class Controller:
    def __init__(self, cache: Path, emit: Callable[[str, dict], None]):
        self.emit = emit
        self.server = MediaServer()
        self.preparation = Preparation(cache)
        self.session: aiohttp.ClientSession | None = None
        self.ws: aiohttp.ClientWebSocketResponse | None = None
        self.reader: asyncio.Task | None = None
        self.receiver_keepalive_task: asyncio.Task | None = None
        self.play_task: asyncio.Task | None = None
        self.preload_task: asyncio.Task | None = None
        self.cast_poll_task: asyncio.Task | None = None
        self.playlist: list[dict] = []
        self.device: Device | None = None
        self.cast = None
        self.audio_mimes: list[str] = []
        self.current_id: str | None = None
        self.current_position = 0.0
        self.playing = False
        self.loop: asyncio.AbstractEventLoop | None = None
        self.cast_enqueued: set[str] = set()
        self.cast_loaded = False
        self.cast_finished_id: str | None = None
        self.mime_types: dict[str, str] = {}
        self._operation_lock = asyncio.Lock()
        self._playback_tasks: set[asyncio.Task] = set()
        self._session_generation = 0
        self._cast_load_result: asyncio.Future | None = None
        self._pending_cast_id: str | None = None
        self._subtitle_revisions: dict[str, int] = {}

    async def cast_call(self, function, *args, **kwargs):
        task = asyncio.create_task(asyncio.to_thread(function, *args, **kwargs))
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            # Cancelling to_thread does not stop the underlying Cast command.
            # Finish it before another operation can take ownership of the session.
            await asyncio.gather(task, return_exceptions=True)
            raise

    def playback_task(self, coroutine) -> asyncio.Task:
        task = asyncio.create_task(coroutine)
        self._playback_tasks.add(task)
        task.add_done_callback(self._playback_tasks.discard)
        return task

    async def cancel_playback(self):
        current = asyncio.current_task()
        tasks = [task for task in self._playback_tasks if task is not current and not task.done()]
        for task in tasks:
            # A second cancellation can interrupt a subprocess's finally block.
            if not task.cancelling():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def start(self):
        self.loop = asyncio.get_running_loop()
        self.session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=12))
        await self.server.start()

    async def sync(self, items: list[dict]):
        async with self._operation_lock:
            await self._sync(items)

    async def _sync(self, items: list[dict]):
        previous_items = {m["id"]: m for m in self.playlist}
        old_order = [m["id"] for m in self.playlist]
        previous_paths = {m["id"]: m["path"] for m in self.playlist}
        prepared_files = dict(self.server.files)
        self.playlist = copy.deepcopy(items)
        self._subtitle_revisions = {m["id"]: self._subtitle_revisions.get(m["id"], 0) for m in self.playlist}
        self.server.files.clear()
        self.server.subtitles.clear()
        for item in self.playlist:
            previous_item = previous_items.get(item["id"])
            if previous_item and self._subtitle_revisions[item["id"]]:
                selected = previous_item.get("selected_subtitle")
                item["selected_subtitle"] = selected if selected in {s["id"] for s in item["subtitles"]} else None
            previous = prepared_files.get(item["id"])
            self.server.files[item["id"]] = previous if previous_paths.get(item["id"]) == item["path"] and previous and previous.is_file() else Path(item["path"])
            for subtitle in item["subtitles"]:
                self.server.subtitles[subtitle["id"]] = Path(subtitle["path"])
        if self.ws and not self.ws.closed:
            await self.send({"type": "playlist.snapshot", "items": self.wire_playlist()})
        if self.cast and self.current_id and old_order != [m["id"] for m in self.playlist]:
            new_order = [m["id"] for m in self.playlist]
            if self.current_id in new_order:
                old_index = old_order.index(self.current_id) if self.current_id in old_order else -1
                new_index = new_order.index(self.current_id)
                old_next = old_order[old_index + 1] if 0 <= old_index < len(old_order) - 1 else None
                new_next = new_order[new_index + 1] if new_index + 1 < len(new_order) else None
                if old_next != new_next:
                    if old_next in self.cast_enqueued:
                        self.queue_play(self.current_id, self.current_position, autoplay=self.playing)
                    else:
                        self.preload_task = self.playback_task(self.preload_next(self.current_id))

    def wire_playlist(self) -> list[dict]:
        if not self.device:
            return []
        host = local_address(self.device.host)
        return [{"id": m["id"], "title": m["title"], "url": self.server.url(host, m["id"]), "selectedSubtitle": m.get("selected_subtitle"), "subtitleRevision": self._subtitle_revisions.get(m["id"], 0), "subtitles": [{"id": s["id"], "label": s["label"], "url": self.server.subtitle_url(host, s["id"])} for s in m["subtitles"]]} for m in self.playlist]

    async def connect(self, device: dict, pin: str = ""):
        await self.cancel_playback()
        async with self._operation_lock:
            await self._connect(device, pin)

    async def _connect(self, device: dict, pin: str):
        await self._disconnect()
        self._session_generation += 1
        self.device = Device(**device)
        self.emit("status", {"message": f"Connecting to {self.device.name}…", "busy": True})
        try:
            if self.device.kind == "receiver":
                if len(pin) != 6 or not pin.isdigit():
                    raise ValueError("Enter the six-digit code displayed by Lantern on your TV.")
                self.ws = await self.session.ws_connect(f"http://{self.device.host}:{self.device.port}/control", heartbeat=20, max_msg_size=1024 * 1024)
                await self.send({"type": "pair", "version": 1, "pin": pin, "name": socket.gethostname()})
                response = await asyncio.wait_for(self.ws.receive_json(), timeout=8)
                if response.get("type") != "pair.ok":
                    raise ValueError(response.get("message", "Pairing was rejected by the TV."))
                self.audio_mimes = response.get("audioMimes", [])
                self.reader = asyncio.create_task(self.read_receiver(self.ws))
                self.receiver_keepalive_task = asyncio.create_task(self.keep_receiver_alive(self.ws))
                await self._sync(self.playlist)
            else:
                device_uuid = uuid.UUID(self.device.uuid) if self.device.uuid else uuid.uuid4()
                self.cast = await self.cast_call(pychromecast.get_chromecast_from_host, (self.device.host, self.device.port, device_uuid, self.device.model, self.device.name))
                await self.cast_call(self.cast.wait, 10)
                self.cast.media_controller.register_status_listener(CastListener(self, self._session_generation))
                self.cast_poll_task = asyncio.create_task(self.poll_cast())
            self.emit("connected", {"name": self.device.name, "kind": self.device.kind})
            self.emit("status", {"message": "Connected · choose a video and play", "busy": False})
        except Exception:
            await self._disconnect()
            raise

    async def send(self, message: dict):
        if self.ws and not self.ws.closed:
            await self.ws.send_json(message)

    async def keep_receiver_alive(self, ws: aiohttp.ClientWebSocketResponse):
        try:
            while not ws.closed:
                await asyncio.sleep(RECEIVER_PING_INTERVAL)
                if not ws.closed:
                    # aiohttp's automatic heartbeat is postponed by every TV
                    # state update. NanoWSD still needs inbound traffic within
                    # 30 seconds, including during untouched/paused playback.
                    await ws.ping()
        except asyncio.CancelledError:
            raise
        except Exception as error:
            logger.warning("TV control keepalive failed: %s", error)
            await ws.close()

    async def read_receiver(self, ws=None):
        ws = ws or self.ws
        try:
            async for message in ws:
                if ws is not self.ws:
                    break
                if message.type != aiohttp.WSMsgType.TEXT:
                    continue
                import json
                data = json.loads(message.data)
                if data.get("type") == "playback.state":
                    self.receiver_state(data)
                elif data.get("type") == "request.play":
                    self.queue_play(data["id"])
                elif data.get("type") == "error":
                    self.emit("error", {"message": data.get("message", "The TV could not play this file.")})
        except asyncio.CancelledError:
            raise
        except Exception as error:
            self.emit("error", {"message": f"TV connection lost: {error}"})
        finally:
            if ws and ws.closed and ws is self.ws:
                logger.info("TV control connection closed (code %s)", ws.close_code)
                asyncio.create_task(self.disconnect(expected_ws=ws))

    def receiver_state(self, data: dict):
        data = dict(data)
        self.current_id = data.get("id")
        self.current_position = data.get("position", 0)
        self.playing = data.get("playing", False)
        item = next((m for m in self.playlist if m["id"] == self.current_id), None)
        if item and "subtitleId" in data:
            selected = data["subtitleId"]
            current_revision = self._subtitle_revisions.get(item["id"], 0)
            revision = data.get("subtitleRevision", current_revision)
            valid = selected is None or selected in {s["id"] for s in item["subtitles"]}
            if valid and isinstance(revision, int) and revision >= current_revision:
                if "subtitleRevision" not in data and selected != item.get("selected_subtitle"):
                    revision += 1
                item["selected_subtitle"] = selected
                self._subtitle_revisions[item["id"]] = revision
            else:
                data.pop("subtitleId", None)
        self.emit("state", data)

    def queue_play(self, media_id: str, position: float = 0, *, autoplay: bool = True):
        if self.play_task and not self.play_task.done() and not self.play_task.cancelling():
            self.play_task.cancel()
        if self.preload_task and not self.preload_task.done() and not self.preload_task.cancelling():
            self.preload_task.cancel()
        self.play_task = self.playback_task(self.guarded_play(media_id, position, autoplay=autoplay, generation=self._session_generation))

    async def guarded_play(self, media_id: str, position: float = 0, *, autoplay: bool = True, generation=None):
        try:
            await self.play(media_id, position, autoplay=autoplay, generation=generation)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            self.emit("error", {"message": str(error)})
        finally:
            if asyncio.current_task() is self.play_task and generation == self._session_generation:
                self.emit("status", {"message": "Ready", "busy": False})

    async def prepared_url(self, item: dict, cast: bool) -> tuple[str, float]:
        path = Path(item["path"])
        if not path.is_file():
            raise ValueError(f"The original file is no longer available: {path.name}")
        info = await probe(path)
        self.mime_types[item["id"]] = "video/mp4" if info.video else "audio/mp4"
        if needs_preparation(info, cast, self.audio_mimes, path.suffix):
            self.emit("status", {"message": f"Preparing {item['title']}…", "busy": True})
            path = await self.preparation.prepare(path, info, lambda percent: self.emit("progress", {"percent": percent, "title": item["title"]}))
        self.server.files[item["id"]] = path
        return self.server.url(local_address(self.device.host), item["id"]), info.duration

    async def play(self, media_id: str, position: float = 0, *, autoplay: bool = True, generation=None):
        task = asyncio.current_task()
        self._playback_tasks.add(task)
        try:
            if self.preload_task and not self.preload_task.done():
                if not self.preload_task.cancelling():
                    self.preload_task.cancel()
                await asyncio.gather(self.preload_task, return_exceptions=True)
            async with self._operation_lock:
                if generation is not None and generation != self._session_generation:
                    return
                await self._play(media_id, position, autoplay=autoplay)
        finally:
            self._playback_tasks.discard(task)

    async def _play(self, media_id: str, position: float = 0, *, autoplay: bool = True):
        if not self.device:
            raise ValueError("Connect to your TV first.")
        item = next(m for m in self.playlist if m["id"] == media_id)
        url, duration = await self.prepared_url(item, self.device.kind == "cast")
        self.current_id = media_id
        self.current_position = position
        if self.device.kind == "receiver":
            wire = next(m for m in self.wire_playlist() if m["id"] == media_id)
            wire.update(url=url, duration=duration, position=position, autoplay=autoplay)
            await self.send({"type": "playback.load", "item": wire})
        else:
            self.cast_enqueued = {media_id}
            self.cast_loaded = False
            self.cast_finished_id = None
            if self.cast.media_controller.status.media_session_id is not None:
                await self.cast_call(self.cast.media_controller.stop)
            self._pending_cast_id = media_id
            result = self._cast_load_result = asyncio.get_running_loop().create_future()
            try:
                await self.cast_load(item, url, False, position, autoplay=autoplay)
                await asyncio.wait_for(asyncio.shield(result), CAST_LOAD_TIMEOUT)
            except TimeoutError:
                self.cast_failed("The Cast receiver did not load the requested file. Check the TV and home network.")
                raise RuntimeError("The Cast receiver did not load the requested file.") from None
            finally:
                if not result.done():
                    result.cancel()
                elif not result.cancelled():
                    result.exception()
                self._cast_load_result = None
                self._pending_cast_id = None
            self.preload_task = self.playback_task(self.preload_next(media_id))
        if self.device.kind == "receiver":
            self.playing = autoplay
            self.emit("state", {"id": media_id, "playing": autoplay, "position": position, "duration": duration})

    async def cast_load(self, item: dict, url: str, enqueue: bool, position: float = 0, *, autoplay: bool = True):
        sub = next((s for s in item["subtitles"] if s["id"] == item.get("selected_subtitle")), None)
        subtitle_url = self.server.subtitle_url(local_address(self.device.host), sub["id"]) if sub else None
        await self.cast_call(self.cast.media_controller.play_media, url, self.mime_types.get(item["id"], "video/mp4"), title=item["title"], stream_type="BUFFERED", subtitles=subtitle_url, subtitles_mime="text/vtt", enqueue=enqueue, current_time=position, autoplay=autoplay)

    async def preload_next(self, media_id: str):
        async with self._operation_lock:
            await self._preload_next(media_id)

    async def _preload_next(self, media_id: str):
        try:
            index = next(i for i, m in enumerate(self.playlist) if m["id"] == media_id)
            if index + 1 < len(self.playlist):
                item = self.playlist[index + 1]
                if item["id"] not in self.cast_enqueued:
                    url, _ = await self.prepared_url(item, True)
                    await self.cast_load(item, url, True)
                    self.cast_enqueued.add(item["id"])
        except asyncio.CancelledError:
            raise
        except Exception as error:
            self.emit("notice", {"message": f"Next video could not be queued: {error}"})
        finally:
            self.emit("status", {"message": "Connected", "busy": False})

    def new_media_status(self, status, generation=None):
        if self.loop and not self.loop.is_closed():
            self.loop.call_soon_threadsafe(self.on_cast_state, {"id": urlparse(status.content_id or "").path.rsplit("/", 1)[-1], "playing": status.player_state == "PLAYING", "position": status.current_time or 0, "duration": status.duration or 0, "playerState": status.player_state, "idleReason": status.idle_reason}, generation)

    def load_media_failed(self, queue_item_id, error_code, generation=None):
        if self.loop and not self.loop.is_closed():
            self.loop.call_soon_threadsafe(self.on_cast_load_failed, error_code, generation)

    def on_cast_load_failed(self, error_code, generation=None):
        if generation is not None and generation != self._session_generation:
            return
        message = f"Cast could not load this file (decoder/network code {error_code})."
        pending = self._cast_load_result
        self.cast_failed(message)
        if pending and not pending.done():
            pending.set_exception(RuntimeError(message))
        else:
            self.emit("error", {"message": message})

    def cast_failed(self, message):
        self.cast_loaded = False
        self.current_id = None
        self.current_position = 0
        self.playing = False
        self.emit("state", {"id": None, "playing": False, "position": 0, "duration": 0, "playerState": "IDLE"})
        self.emit("notice", {"message": message})

    async def poll_cast(self):
        while self.cast:
            await asyncio.sleep(2)
            if self.cast and self.cast_loaded:
                try:
                    await self.cast_call(self.cast.media_controller.update_status)
                except Exception:
                    self.emit("notice", {"message": "Cast status is temporarily unavailable. Reconnect if playback has stopped."})

    def on_cast_state(self, data, generation=None):
        if generation is not None and generation != self._session_generation:
            return
        pending = self._cast_load_result
        if pending and not pending.done() and data["id"] == self._pending_cast_id:
            if data.get("idleReason") == "ERROR":
                self.on_cast_load_failed("PLAYBACK_ERROR", generation)
                return
            if data.get("playerState") in {"PLAYING", "PAUSED", "BUFFERING"}:
                self.cast_loaded = True
                pending.set_result(data)
        if not self.cast_loaded:
            return
        if data.get("idleReason") == "ERROR":
            self.on_cast_load_failed("PLAYBACK_ERROR", generation)
            return
        if data["id"] in {m["id"] for m in self.playlist}:
            changed = self.current_id != data["id"]
            self.current_id = data["id"]
            self.current_position = data["position"]
            self.playing = data["playing"]
            self.emit("state", data)
            if changed:
                self.preload_task = self.playback_task(self.preload_next(self.current_id))
        else:
            self.cast_failed("Cast playback ended or another app took control of the TV.")

    async def command(self, action: str, value=None):
        if not self.device:
            return
        if self.device.kind == "receiver":
            await self.send({"type": "playback.command", "action": action, "value": value})
        elif self.cast:
            mc = self.cast.media_controller
            if action == "play":
                await self.cast_call(mc.play)
            elif action == "pause":
                await self.cast_call(mc.pause)
            elif action == "seek":
                await self.cast_call(mc.seek, max(0, float(value)))
            elif action == "volume":
                await self.cast_call(self.cast.set_volume, float(value))
            elif action in {"next", "previous"} and self.current_id:
                index = next((i for i, m in enumerate(self.playlist) if m["id"] == self.current_id), 0)
                index = max(0, min(len(self.playlist) - 1, index + (1 if action == "next" else -1)))
                self.queue_play(self.playlist[index]["id"])

    async def subtitle(self, media_id: str, subtitle_id: str | None):
        async with self._operation_lock:
            await self._subtitle(media_id, subtitle_id)

    async def _subtitle(self, media_id: str, subtitle_id: str | None):
        item = next(m for m in self.playlist if m["id"] == media_id)
        if subtitle_id is not None and subtitle_id not in {s["id"] for s in item["subtitles"]}:
            raise ValueError("This subtitle is no longer in the playlist.")
        item["selected_subtitle"] = subtitle_id
        revision = self._subtitle_revisions[media_id] = self._subtitle_revisions.get(media_id, 0) + 1
        if self.device and self.device.kind == "receiver":
            await self.send({"type": "playlist.snapshot", "items": self.wire_playlist()})
            if self.current_id == media_id:
                await self.send({"type": "playback.command", "action": "subtitle", "value": subtitle_id, "revision": revision})
        elif self.current_id == media_id:
            self.emit("notice", {"message": "The subtitle selection will apply when you play this video again in Cast mode."})

    async def stop(self):
        await self.cancel_playback()
        async with self._operation_lock:
            await self._stop()

    async def _stop(self):
        self.cast_loaded = False
        try:
            if self.ws and not self.ws.closed:
                await asyncio.wait_for(self.send({"type": "playback.command", "action": "stop"}), timeout=3)
            if self.cast and self.cast.media_controller.status.media_session_id is not None:
                await self.cast_call(self.cast.media_controller.stop)
        except Exception as error:
            logger.warning("Remote stop failed: %s", error)
        finally:
            self.server.reset()
            self.current_id = None
            self.current_position = 0
            self.playing = False
            self.cast_enqueued.clear()
            await self.preparation.cleanup_async()
            for item in self.playlist:
                self.server.files[item["id"]] = Path(item["path"])
                for subtitle in item["subtitles"]:
                    self.server.subtitles[subtitle["id"]] = Path(subtitle["path"])
            self.emit("state", {"playing": False, "position": 0, "duration": 0})
            self.emit("status", {"message": "Stopped", "busy": False})
        try:
            await asyncio.wait_for(self.send({"type": "playlist.snapshot", "items": self.wire_playlist()}), timeout=3)
        except Exception as error:
            logger.warning("Stopped playlist could not be published: %s", error)

    async def disconnect(self, expected_ws=None):
        if expected_ws is not None and expected_ws is not self.ws:
            return
        await self.cancel_playback()
        async with self._operation_lock:
            if expected_ws is None or expected_ws is self.ws:
                await self._disconnect()

    async def _disconnect(self):
        self._session_generation += 1
        try:
            await self._stop()
        finally:
            if self.receiver_keepalive_task:
                self.receiver_keepalive_task.cancel()
                await asyncio.gather(self.receiver_keepalive_task, return_exceptions=True)
                self.receiver_keepalive_task = None
            if self.reader:
                self.reader.cancel()
                await asyncio.gather(self.reader, return_exceptions=True)
                self.reader = None
            if self.ws:
                try:
                    await self.ws.close()
                except Exception as error:
                    logger.warning("Receiver socket cleanup failed: %s", error)
                finally:
                    self.ws = None
            if self.cast:
                if self.cast_poll_task:
                    self.cast_poll_task.cancel()
                    await asyncio.gather(self.cast_poll_task, return_exceptions=True)
                    self.cast_poll_task = None
                try:
                    await self.cast_call(self.cast.disconnect)
                except Exception as error:
                    logger.warning("Cast cleanup failed: %s", error)
                finally:
                    self.cast = None
            self.device = None
            self.emit("disconnected", {})

    async def close(self):
        try:
            await self.disconnect()
        finally:
            if self.session:
                await self.session.close()
            await self.server.stop()
