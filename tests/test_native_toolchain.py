import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest


def discover(tmp_path, *, installation=None, legacy=False, exit_code=0, vswhere=True):
    powershell = shutil.which("pwsh") or shutil.which("powershell")
    if not powershell:
        pytest.skip("MSVC discovery requires PowerShell")
    root = Path(__file__).resolve().parents[1]
    query = tmp_path / "vswhere.ps1"
    arguments = tmp_path / "arguments.json"
    if vswhere:
        query.write_text(
            "$global:LASTEXITCODE = [int]$env:LANTERN_TEST_VSWHERE_EXIT\n"
            "ConvertTo-Json -InputObject @($args) -Compress | Set-Content -LiteralPath $env:LANTERN_TEST_ARGUMENTS\n"
            "if ($env:LANTERN_TEST_INSTALLATION) { $env:LANTERN_TEST_INSTALLATION }\n"
        )
    legacy_path = tmp_path / "Old standalone tools" / "vcvarsall.bat"
    if legacy:
        legacy_path.parent.mkdir(parents=True)
        legacy_path.touch()
    environment = dict(os.environ, LANTERN_TEST_VSWHERE=str(query),
                       LANTERN_TEST_LEGACY=str(legacy_path),
                       LANTERN_TEST_HELPER=str(root / "scripts/native-toolchain.ps1"),
                       LANTERN_TEST_ARGUMENTS=str(arguments),
                       LANTERN_TEST_INSTALLATION=str(installation) if installation else "",
                       LANTERN_TEST_VSWHERE_EXIT=str(exit_code))
    result = subprocess.run(
        [powershell, "-NoProfile", "-Command",
         "$ErrorActionPreference = 'Stop'; . $env:LANTERN_TEST_HELPER; "
         "Find-MsvcBuildEnvironment -VsWhere $env:LANTERN_TEST_VSWHERE -LegacyTools $env:LANTERN_TEST_LEGACY"],
        env=environment, capture_output=True, text=True, timeout=30,
    )
    return result, arguments, legacy_path


@pytest.mark.parametrize("edition", ["Visual Studio 2022 Community", "Visual Studio 18 Enterprise"])
def test_registered_cpp_tools_take_priority_over_stale_legacy_installation(tmp_path, edition):
    installation = tmp_path / edition
    setup = installation / "VC/Auxiliary/Build/vcvarsall.bat"
    setup.parent.mkdir(parents=True)
    setup.touch()
    result, arguments, _ = discover(tmp_path, installation=installation, legacy=True)
    assert result.returncode == 0, result.stderr
    assert Path(result.stdout.strip()) == setup
    assert json.loads(arguments.read_text(encoding="utf-8-sig")) == [
        "-latest", "-products", "*", "-requires",
        "Microsoft.VisualStudio.Component.VC.Tools.x86.x64", "-property", "installationPath",
    ]


@pytest.mark.parametrize("vswhere", [True, False])
def test_unregistered_standalone_tools_remain_supported(tmp_path, vswhere):
    result, _, legacy = discover(tmp_path, legacy=True, vswhere=vswhere)
    assert result.returncode == 0, result.stderr
    assert Path(result.stdout.strip()) == legacy


def test_missing_cpp_tools_has_actionable_error(tmp_path):
    result, _, _ = discover(tmp_path)
    assert result.returncode != 0
    assert "Desktop development with C++" in result.stderr


def test_failed_discovery_is_not_silently_treated_as_missing_installation(tmp_path):
    result, _, _ = discover(tmp_path, legacy=True, exit_code=1)
    assert result.returncode != 0
    assert "discovery failed" in result.stderr
