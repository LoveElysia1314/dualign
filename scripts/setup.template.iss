; Dualign 安装包脚本 — 模板文件
; 由 build.py 读取后填充 @PLACEHOLDER@ 并生成临时 setup.iss 用于编译
; 模板本身不含任何本机路径信息，可安全提交到仓库

#define MyAppName "@APP_NAME@"
#define MyAppVersion "@APP_VERSION@"
#define MyAppExeName "@APP_EXE_NAME@"

[Setup]
AppId=B8F3A1D2-6E4C-4F9A-8E7D-1C5B2A3F0D9E
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher=Dualign Contributors
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
VersionInfoVersion=@APP_VERSION@
VersionInfoCompany=Dualign
VersionInfoDescription=双语平行文档对齐与 AI 辅助校验工具
VersionInfoTextVersion=@APP_VERSION@
OutputBaseFilename=Dualign_Setup_v@APP_VERSION@
Compression=lzma2/max
SolidCompression=yes
PrivilegesRequired=admin
ArchitecturesInstallIn64BitMode=x64os
ArchitecturesAllowed=x64compatible
WizardStyle=modern
SetupLogging=yes
ChangesEnvironment=yes
SetupIconFile=..\assets\branding\dualign.ico
UninstallDisplayIcon={app}\{#MyAppExeName}

[Languages]
Name: "chinesesimplified"; MessagesFile: "Languages/ChineseSimplified.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "快捷方式:"
Name: "addtopath"; Description: "将安装目录添加到系统 PATH（可在任意终端使用 dualign 命令）"; GroupDescription: "环境变量:"; Flags: unchecked

; @APP_DIR_RELATIVE@ 由 build.py 填充为相对路径（相对于 scripts/ 目录）
[Files]
Source: "@APP_DIR_RELATIVE@\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\卸载 {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "启动 {#MyAppName}"; Flags: nowait postinstall skipifsilent

[Code]

// ── PATH 环境变量操作 ──

const
  EnvironmentKey = 'SYSTEM\CurrentControlSet\Control\Session Manager\Environment';

procedure AddToPath(InstallDir: string);
var
  PathStr: string;
begin
  if not RegQueryStringValue(HKLM, EnvironmentKey, 'Path', PathStr) then
    PathStr := '';
  // 检查是否已存在，避免重复添加
  if Pos(LowerCase(InstallDir), LowerCase(PathStr)) = 0 then
  begin
    PathStr := PathStr + ';' + InstallDir;
    if RegWriteExpandStringValue(HKLM, EnvironmentKey, 'Path', PathStr) then
      Log('PATH 已添加: ' + InstallDir)
    else
      Log('PATH 写入失败');
  end;
end;

procedure RemoveFromPath(InstallDir: string);
var
  PathStr: string;
  P: Integer;
begin
  if RegQueryStringValue(HKLM, EnvironmentKey, 'Path', PathStr) then
  begin
    P := Pos(';' + LowerCase(InstallDir), LowerCase(PathStr));
    if P > 0 then
      Delete(PathStr, P, Length(InstallDir) + 1)
    else
    begin
      P := Pos(LowerCase(InstallDir) + ';', LowerCase(PathStr));
      if P > 0 then
        Delete(PathStr, P, Length(InstallDir) + 1)
      else
      begin
        P := Pos(LowerCase(InstallDir), LowerCase(PathStr));
        if P > 0 then
          Delete(PathStr, P, Length(InstallDir));
      end;
    end;
    RegWriteExpandStringValue(HKLM, EnvironmentKey, 'Path', PathStr);
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if (CurStep = ssPostInstall) and IsTaskSelected('addtopath') then
    AddToPath(ExpandConstant('{app}'));
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  appDataPath: string;
  userDataPath: string;
  choice: Integer;
begin
  case CurUninstallStep of
    usPostUninstall:
      begin
        // 清理系统 PATH
        RemoveFromPath(ExpandConstant('{app}'));

        // 询问删除用户缓存数据
        appDataPath := ExpandConstant('{userappdata}');
        userDataPath := appDataPath + '\.dualign';
        if DirExists(userDataPath) then
        begin
          choice := MsgBox('是否删除用户缓存数据 (对齐缓存 / 配置)？' #13#13 '路径: ' + userDataPath,
            mbConfirmation, MB_YESNO or MB_DEFBUTTON2);
          if choice = IDYES then
          begin
            if not DelTree(userDataPath, True, True, True) then
              MsgBox('无法完全删除用户数据，部分文件可能被占用。', mbError, MB_OK);
          end;
        end;
      end;
  end;
end;
