"""Download project-local tools; existing Java and Android SDK installations are reused."""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / ".tools"
TOOLS.mkdir(exist_ok=True)


def fetch(url: str, destination: Path, expected: str | None = None):
    invalid = expected and destination.exists() and hashlib.sha256(destination.read_bytes()).hexdigest().lower() != expected.strip().lower()
    if not destination.exists() or destination.stat().st_size == 0 or invalid:
        print(f"Downloading {destination.name}", flush=True)
        partial = destination.with_suffix(destination.suffix + ".download")
        subprocess.run(["curl.exe", "-fLsS", "--retry", "2", "--connect-timeout", "15", "--max-time", "240", url, "-o", str(partial)], check=True)
        partial.replace(destination)
    digest = hashlib.sha256(destination.read_bytes()).hexdigest()
    if expected and digest.lower() != expected.strip().lower():
        destination.unlink()
        raise RuntimeError(f"Checksum mismatch for {destination.name}")
    return digest


def extract(archive: Path, directory: Path):
    directory.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as bundle:
        for member in bundle.infolist():
            target = (directory / member.filename).resolve()
            if not target.is_relative_to(directory.resolve()):
                raise RuntimeError("Archive contains an unsafe path")
        bundle.extractall(directory)


def main():
    records = json.loads((TOOLS / "downloads.json").read_text()) if (TOOLS / "downloads.json").exists() else {}
    cached_gradle = list((Path.home() / ".gradle" / "wrapper" / "dists" / "gradle-8.10.2-all").glob("*/gradle-8.10.2/bin/gradle.bat"))
    if not cached_gradle and not (TOOLS / "gradle-8.10.2" / "bin" / "gradle.bat").exists():
        checksum = urllib.request.urlopen("https://services.gradle.org/distributions/gradle-8.10.2-bin.zip.sha256").read().decode().strip()
        archive = TOOLS / "gradle.zip"
        records["gradle"] = fetch("https://services.gradle.org/distributions/gradle-8.10.2-bin.zip", archive, checksum)
        extract(archive, TOOLS)
    if not (TOOLS / "ffmpeg" / "ffmpeg.exe").exists():
        archive = TOOLS / "ffmpeg-essentials.zip"
        checksum = urllib.request.urlopen("https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip.sha256").read().decode().split()[0]
        records["ffmpeg"] = fetch("https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip", archive, checksum)
        extract(archive, TOOLS / "ffmpeg-download")
        destination = TOOLS / "ffmpeg"
        destination.mkdir(exist_ok=True)
        for source in (TOOLS / "ffmpeg-download").rglob("*"):
            if source.is_file() and (source.name in {"ffmpeg.exe", "ffprobe.exe"} or source.suffix == ".dll" or "license" in source.name.lower()):
                shutil.copy2(source, destination / source.name)
    if not (TOOLS / "node" / "node.exe").exists():
        manifest = urllib.request.urlopen("https://nodejs.org/dist/latest-v24.x/SHASUMS256.txt").read().decode()
        digest, filename = next(line.split() for line in manifest.splitlines() if line.endswith("-win-x64.zip"))
        archive = TOOLS / "node.zip"
        records["node"] = fetch(f"https://nodejs.org/dist/latest-v24.x/{filename}", archive, digest)
        extract(archive, TOOLS / "node-download")
        shutil.copytree(TOOLS / "node-download" / filename.removesuffix(".zip"), TOOLS / "node", dirs_exist_ok=True)
    records["jadx"] = fetch("https://github.com/1013503897/jadx-headless-mcp/releases/download/v0.7.1/jadx-headless-mcp-0.7.1-all.jar", TOOLS / "jadx-headless-mcp.jar", "6e5eacf500b64292bfb73c49797c1958f6ee44646e43e868039ae7feb573ff75")
    (TOOLS / "downloads.json").write_text(json.dumps(records, indent=2))
    print("Project-local tools ready", flush=True)


if __name__ == "__main__":
    main()
