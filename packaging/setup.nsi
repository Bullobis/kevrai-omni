; H3Studio NSIS 安装脚本（Linux 下 makensis 直接编译出 Windows 安装包）
; 用法: makensis packaging/setup.nsi

!ifndef APPVERSION
  !define APPVERSION "1.9.0"
!endif

Unicode true
Name "MiniMax H3 Studio"
OutFile "Output\H3Studio-Setup-${APPVERSION}.exe"

; 64 位安装到 Program Files
InstallDir "$PROGRAMFILES64\H3Studio"
InstallDirRegKey HKLM "Software\H3Studio" "InstallDir"
RequestExecutionLevel admin

SetCompressor /SOLID lzma
CRCCheck on

; ── 界面 ──
!include "MUI2.nsh"
!define MUI_ABORTWARNING
!define MUI_ICON "${NSISDIR}\Contrib\Graphics\Icons\modern-install.ico"
!define MUI_UNICON "${NSISDIR}\Contrib\Graphics\Icons\modern-uninstall.ico"
!define MUI_WELCOMEPAGE_TITLE "欢迎使用 MiniMax H3 Studio 安装向导"
!define MUI_WELCOMEPAGE_TEXT "本软件是 MiniMax H3 视频生成模型的一站式工作站：$\r$\n$\r$\n· 模型市场：多源测速 + 断点续传下载$\r$\n· 本地推理：显存自动管理，8GB 显存可跑 NF4 版$\r$\n· 全模式生成：文生视频 / 首尾帧 / 多模态参考 / 视频编辑$\r$\n$\r$\n注意：本地推理需要 NVIDIA 显卡；模型权重（35GB 起）将在软件内按需下载。"

!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_LICENSE "..\LICENSE.txt"
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!define MUI_FINISHPAGE_RUN "$INSTDIR\H3Studio.exe"
!define MUI_FINISHPAGE_RUN_TEXT "立即启动 MiniMax H3 Studio"
!insertmacro MUI_PAGE_FINISH

!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES

!insertmacro MUI_LANGUAGE "SimpChinese"

; ── 版本信息 ──
VIProductVersion "${APPVERSION}.0"
VIAddVersionKey /LANG=${LANG_SIMPCHINESE} "ProductName" "MiniMax H3 Studio"
VIAddVersionKey /LANG=${LANG_SIMPCHINESE} "FileVersion" "${APPVERSION}"
VIAddVersionKey /LANG=${LANG_SIMPCHINESE} "FileDescription" "MiniMax H3 视频生成工作站"

; ── 安装 ──
Section "主程序" SecMain
  SectionIn RO
  SetOutPath "$INSTDIR"
  File /r "..\dist\H3Studio\*.*"

  ; 卸载器
  WriteUninstaller "$INSTDIR\Uninstall.exe"
  WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\H3Studio" "DisplayName" "MiniMax H3 Studio"
  WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\H3Studio" "UninstallString" '"$INSTDIR\Uninstall.exe"'
  WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\H3Studio" "DisplayIcon" '"$INSTDIR\H3Studio.exe"'
  WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\H3Studio" "DisplayVersion" "${APPVERSION}"
  WriteRegStr HKLM "Software\H3Studio" "InstallDir" "$INSTDIR"

  CreateDirectory "$SMPROGRAMS\MiniMax H3 Studio"
  CreateShortcut "$SMPROGRAMS\MiniMax H3 Studio\MiniMax H3 Studio.lnk" "$INSTDIR\H3Studio.exe"
  CreateShortcut "$SMPROGRAMS\MiniMax H3 Studio\卸载.lnk" "$INSTDIR\Uninstall.exe"
  CreateShortcut "$DESKTOP\MiniMax H3 Studio.lnk" "$INSTDIR\H3Studio.exe"
SectionEnd

; ── NVIDIA 提示 ──
Function .onInit
  IfFileExists "$SYSDIR\nvcuda.dll" nvidia_ok 0
    MessageBox MB_OK|MB_ICONINFORMATION "未检测到 NVIDIA 驱动。$\r$\n$\r$\n本地推理功能需要 NVIDIA 显卡（CUDA）。你仍可继续安装，下载模型等功能可用。"
  nvidia_ok:
FunctionEnd

; ── 卸载 ──
Section "Uninstall"
  Delete "$DESKTOP\MiniMax H3 Studio.lnk"
  Delete "$SMPROGRAMS\MiniMax H3 Studio\MiniMax H3 Studio.lnk"
  Delete "$SMPROGRAMS\MiniMax H3 Studio\卸载.lnk"
  RMDir "$SMPROGRAMS\MiniMax H3 Studio"
  RMDir /r "$INSTDIR"
  DeleteRegKey HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\H3Studio"
  DeleteRegKey HKLM "Software\H3Studio"
SectionEnd
