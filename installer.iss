; BAZZ.AGENT —— 便携式 Windows 安装包（Inno Setup 6）
; 构建（CI 传 -DSourceDir 与 /DMyAppVersion）：
;   iscc /DSourceDir="<stage>\BAZZ.AGENT-win32-x64" /DMyAppVersion=1.2.16 installer.iss
;
; 设计要点：
;   * 便携式：装到用户可写目录 {localappdata}\Programs\BAZZ.AGENT，无需管理员权限。
;   * 自更新（整目录替换 BAZZ.AGENT-win32-x64）与 workspace 数据机制保持不变。
;   * 生成桌面 + 开始菜单快捷方式，并带卸载器。
#ifndef SourceDir
  #define SourceDir "dist_desktop\BAZZ.AGENT-win32-x64"
#endif
#ifndef MyAppVersion
  #define MyAppVersion "1.2.16"
#endif
#ifndef distout
  #define distout "dist_desktop"
#endif

#define MyAppName "BAZZ.AGENT"
#define MyAppPublisher "xinyuzjj"
#define MyAppExeName "BAZZ.AGENT.exe"
#define MyAppId "{{8A2B2F4E-9C1D-4B6A-B3C2-7D4E5F6A7B8C}"

[Setup]
AppId={#MyAppId}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={localappdata}\Programs\{#MyAppName}
DisableProgramGroupPage=yes
DisableDirPage=auto
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
OutputDir={#distout}
OutputBaseFilename=BAZZ.AGENT-v{#MyAppVersion}-setup
SetupIconFile={#SourceDir}\resources\assets\icon.ico
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
UninstallDisplayName={#MyAppName}
UsePreviousAppDir=yes
CloseApplications=yes
RestartApplications=no

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
; 整包递归拷贝，保留目录结构；excludes 掉源码/缓存
Source: "{#SourceDir}\*"; DestDir: "{app}"; \
  Flags: recursesubdirs createallsubdirs ignoreversion; \
  Excludes: "*.pyc,__pycache__"

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; \
  Flags: nowait postinstall skipifsilent

[UninstallDelete]
Type: filesandordirs; Name: "{app}"
Type: filesandordirs; Name: "{app}\..\{#MyAppName}-win32-x64"

[Code]
// v1.5.20：安装前强杀自家后台进程。mihomo 内核 / runtime node / 后端都是无窗口
// 进程，Restart Manager 关不掉 → CloseApplications 弹「Select action」卡住安装。
// taskkill 按镜像名杀自有进程（BAZZ.AGENT/ScoutBackend/mihomo 名称唯一）；
// node/python 按路径锚定安装根，避免误杀用户自己的同名进程。
function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  AppDir, PsCmd: String;
  ResultCode: Integer;
begin
  Result := '';
  AppDir := ExpandConstant('{app}');
  Exec(ExpandConstant('{sys}\taskkill.exe'), '/F /IM BAZZ.AGENT.exe /T', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  Exec(ExpandConstant('{sys}\taskkill.exe'), '/F /IM ScoutBackend.exe /T', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  Exec(ExpandConstant('{sys}\taskkill.exe'), '/F /IM mihomo.exe /T', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  PsCmd := '-NoProfile -ExecutionPolicy Bypass -Command "Get-Process node,python -ErrorAction SilentlyContinue | Where-Object {{ $_.Path -and $_.Path.StartsWith(''' + AppDir + ''') }} | Stop-Process -Force"';
  Exec(ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe'), PsCmd, '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  Sleep(800);
end;