"""Open a web video in a private Firefox profile owned by Lantern."""
from __future__ import annotations

import asyncio
import contextlib
import json
import os
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import uuid
import zipfile
from pathlib import Path
from urllib.parse import urlsplit

from aiohttp import web

from .browser_bridge import BrowserCommandError, MAX_MESSAGE, validate_message
from .web_stream import PublicResolver, WebStreamError, checked_url

ADDON_ID = "lantern-tv@lantern.local"


def firefox_path() -> str:
    for directory in (os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)"), os.environ.get("LOCALAPPDATA")):
        if directory:
            candidate = Path(directory) / "Mozilla Firefox" / "firefox.exe"
            if candidate.is_file():
                return str(candidate)
    path = shutil.which("firefox")
    if path:
        return path
    raise BrowserCommandError("Install Firefox to play website links in Lantern.")


def addon_folder() -> Path:
    root = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parents[2]
    return root / "extension" / "firefox"


def managed_addon(path: Path, config: dict) -> None:
    folder = addon_folder()
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    manifest["permissions"].remove("nativeMessaging")
    manifest["permissions"].append("http://127.0.0.1/*")
    manifest["background"]["scripts"].extend(["managed-config.js", "managed.js"])
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as addon:
        for name in ("core.js", "background.js", "content.js", "popup.html", "popup.css", "popup.js", "managed.js"):
            addon.write(folder / name, name)
        addon.writestr("manifest.json", json.dumps(manifest))
        addon.writestr("managed-config.js", "const LANTERN_MANAGED = " + json.dumps(config) + ";")


async def bootstrap(process, port: int, addon: Path) -> None:
    """Load Lantern's temporary add-on before letting Firefox open a website."""
    async with asyncio.timeout(20):
        while True:
            if process.returncode is not None:
                raise BrowserCommandError("Firefox could not start the video session.")
            try:
                reader, writer = await asyncio.open_connection("127.0.0.1", port, limit=65536)
                break
            except OSError:
                await asyncio.sleep(0.15)
        async def read():
            length = await reader.readuntil(b":")
            if not length[:-1].isdigit() or len(length) > 8 or not 0 < int(length[:-1]) <= 65536:
                raise BrowserCommandError("Firefox returned an invalid setup response.")
            return json.loads(await reader.readexactly(int(length[:-1])))

        async def command(number, name, parameters):
            message = json.dumps([0, number, name, parameters]).encode()
            writer.write(str(len(message)).encode() + b":" + message)
            await writer.drain()
            response = await read()
            if not isinstance(response, list) or len(response) != 4 or response[:3] != [1, number, None]:
                raise BrowserCommandError("Firefox could not set up Lantern video playback. Update Firefox and try again.")

        try:
            greeting = await read()
            if not isinstance(greeting, dict) or greeting.get("applicationType") != "gecko":
                raise BrowserCommandError("Firefox setup did not start.")
            await command(1, "WebDriver:NewSession", {"capabilities": {"alwaysMatch": {}}})
            await command(2, "Addon:Install", {"path": str(addon), "temporary": True})
            await command(3, "Marionette:AcceptConnections", {"value": False})
        finally:
            writer.close()
            with contextlib.suppress(OSError):
                await writer.wait_closed()
        # A listening setup port is a failure. The addon waits for this check
        # before it receives the user's URL.
        try:
            _, probe = await asyncio.open_connection("127.0.0.1", port)
        except OSError:
            return
        probe.close()
        await probe.wait_closed()
        raise BrowserCommandError("Firefox could not close its setup connection.")


