; SPDX-License-Identifier: GPL-3.0-or-later
; Inno Setup script for pdf/45.
; Build from the repository root after `pyinstaller packaging/pdf45.spec`:
;   iscc /DAppVersion=0.1.0 packaging\windows\pdf45.iss
; Output: dist\pdf45-<version>-setup.exe

#ifndef AppVersion
  #define AppVersion "1.0.0"
#endif
; Must match the organisation on the code-signing certificate (src/pdf45/__init__.py: PUBLISHER).
#ifndef AppPublisher
  #define AppPublisher "LAST CHANCE SRL"
#endif
#define AppName "pdf/45"
; Windows folder and shortcut names cannot contain "/", so a safe variant is used for those.
#define AppSafeName "pdf45"
#define AppExe "pdf45.exe"
#define Root "..\.."

[Setup]
AppId={{6E1B3B8E-7B1F-4E1A-9C3B-1C7E4B6D0F01}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
AppCopyright=Copyright (C) 2026 Dragos Dimitriu. GPL-3.0-or-later
VersionInfoVersion={#AppVersion}
VersionInfoCompany={#AppPublisher}
VersionInfoProductName={#AppName}
VersionInfoDescription={#AppName} setup
AppPublisherURL=https://github.com/dragosdimitriu/pdf45
DefaultDirName={autopf}\{#AppSafeName}
DefaultGroupName={#AppSafeName}
UninstallDisplayIcon={app}\{#AppExe}
OutputDir={#Root}\dist
OutputBaseFilename=pdf45-{#AppVersion}-setup
SetupIconFile={#Root}\src\pdf45\resources\pdf45.ico
LicenseFile={#Root}\LICENSE
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequiredOverridesAllowed=dialog
ChangesAssociations=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked
Name: "pdfassoc"; Description: "Open .pdf files with {#AppName}"; GroupDescription: "File associations:"; Flags: unchecked

[Files]
Source: "{#Root}\dist\pdf45\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppSafeName}"; Filename: "{app}\{#AppExe}"
Name: "{group}\Uninstall {#AppSafeName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppSafeName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Registry]
; Register as an application capable of opening PDFs; Windows offers it in "Open with".
Root: HKA; Subkey: "Software\Classes\Applications\{#AppExe}"; ValueType: string; ValueName: "FriendlyAppName"; ValueData: "{#AppName}"; Flags: uninsdeletekey
Root: HKA; Subkey: "Software\Classes\Applications\{#AppExe}\shell\open\command"; ValueType: string; ValueData: """{app}\{#AppExe}"" ""%1"""; Flags: uninsdeletekey
Root: HKA; Subkey: "Software\Classes\Applications\{#AppExe}\SupportedTypes"; ValueType: string; ValueName: ".pdf"; ValueData: ""; Flags: uninsdeletekey
; Optional default association (task).
Root: HKA; Subkey: "Software\Classes\pdf45.pdf"; ValueType: string; ValueData: "PDF document"; Flags: uninsdeletekey; Tasks: pdfassoc
Root: HKA; Subkey: "Software\Classes\pdf45.pdf\DefaultIcon"; ValueType: string; ValueData: "{app}\{#AppExe},0"; Tasks: pdfassoc
Root: HKA; Subkey: "Software\Classes\pdf45.pdf\shell\open\command"; ValueType: string; ValueData: """{app}\{#AppExe}"" ""%1"""; Tasks: pdfassoc
Root: HKA; Subkey: "Software\Classes\.pdf\OpenWithProgids"; ValueType: string; ValueName: "pdf45.pdf"; ValueData: ""; Flags: uninsdeletevalue; Tasks: pdfassoc

[Run]
Filename: "{app}\{#AppExe}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent
