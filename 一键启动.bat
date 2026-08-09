@echo off
chcp 65001 >nul
setlocal EnableDelayedExpansion
title MiniMax H3 Studio 一键启动
cd /d "%~dp0"
set "ROOT=%cd%"

echo ══════════════════════════════════════════════
echo     MiniMax H3 Studio · 一键启动（免打包）
echo ══════════════════════════════════════════════
echo.

REM ── [0] 路径体检（中文 / 空格 / OneDrive 都可能引发奇怪错误）──
set "PATHWARN="
for /f "delims=" %%r in ('powershell -NoProfile -Command "if ('%ROOT%' -match '[\u4e00-\u9fa5]') {'CN'} else {'OK'}" 2^>nul') do set "CNCHK=%%r"
if "%CNCHK%"=="CN" (
    echo [提醒] 当前路径包含中文字符：
    echo        %ROOT%
    echo        中文路径偶尔会导致依赖安装或打包出错。
    echo        如遇报错，建议把整个文件夹移动到纯英文路径（如 D:\H3Studio）再试。
    echo.
    set "PATHWARN=1"
)
echo %ROOT% | findstr /I "OneDrive" >nul && (
    echo [提醒] 当前位于 OneDrive 同步目录，文件同步锁定可能导致启动/打包失败。
    echo        建议把文件夹移出 OneDrive（例如 D:\H3Studio）。
    echo.
    set "PATHWARN=1"
)
echo %ROOT% | findstr " " >nul && (
    echo [提醒] 路径包含空格，一般不会有问题，如遇异常报错请换纯英文无空格路径。
    echo.
)

REM ── [1] 找 Python ──
set "PY=python"
where python >nul 2>nul
if errorlevel 1 (
    where py >nul 2>nul
    if errorlevel 1 (
        echo [错误] 未找到 Python。
        echo        请先安装 Python 3.10 ~ 3.14（推荐 3.12/3.13，安装时务必勾选 Add python.exe to PATH）：
        echo        下载地址 https://www.python.org/downloads/
        echo.
        pause
        exit /b 1
    )
    set "PY=py"
)
echo [信息] 使用 Python：
%PY% --version
REM 版本门禁：PyTorch Windows 版支持 3.10~3.14（2026-08 核实于 PyTorch 官方兼容矩阵）
%PY% -c "import sys; v=sys.version_info[:2]; sys.exit(0 if (3,10)<=v<=(3,14) else 1)" >nul 2>nul
if errorlevel 1 (
    echo [错误] 当前 Python 版本不在 3.10 ~ 3.14 范围内。
    echo        PyTorch Windows 版目前支持 3.10~3.14（推荐 3.12 或 3.13）。
    echo        Python 3.15 暂无 Windows 版 PyTorch，请勿使用。
    echo        下载地址：https://www.python.org/downloads/
    pause & exit /b 1
)
echo.

REM ── [2] 虚拟环境 ──
if not exist .venv (
    echo [1/4] 首次启动：创建虚拟环境...
    %PY% -m venv .venv || (
        echo [错误] 虚拟环境创建失败。请确认 Python 版本为 3.10~3.12，或重新安装 Python。
        pause & exit /b 1
    )
) else (
    echo [1/4] 虚拟环境已存在，跳过创建。
)
call .venv\Scripts\activate.bat

REM ── [3] 依赖检测与安装 ──
python -c "import PySide6" >nul 2>nul
if errorlevel 1 (
    echo [2/4] 首次启动：安装 PyTorch CUDA 版（约 2.5GB，请耐心等待）...
    echo   使用 PyTorch 官方 cu128 源（覆盖 RTX 20/30/40/50 系；
    echo   注意：国内 PyPI 镜像的 torch 可能匹配到 CPU 版，故必须走官方 CUDA 源）
    pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu128 >nul 2>nul
    if errorlevel 1 (
        echo   cu128 失败（GTX 10/900 系老卡不支持），尝试 cu126...
        pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu126 || (
            echo [错误] PyTorch 安装失败，请检查网络后重新双击本脚本（会自动续装）。
            pause & exit /b 1
        )
    )
    echo [3/4] 安装应用依赖...
    pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple 2>nul || ^
    pip install -r requirements.txt -i https://mirrors.cloud.tencent.com/pypi/simple || (
        echo [错误] 依赖安装失败，请检查网络后重新双击本脚本（会自动续装）。
        pause & exit /b 1
    )
) else (
    echo [2/4] PyTorch 已安装，跳过。
    echo [3/4] 应用依赖已安装，跳过。
)

REM ── [4] 启动程序 ──
echo [4/4] 正在启动 MiniMax H3 Studio ...
echo.
python -m h3studio.main
if errorlevel 1 (
    echo.
    echo [错误] 程序启动失败，请查看上方报错信息。
    echo        常见原因：显卡驱动未安装 / 依赖损坏（可删除 .venv 文件夹后重试）。
    pause
    exit /b 1
)
endlocal
