; Compile with python -m tools.build_installer on Windows.
; A stable AppId lets future versions upgrade this per-user installation.
#ifndef MyAppVersion
  #error Pass /DMyAppVersion to the compiler
#endif
#ifndef SourceRoot
  #error Pass /DSourceRoot to the compiler
#endif

[Setup]
AppId={{B11481EC-09B3-4B5B-9706-4D1B3D745CB9}
AppName=CraftFetch
AppVersion={#MyAppVersion}
AppPublisher=CraftFetch contributors
AppPublisherURL=https://github.com/LengthXXXL/CraftFetch
AppSupportURL=https://github.com/LengthXXXL/CraftFetch/issues
AppUpdatesURL=https://github.com/LengthXXXL/CraftFetch/releases
DefaultDirName={localappdata}\Programs\CraftFetch
DefaultGroupName=CraftFetch
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
SourceDir={#SourceRoot}
OutputBaseFilename=CraftFetch-Setup-{#MyAppVersion}-Windows-x64
VersionInfoVersion={#MyAppVersion}
UninstallDisplayName=CraftFetch
UninstallDisplayIcon={app}\CraftFetch.exe
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
DisableWelcomePage=yes
DisableDirPage=yes
DisableProgramGroupPage=yes
DisableReadyPage=no
DisableFinishedPage=yes
CloseApplications=yes
RestartApplications=no

[Languages]
#if FileExists(AddBackslash(CompilerPath) + "Languages\ChineseSimplified.isl")
Name: "chinesesimplified"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"
#else
Name: "chinesesimplified"; MessagesFile: "compiler:Default.isl"
#endif

[Messages]
SetupWindowTitle=CraftFetch 安装
WizardReady=一键安装 CraftFetch
ReadyLabel1=准备好开始使用 CraftFetch 了。
ReadyLabel2a=点击“一键安装”即可完成安装，随后自动打开下载器。无需安装 Python，也不需要管理员权限。
ReadyLabel2b=点击“一键安装”即可完成安装，随后自动打开下载器。无需安装 Python，也不需要管理员权限。
ReadyMemoDir=安装位置：
ReadyMemoGroup=开始菜单：
ButtonInstall=一键安装(&I)
ButtonCancel=取消
WizardInstalling=正在安装 CraftFetch
InstallingLabel=正在安装程序并创建桌面快捷方式，请稍候。
StatusCreateDirs=正在创建安装目录…
StatusExtractFiles=正在安装程序…
StatusCreateIcons=正在创建快捷方式…
StatusCreateRegistryEntries=正在保存安装信息…
StatusSavingUninstall=正在准备卸载程序…
StatusRunProgram=正在打开 CraftFetch…

[Files]
Source: "dist\CraftFetch.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "README.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "LICENSE"; DestDir: "{app}"; Flags: ignoreversion
Source: "CHANGELOG.md"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{autodesktop}\CraftFetch"; Filename: "{app}\CraftFetch.exe"; WorkingDir: "{app}"
Name: "{autoprograms}\CraftFetch\CraftFetch"; Filename: "{app}\CraftFetch.exe"; WorkingDir: "{app}"

[Run]
Filename: "{app}\CraftFetch.exe"; WorkingDir: "{app}"; Flags: nowait skipifsilent
