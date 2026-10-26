"""Download project-local tools; existing Java and Android SDK installations are reused."""
from __future__ import annotations

import hashlib
import argparse
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
LOCK = json.loads((Path(__file__).with_name("toolchain-lock.json")).read_text())


def digest(path: Path) -> str:
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def runtime_tools_valid(folder: Path | None = None) -> bool:
    folder = folder or TOOLS / "ffmpeg"
    return all((folder / name).is_file() and digest(folder / name) == expected for name, expected in LOCK["ffmpeg"]["executables"].items())


def verify_runtime_tools():
    if not runtime_tools_valid():
        raise RuntimeError("FFmpeg/ffprobe do not match the verified toolchain lock. Run scripts/bootstrap.ps1 to restore them.")


def fetch(url: str, destination: Path, expected: str):
    if not url.startswith("https://") or not re.fullmatch(r"[a-fA-F0-9]{64}", expected):
        raise ValueError("Tool downloads require HTTPS and a pinned SHA-256 checksum.")
    invalid = destination.exists() and digest(destination) != expected.lower()
    if not destination.exists() or destination.stat().st_size == 0 or invalid:
        print(f"Downloading {destination.name}", flush=True)
        partial = destination.with_suffix(destination.suffix + ".download")
        subprocess.run(["curl.exe", "-fLsS", "--proto", "=https", "--proto-redir", "=https", "--retry", "2", "--connect-timeout", "15", "--max-time", "240", url, "-o", str(partial)], check=True)
        if digest(partial) != expected.lower():
            partial.unlink()
            raise RuntimeError(f"Checksum mismatch for {destination.name}")
        partial.replace(destination)
    return digest(destination)


def extract(archive: Path, directory: Path):
    directory.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as bundle:
        for member in bundle.infolist():
            if (member.external_attr >> 16) & 0o170000 == 0o120000:
                raise RuntimeError("Archive contains a symbolic link")
            target = (directory / member.filename).resolve()
            if not target.is_relative_to(directory.resolve()):
                raise RuntimeError("Archive contains an unsafe path")
        bundle.extractall(directory)


def main(research: bool = False):
    records = json.loads((TOOLS / "downloads.json").read_text()) if (TOOLS / "downloads.json").exists() else {}
    cached_gradle = list((Path.home() / ".gradle" / "wrapper" / "dists" / "gradle-8.10.2-all").glob("*/gradle-8.10.2/bin/gradle.bat"))
    if not cached_gradle and not (TOOLS / "gradle-8.10.2" / "bin" / "gradle.bat").exists():
        archive = TOOLS / "gradle.zip"
        records["gradle"] = fetch(LOCK["gradle"]["url"], archive, LOCK["gradle"]["sha256"])
        extract(archive, TOOLS)
    if not runtime_tools_valid():
        archive = TOOLS / "ffmpeg-essentials.zip"
        records["ffmpeg"] = fetch(LOCK["ffmpeg"]["url"], archive, LOCK["ffmpeg"]["sha256"])
        extract(archive, TOOLS / "ffmpeg-download")
        destination = TOOLS / "ffmpeg"
        destination.mkdir(exist_ok=True)
        for source in (TOOLS / "ffmpeg-download").rglob("*"):
            if source.is_file() and (source.name in {"ffmpeg.exe", "ffprobe.exe"} or source.suffix == ".dll" or "license" in source.name.lower()):
                shutil.copy2(source, destination / source.name)
    verify_runtime_tools()
    if research:
        setup_research_tools(records)
    (TOOLS / "downloads.json").write_text(json.dumps(records, indent=2))
    print("Project-local tools ready", flush=True)


def setup_research_tools(records):
    executable = TOOLS / "node" / "node.exe"
    if not executable.is_file() or digest(executable) != LOCK["node"]["executable_sha256"]:
        filename = LOCK["node"]["url"].rsplit("/", 1)[1]
        archive = TOOLS / "node.zip"
        records["node"] = fetch(LOCK["node"]["url"], archive, LOCK["node"]["sha256"])
        extract(archive, TOOLS / "node-download")
        shutil.copytree(TOOLS / "node-download" / filename.removesuffix(".zip"), TOOLS / "node", dirs_exist_ok=True)
    records["jadx"] = fetch("https://github.com/1013503897/jadx-headless-mcp/releases/download/v0.7.1/jadx-headless-mcp-0.7.1-all.jar", TOOLS / "jadx-headless-mcp.jar", "6e5eacf500b64292bfb73c49797c1958f6ee44646e43e868039ae7feb573ff75")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--research", action="store_true", help="Also install Node and JADX for the optional research scripts")
    main(parser.parse_args().research)
