param([switch]$NativeHelper)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot -Parent
$extensionRoot = Join-Path $projectRoot 'extension/firefox'
$distRoot = Join-Path $projectRoot 'dist'
New-Item -ItemType Directory -Path $distRoot -Force | Out-Null
$extensionVersion = (Get-Content -LiteralPath (Join-Path $extensionRoot 'manifest.json') -Raw | ConvertFrom-Json).version
if ($extensionVersion -notmatch '^\d+\.\d+\.\d+(?:\.\d+)?$') { throw 'Invalid Firefox extension version.' }
$outputPath = Join-Path $distRoot "Lantern-Firefox-$extensionVersion.xpi"
Add-Type -AssemblyName System.IO.Compression
$output = [System.IO.File]::Open($outputPath, [System.IO.FileMode]::Create)
try {
    $archive = [System.IO.Compression.ZipArchive]::new($output, [System.IO.Compression.ZipArchiveMode]::Create)
    try {
        # Explicit file list excludes tests, helper sources and development files.
        foreach ($name in @('manifest.json', 'core.js', 'background.js', 'content.js', 'popup.html', 'popup.css', 'popup.js')) {
            $entry = $archive.CreateEntry($name)
            $entryStream = $entry.Open()
            $inputStream = [System.IO.File]::OpenRead((Join-Path $extensionRoot $name))
            try { $inputStream.CopyTo($entryStream) }
            finally { $inputStream.Dispose(); $entryStream.Dispose() }
        }
    } finally { $archive.Dispose() }
} finally { $output.Dispose() }
Write-Host "Unsigned development extension: $outputPath"
if ($NativeHelper) {
    $pythonPath = Join-Path $projectRoot '.venv/Scripts/python.exe'
    if (-not (Test-Path -LiteralPath $pythonPath)) { throw 'Run scripts/bootstrap.ps1 first.' }
    $previousCache = $env:PYINSTALLER_CONFIG_DIR
    try {
        $env:PYINSTALLER_CONFIG_DIR = Join-Path $projectRoot '.tmp/pyinstaller-firefox'
        & $pythonPath -m PyInstaller --noconfirm --clean --console --onefile --name LanternFirefoxBridge --paths (Join-Path $projectRoot 'src') --distpath $distRoot --workpath (Join-Path $projectRoot 'build/firefox-bridge') --specpath (Join-Path $projectRoot 'build/firefox-bridge') --exclude-module PySide6 --exclude-module pytest (Join-Path $extensionRoot 'native/entry.py')
    } finally { $env:PYINSTALLER_CONFIG_DIR = $previousCache }
    if ($LASTEXITCODE) { throw 'Firefox native helper packaging failed.' }
    Write-Host "Native helper: $(Join-Path $distRoot 'LanternFirefoxBridge.exe')"
}
