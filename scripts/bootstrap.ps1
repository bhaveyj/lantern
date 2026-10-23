param([switch]$ResearchTools)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot -Parent
Set-Location $projectRoot
New-Item -ItemType Directory -Force .tmp | Out-Null
$env:TEMP = Join-Path $projectRoot '.tmp'
$env:TMP = $env:TEMP
if (-not (Test-Path '.venv\Scripts\pip.exe')) {
    python -m venv .venv
    if ($LASTEXITCODE) { throw 'Python virtual environment setup failed.' }
}
& '.\.venv\Scripts\python.exe' -m pip install -r requirements-lock.txt
if ($LASTEXITCODE) { throw 'Dependency installation failed.' }
& '.\.venv\Scripts\python.exe' -m pip install --no-build-isolation --no-deps -e '.[dev]'
if ($LASTEXITCODE) { throw 'Editable project installation failed.' }
$toolArgs = if ($ResearchTools) { @('--research') } else { @() }
& '.\.venv\Scripts\python.exe' scripts/setup_tools.py @toolArgs
if ($LASTEXITCODE) { throw 'Tool download failed.' }
Write-Host 'Run .\run.ps1 to open Lantern.'
