param([switch]$Release)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'release-signing.ps1')
if ($Release) { Get-LanternSigningCertificate | Out-Null }
$projectRoot = Split-Path $PSScriptRoot -Parent
Set-Location $projectRoot
$env:TEMP = Join-Path $projectRoot '.tmp'
$env:TMP = $env:TEMP
& '.\.venv\Scripts\python.exe' scripts/check_dependencies.py
if ($LASTEXITCODE) { throw 'Restore the tested dependency versions with scripts/bootstrap.ps1 before packaging.' }
& '.\.venv\Scripts\python.exe' scripts/check_tools.py
if ($LASTEXITCODE) { throw 'Restore verified media tools before packaging.' }
& '.\.venv\Scripts\python.exe' scripts/generate_windows_icon.py
if ($LASTEXITCODE) { throw 'Windows icon generation failed.' }
$buildPath = $env:PATH
try {
    # Keep unrelated native tools on the developer's PATH out of the frozen DLL set.
    $env:PATH = "$projectRoot\.venv\Scripts;$env:SystemRoot\System32;$env:SystemRoot;$env:SystemRoot\System32\Wbem"
    & '.\.venv\Scripts\python.exe' -m PyInstaller --noconfirm --clean --windowed --name Lantern --icon assets/lantern-windows.ico --paths src --add-data '.tools/ffmpeg;tools' --recursive-copy-metadata lantern-tv --collect-all faster_whisper --collect-all ctranslate2 --collect-all onnxruntime --collect-all av --collect-all tokenizers --exclude-module pytest scripts/desktop_entry.py
} finally { $env:PATH = $buildPath }
if ($LASTEXITCODE) { throw 'Windows packaging failed.' }
if ($Release) { Set-LanternReleaseSignature (Join-Path $projectRoot 'dist/Lantern/Lantern.exe') }
Get-ChildItem .venv/Lib/site-packages/PySide6 -File | Where-Object { $_.Name -match '^(MSVCP140|VCRUNTIME140|CONCRT140).*\.dll$' } | Copy-Item -Destination dist/Lantern/_internal -Force
Copy-Item -LiteralPath README.md -Destination dist/Lantern/README.md
Copy-Item -LiteralPath THIRD_PARTY.md -Destination dist/Lantern/THIRD_PARTY.md
Copy-Item -LiteralPath LICENSE -Destination dist/Lantern/LICENSE
if (Test-Path '.tools/inno/ISCC.exe') {
    & '.tools/inno/ISCC.exe' scripts/lantern.iss
    if ($LASTEXITCODE) { throw 'Windows installer compilation failed.' }
    if ($Release) { Set-LanternReleaseSignature (Join-Path $projectRoot 'dist/Lantern-Setup-0.1.0.exe') }
}
Compress-Archive -Path dist/Lantern -DestinationPath dist/Lantern-Windows-0.1.0.zip -Force
Write-Host 'Portable Windows app: dist/Lantern/Lantern.exe'
