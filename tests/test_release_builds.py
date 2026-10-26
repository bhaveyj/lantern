import os
from pathlib import Path
import shutil
import subprocess

import pytest


@pytest.mark.parametrize("script,variable", [("build-windows.ps1", "LANTERN_WINDOWS_CERT_THUMBPRINT"), ("build-android.ps1", "LANTERN_ANDROID_KEYSTORE")])
def test_release_build_refuses_missing_signing_credentials(script, variable):
    powershell = shutil.which("pwsh") or shutil.which("powershell")
    if not powershell:
        pytest.skip("Windows release scripts require PowerShell")
    environment = {key: value for key, value in os.environ.items() if not key.startswith("LANTERN_WINDOWS_") and not key.startswith("LANTERN_ANDROID_")}
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run([powershell, "-NoProfile", "-File", str(root / "scripts" / script), "-Release"], env=environment, capture_output=True, text=True, timeout=30)
    assert result.returncode != 0
    assert variable in result.stderr
