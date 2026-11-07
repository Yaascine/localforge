[Setup]
AppName=LocalForge
AppVersion=0.1.2
AppId=LocalForge.Installer
DefaultDirName={localappdata}\Programs\LocalForge
DefaultGroupName=LocalForge
PrivilegesRequired=lowest
OutputDir=..\dist
OutputBaseFilename=LocalForge-0.1.2-windows-setup
Compression=lzma2
SolidCompression=yes
UninstallDisplayIcon={app}\LocalForge.exe
[Files]
Source: "..\dist\LocalForge\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
[Icons]
Name: "{group}\LocalForge"; Filename: "{app}\LocalForge.exe"
Name: "{userdesktop}\LocalForge"; Filename: "{app}\LocalForge.exe"; Tasks: desktopicon
[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; Flags: unchecked
[Run]
Filename: "{app}\LocalForge.exe"; Description: "Open LocalForge"; Flags: nowait postinstall skipifsilent
