"""Run bounded static REA inspection of the vendor's public receiver APK."""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "artifacts" / "research"
OUTPUT.mkdir(parents=True, exist_ok=True)
html = (ROOT / ".tmp" / "fasttv-home.html").read_text(encoding="utf-8")
url = next(iter(sorted(set(re.findall(r'https://[^\s"<>]+\.apk', html)))))
apk = OUTPUT / "fasttv-receiver.apk"
subprocess.run(["curl.exe", "-fLsS", "--connect-timeout", "10", "--max-time", "120", url, "-o", str(apk)], check=True)
if not apk.read_bytes().startswith(b"PK"):
    raise RuntimeError("Official APK response is not a ZIP/APK")
env = dict(os.environ, REA_JADX_MCP_JAR=str(ROOT / ".tools" / "jadx-headless-mcp.jar"))
node = ROOT / ".tools" / "node" / "node.exe"
rea = ROOT / ".tools" / "rea" / "node_modules" / "rea-agents" / "scripts" / "rea.mjs"
records = {"source": url, "sha256": hashlib.sha256(apk.read_bytes()).hexdigest(), "rea_version": "6.7.0"}
for command, suffix in [("inspect-android-package", []), ("search-android-classes", ["fasttv"])]:
    output = OUTPUT / f"{command}.json"
    with output.open("w", encoding="utf-8") as target:
        result = subprocess.run([str(node), str(rea), command, str(apk), *suffix, "--json"], env=env, stdout=target, stderr=subprocess.PIPE, text=True, timeout=180)
    records[command] = {"exit_code": result.returncode, "stderr": result.stderr[-2000:], "output": output.name}
    print(command, "exit", result.returncode, flush=True)
    if result.returncode:
        print(result.stderr[-1200:], flush=True)
(OUTPUT / "provenance.json").write_text(json.dumps(records, indent=2))
print("Research evidence saved in artifacts/research", flush=True)
