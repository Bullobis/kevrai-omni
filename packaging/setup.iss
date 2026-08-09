; H3Studio Inno Setup 安装脚本（Inno Setup 6）
; 由 build_windows.bat 自动调用 ISCC 编译

#define MyAppName "MiniMax H3 Studio"
#define MyAppVersion "1.9.1"
#define MyAppPublisher "H3Studio"
#define MyAppExeName "H3Studio.exe"

[Setup]
AppId={{B7C4D2E8-5A1F-4H3D-9E6B-2C8D4F6A1E35}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\H3Studio
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
OutputDir=Output
OutputBaseFilename=H3Studio-Setup
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesInstallIn64BitMode=x64
; 安装包较大（含 PyTorch CUDA 运行库），允许大文件
DiskSpanning=no
SetupLogging=yes

[Languages]
Name: "chinesesimplified"; MessagesFile: "compiler:Languages\Unofficial\ChineseSimplified.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "..\dist\H3Studio\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent

[Code]
// 启动前检查加速硬件：NVIDIA（nvidia-smi）或 AMD（驱动 DLL），仅提示不阻止
function InitializeSetup(): Boolean;
var
  ResultCode: Integer;
begin
  Result := True;
  if FileExists(ExpandConstant('{sys}\atiadlxx.dll')) then
  begin
    // 检测到 AMD 驱动，直接通过
  end
  else if not Exec('cmd.exe', '/c nvidia-smi >nul 2>&1', '', SW_HIDE, ewWaitUntilTerminated, ResultCode) or (ResultCode <> 0) then
  begin
    MsgBox('未检测到 NVIDIA 或 AMD 显卡驱动。' #13#10 '本地推理需要加速硬件：NVIDIA（CUDA）/ AMD（ROCm）/ 华为昇腾（NPU）。' #13#10 '你仍可继续安装（模型下载、作品管理等功能可用）。', mbInformation, MB_OK);
  end;
end;
