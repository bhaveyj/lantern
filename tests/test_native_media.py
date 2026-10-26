import asyncio
import sys

import pytest
from aiohttp import web

from lantern.media import CREATE_NO_WINDOW, probe
from lantern.native_media import communicate_bounded
from lantern.subtitle_jobs import inspect_media


@pytest.mark.parametrize("inspect", [probe, inspect_media])
async def test_disguised_network_playlist_cannot_fetch_resources(aiohttp_server, tmp_path, inspect):
    requests = []
    async def resource(request):
        requests.append(request.path)
        return web.Response(body=b"private resource")
    app = web.Application()
    app.router.add_get("/private.ts", resource)
    server = await aiohttp_server(app)
    source = tmp_path / "movie.mp4"
    source.write_text(f"#EXTM3U\n#EXT-X-TARGETDURATION:10\n#EXTINF:10,\nhttp://127.0.0.1:{server.port}/private.ts\n#EXT-X-ENDLIST\n")
    with pytest.raises((RuntimeError, ValueError)):
        await inspect(source)
    assert not requests


@pytest.mark.parametrize("command,timeout,error", [
    ("import sys,time; sys.stdout.buffer.write(b'x' * (5 * 1024**2)); sys.stdout.flush(); time.sleep(60)", 5, ValueError),
    ("import time; time.sleep(60)", .1, TimeoutError),
])
async def test_output_overflow_and_timeout_terminate_actual_child(command, timeout, error):
    process = await asyncio.create_subprocess_exec(sys.executable, "-c", command, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, creationflags=CREATE_NO_WINDOW)
    with pytest.raises(error):
        await asyncio.wait_for(communicate_bounded(process, timeout), 10)
    assert process.returncode is not None
