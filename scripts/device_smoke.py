"""Exercise a real receiver or Cast device with synthetic local media."""
from __future__ import annotations

import argparse
import asyncio
import json
import time
from dataclasses import asdict
from pathlib import Path

from lantern.controller import Controller, Device


async def run(args):
    events = []
    def event(kind, data):
        events.append((time.monotonic(), kind, data))
        if kind in {"error", "notice", "connected"}:
            print(kind, data, flush=True)

    root = Path(__file__).resolve().parents[1]
    video = args.video.resolve()
    subtitle = video.with_suffix(".srt")
    subtitle.write_text("1\n00:00:00,000 --> 00:00:40,000\nLantern: streaming from your Windows PC\n", encoding="utf-8")
    items = [{"id": name, "title": title, "path": str(video), "subtitles": [{"id": "en", "label": "English", "path": str(subtitle)}], "selected_subtitle": "en"} for name, title in [("sample-one", "Lantern · playback check"), ("sample-two", "Lantern · playlist check")]]
    controller = Controller(root / ".tmp" / "device-cache", event)
    await controller.start()

    async def state_matches(predicate, description, timeout=18):
        since = time.monotonic()
        while time.monotonic() - since < timeout:
            matching = [data for stamp, kind, data in events if stamp >= since and kind == "state" and (args.cast or data.get("type") == "playback.state") and predicate(data)]
            if matching:
                print("PASS", description, matching[-1], flush=True)
                return matching[-1]
            errors = [data for stamp, kind, data in events if stamp >= since and kind == "error"]
            if errors:
                raise AssertionError(errors[-1]["message"])
            await asyncio.sleep(0.25)
        raise AssertionError(f"Timed out: {description}")

    try:
        await controller.sync(items)
        await controller.connect(asdict(Device("physical-tv", "Sony BRAVIA", args.host, 8009 if args.cast else 8765, "cast" if args.cast else "receiver")), args.pin)
        await controller.play("sample-one")
        await state_matches(lambda s: s.get("playing") and s.get("duration", 0) > 40 and s.get("position", 0) > 0, "local video starts")
        await controller.command("volume", 0.2)
        await controller.command("seek", 12)
        await controller.command("pause")
        await state_matches(lambda s: not s.get("playing") and 10 <= s.get("position", 0) <= 16, "seek and pause")
        await controller.command("play")
        await state_matches(lambda s: s.get("playing"), "resume")
        if not args.cast:
            await controller.subtitle("sample-one", None)
            await state_matches(lambda s: s.get("subtitleId") is None, "disable captions live")
            await controller.subtitle("sample-one", "en")
            await state_matches(lambda s: s.get("subtitleId") == "en", "enable captions live")
            await controller.command("fit", "fill")
            await controller.command("fit", "fit")
            if args.adb:
                async def key(code):
                    process = await asyncio.create_subprocess_exec(str(args.adb), "-s", f"{args.host}:5555", "shell", "input", "keyevent", str(code), stdout=asyncio.subprocess.DEVNULL)
                    await process.wait()
                await key(85)
                await state_matches(lambda s: not s.get("playing"), "TV remote pause")
                await key(85)
                await state_matches(lambda s: s.get("playing"), "TV remote resume")
                await key(82)
                await key(20)
                await key(23)
                await state_matches(lambda s: s.get("id") == "sample-two" and s.get("playing"), "TV playlist selects a different file")
                await controller.command("previous")
                await state_matches(lambda s: s.get("id") == "sample-one" and s.get("playing"), "previous playlist item")
        print(f"Holding playback for {args.hold}s for visual inspection", flush=True)
        await asyncio.sleep(args.hold)
        await controller.command("next")
        await state_matches(lambda s: s.get("id") == "sample-two" and s.get("playing") and s.get("position", 0) > 0, "next playlist item")
        report = {"mode": "cast" if args.cast else "receiver", "host": args.host, "result": "passed", "events": [{"type": kind, "data": data} for _, kind, data in events if kind in {"state", "error", "notice"}]}
        output = root / "artifacts" / ("cast-smoke.json" if args.cast else "receiver-smoke.json")
        output.parent.mkdir(exist_ok=True)
        output.write_text(json.dumps(report, indent=2))
        print(f"Saved {output}", flush=True)
    finally:
        await controller.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("host")
    parser.add_argument("--pin", default="")
    parser.add_argument("--cast", action="store_true")
    parser.add_argument("--video", type=Path, default=Path(".tmp/lantern-sample.mp4"))
    parser.add_argument("--hold", type=int, default=90)
    parser.add_argument("--adb", type=Path, help="Also test TV remote keys through ADB")
    asyncio.run(run(parser.parse_args()))
