@echo off
chcp 65001 >nul
setlocal EnableDelayedExpansion
title MiniMax H3 Studio 一键打包
cd /d "%~dp0"
set "ROOT=%cd%"
set "LOGFILE=%ROOT%\build_log.txt"

echo ══════════════════════════════════════════════
echo     MiniMax H3 Studio · 一键打包（出安装包）
echo     完整日志将保存到 build_log.txt
echo ══════════════════════════════════════════════
echo.
echo 开始时间 %date% %time% > "%LOGFILE%"

REM ── [0] 路径体检：桌面/中文/OneDrive 是打包失败三大元凶 ──
for /f "delims=" %%r in ('powershell -NoProfile -Command "if ('%ROOT%' -match '[\u4e00-\u9fa5]') {'CN'} else {'OK'}" 2^>nul') do set "CNCHK=%%r"
if "%CNCHK%"=="CN" (
    echo [重要提醒] 当前路径包含中文字符：
    echo     %ROOT%
    echo     打包工具对中文路径兼容性差，强烈建议：
    echo     把整个 H3Studio 文件夹复制到纯英文路径（例如 D:\H3Studio）再运行本脚本。
    echo.
    set /p GOON="仍要在当前路径继续打包吗？(Y/N) "
    if /i not "!GOON!"=="Y" exit /b 0
)
echo %ROOT% | findstr /I "OneDrive" >nul && (
    echo [重要提醒] 当前位于 OneDrive 同步目录（常见于"桌面"被 OneDrive 接管）。
    echo     文件同步锁定会导致打包中途失败，请把文件夹移出 OneDrive 再打包。
    echo.
    set /p GOON2="仍要继续吗？(Y/N) "
    if /i not "!GOON2!"=="Y" exit /b 0
)

REM ── [1] 找 Python ──
set "PY=python"
where python >nul 2>nul
if errorlevel 1 (
    where py >nul 2>nul
    if errorlevel 1 (
        echo [错误] 未找到 Python。请先安装 Python 3.10~3.14（推荐 3.12/3.13，勾选 Add to PATH）。
        pause & exit /b 1
    )
    set "PY=py"
)
echo [信息] Python 版本：
%PY% --version
REM 版本门禁：PyTorch Windows 版支持 3.10~3.14（2026-08 核实于 PyTorch 官方兼容矩阵）
%PY% -c "import sys; v=sys.version_info[:2]; sys.exit(0 if (3,10)<=v<=(3,14) else 1)" >nul 2>nul
if errorlevel 1 (
    echo [错误] Python 版本需在 3.10~3.14 之间（推荐 3.12/3.13）。3.15 暂无 Windows 版 PyTorch。
    pause & exit /b 1
)

REM ── [2] 虚拟环境 ──
echo.
echo [1/5] 创建/复用虚拟环境...
if not exist .venv (
    %PY% -m venv .venv >> "%LOGFILE%" 2>&1 || (
        echo [错误] 虚拟环境创建失败，详见 build_log.txt
        pause & exit /b 1
    )
)
call .venv\Scripts\activate.bat

REM ── [3] PyTorch CUDA ──
echo [2/5] 检查/安装 PyTorch CUDA（约 2.5GB）...
python -c "import torch" >nul 2>nul
if errorlevel 1 (
    REM 必须走 PyTorch 官方 CUDA 源：国内 PyPI 镜像的 torch 会匹配到 CPU 版（2026-08 实测确认）
    pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu128 >> "%LOGFILE%" 2>&1
    if errorlevel 1 (
        echo   cu128 失败（老卡 GTX 10/900 系），尝试 cu126...
        pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu126 >> "%LOGFILE%" 2>&1 || (
            echo [错误] PyTorch 安装失败，详见 build_log.txt。检查网络后重跑本脚本可续装。
            pause & exit /b 1
        )
    )
)
python -c "import torch; print('  torch', torch.__version__, '| CUDA:', torch.version.cuda)"

