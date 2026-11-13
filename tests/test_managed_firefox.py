import asyncio
from types import SimpleNamespace

from aiohttp import ClientSession

from lantern import managed_firefox
from lantern.web_stream import WebStreamError


async def test_failed_detection_can_continue_in_visible_firefox(tmp_path, monkeypatch):
    class Process:
        def __init__(self):
            self.pid = 123
            self.returncode = None
            self.exited = asyncio.Event()

        async def wait(self):
            await self.exited.wait()

        def terminate(self):
            self.returncode = 0
            self.exited.set()

    sessions = asyncio.Queue()
    launches = []
    commands = []
    events = []

    async def noop(*args):
        pass

    async def spawn(executable, *args, **kwargs):
        if args[0] == "/PID":
            process = launches[-1][1]
            process.terminate()
            return process
        process = Process()
        launches.append((args, process))
        return process

    async def handle(message):
        commands.append(message["action"])
        if len(commands) == 1:
            raise WebStreamError("The video source is unavailable.")
        return {"ok": True}

    monkeypatch.setattr(managed_firefox, "firefox_path", lambda: "firefox")
    monkeypatch.setattr(managed_firefox, "PublicResolver", lambda: SimpleNamespace(resolve=noop, close=noop))
    monkeypatch.setattr(managed_firefox, "bootstrap", noop)
    monkeypatch.setattr(managed_firefox, "managed_addon", lambda path, config: sessions.put_nowait(config))
    monkeypatch.setattr(managed_firefox.asyncio, "create_subprocess_exec", spawn)
    browser = managed_firefox.ManagedFirefox(tmp_path, handle, lambda event, data: events.append((event, data)))

    async with ClientSession() as client:
        async def command(config, action):
            source = {"url": "https://media.example/movie.m3u8", "sessionId": config["sessionId"]}
            async with client.post(config["endpoint"], json={"action": action, "source": source},
                                   headers={"X-Lantern-Link": config["token"]}) as response:
                assert response.status == 200
                return await response.json()

        try:
            browser.play("https://movies.example/watch")
            hidden = await asyncio.wait_for(sessions.get(), 2)
            assert hidden["interactive"] is False and "-headless" in launches[-1][0]
            assert await command(hidden, "play") == {"ok": False, "retryable": True}
            assert not browser.task.done()
            await command(hidden, "no-video")
            await asyncio.wait_for(browser.task, 2)
            retry = next(data for event, data in events if event == "web.interaction_required")
            assert retry == {"retry_id": hidden["sessionId"], "message": "The video source is unavailable."}

            browser.continue_playback("stale-request", True)
            assert browser.task.done()
            browser.continue_playback(retry["retry_id"], True)
            visible = await asyncio.wait_for(sessions.get(), 2)
            assert visible["interactive"] is True and "-headless" not in launches[-1][0]
            assert await command(visible, "play") == {"ok": True}
            await command(visible, "no-video")
            assert not browser.task.done()
            launches[-1][1].terminate()
            await asyncio.wait_for(browser.task, 2)
            assert commands == ["play", "play", "stop"]
            assert not browser.pending_retry
        finally:
            await browser.close()
