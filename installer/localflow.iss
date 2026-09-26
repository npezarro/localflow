; Windows installer for LocalFlow (Inno Setup 6). Built in CI:
;   iscc /DAppVersion=<version> /DSourceDir=<pyinstaller dist\LocalFlow> installer\localflow.iss
; Per-user install: no admin prompt. Settings go to %LOCALAPPDATA%\LocalFlow
; (the installed copy has no "data" folder next to it, so it isn't portable).

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#ifndef SourceDir
  #define SourceDir "..\dist\LocalFlow"
#endif

[Setup]
AppId={{6B1C2F0E-8E0B-4E36-9C0A-4C4F57C1D2A7}
AppName=LocalFlow
AppVersion={#AppVersion}
AppPublisher=LocalFlow
AppPublisherURL=https://github.com/npezarro/localflow
AppSupportURL=https://github.com/npezarro/localflow/issues
DefaultDirName={localappdata}\Programs\LocalFlow
DefaultGroupName=LocalFlow
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=..\dist
OutputBaseFilename=LocalFlow-Setup-x64
SetupIconFile=..\assets\icon.ico
UninstallDisplayIcon={app}\LocalFlow.exe
Compression=lzma2/max
SolidCompression=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
WizardStyle=modern
CloseApplications=force
RestartApplications=no

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Shortcuts:"
Name: "autostart"; Description: "Start LocalFlow when I sign in to Windows"; GroupDescription: "Startup:"; Flags: unchecked

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs; Excludes: "data\*"

[Icons]
Name: "{group}\LocalFlow"; Filename: "{app}\LocalFlow.exe"
Name: "{group}\Uninstall LocalFlow"; Filename: "{uninstallexe}"
Name: "{userdesktop}\LocalFlow"; Filename: "{app}\LocalFlow.exe"; Tasks: desktopicon

[Registry]
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "LocalFlow"; ValueData: """{app}\LocalFlow.exe"""; Flags: uninsdeletevalue; Tasks: autostart

[Run]
Filename: "{app}\LocalFlow.exe"; Description: "Launch LocalFlow"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "{cmd}"; Parameters: "/c taskkill /IM LocalFlow.exe /F"; Flags: runhidden; RunOnceId: "StopLocalFlow"
