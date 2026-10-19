"""Verify the no-console Windows subprocess path used by the GUI executable."""
import asyncio
import json
import sys
import traceback
from dataclasses import asdict
from pathlib import Path

from lantern.media import Preparation, probe

async def check():
    source = Path(sys.argv[1])
    info = await probe(source)
    preparation = Preparation(Path(sys.argv[2]).parent / 'windowed-cache')
    output = await preparation.prepare(source, info, lambda _: None)
    result = {'source': asdict(info), 'prepared': asdict(await probe(output))}
    await preparation.cleanup_async()
    Path(sys.argv[2]).write_text(json.dumps(result, indent=2))

try:
    asyncio.run(check())
except Exception:
    Path(sys.argv[2]).write_text(traceback.format_exc())
    sys.exit(1)
