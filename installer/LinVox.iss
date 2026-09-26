; ---------------------------------------------------------------------------
; LinVox 安装包脚本（Inno Setup 6）
;
; 前置：先出 PyInstaller 产物
;     pyinstaller LinVox.spec --noconfirm        # → dist\LinVox\LinVox.exe
;
; 编译：
;     iscc installer\LinVox.iss                  # → installer\Output\LinVox_Setup_x.y.z.exe
;     （或双击 installer\build_installer.ps1）
;
; 三条不能动的约定：
; 1. **不要**写 ``Source: ..\dist\LinVox\* ... recursesubdirs``。
;    dist\LinVox\RVC20260718DML 是个目录联接（README 里建议的性能优化），指到
;    8.4 GB 的 RVC 工程；Inno 递归会跟着联接一起扫进去，安装包直接变成几个 G。
;    所以这里显式列 LinVox.exe + _internal，天然绕开它。
; 2. **默认装到 %LOCALAPPDATA%\Programs\LinVox**（PrivilegesRequired=lowest，
;    {autopf} 因此在用户目录）。RVC 工程要放在 exe 同级，且它会往自己的
;    logs/ TEMP/ assets/weights/ 里写东西 —— 装进 Program Files 会因为没
;    写权限直接哑火。
; 3. 8.4 GB 的 RVC 工程**不在包里**（3.6 万个文件，压出来的包没法分发）。
;    安装后在 {app} 里留一份 RVC_NOTICE.txt 说明怎么补。
; ---------------------------------------------------------------------------

#define MyAppName "LinVox"
#define MyAppVersion "1.0.0"
#define MyAppPublisher "LinVox"
#define MyAppExeName "LinVox.exe"
#define MySrcDir "..\dist\LinVox"

[Setup]
AppId={{A9F3C1D2-4E5B-4C71-8D6A-2B7E9F0C1A34}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
VersionInfoVersion={#MyAppVersion}
VersionInfoProductName={#MyAppName}
VersionInfoCopyright=Copyright (C) 2026 LinVox
DefaultDirName={autopf}\LinVox
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
DisableDirPage=auto
AppendDefaultDirName=yes
AllowNoIcons=yes
PrivilegesRequired=lowest
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
Compression=lzma2/max
LZMAAlgorithm=1
SolidCompression=yes
SetupIconFile=..\app\resources\logo.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
OutputDir=Output
OutputBaseFilename=LinVox_Setup_{#MyAppVersion}
SetupMutex=LinVoxSetupMutex
CloseApplications=yes
RestartApplications=no

[Languages]
Name: "chinesesimplified"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "{#MySrcDir}\{#MyAppExeName}"; DestDir: "{app}"; Flags: ignoreversion comparetimestamp
Source: "{#MySrcDir}\_internal\*"; DestDir: "{app}\_internal"; Flags: ignoreversion recursesubdirs createallsubdirs comparetimestamp
Source: "RVC_NOTICE.txt"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"
Name: "{group}\{cm:UninstallProgram,{#MyAppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent
; 打开安装目录：把 RVC20260718DML 拷到这里就能解锁全部 AI 功能（默认不勾）
Filename: "{app}"; Description: "打开安装目录（放入 RVC 工程）"; Flags: nowait postinstall skipifsilent shellexec unchecked

[UninstallDelete]
; 8.4 GB 的 RVC 是用户自己放的数据目录，卸载时不跟着删
Type: files; Name: "{app}\RVC_NOTICE.txt"