class ManagedFirefox:
    def __init__(self, data: Path, handle, emit):
        self.data = data
        self.handle = handle
        self.emit = emit
        self.task: asyncio.Task | None = None
        self.cleanup: set[asyncio.Task] = set()

    def cancel(self):
        task, self.task = self.task, None
        if task and not task.done():
            task.cancel()
            self.cleanup.add(task)
            task.add_done_callback(self.cleanup.discard)

    async def close(self):
        self.cancel()
        await asyncio.gather(*list(self.cleanup), return_exceptions=True)

    def play(self, url: str):
        url = checked_url(url.strip())
        executable = firefox_path()
        self.cancel()
        self.emit("status", {"message": "Opening video link…", "busy": True})
        self.task = asyncio.create_task(self._run(executable, url))

    async def _run(self, executable: str, url: str):
        process = runner = folder = None
        active_requests: set[asyncio.Task] = set()
        ready = asyncio.Event()
        finished = asyncio.Event()
        error = None
        started = False
        token, session_id, extension_uuid = secrets.token_urlsafe(32), uuid.uuid4().hex, str(uuid.uuid4())
        port = 0

        async def command(request):
            nonlocal error, started
            if (request.remote != "127.0.0.1" or request.headers.get("Host") != f"127.0.0.1:{port}"
                    or request.headers.get("Origin") not in (None, f"moz-extension://{extension_uuid}")
                    or not secrets.compare_digest(request.headers.get("X-Lantern-Link", "").encode(), token.encode())):
                raise web.HTTPForbidden()
            if request.content_type != "application/json" or len(active_requests) >= 2:
                raise web.HTTPBadRequest()
            task = asyncio.current_task()
            active_requests.add(task)
            try:
                data = await request.json()
                if not isinstance(data, dict):
                    raise ValueError
                action = data.get("action")
                if action == "open":
                    return web.json_response({"url": url if ready.is_set() else None})
                if not ready.is_set():
                    raise web.HTTPForbidden()
                if action == "no-video":
                    error = "The page did not start a playable video. Try a direct HLS or MP4 link."
                    finished.set()
                    return web.json_response({"ok": False})
                message = validate_message(data)
                source = message.get("source") or {}
                if action not in {"play", "refresh"} or source.get("sessionId") != session_id:
                    raise ValueError
                source["preferCompatible"] = True
                try:
                    async with asyncio.timeout(42):
                        result = await self.handle(message)
                    started = started or action == "play"
                    return web.json_response(result)
                except (BrowserCommandError, WebStreamError) as exc:
                    error = str(exc)
                except (TimeoutError, OSError):
                    error = "The video link took too long to start."
                if action == "play":
                    finished.set()
                return web.json_response({"ok": False, "error": error})
            except (ValueError, TypeError, RecursionError):
                raise web.HTTPBadRequest() from None
            finally:
                active_requests.discard(task)

        try:
            resolver = PublicResolver()
            try:
                async with asyncio.timeout(6):
                    await resolver.resolve(urlsplit(url).hostname, 443, socket.AF_INET)
            except (OSError, TimeoutError):
                raise BrowserCommandError("The video website could not be reached at a public address.") from None
            finally:
                await resolver.close()
            self.data.mkdir(parents=True, exist_ok=True)
            folder = Path(tempfile.mkdtemp(prefix="link-", dir=self.data))
            profile = folder / "profile"
            profile.mkdir()
            app = web.Application(client_max_size=MAX_MESSAGE)
            app.router.add_post("/command", command)
            runner = web.AppRunner(app, access_log=None)
            await runner.setup()
            await web.TCPSite(runner, "127.0.0.1", 0).start()
            port = runner.addresses[0][1]
            with socket.socket() as listener:
                listener.bind(("127.0.0.1", 0))
                setup_port = listener.getsockname()[1]
            preferences = {
                "marionette.port": setup_port, "browser.shell.checkDefaultBrowser": False,
                "browser.startup.homepage_override.mstone": "ignore", "media.autoplay.default": 0,
                "browser.download.folderList": 2, "browser.download.dir": str(folder / "downloads"),
                "extensions.webextensions.uuids": json.dumps({ADDON_ID: extension_uuid}),
            }
            (profile / "user.js").write_text(
                "\n".join(f"user_pref({json.dumps(name)}, {json.dumps(value)});" for name, value in preferences.items()),
                encoding="utf-8",
            )
            addon = folder / "lantern.xpi"
            managed_addon(addon, {"endpoint": f"http://127.0.0.1:{port}/command", "token": token, "sessionId": session_id})
            process = await asyncio.create_subprocess_exec(
                executable, "-wait-for-browser", "-headless", "-no-remote", "-profile", str(profile),
                "-marionette", "about:blank", stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                **({"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}),
            )
            await bootstrap(process, setup_port, addon)
            ready.set()
            async def startup_timeout():
                nonlocal error
                await asyncio.sleep(90)
                if not started:
                    error = "The page did not start a playable video."
                    finished.set()

            watchdog = asyncio.create_task(startup_timeout())
            watchers = {asyncio.create_task(process.wait()), asyncio.create_task(finished.wait())}
            try:
                await asyncio.wait(watchers, return_when=asyncio.FIRST_COMPLETED)
            finally:
                watchers.add(watchdog)
                for watcher in watchers:
                    watcher.cancel()
                await asyncio.gather(*watchers, return_exceptions=True)
            if error:
                raise BrowserCommandError(error)
            if not started:
                raise BrowserCommandError("Firefox closed before the video started.")
            await self.handle({"action": "stop", "source": {"sessionId": session_id}})
        except BrowserCommandError as exc:
            self.emit("error", {"message": str(exc)})
        except (TimeoutError, OSError, ValueError, asyncio.IncompleteReadError):
            self.emit("error", {"message": "Lantern could not open the video in Firefox. Check that Firefox is up to date and try again."})
        finally:
            for task in list(active_requests):
                task.cancel()
            if active_requests:
                await asyncio.gather(*list(active_requests), return_exceptions=True)
            if runner:
                await runner.cleanup()
            if process and process.returncode is None:
                if os.name == "nt":
                    killer = await asyncio.create_subprocess_exec(
                        str(Path(os.environ["SystemRoot"]) / "System32" / "taskkill.exe"),
                        "/PID", str(process.pid), "/T", "/F", stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                        creationflags=subprocess.CREATE_NO_WINDOW,
                    )
                    await killer.wait()
                else:
                    process.terminate()
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(process.wait(), 5)
            if folder and folder.resolve().parent == self.data.resolve() and folder.name.startswith("link-"):
                for _ in range(12):
                    try:
                        await asyncio.to_thread(shutil.rmtree, folder)
                        break
                    except FileNotFoundError:
                        if not folder.exists():
                            break
                    except OSError:
                        pass
                    # Windows can briefly retain a handle after Firefox exits.
                    await asyncio.sleep(0.25)
