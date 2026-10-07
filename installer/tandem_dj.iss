; Inno Setup script building "Tandem DJ Setup <version>.exe" from the folder PyInstaller produced.
; scripts/build.py compiles it and passes ApplicationVersion, SourceFolder, OutputFolder and IconFile.
;
; The application is installed for the current user only, so no administrator rights are asked for.
; Settings, download history and logs live in %APPDATA%\Tandem DJ and are left in place by the uninstaller.

#define ApplicationName "Tandem DJ"
#define ApplicationProgram "Tandem DJ.exe"

[Setup]
AppId={{6F0B1C7E-3A4D-4E55-9B5C-7D1A2E9F4C10}
AppName={#ApplicationName}
AppVersion={#ApplicationVersion}
AppVerName={#ApplicationName} {#ApplicationVersion}
DefaultDirName={autopf}\{#ApplicationName}
DefaultGroupName={#ApplicationName}
DisableProgramGroupPage=yes
DisableDirPage=yes
PrivilegesRequired=lowest
OutputDir={#OutputFolder}
OutputBaseFilename={#ApplicationName} Setup {#ApplicationVersion}
SetupIconFile={#IconFile}
UninstallDisplayIcon={app}\{#ApplicationProgram}
UninstallDisplayName={#ApplicationName}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
CloseApplications=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"
Name: "french"; MessagesFile: "compiler:Languages\French.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "{#SourceFolder}\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{autoprograms}\{#ApplicationName}"; Filename: "{app}\{#ApplicationProgram}"
Name: "{autodesktop}\{#ApplicationName}"; Filename: "{app}\{#ApplicationProgram}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#ApplicationProgram}"; Description: "{cm:LaunchProgram,{#ApplicationName}}"; Flags: nowait postinstall skipifsilent
