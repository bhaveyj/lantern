#Requires -RunAsAdministrator
param([string]$Executable, [switch]$SetPrivate, [string]$TvAddress)
$ErrorActionPreference = 'Stop'
if (-not $Executable) { $Executable = Join-Path (Split-Path $PSScriptRoot -Parent) 'dist\Lantern\Lantern.exe' }
$programPath = (Resolve-Path -LiteralPath $Executable).Path
if ($SetPrivate) {
    if (-not $TvAddress) { throw 'Provide -TvAddress to identify the home-network adapter.' }
    $route = Find-NetRoute -RemoteIPAddress $TvAddress | Select-Object -First 1
    Set-NetConnectionProfile -InterfaceIndex $route.InterfaceIndex -NetworkCategory Private
}
$ruleName = 'LanternTV-Private-LAN'
$existingRule = Get-NetFirewallRule -Name $ruleName -ErrorAction SilentlyContinue
if ($existingRule) { $existingRule | Remove-NetFirewallRule }
New-NetFirewallRule -Name $ruleName -DisplayName 'Lantern TV - private home network' -Direction Inbound -Action Allow -Profile Private -Program $programPath -Protocol TCP -RemoteAddress LocalSubnet | Out-Null
$discoveryRule = Get-NetFirewallRule -Name 'LanternTV-Discovery' -ErrorAction SilentlyContinue
if ($discoveryRule) { $discoveryRule | Remove-NetFirewallRule }
New-NetFirewallRule -Name 'LanternTV-Discovery' -DisplayName 'Lantern TV - device discovery' -Direction Inbound -Action Allow -Profile Private -Program $programPath -Protocol UDP -LocalPort 5353 -RemoteAddress LocalSubnet | Out-Null
Write-Host "Allowed local media streaming for $programPath on private networks."
