"""Reject Python dependency drift before producing a Windows distribution."""
from __future__ import annotations

import argparse
from importlib import metadata
from pathlib import Path


def mismatches(lock: Path, version_of=metadata.version) -> list[str]:
    errors = []
    for raw in lock.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        name, separator, expected = line.partition("==")
        if not separator or not name.strip() or not expected.strip():
            raise ValueError("The dependency lock must contain exact name==version pins.")
        try:
            actual = version_of(name.strip())
        except metadata.PackageNotFoundError:
            actual = "not installed"
        if actual != expected.strip():
            errors.append(f"{name.strip()}: expected {expected.strip()}, found {actual}")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--lock", type=Path, default=Path(__file__).resolve().parents[1] / "requirements-lock.txt")
    args = parser.parse_args()
    errors = mismatches(args.lock)
    if errors:
        print("Python dependencies differ from the tested lock. Run scripts/bootstrap.ps1.")
        print("\n".join(errors))
        return 1
    print("Python dependencies match requirements-lock.txt.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
