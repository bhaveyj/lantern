#define AppVersion "0.1.0"
[Setup]
AppId={{994EF0D3-C33F-4386-ACBC-E01CC4755607}
AppName=Lantern TV
AppVersion={#AppVersion}
AppPublisher=Lantern contributors
DefaultDirName={localappdata}\Programs\LanternTV
DefaultGroupName=Lantern TV
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\dist
OutputBaseFilename=Lantern-Setup-{#AppVersion}
Compression=lzma2/fast
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\Lantern.exe
SetupIconFile=..\assets\lantern-windows.ico
LicenseFile=..\LICENSE
CloseApplications=yes

[Files]
Source: "..\dist\Lantern\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\Lantern TV"; Filename: "{app}\Lantern.exe"
Name: "{autodesktop}\Lantern TV"; Filename: "{app}\Lantern.exe"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; Flags: unchecked

[Run]
Filename: "{app}\Lantern.exe"; Description: "Open Lantern TV"; Flags: nowait postinstall skipifsilent
