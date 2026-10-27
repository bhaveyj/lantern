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
    if (-not $available) { throw 'Install CMake 3.22 or newer and Visual Studio 2022 C++ build tools.' }
    $cmake = $available.Source
}
if (-not $env:JAVA_HOME) { $env:JAVA_HOME = Split-Path (Split-Path (Get-Command java).Source -Parent) -Parent }
$legacyTools = 'C:/Program Files (x86)/Microsoft Visual Studio/2019/BuildTools/VC/Auxiliary/Build/vcvarsall.bat'
if (Test-Path -LiteralPath $legacyTools) {
    # Reuse a standalone Build Tools installation even when it is no longer
    # registered with the Visual Studio installer. Never dump its environment.
    $nativeBuild = Join-Path $projectRoot '.tmp/pairing-windows-ninja'
    $ninja = Join-Path (Split-Path $cmake -Parent) 'ninja.exe'
    $batch = Join-Path $projectRoot '.tmp/pairing-windows-build.cmd'
    @"
@echo off
call "$legacyTools" x64 >nul
if errorlevel 1 exit /b 1
"$cmake" -S "$projectRoot/native/pairing" -B "$nativeBuild" -G Ninja "-DCMAKE_MAKE_PROGRAM=$ninja" "-DBORINGSSL_SOURCE=$source" -DCMAKE_BUILD_TYPE=Release -DBUILD_TESTING=OFF
if errorlevel 1 exit /b 1
"$cmake" --build "$nativeBuild" --target lantern_pake lantern_pairing_test --parallel 4
"@ | Set-Content -LiteralPath $batch
    & $env:ComSpec /d /c $batch
    $builtLibrary = Join-Path $nativeBuild 'lantern_pake.dll'
    $selfTest = Join-Path $nativeBuild 'lantern_pairing_test.exe'
} else {
    & $cmake -S native/pairing -B .tmp/pairing-windows -G 'Visual Studio 17 2022' -A x64 "-DBORINGSSL_SOURCE=$source" -DBUILD_TESTING=OFF
    if ($LASTEXITCODE) { throw 'Windows pairing configuration failed.' }
    & $cmake --build .tmp/pairing-windows --config Release --target lantern_pake lantern_pairing_test --parallel 4
    $builtLibrary = '.tmp/pairing-windows/Release/lantern_pake.dll'
    $selfTest = '.tmp/pairing-windows/Release/lantern_pairing_test.exe'
}
if ($LASTEXITCODE) { throw 'Windows pairing build failed.' }
& $selfTest
if ($LASTEXITCODE) { throw 'SPAKE2+ RFC 9383 known-answer verification failed.' }
Write-Output 'SPAKE2+ RFC 9383 known-answer verification passed.'
New-Item -ItemType Directory -Force .tools/pairing | Out-Null
Copy-Item -LiteralPath $builtLibrary -Destination .tools/pairing/lantern_pake.dll
Copy-Item -LiteralPath (Join-Path $source 'LICENSE') -Destination .tools/pairing/BORINGSSL-LICENSE.txt
