@echo off
chcp 65001 >nul
setlocal EnableDelayedExpansion
title MiniMax H3 Studio 一键打包
cd /d "%~dp0"
set "ROOT=%cd%"
set "LOGFILE=%ROOT%\build_log.txt"

echo ═══════════════════════════════════════════════════════
echo     MiniMax H3 Studio · 一键打包
echo     完整日志将保存到 build_log.txt
echo ═══════════════════════════════════════════════════════
echo.
echo   [1] 完整版 — 含 torch CUDA + diffsynth 推理引擎
echo        包体积：约 2GB / 安装包约 1.5~2GB
echo        适合：最终用户分发，解压即用
echo.
echo   [2] 精简版 — 仅核心 + 界面（~80MB）
echo        包体积：约 80MB
echo        适合：快速测试、小体积分发，首次运行通过应用内
echo              市场下载推理引擎
echo.
set /p MODE="请选择 (1/2，默认1): "
if "!MODE!"=="" set MODE=1
if not "!MODE!"=="1" if not "!MODE!"=="2" (
    echo 无效选择，请输入 1 或 2
    pause & exit /b 1
)
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
%PY% -c "import sys; v=sys.version_info[:2]; sys.exit(0 if (3,10)<=v<=(3,14) else 1)" >nul 2>nul
if errorlevel 1 (
    echo [错误] Python 版本需在 3.10~3.14 之间（推荐 3.12/3.13）。
    pause & exit /b 1
)

REM ── [2] 虚拟环境 ──
echo.
if "!MODE!"=="1" (echo [1/5] 创建/复用虚拟环境...) else (echo [1/3] 创建/复用虚拟环境...)
if not exist .venv (
    %PY% -m venv .venv >> "%LOGFILE%" 2>&1 || (
        echo [错误] 虚拟环境创建失败，详见 build_log.txt
        pause & exit /b 1
    )
)
call .venv\Scripts\activate.bat

REM ═══════════════════════════════════════════════════════
if "!MODE!"=="1" goto FULL_BUILD
if "!MODE!"=="2" goto LITE_BUILD

REM ═══════════════════════════════════════════════════════
REM ── 完整版打包 ──
REM ═══════════════════════════════════════════════════════
:FULL_BUILD
echo [2/5] 检查/安装 PyTorch CUDA（约 2.5GB）...
python -c "import torch" >nul 2>nul
if errorlevel 1 (
    pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu128 >> "%LOGFILE%" 2>&1
    if errorlevel 1 (
        echo   cu128 失败（老卡 GTX 10/900 系），尝试 cu126...
        pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu126 >> "%LOGFILE%" 2>&1 || (
            echo [错误] PyTorch 安装失败，详见 build_log.txt。
            pause & exit /b 1
        )
    )
)
python -c "import torch; print('  torch', torch.__version__, '| CUDA:', torch.version.cuda)"

echo [3/5] 检查/安装应用依赖...
python -c "import PySide6, diffsynth" >nul 2>nul
if errorlevel 1 (
    pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple >> "%LOGFILE%" 2>&1 || ^
    pip install -r requirements.txt -i https://mirrors.cloud.tencent.com/pypi/simple >> "%LOGFILE%" 2>&1 || (
        echo [错误] 依赖安装失败，详见 build_log.txt。
        pause & exit /b 1
    )
)
pip install pyinstaller -i https://pypi.tuna.tsinghua.edu.cn/simple >> "%LOGFILE%" 2>&1 || ^
pip install pyinstaller -i https://mirrors.cloud.tencent.com/pypi/simple >> "%LOGFILE%" 2>&1 || (
    echo [错误] PyInstaller 安装失败，详见 build_log.txt
    pause & exit /b 1
)

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
  --hidden-import h3studio.ui.page_custom --hidden-import h3studio.ui.page_help ^
  --hidden-import h3studio.ui.page_image ^
  --hidden-import h3studio.i18n --hidden-import h3studio.customizer --hidden-import h3studio.image_gen ^
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
    pause & exit /b 1
)

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
    echo ═══════════ 完整版打包完成 ═══════════
    echo 安装包：packaging\Output\H3Studio-Setup-2.1.0.exe
    echo 绿色版：dist\H3Studio\H3Studio.exe
) else (
    echo [提示] 未检测到 Inno Setup 6，已跳过安装包编译。
    echo        当前已产出绿色版：dist\H3Studio\H3Studio.exe
)
goto END

