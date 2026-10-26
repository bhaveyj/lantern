"""Audit exact Python and resolved Maven versions against OSV; fail closed."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import urllib.request


def python_packages(lock: Path) -> list[dict]:
    packages = []
    for line in lock.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        match = re.fullmatch(r"([A-Za-z0-9_.-]+)==([A-Za-z0-9_.+-]+)", line)
        if not match:
            raise ValueError("Dependency audit requires exact package versions.")
        packages.append({"package": {"ecosystem": "PyPI", "name": match[1]}, "version": match[2]})
    return packages


def query_osv(packages: list[dict]) -> list[dict]:
    request = urllib.request.Request("https://api.osv.dev/v1/querybatch", data=json.dumps({"queries": packages}).encode(), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=60) as response:
        payload = response.read(4 * 1024**2 + 1)
    if len(payload) > 4 * 1024**2:
        raise ValueError("Dependency audit response is too large.")
    results = json.loads(payload)["results"]
    if len(results) != len(packages):
        raise ValueError("Dependency audit returned incomplete results.")
    return results


def findings(packages: list[dict], results: list[dict]) -> list[str]:
    if len(packages) != len(results):
        raise ValueError("Incomplete audit results")
    return [f"{package['package']['name']}=={package['version']}: {vulnerability['id']}" for package, result in zip(packages, results) for vulnerability in result.get("vulns", [])]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--maven", type=Path, help="Resolved Gradle securityDependencyInventory JSON")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    packages = python_packages(root / "requirements-lock.txt")
    if args.maven:
        for package in json.loads(args.maven.read_text()):
            packages.append({"package": {"ecosystem": "Maven", "name": package["name"]}, "version": package["version"]})
    try:
        issues = findings(packages, query_osv(packages))
    except Exception as error:
        raise SystemExit(f"Dependency audit could not complete: {type(error).__name__}. Retry before merging.") from None
    if issues:
        raise SystemExit("Vulnerable dependencies require review:\n" + "\n".join(issues))
    print(f"OSV: no published advisories matched {len(packages)} exact Python/Maven package versions.")


if __name__ == "__main__":
    main()
