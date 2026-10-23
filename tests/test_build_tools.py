from importlib import metadata

import pytest

from scripts.check_dependencies import mismatches
from scripts import setup_tools


def test_dependency_check_reports_drift_and_missing_packages(tmp_path):
    lock = tmp_path / "lock.txt"
    lock.write_text("# tested environment\nmatching==1.0\ndrifted==2.0\nmissing==3.0\n")
    def version(name):
        if name == "missing": raise metadata.PackageNotFoundError(name)
        return {"matching": "1.0", "drifted": "1.5"}[name]
    assert mismatches(lock, version) == ["drifted: expected 2.0, found 1.5", "missing: expected 3.0, found not installed"]


def test_dependency_lock_rejects_unpinned_requirements(tmp_path):
    lock = tmp_path / "lock.txt"
    lock.write_text("package>=1\n")
    with pytest.raises(ValueError, match="exact"):
        mismatches(lock)


def test_runtime_bootstrap_skips_optional_research_tools(tmp_path, monkeypatch):
    (tmp_path / "ffmpeg").mkdir()
    (tmp_path / "ffmpeg" / "ffmpeg.exe").touch()
    (tmp_path / "gradle-8.10.2" / "bin").mkdir(parents=True)
    (tmp_path / "gradle-8.10.2" / "bin" / "gradle.bat").touch()
    monkeypatch.setattr(setup_tools, "TOOLS", tmp_path)
    calls = []
    monkeypatch.setattr(setup_tools, "setup_research_tools", lambda records: calls.append(True))
    setup_tools.main()
    assert not calls
    setup_tools.main(research=True)
    assert calls == [True]
