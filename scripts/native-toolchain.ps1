function Find-MsvcBuildEnvironment {
    param(
        [string]$VsWhere = (Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio/Installer/vswhere.exe'),
        [string]$LegacyTools = (Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio/2019/BuildTools/VC/Auxiliary/Build/vcvarsall.bat')
    )
    if (Test-Path -LiteralPath $VsWhere) {
        # Query the C++ component, not a particular Visual Studio release or edition.
        # -products * includes standalone Build Tools installations.
        $installation = & $VsWhere -latest -products '*' -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath
        if ($LASTEXITCODE) { throw 'Visual Studio C++ tool discovery failed.' }
        if ($installation) {
            $environment = Join-Path ($installation | Select-Object -First 1) 'VC/Auxiliary/Build/vcvarsall.bat'
            if (Test-Path -LiteralPath $environment) { return $environment }
        }
    }
    # Some existing standalone 2019 toolchains are no longer registered.
    if (Test-Path -LiteralPath $LegacyTools) { return $LegacyTools }
    throw 'No MSVC x64 C++ build tools found. Install the Desktop development with C++ workload in Visual Studio or Build Tools, then retry.'
}
