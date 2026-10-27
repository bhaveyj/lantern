$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot -Parent
Set-Location $projectRoot
& '.venv/Scripts/python.exe' scripts/restore_pairing.py
if ($LASTEXITCODE) { throw 'Pinned pairing source restoration failed.' }
$lock = Get-Content -LiteralPath scripts/toolchain-lock.json -Raw | ConvertFrom-Json
$source = Join-Path $projectRoot ('.tools/boringssl-source/boringssl-' + $lock.boringssl.commit)
$sdk = if ($env:ANDROID_HOME) { $env:ANDROID_HOME } else { Join-Path $env:LOCALAPPDATA 'Android/Sdk' }
$cmake = Join-Path $sdk 'cmake/3.22.1/bin/cmake.exe'
if (-not (Test-Path -LiteralPath $cmake)) {
    $available = Get-Command cmake -ErrorAction SilentlyContinue
    if (-not $available) { throw 'Install CMake 3.22 or newer and Visual Studio C++ build tools.' }
    $cmake = $available.Source
}
if (-not $env:JAVA_HOME) { $env:JAVA_HOME = Split-Path (Split-Path (Get-Command java).Source -Parent) -Parent }
. (Join-Path $PSScriptRoot 'native-toolchain.ps1')
$msvcEnvironment = Find-MsvcBuildEnvironment
$ninja = Join-Path (Split-Path $cmake -Parent) 'ninja.exe'
if (-not (Test-Path -LiteralPath $ninja)) {
    $available = Get-Command ninja -ErrorAction SilentlyContinue
    if (-not $available) { throw 'Install Ninja or use the Android SDK CMake package, which includes it.' }
    $ninja = $available.Source
}
# Ninja uses the discovered compiler environment and needs no release-specific
# Visual Studio generator support in CMake. Keep paths quoted and the environment private.
$nativeBuild = Join-Path $projectRoot '.tmp/pairing-windows-ninja'
$batch = Join-Path $projectRoot '.tmp/pairing-windows-build.cmd'
New-Item -ItemType Directory -Force (Split-Path $batch -Parent) | Out-Null
@"
@echo off
call "$msvcEnvironment" x64 >nul
if errorlevel 1 exit /b 1
"$cmake" -S "$projectRoot/native/pairing" -B "$nativeBuild" -G Ninja "-DCMAKE_MAKE_PROGRAM=$ninja" "-DBORINGSSL_SOURCE=$source" -DCMAKE_BUILD_TYPE=Release -DBUILD_TESTING=OFF
if errorlevel 1 exit /b 1
"$cmake" --build "$nativeBuild" --target lantern_pake lantern_pairing_test --parallel 4
"@ | Set-Content -LiteralPath $batch
& $env:ComSpec /d /c $batch
if ($LASTEXITCODE) { throw 'Windows pairing build failed.' }
$builtLibrary = Join-Path $nativeBuild 'lantern_pake.dll'
$selfTest = Join-Path $nativeBuild 'lantern_pairing_test.exe'
& $selfTest
if ($LASTEXITCODE) { throw 'SPAKE2+ RFC 9383 known-answer verification failed.' }
Write-Output 'SPAKE2+ RFC 9383 known-answer verification passed.'
New-Item -ItemType Directory -Force .tools/pairing | Out-Null
Copy-Item -LiteralPath $builtLibrary -Destination .tools/pairing/lantern_pake.dll
Copy-Item -LiteralPath (Join-Path $source 'LICENSE') -Destination .tools/pairing/BORINGSSL-LICENSE.txt
