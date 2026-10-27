"""Restore the checksum-pinned upstream PAKE implementation for both platforms."""
import json
import hashlib
from pathlib import Path
import zipfile

from setup_tools import ROOT, TOOLS, fetch

lock = json.loads((ROOT / "scripts/toolchain-lock.json").read_text())["boringssl"]
archive = TOOLS / "boringssl-source.zip"
fetch(lock["url"], archive, lock["sha256"])
destination = TOOLS / "boringssl-source"
destination.mkdir(exist_ok=True)
with zipfile.ZipFile(archive) as bundle:
    for member in bundle.infolist():
        target = (destination / member.filename).resolve()
        if not target.is_relative_to(destination.resolve()) or (member.external_attr >> 16) & 0o170000 == 0o120000:
            raise ValueError("Unsafe pairing source archive")
        if member.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            continue
        with bundle.open(member) as incoming:
            expected = hashlib.file_digest(incoming, "sha256").digest()
        if target.is_file():
            with target.open("rb") as existing:
                if hashlib.file_digest(existing, "sha256").digest() == expected:
                    continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(bundle.read(member))
print("Pinned BoringSSL pairing source verified and restored.")
