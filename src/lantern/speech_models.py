"""Download only pinned, public speech-model data, with resume and integrity checks."""
from __future__ import annotations

import hashlib
import json
import shutil
import time
import urllib.request
from pathlib import Path

MODEL_REVISIONS = {
    "base": "ebe41f70d5b6dfa9166e2c581c45c9c0cfc57b66",
    "small": "536b0662742c02347bc0e980a01041f333bce120",
    "medium": "08e178d48790749d25932bbc082711ddcfdfbc4f",
}
MODELS = tuple(MODEL_REVISIONS)
FILES = {"config.json", "preprocessor_config.json", "model.bin", "tokenizer.json", "vocabulary.txt", "vocabulary.json"}


def verified(path: Path, entry: dict) -> bool:
    if not path.is_file() or path.stat().st_size != entry["size"]:
        return False
    large = entry.get("lfs")
    digest = hashlib.sha256() if large else hashlib.sha1()
    if not large:
        digest.update(f"blob {entry['size']}\0".encode())
    with path.open("rb") as source:
        while block := source.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest() == (large["sha256"] if large else entry["blobId"])


def cached_model(model: str, root: Path) -> Path | None:
    folder = root / f"{model}-{MODEL_REVISIONS[model]}"
    try:
        marker = json.loads((folder / "verified.json").read_text(encoding="utf-8"))
        if marker["revision"] != MODEL_REVISIONS[model]:
            return None
        files = marker["files"]
        if not {"model.bin", "config.json", "tokenizer.json"}.issubset(files) or not ({"vocabulary.txt", "vocabulary.json"} & files.keys()):
            return None
        for name, stamp in files.items():
            if name not in FILES:
                return None
            stat = (folder / name).stat()
            if [stat.st_size, stat.st_mtime_ns] != stamp:
                return None
        return folder
    except (OSError, ValueError, KeyError, TypeError):
        return None


def ensure_model(model: str, root: Path, allow_download: bool, progress) -> Path:
    cached = cached_model(model, root)
    if cached:
        return cached
    if not allow_download:
        raise ValueError("This speech model is not downloaded. Allow the first-use model download and try again.")
    revision = MODEL_REVISIONS[model]
    repository = f"Systran/faster-whisper-{model}"
    progress("Checking the free model download…", None)
    try:
        with urllib.request.urlopen(f"https://huggingface.co/api/models/{repository}/revision/{revision}?blobs=true", timeout=30) as response:
            raw = response.read(1024 * 1024 + 1)
        if len(raw) > 1024 * 1024:
            raise ValueError("Unexpected model metadata size.")
        metadata = json.loads(raw)
        if metadata["sha"] != revision:
            raise ValueError("The model revision could not be verified.")
        entries = [entry for entry in metadata["siblings"] if entry["rfilename"] in FILES]
        names = {entry["rfilename"] for entry in entries}
        if not {"model.bin", "config.json", "tokenizer.json"}.issubset(names) or not ({"vocabulary.txt", "vocabulary.json"} & names):
            raise ValueError("The model download is missing required files.")
        total = sum(int(entry["size"]) for entry in entries)
        if not 0 < total <= 2 * 1024**3:
            raise ValueError("Unexpected model download size.")
        root.mkdir(parents=True, exist_ok=True)
        folder = root / f"{model}-{revision}"
        folder.mkdir(exist_ok=True)
        trusted = {entry["rfilename"] for entry in entries if verified(folder / entry["rfilename"], entry)}
        remaining = sum(entry["size"] for entry in entries if entry["rfilename"] not in trusted)
        if shutil.disk_usage(root).free < remaining + 1024**3:
            raise ValueError("Not enough free space to save this model and leave 1 GiB available.")
        completed = 0
        for entry in entries:
            name, size = entry["rfilename"], int(entry["size"])
            destination = folder / name
            if name in trusted:
                completed += size
                continue
            partial = folder / f"{name}.download"
            for attempt in range(3):
                try:
                    offset = partial.stat().st_size if partial.exists() else 0
                    if offset > size:
                        partial.unlink()
                        offset = 0
                    if offset < size:
                        request = urllib.request.Request(
                            f"https://huggingface.co/{repository}/resolve/{revision}/{name}?download=true&lantern={time.time_ns()}",
                            headers={"Range": f"bytes={offset}-"} if offset else {},
                        )
                        with urllib.request.urlopen(request, timeout=60) as response:
                            if response.status == 206:
                                if not response.headers.get("Content-Range", "").startswith(f"bytes {offset}-"):
                                    raise ValueError("Unexpected partial model response.")
                            elif response.status == 200:
                                offset = 0
                            else:
                                raise ValueError("Unexpected model download response.")
                            with partial.open("ab" if offset else "wb") as output:
                                last_report = 0.0
                                while block := response.read(256 * 1024):
                                    offset += len(block)
                                    if offset > size:
                                        raise ValueError("Model download exceeded its expected size.")
                                    if shutil.disk_usage(root).free < len(block) + 1024**3:
                                        raise ValueError("Model download stopped to leave at least 1 GiB of free space.")
                                    output.write(block)
                                    now = time.monotonic()
                                    if now - last_report >= .25:
                                        progress("Downloading the free speech model…", (completed + offset) * 100 / total)
                                        last_report = now
                    if not verified(partial, entry):
                        partial.unlink(missing_ok=True)
                        raise ValueError("Model download failed its integrity check.")
                    partial.replace(destination)
                    completed += size
                    break
                except Exception:
                    if attempt == 2:
                        raise
                    time.sleep(attempt + 1)
        marker = {"revision": revision, "files": {name: [(folder / name).stat().st_size, (folder / name).stat().st_mtime_ns] for name in names}}
        pending = folder / "verified.tmp"
        pending.write_text(json.dumps(marker), encoding="utf-8")
        pending.replace(folder / "verified.json")
        return folder
    except ValueError:
        raise
    except Exception:
        raise ValueError("The free speech model could not be downloaded. Check the connection and retry; partial downloads can resume.") from None
