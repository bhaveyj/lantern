import hashlib
import io
import json
import os
from pathlib import Path

import pytest

from lantern.speech_models import MODEL_REVISIONS, cached_model, ensure_model


class Response(io.BytesIO):
    def __init__(self, data, status=200, headers=None):
        super().__init__(data)
        self.status, self.headers = status, headers or {}


def model_fixture(monkeypatch, *, broken=False, interrupt=False):
    files = {"config.json": b"{}", "tokenizer.json": b"{}", "vocabulary.txt": b"hello", "model.bin": b"weights" * 100000}
    siblings = []
    for name, data in files.items():
        entry = {"rfilename": name, "size": len(data), "blobId": hashlib.sha1(f"blob {len(data)}\0".encode() + data).hexdigest()}
        if name == "model.bin":
            entry["lfs"] = {"sha256": hashlib.sha256(data).hexdigest()}
        siblings.append(entry)
    metadata = {"sha": MODEL_REVISIONS["base"], "siblings": siblings}
    calls, interrupted = [], [False]
    class Interrupted(Response):
        def read(self, count=-1):
            if self.tell() >= 256 * 1024:
                raise ConnectionError("interrupted transfer")
            return super().read(count)
    def urlopen(request, timeout):
        if isinstance(request, str):
            calls.append("metadata")
            return Response(json.dumps(metadata).encode())
        name = request.full_url.split("?")[0].rsplit("/", 1)[-1]
        offset = int(request.headers.get("Range", "bytes=0-").split("=")[1].split("-")[0])
        calls.append((name, offset))
        data = files[name]
        if broken and name == "model.bin":
            data = b"x" * len(data)
        if interrupt and name == "model.bin" and not interrupted[0]:
            interrupted[0] = True
            return Interrupted(data)
        return Response(data[offset:], 206 if offset else 200, {"Content-Range": f"bytes {offset}-{len(data)-1}/{len(data)}"})
    monkeypatch.setattr("lantern.speech_models.urllib.request.urlopen", urlopen)
    monkeypatch.setattr("lantern.speech_models.time.sleep", lambda _: None)
    return calls


def test_verified_download_is_offline_afterwards_and_detects_modified_files(monkeypatch, tmp_path):
    calls = model_fixture(monkeypatch)
    folder = ensure_model("base", tmp_path, True, lambda *_: None)
    assert cached_model("base", tmp_path) == folder
    previous = list(calls)
    assert ensure_model("base", tmp_path, False, lambda *_: None) == folder
    assert calls == previous
    binary = folder / "model.bin"
    stat = binary.stat()
    os.utime(binary, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1000000))
    assert cached_model("base", tmp_path) is None
    with pytest.raises(ValueError, match="not downloaded"):
        ensure_model("base", tmp_path, False, lambda *_: None)
    assert calls == previous


def test_interrupted_weights_resume_and_are_verified(monkeypatch, tmp_path):
    calls = model_fixture(monkeypatch, interrupt=True)
    folder = ensure_model("base", tmp_path, True, lambda *_: None)
    assert ("model.bin", 256 * 1024) in calls
    assert cached_model("base", tmp_path) == folder
    assert not list(folder.glob("*.download"))


def test_corrupt_weights_are_never_marked_as_ready(monkeypatch, tmp_path):
    model_fixture(monkeypatch, broken=True)
    with pytest.raises(ValueError, match="integrity"):
        ensure_model("base", tmp_path, True, lambda *_: None)
    assert cached_model("base", tmp_path) is None
    assert not list(tmp_path.rglob("verified.json"))