REM ═══════════════════════════════════════════════════════
REM ── 精简版打包（不含 torch/diffsynth，~80MB）──
REM ═══════════════════════════════════════════════════════
:LITE_BUILD
echo [2/3] 检查/安装精简依赖（PySide6 + 基础库）...
python -c "import PySide6" >nul 2>nul
if errorlevel 1 (
    pip install PySide6>=6.7 requests pillow -i https://pypi.tuna.tsinghua.edu.cn/simple >> "%LOGFILE%" 2>&1 || ^
    pip install PySide6>=6.7 requests pillow -i https://mirrors.cloud.tencent.com/pypi/simple >> "%LOGFILE%" 2>&1 || (
        echo [错误] 依赖安装失败，详见 build_log.txt。
        pause & exit /b 1
    )
)
pip install pyinstaller -i https://pypi.tuna.tsinghua.edu.cn/simple >> "%LOGFILE%" 2>&1 || ^
pip install pyinstaller -i https://mirrors.cloud.tencent.com/pypi/simple >> "%LOGFILE%" 2>&1 || (
    echo [错误] PyInstaller 安装失败，详见 build_log.txt
    pause & exit /b 1
)

echo [3/3] PyInstaller 打包精简版（约 2~5 分钟）...
if exist dist rmdir /s /q dist
if exist build rmdir /s /q build
python -m PyInstaller --noconfirm --clean --windowed --name H3Studio ^
  --paths . ^
  --add-data "resources;resources" ^
  --hidden-import h3studio --hidden-import h3studio.facts --hidden-import h3studio.config ^
  --hidden-import h3studio.hardware --hidden-import h3studio.sources --hidden-import h3studio.downloader ^
  --hidden-import h3studio.planner ^
  --hidden-import h3studio.ui --hidden-import h3studio.ui.styles ^
  --hidden-import h3studio.ui.widgets --hidden-import h3studio.ui.main_window ^
  --hidden-import h3studio.ui.page_generate --hidden-import h3studio.ui.page_market ^
  --hidden-import h3studio.ui.page_library --hidden-import h3studio.ui.page_gallery ^
  --hidden-import h3studio.ui.page_settings ^
  --hidden-import h3studio.ui.page_custom --hidden-import h3studio.ui.page_help ^
  --hidden-import h3studio.ui.page_image ^
  --hidden-import h3studio.i18n --hidden-import h3studio.customizer --hidden-import h3studio.image_gen ^
  --hidden-import PIL --hidden-import requests ^
  --exclude-module torch --exclude-module torchaudio --exclude-module torchvision ^
  --exclude-module diffsynth --exclude-module modelscope --exclude-module huggingface_hub ^
  --exclude-module bitsandbytes --exclude-module av --exclude-module transformers ^
  --exclude-module safetensors --exclude-module einops --exclude-module imageio ^
  --exclude-module imageio_ffmpeg --exclude-module psutil ^
  --exclude-module tkinter --exclude-module matplotlib --exclude-module IPython ^
  --exclude-module jupyter --exclude-module pytest ^
  h3studio\main.py >> "%LOGFILE%" 2>&1 || (
    echo [错误] PyInstaller 打包失败，详见 build_log.txt
    pause & exit /b 1
)

echo.
echo ═══════════ 精简版打包完成 ═══════════
echo 绿色版：dist\H3Studio\H3Studio.exe
echo 体积：约 80MB（首次运行通过应用内市场下载推理引擎）
echo ═══════════════════════════════════════
goto END

:END
echo.
pause
exit /b 0
