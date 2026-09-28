#define MyAppName "Sydney Road Electricity Monitor"
#define MyAppVersion "1.1.0"
#define MyAppPublisher "Connect Logistics"
#define MyAppExeName "Sydney Road Electricity Monitor.exe"

[Setup]
AppId={{FC548C79-B973-4E9C-97F8-5332ACED3C00}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={localappdata}\Programs\Connect Logistics\{#MyAppName}
DefaultGroupName=Connect Logistics
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\dist\installer
OutputBaseFilename=Sydney_Road_Electricity_Monitor_Setup_{#MyAppVersion}
SetupIconFile=staging\resources\branding\Connect-Logistics-Icon.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
RestartApplications=no
AppMutex=Local\ConnectLogistics.SydneyRoadElectricityMonitor

[Files]
Source: "..\dist\Sydney Road Electricity Monitor\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\Connect Logistics\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Launch {#MyAppName}"; Flags: nowait postinstall skipifsilent