REM ── [4] 应用依赖 ──
echo [3/5] 检查/安装应用依赖...
python -c "import PySide6, diffsynth" >nul 2>nul
if errorlevel 1 (
    pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple >> "%LOGFILE%" 2>&1 || ^
    pip install -r requirements.txt -i https://mirrors.cloud.tencent.com/pypi/simple >> "%LOGFILE%" 2>&1 || (
        echo [错误] 依赖安装失败，详见 build_log.txt。重跑本脚本可续装。
        pause & exit /b 1
    )
)
pip install pyinstaller -i https://pypi.tuna.tsinghua.edu.cn/simple >> "%LOGFILE%" 2>&1 || ^
pip install pyinstaller -i https://mirrors.cloud.tencent.com/pypi/simple >> "%LOGFILE%" 2>&1 || (
    echo [错误] PyInstaller 安装失败，详见 build_log.txt
    pause & exit /b 1
)

REM ── [5] PyInstaller 打包 ──
echo [4/5] PyInstaller 打包（约 5~15 分钟）...
if exist dist rmdir /s /q dist
if exist build rmdir /s /q build
python -m PyInstaller --noconfirm --clean --windowed --name H3Studio ^
  --paths . ^
  --add-data "resources;resources" ^
  --collect-all diffsynth --collect-all bitsandbytes --collect-all av ^
  --hidden-import h3studio --hidden-import h3studio.facts --hidden-import h3studio.config ^
  --hidden-import h3studio.hardware --hidden-import h3studio.sources --hidden-import h3studio.downloader ^
  --hidden-import h3studio.engine --hidden-import h3studio.planner --hidden-import h3studio.ui ^
  --hidden-import h3studio.ui.styles --hidden-import h3studio.ui.widgets --hidden-import h3studio.ui.main_window ^
  --hidden-import h3studio.ui.page_generate --hidden-import h3studio.ui.page_market ^
  --hidden-import h3studio.ui.page_library --hidden-import h3studio.ui.page_gallery ^
  --hidden-import h3studio.ui.page_settings ^
  --hidden-import diffsynth.pipelines.minimax_h3_audio_video ^
  --hidden-import diffsynth.utils.data.audio_video --hidden-import diffsynth.utils.data.audio ^
  --hidden-import modelscope --hidden-import huggingface_hub --hidden-import safetensors ^
  --hidden-import einops --hidden-import transformers --hidden-import PIL --hidden-import psutil ^
  --hidden-import requests --hidden-import torchaudio --hidden-import torchvision ^
  --hidden-import imageio --hidden-import imageio.v2 --hidden-import imageio_ffmpeg ^
  --exclude-module tkinter --exclude-module matplotlib --exclude-module IPython ^
  --exclude-module jupyter --exclude-module pytest ^
  h3studio\main.py >> "%LOGFILE%" 2>&1 || (
    echo [错误] PyInstaller 打包失败，详见 build_log.txt
    echo        （中文路径/OneDrive 目录/杀毒软件拦截是三大常见原因）
    pause & exit /b 1
)

REM ── [6] Inno Setup 编译安装包 ──
echo [5/5] 编译安装包...
set "ISCC="
if exist "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" set "ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if exist "%ProgramFiles%\Inno Setup 6\ISCC.exe" set "ISCC=%ProgramFiles%\Inno Setup 6\ISCC.exe"
if defined ISCC (
    if not exist packaging\Output mkdir packaging\Output
    "!ISCC!" packaging\setup.iss >> "%LOGFILE%" 2>&1 || (
        echo [错误] 安装包编译失败，详见 build_log.txt
        pause & exit /b 1
    )
    echo.
    echo ═══════════ 打包完成 ═══════════
    echo 安装包：packaging\Output\H3Studio-Setup-1.10.0.exe
    echo 绿色版：dist\H3Studio\H3Studio.exe
) else (
    echo [提示] 未检测到 Inno Setup 6，已跳过安装包编译。
    echo        安装免费的 Inno Setup 6（jrsoftware.org/isdl.php）后重跑本脚本即可生成安装包。
    echo        当前已产出绿色版：dist\H3Studio\H3Studio.exe
)

echo.
pause
exit /b 0
