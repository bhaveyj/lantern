param([switch]$Install, [string]$Device)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot -Parent
Set-Location $projectRoot
$sdkDirectory = if ($env:ANDROID_HOME) { $env:ANDROID_HOME } else { Join-Path $env:LOCALAPPDATA 'Android\Sdk' }
if (-not (Test-Path $sdkDirectory)) { throw 'Install Android SDK command-line tools and Android platform 35, then set ANDROID_HOME.' }
$sdkProperty = $sdkDirectory.Replace('\', '/').Replace(':', '\:')
Set-Content -LiteralPath 'android/local.properties' -Value "sdk.dir=$sdkProperty"
$env:TEMP = Join-Path $projectRoot '.tmp'
$env:TMP = $env:TEMP
$gradlePath = Join-Path $projectRoot '.tools\gradle-8.10.2\bin\gradle.bat'
if (-not (Test-Path $gradlePath)) {
    $cachedGradle = Get-ChildItem -LiteralPath (Join-Path $env:USERPROFILE '.gradle\wrapper\dists\gradle-8.10.2-all') -Filter gradle.bat -Recurse -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($cachedGradle) { $gradlePath = $cachedGradle.FullName } else { throw 'Run scripts/bootstrap.ps1 to install Gradle.' }
}
& $gradlePath -p android --no-daemon assembleDebug testDebugUnitTest
if ($LASTEXITCODE) { throw 'Android build failed.' }
New-Item -ItemType Directory -Force dist | Out-Null
Copy-Item -LiteralPath android/app/build/outputs/apk/debug/app-debug.apk -Destination dist/Lantern-TV-0.1.0.apk
if ($Install) {
    $adbPath = Join-Path $sdkDirectory 'platform-tools\adb.exe'
    $deviceArgs = if ($Device) { @('-s', $Device) } else { @() }
    & $adbPath @deviceArgs install -r dist/Lantern-TV-0.1.0.apk
    if ($LASTEXITCODE) { throw 'TV installation failed. Enable and approve debugging first.' }
    & $adbPath @deviceArgs shell am start -n app.lantern.tv/.MainActivity
}
