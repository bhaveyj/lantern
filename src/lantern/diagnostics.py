"""Installed-executable receiver stability check for development verification."""
from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import asdict
from pathlib import Path

from .controller import Controller, Device, validate_host


def check_receiver(config_path: Path, data_directory: Path) -> None:
    config = json.loads(config_path.read_text(encoding="utf-8-sig"))
    output = Path(config["report"])
    logging.basicConfig(filename=data_directory / "diagnostics.log", level=logging.INFO)

    async def check():
        began = time.monotonic()
        observations = []
        errors = []
        state = {}
        monitoring = False
        report = {"result": "running", "transport": "LAN", "stages": []}

        def save():
            report["elapsedSeconds"] = round(time.monotonic() - began, 2)
            report["lastState"] = state
            report["stateSamples"] = len(observations)
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(json.dumps(report, indent=2), encoding="utf-8")

        def emit(kind, data):
            nonlocal state
            if kind == "state" and data.get("type") == "playback.state":
                state = data
                observations.append((time.monotonic(), dict(data)))
            if monitoring and (kind == "error" or kind == "disconnected"):
                errors.append(data.get("message", "Receiver disconnected"))

        controller = Controller(data_directory / "cache", emit)

        def healthy():
            if errors:
                raise RuntimeError(errors[0])
            if controller.ws is None or controller.ws.closed:
                raise RuntimeError("Receiver control connection closed")

        async def wait_for(predicate, description):
            for _ in range(120):
                healthy()
                if predicate(state):
                    return
                await asyncio.sleep(.5)
            raise RuntimeError(f"Timed out: {description}; state={state}")

        async def hold(name, seconds, playing):
            start = time.monotonic()
            initial = state.get("position", 0)
            sample_count = len(observations)
            while time.monotonic() - start < seconds:
                healthy()
                save()
                await asyncio.sleep(1)
            healthy()
            assert len(observations) - sample_count >= seconds / 3, "TV status updates stopped"
            if playing:
                assert state.get("playing"), "Playback stopped during the stability check"
                assert state.get("position", 0) - initial >= seconds * .7, "Playback did not advance normally"
            else:
                assert not state.get("playing"), "Paused playback unexpectedly resumed"
                assert abs(state.get("position", 0) - initial) < 2, "Paused position changed"
            report["stages"].append({"name": name, "seconds": seconds, "initialPosition": initial, "finalPosition": state.get("position"), "result": "passed"})
            print(f"PASS {name}: {seconds}s, position {state.get('position')}", flush=True)
            save()

        await controller.start()
        try:
            video = Path(config["video"])
            await controller.sync([{"id": "stability-check", "title": video.stem, "path": str(video), "subtitles": [], "selected_subtitle": None}])
            host = validate_host(config["host"])
            await controller.connect(asdict(Device("stability-tv", "TV", host, int(config.get("port", 8765)), "receiver")), config.get("pin", ""))
            if controller.ws is None:
                raise ValueError("Pair this TV once by supplying its six-digit code in the diagnostic configuration.")
            monitoring = True
            await controller.play("stability-check")
            await wait_for(lambda s: s.get("playing") and s.get("position", 0) >= 2, "playback startup")
            await hold("uninterrupted playback", int(config.get("playSeconds", 180)), True)
            await controller.command("pause")
            await wait_for(lambda s: not s.get("playing"), "pause")
            await hold("paused connection", int(config.get("pauseSeconds", 65)), False)
            await controller.command("play")
            await wait_for(lambda s: s.get("playing"), "resume")
            await hold("resumed playback", int(config.get("resumeSeconds", 35)), True)
            report["result"] = "passed"
        except Exception as error:
            report["result"] = "failed"
            report["error"] = str(error)
            raise
        finally:
            monitoring = False
            save()
            await controller.close()

    asyncio.run(check())
