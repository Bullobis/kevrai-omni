@echo off
chcp 65001 >nul
setlocal EnableDelayedExpansion
title Kevrai Omni — 一键打包成安装包

REM ============================================================
REM  Kevrai Omni 一键打包（Windows）
REM  双击即可，不需要懂命令行。
REM
REM  产出（在 build\output 目录）：
REM    Kevrai-Omni-<版本>-x64.exe   ← 安装版，双击安装到电脑
REM    Kevrai-Omni-<版本>-x64.zip   ← 绿色版，解压就能用，不用安装
REM    latest.yml                    ← 自动更新用的元数据
REM
REM  安装包含 PyTorch 等运行库（约 1.5~2GB），这是本地 AI 软件的共同现状。
REM  模型权重不打进安装包，由用户在软件内按需下载。
REM ============================================================

echo.
echo  ╔══════════════════════════════════════════════════════╗
echo  ║        Kevrai Omni  一键打包成安装包                 ║
echo  ╚══════════════════════════════════════════════════════╝
echo.

REM ---------- 0. 切到脚本所在目录（项目根目录） ----------
cd /d "%~dp0"
echo [0/6] 工作目录：%CD%
echo.

REM ---------- 1. 检查 Node.js ----------
echo [1/6] 检查环境...
where node >nul 2>&1
if errorlevel 1 (
    echo.
    echo   ✗ 没有找到 Node.js
    echo.
    echo   请先安装 Node.js （选 LTS 版本，一路下一步即可）：
    echo     https://nodejs.org/zh-cn/download
    echo   或用国内镜像：https://npmmirror.com/mirrors/node/
    echo.
    echo   装完后重新双击本脚本。
    echo.
    pause
    exit /b 1
)
for /f "tokens=*" %%v in ('node -v') do set NODEV=%%v
echo   ✓ Node.js %NODEV%

where python >nul 2>&1
if errorlevel 1 (
    where python3 >nul 2>&1
    if errorlevel 1 (
        echo   ! 没找到 Python —— 打包本身不需要，但建议装上以便软件内跑模型
    ) else (
        echo   ✓ Python（python3）
    )
) else (
    echo   ✓ Python
)
echo.

REM ---------- 2. 配置国内镜像（避免下载卡住） ----------
echo [2/6] 配置国内下载镜像（避免卡在国外网络）...
set npm_config_registry=https://registry.npmmirror.com
set ELECTRON_MIRROR=https://npmmirror.com/mirrors/electron/
set ELECTRON_BUILDER_BINARIES_MIRROR=https://npmmirror.com/mirrors/electron-builder-binaries/
echo   ✓ 已使用 npmmirror 镜像
echo.

REM ---------- 3. 安装依赖 ----------
echo [3/6] 安装依赖（首次约需 3~10 分钟，取决于网速）...
if exist node_modules (
    echo   发现已有 node_modules，执行增量安装...
) else (
    echo   首次安装，正在下载 Electron（约 100MB），请耐心等待...
)
call npm install --no-audit --no-fund
if errorlevel 1 (
    echo.
    echo   ✗ 依赖安装失败。常见原因与处理：
    echo     1. 网络不通 —— 换个网络，或确认能访问 npmmirror.com
    echo     2. 杀毒软件拦截 —— 临时关闭后重试
    echo     3. 目录路径含中文/空格 —— 把项目移到纯英文路径下再试
    echo.
    pause
    exit /b 1
)
echo   ✓ 依赖安装完成
echo.

REM ---------- 4. 安装 Python 依赖（可选，失败不阻断） ----------
echo [4/6] 安装 Python 依赖（可选，失败不阻断打包）...
where python >nul 2>&1
if not errorlevel 1 (
    python -m pip install -q -i https://mirrors.tencent.com/pypi/simple/ fastapi uvicorn httpx pydantic 2>nul
    echo   ✓ Python 依赖已处理
) else (
    echo   - 跳过（未找到 Python）
)
echo.

REM ---------- 5. 构建安装包 ----------
echo [5/6] 正在打包（这一步最耗时，约 2~8 分钟）...
echo      会同时产出 安装版 exe 和 绿色版 zip
echo.
call npx electron-builder --win --x64
if errorlevel 1 (
    echo.
    echo   ✗ 打包失败。
    echo.
    echo   最常见的原因是项目路径含中文或空格。
    echo   请把整个文件夹移动到类似  C:\KevraiOmni  这样的纯英文路径下，
    echo   然后重新双击本脚本。
    echo.
    pause
    exit /b 1
)
echo   ✓ 打包完成
echo.

REM ---------- 6. 展示结果 ----------
echo [6/6] 完成！产物在下面的目录里：
echo.
echo   %CD%\build\output
echo.
echo  ─────────────────────────────────────────────
if exist build\output (
    for %%f in (build\output\*.exe build\output\*.zip build\output\latest.yml) do (
        echo   %%~nxf
    )
) else (
    echo   （目录为空 —— 请检查上面的错误信息）
)
echo  ─────────────────────────────────────────────
echo.
echo   安装版 .exe  = 双击安装到电脑，适合长期使用
echo   绿色版 .zip  = 解压即用，不用安装，适合放 U 盘
echo.

set /p OPEN=是否现在打开产物目录？(Y/N，直接回车为 Y)
if /i "%OPEN%"=="" set OPEN=Y
if /i "%OPEN%"=="Y" start "" "%CD%\build\output"

echo.
echo  按任意键退出...
pause >nul
endlocal
