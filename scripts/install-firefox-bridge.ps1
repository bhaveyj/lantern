param([string]$HelperPath = '', [switch]$Uninstall)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot -Parent
$registryPath = 'HKCU:\Software\Mozilla\NativeMessagingHosts\app.lantern.tv'
$manifestDirectory = Join-Path $env:LOCALAPPDATA 'LanternTV'
$manifestPath = Join-Path $manifestDirectory 'firefox-native.json'
if ($Uninstall) {
    if (Test-Path -LiteralPath $registryPath) { Remove-Item -LiteralPath $registryPath }
    if (Test-Path -LiteralPath $manifestPath) { Remove-Item -LiteralPath $manifestPath }
    Write-Host 'Lantern Firefox bridge registration removed. Extension and app data remain available.'
    return
}
if (-not $HelperPath) {
    $HelperPath = Join-Path $projectRoot 'LanternFirefoxBridge.exe'
    if (-not (Test-Path -LiteralPath $HelperPath)) {
        $HelperPath = Join-Path $projectRoot 'dist/LanternFirefoxBridge.exe'
        if (-not (Test-Path -LiteralPath $HelperPath)) {
            $buildScript = Join-Path $PSScriptRoot 'build-firefox-extension.ps1'
            if (-not (Test-Path -LiteralPath $buildScript)) { throw 'The native helper is missing. Restore the complete Lantern package.' }
            & $buildScript -NativeHelper
        }
    }
}
$HelperPath = (Resolve-Path -LiteralPath $HelperPath).Path
if ([System.IO.Path]::GetExtension($HelperPath) -ne '.exe') { throw 'Use the console native helper executable, not the windowed Lantern app.' }
New-Item -ItemType Directory -Path $manifestDirectory -Force | Out-Null
$manifest = [ordered]@{
    name = 'app.lantern.tv'
    description = 'Lantern TV Firefox native messaging bridge'
    path = $HelperPath
    type = 'stdio'
    allowed_extensions = @('lantern-tv@lantern.local')
}
$manifestJson = $manifest | ConvertTo-Json -Depth 4
[System.IO.File]::WriteAllText($manifestPath, $manifestJson, [System.Text.UTF8Encoding]::new($false))
New-Item -Path $registryPath -Force | Out-Null
Set-Item -LiteralPath $registryPath -Value $manifestPath
Write-Host 'Firefox bridge registered for this Windows account.'
Write-Host 'Open about:debugging#/runtime/this-firefox, choose Load Temporary Add-on, and select extension/firefox/manifest.json.'
Write-Host 'Open Lantern, connect your TV, then reload your video page and choose Play on Lantern TV.'
