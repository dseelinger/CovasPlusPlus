; covas.iss - Inno Setup script for COVAS++ (I6). PER-USER, UNSIGNED installer.
;
;   Build:  .\build.ps1 -Installer      (freezes with PyInstaller, then compiles this with ISCC)
;   Output: dist\installer\COVAS++ Setup.exe
;
; Needs Inno Setup 6 (ISCC.exe) installed: https://jrsoftware.org/isdl.php
;
; Design (INSTALLER_DESIGN.md):
;   * Per-user install to %LOCALAPPDATA%\Programs\COVAS++ - NO admin/UAC prompt (PrivilegesRequired
;     =lowest), which matters more for an unsigned app and pairs with the writable-state-under-user
;     -profile design. The installer writes ONLY the payload dir; it never touches user state in
;     %APPDATA%\COVAS++ (keys/overrides/logs), so updates preserve settings (decision #6).
;   * UNSIGNED by decision (#7). Consequence: Windows SmartScreen shows "Windows protected your PC /
;     unknown publisher" on first launch of Setup.exe. Users click "More info" then "Run anyway".
;     This is documented for users in installer\SMARTSCREEN.md (folded into docs/ at I7).
;   * Version is stamped from covas/__version__.py by build.ps1 (passed as /DAppVersion=x.y.z).

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#define AppName "COVAS++"
#define AppExeName "COVAS++.exe"
#define AppPublisher "Doug Seelinger"
#define IconFile "covas\assets\icons\covas.ico"

[Setup]
; A STABLE AppId is what makes "install over previous" an in-place upgrade - never change it.
AppId={{7C3A1E90-2B4D-4F6A-9E21-8F0A1B2C3D4E}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
VersionInfoVersion={#AppVersion}
; Per-user, no elevation.
DefaultDirName={localappdata}\Programs\COVAS++
DisableProgramGroupPage=yes
DisableDirPage=auto
PrivilegesRequired=lowest
; Upgrade in place (same AppId); ask to close a running instance so files can be replaced.
CloseApplications=yes
RestartApplications=no
OutputDir=dist\installer
OutputBaseFilename=COVAS++ Setup
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
UninstallDisplayName={#AppName}
UninstallDisplayIcon={app}\{#AppExeName}
#if FileExists(IconFile)
SetupIconFile={#IconFile}
#endif

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
; The entire PyInstaller one-folder output (COVAS++.exe + _internal\). recursesubdirs keeps the
; _internal tree; ignoreversion so an upgrade always overwrites (bundled DLLs have no useful
; version stamps to compare).
Source: "dist\COVAS++\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExeName}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExeName}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent
