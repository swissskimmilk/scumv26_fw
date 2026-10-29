; Inno Setup script for the Windows installer. Run after PyInstaller (packaging/build_app.ps1
; does both):  iscc /DAppVersion=1.2.3 packaging\installer.iss  ->  dist\SCuMV26-<ver>-windows-setup.exe
#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

[Setup]
AppId={{6B1F3C2E-5A0D-4C59-9E0B-2F6C0D3A9E41}
AppName=SCuMV26 bring-up
AppVersion={#AppVersion}
AppPublisher=SCuMV26 bring-up
DefaultDirName={autopf}\SCuMV26
DefaultGroupName=SCuMV26
UninstallDisplayIcon={app}\SCuMV26.exe
OutputDir=..\dist
OutputBaseFilename=SCuMV26-{#AppVersion}-windows-setup
Compression=lzma2
SolidCompression=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
; Per-user install by default: no admin prompt, the user can still pick all users
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
WizardStyle=modern

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "..\dist\SCuMV26\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\SCuMV26 bring-up"; Filename: "{app}\SCuMV26.exe"
Name: "{group}\SCuMV26 bring-up (simulator)"; Filename: "{app}\SCuMV26.exe"; Parameters: "--sim"
Name: "{autodesktop}\SCuMV26 bring-up"; Filename: "{app}\SCuMV26.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\SCuMV26.exe"; Description: "{cm:LaunchProgram,SCuMV26 bring-up}"; Flags: nowait postinstall skipifsilent
