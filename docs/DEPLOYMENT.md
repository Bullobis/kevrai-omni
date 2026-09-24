# DEPLOYMENT.md — 终端用户本地部署指南

> 适用版本：Kevrai Omni v2.9.0。本文面向**普通终端用户**（在自己的工作站上安装、跑起来、下模型）。
> 开发者从源码搭建环境请见 [DEVELOPMENT.md](./DEVELOPMENT.md)；架构原理见 [ARCHITECTURE.md](./ARCHITECTURE.md)。

## 目录

- [1. 系统要求](#1-系统要求)
- [2. 硬件要求](#2-硬件要求)
- [3. 一键引导（Release 安装包）](#3-一键引导release-安装包)
- [4. 从源码运行（开发者预览）](#4-从源码运行开发者预览)
- [5. GPU / 驱动配置](#5-gpu--驱动配置)
- [6. 离线环境部署](#6-离线环境部署)
- [7. 可选：Docker 思路](#7-可选docker-思路)
- [8. 常见安装问题](#8-常见安装问题)

---

## 1. 系统要求

| 操作系统 | 版本 | 架构 | 备注 |
|---|---|---|---|
| Windows | 10 / 11 | x64（AMD64） | 推荐。安装包为 NSIS `.exe` + 免安装 `.zip`。 |
| Linux | Ubuntu 22.04+ / Debian 12+ / Fedora 38+ | x64 | 提供 `.AppImage` 与 `.deb`。 |
| macOS | 12 (Monterey) 及以上 | Intel (x64) / Apple Silicon (arm64) | 提供 `.dmg`，x64 与 arm64 两个产物。内部构建未签名，首次打开需在「系统设置 → 隐私与安全性」放行。 |

> **macOS 说明**：electron-builder 25 支持产出 mac DMG（见根目录 `electron-builder.yml` 的 `mac.target`），但当前 CI 并未在 mac runner 上签名/公证。若你在 Apple Silicon 上遇到「无法打开，因为无法验证开发者」，按系统提示在「隐私与安全性」中点击「仍要打开」即可。此行为随版本演进，**待核实**最新 Release 资产清单。

最低软件依赖：

- 安装包形态（推荐）：**无需预装 Python / Node**。App 首次启动时会按需引导下载一个便携 Python 运行时（Windows 上自动安装到用户数据目录，约 11 MB）。
- 从源码运行：见 [第 4 节](#4-从源码运行开发者预览)。

---

## 2. 硬件要求

### 2.1 内存与 CPU

| 档位 | CPU | 内存 (RAM) | 适用场景 |
|---|---|---|---|
| 最低 | 现代 x64 多核（2018 年后） | **8 GB** | 纯 CPU 跑 1.5B–3B 量化 LLM、TTS、图像生成小模型。会明显慢。 |
| 推荐 | 现代 x64 多核 | **16 GB 及以上** | 日常 LLM（7B 量化）、图像/音频生成。 |
| 流畅 | 8 核以上 | 32 GB+ | 视频生成（LTX-2.5）、多模型并发、3D 生成。 |

### 2.2 GPU / NPU

Kevrai Omni 启动时会通过 `nvidia-smi` / `rocm-smi` / `npu-smi` / `system_profiler` 自动探测显卡（见 `python/app/gpu.py`）。支持的厂商：

| 厂商 | 探测命令 | 说明 |
|---|---|---|
| **NVIDIA** | `nvidia-smi` | 最主流。显存需求随模型而定（见下表）。 |
| **AMD** | `rocm-smi --json` | 需安装 ROCm 驱动栈；消费卡与计算卡支持度因引擎而异，**待核实**你目标引擎的 ROCm 兼容性矩阵。 |
| **华为昇腾 Ascend** | `npu-smi info` | 需安装 CANN 驱动；部分引擎还需 `torch_npu`（由对应引擎在安装时拉取，版本以引擎页说明为准）。 |
| **Apple Silicon** | `system_profiler SPDisplaysDataType` | macOS 上统一内存，MLX 引擎走 Metal。 |
| **纯 CPU 回退** | — | 无任何 GPU 时自动回退到 CPU 推理，仅建议用于小模型/测试。 |

**NVIDIA 显存参考**（以 LTX-2.5 视频生成引擎为例，取自 `python/app/ltx_runtime.py` 的预设表；其他模型在模型市场详情页会标注推荐显存）：

| 显存档位 | 预设 | 帧数 | 步数 |
|---|---|---|---|
| 24 GB | `quality` | 高 | 40 |
| 16 GB | `balanced-high` | 中高 | 30 |
| 12 GB | `balanced` | 中 | 25 |
| 8 GB | `speed` | 低 | 20 |
| 4 GB | `fast` | 很低 | 12 |

> LLM（llama.cpp / MNN）走 GGUF 量化，7B Q4 大约需要 5–6 GB 显存/内存。

### 2.3 磁盘空间

- 安装包本体：约 100–200 MB。
- 引擎（llama.cpp、MNN、LTX/diffusers 等）：每个 100 MB – 数 GB 不等，按需下载。
- 模型权重：**这是大头**。单个 GGUF 7B Q4 约 4–5 GB；视频/3D/多模态模型可达 10–50 GB 甚至更多。
- **预留 50–100 GB 以上空闲磁盘**比较稳妥；模型市场设置里有 `max_model_size_gb` 上限（默认 200 GB，可调）。

模型与引擎默认存放在用户数据目录（见下），可在「设置」里改到大盘：

| 平台 | 默认数据根目录 |
|---|---|
| Windows | `%APPDATA%\KevraiOmni\` |
| macOS | `~/Library/Application Support/KevraiOmni/` |
| Linux | `~/.local/share/KevraiOmni/`（或 `$XDG_DATA_HOME/KevraiOmni/`） |

子目录：`models/`、`engines/`、`downloads/`、`logs/`、`python-runtime/`（Windows 便携 Python）、`settings.json`。

---

## 3. 一键引导（Release 安装包）

### 3.1 Windows

1. 打开 <https://github.com/Bullobis/kevrai-omni/releases>，下载最新版的 `Kevrai-Omni-<版本>-x64.exe`（NSIS 安装包；同目录还有一个 `-x64.zip` 免安装包）。
2. 双击 `.exe`：向导跟随系统语言（中文系统默认简体中文），可自定义安装目录，会创建桌面快捷方式与开始菜单项。
3. 首次启动：
   - 若机器上没有可用 Python，会进入「环境准备」页，点 **「一键安装 Python 环境」**——约 11 MB，从 npmmirror / 华为云镜像拉取便携版，装到用户数据目录，**不污染系统 Python**。
   - 之后显示「三步上手」引导：**装引擎 → 下模型 → 开始生成**。
4. 在「AI 引擎」页选引擎（如 llama.cpp、MNN）点安装；在「模型市场」搜索并下载模型。

### 3.2 Linux

| 形态 | 文件 | 使用方式 |
|---|---|---|
| AppImage | `Kevrai-Omni-<版本>-x86_64.AppImage` | `chmod +x Kevrai-Omni-*.AppImage && ./Kevrai-Omni-*.AppImage` |
| deb | `kevrai-omni_<版本>_amd64.deb` | `sudo apt install ./kevrai-omni_<版本>_amd64.deb` |

deb 会写入桌面项与开始菜单项；AppImage 无需安装。AppImage 在某些发行版上需要 FUSE（`sudo apt install libfuse2`）。

### 3.3 macOS

1. 下载 `Kevrai-Omni-<版本>-<arch>.dmg`（`x64` 对应 Intel，`arm64` 对应 Apple Silicon）。
2. 拖入「应用程序」。
3. 首次打开若被 Gatekeeper 拦截：**系统设置 → 隐私与安全性 → 仍要打开**。

### 3.4 升级

应用内「检查更新」走 electron-updater，从 GitHub Releases 拉取 `latest.yml`。默认不自动下载，用户确认后再下、下完提示「重启并安装」。

---

## 4. 从源码运行（开发者预览）

> 完整开发者环境（含测试、lint、调试）见 [DEVELOPMENT.md](./DEVELOPMENT.md)。这里只列最小步骤。

前置：

- **Node.js 20.x 或更高**（Electron 33 内置 Node 20.x；CI 在 Node 22 上跑 `node --check`）。
- **Python 3.10+**（pyproject 声明 `>=3.10`；CI 实测 3.11；App 内引导默认下载 3.12.7）。
- git。

```bash
git clone https://github.com/Bullobis/kevrai-omni.git
cd kevrai-omni

# 前端依赖
npm install

# Python sidecar 依赖（国内镜像建议加 -i）
python3 -m pip install -i https://mirrors.tencent.com/pypi/simple/ -r python/requirements.txt

# 启动
npm start
```

开发模式（开 DevTools、Electron 日志打开）：

```bash
npm run dev
```

> 也可以直接用 `scripts/setup/bootstrap.sh --dry-run` 做环境自检（见仓库根 `scripts/setup/`）。

---

## 5. GPU / 驱动配置

### 5.1 通用

App 本身不捆绑任何 CUDA / ROCm / CANN 运行时——引擎（llama.cpp、MNN、diffusers/LTX 等）在「AI 引擎」页按需下载，引擎包自带或在安装时拉取对应计算库。你需要做的是**装好系统级驱动**：

- NVIDIA：装好最新 **NVIDIA 显卡驱动**（`nvidia-smi` 能出结果即可）。
- AMD：装好 **ROCm** 用户态栈（`rocm-smi` 能列出卡）。
- 昇腾：装好 **CANN** 驱动与固件（`npu-smi info` 能出结果）。

启动后在「硬件」页能看到探测到的 GPU 列表即代表识别成功。

### 5.2 NVIDIA / CUDA

- 不需要手动全局装 CUDA Toolkit——引擎自带的 wheel 里一般已打包 CUDA 运行库。
- PyTorch 由 LTX/diffusers 类引擎**惰性导入**（见 `python/app/ltx_runtime.py`，`import torch` 仅在真正跑视频生成时才发生），其 CUDA 版本由对应引擎决定。
- 若 `torch.cuda.is_available()` 返回 False：
  1. 确认 `nvidia-smi` 在终端可用、驱动版本够新；
  2. 确认引擎安装完整（重新点「检查引擎更新」）；
  3. 见 [TROUBLESHOOTING.md](./TROUBLESHOOTING.md#生成类)。
- **具体 CUDA 版本号与 PyTorch 的对应关系以你安装的引擎页说明为准**——本仓库不统一锁定 PyTorch 版本（`requirements.txt` 不含 torch）。

### 5.3 AMD ROCm

- 探测命令：`rocm-smi --json`。
- ROCm 版本与内核/显卡的兼容矩阵请参考 ROCm 官方文档；Kevrai Omni 不强制特定 ROCm 版本。
- 消费级 AMD 卡（RDNA 系列）在部分引擎上需要额外的环境变量才能被识别（如 `HSA_OVERRIDE_GFX_VERSION`），**待核实**你所用引擎的 README。

### 5.4 华为昇腾 CANN

- 探测命令：`npu-smi info`（默认路径 `/usr/local/Ascend/driver/tools/npu-smi`）。
- 部分引擎（基于 torch 的）需要 `torch_npu`，通常在引擎安装时自动拉取；若手动部署，参考昇腾官方「CANN + torch_npu」配对表。
- Kevrai Omni 把昇腾识别为 `vendor="ascend"`（见 `python/app/gpu.py`），但具体哪些引擎已适配 NPU 请以「AI 引擎」页标注为准。

### 5.5 无 GPU 回退

没有任何 GPU/NPU 时，App 自动回退到 CPU 推理（`vendor="cpu"`）。可用，但：

- 只能跑小参数量、高量化档的模型；
- 视频生成、3D 生成基本不现实；
- LTX 引擎会自动选用 `float32`（CPU 路径）并可开启 `enable_model_cpu_offload`。

---

## 6. 离线环境部署

目标机器不能联网时，在一台**有网机器**上准备好安装包 + Python 运行时 + 引擎 + 模型，再整体搬过去。

### 6.1 安装包与 Python

1. 在有网机器上下载 Release 安装包（`.exe` / `.AppImage` / `.deb` / `.dmg`）。
2. 首次在有网机器上启动一次 App，让它自动下载便携 Python 运行时到用户数据目录（Windows：`%APPDATA%\KevraiOmni\python-runtime\`）。
3. 把整个用户数据目录（或至少 `python-runtime/`）随安装包一起拷到离线机器相同位置。

### 6.2 引擎与模型

两种方式：

- **方式 A（推荐）**：在有网机器上把要用的引擎和模型全部下载好，然后把整个 `models/` 与 `engines/` 目录打包拷到离线机器的对应路径。
- **方式 B**：在模型市场详情页手动下载 GGUF/权重文件，通过 App 的「导入」功能（拖拽文件到窗口）或放到 `models/` 目录后让 App 扫描识别。

### 6.3 环境变量

App 的下载器与 hub 客户端尊重标准 Hugging Face 环境变量（透传到 sidecar 进程，见 `electron/main.js` 的 `sidecarEnv()`）：

| 变量 | 作用 |
|---|---|
| `HF_ENDPOINT` | 覆盖 Hugging Face 端点。国内可用 `https://hf-mirror.com`。 |
| `HF_HUB_OFFLINE=1` | 强制离线模式，禁止任何对 HF 的网络请求。 |
| `HF_TOKEN` | Hugging Face 访问令牌（gated 模型需要；也可在 App「设置」里填，二者取其一）。 |
| `KEVRAI_PYTHON` | 覆盖 sidecar 使用的 Python 解释器路径（开发/调试用）。 |
| `KEVRAI_PORT` | sidecar HTTP 端口，默认 17890（一般不用改）。 |

> App 内置的镜像选择器（设置 → 多源镜像）默认已经把 `https://hf-mirror.com` 加进 `extra_model_mirrors`，并对每个源做测速。离线场景下请在设置里**关闭自动测速**、锁定你要用的本地/内网源，避免 App 反复探测外网超时。

### 6.4 离线注意事项

- 首次启动仍需写用户数据目录（设置、日志），该目录在离线机器上必须可写。
- electron-updater 在离线机器上会检查更新失败——这是预期行为，不会阻断使用；可在设置里关闭自动更新提示。

---

## 7. 可选：Docker 思路

> **仓库当前不提供官方 Dockerfile**。以下是可选方案，供有容器化需求的用户参考；未经官方 CI 验证。

Kevrai Omni 是一个 Electron 桌面应用，**完整跑 GUI 需要 X/Wayland**，Docker 化通常只适合跑 **Python sidecar**（FastAPI 在 `127.0.0.1:17890`）做无头测试/API 服务。

基础镜像建议：

```dockerfile
# 仅 sidecar，不含 Electron UI
FROM python:3.11-slim

WORKDIR /app
COPY python/requirements.txt /app/python/requirements.txt
RUN pip install --no-cache-dir -i https://mirrors.tencent.com/pypi/simple/ \
    -r /app/python/requirements.txt

COPY python /app/python
COPY catalog /app/catalog

# sidecar 监听 17890；容器内绑定 0.0.0.0 才能被宿主访问（桌面版默认绑 127.0.0.1）
EXPOSE 17890
CMD ["python", "-m", "app.main"]
```

注意：

- 桌面版主进程把 sidecar 绑在 `127.0.0.1:17890`；容器化时需自行改 host 或做端口转发。
- GPU 透传需要 `--gpus all`（NVIDIA Container Toolkit）或 ROCm/ASCEND 对应设备插件。
- 模型体积大，建议把 `models/` 挂成卷：`-v /data/kevrai/models:/root/.local/share/KevraiOmni/models`。

---

## 8. 常见安装问题

### 8.1 权限问题

- **Windows**：如果安装目录选在 `C:\Program Files\`，而模型又想放在同目录，可能因写权限失败。建议模型目录放到 `%APPDATA%\KevraiOmni\models\` 或自定义到非系统盘。
- **Linux**：AppImage 不要放到 `root` 拥有的目录下运行；deb 安装需要 `sudo`，但运行不需要。
- **macOS**：未签名 App 首次启动被拦，见 [3.3](#33-macos)。

### 8.2 路径含中文 / 空格

- 安装目录本身可以含空格（`artifactName` 特意用无空格品牌名 `Kevrai-Omni-...` 以避免 electron-updater 的 URL 问题）。
- **模型目录**请尽量避免中文/空格/特殊字符 `<>:"/\|?*`——部分引擎（尤其是 llama.cpp 的某些量化工具链）对非 ASCII 路径支持不佳。App 的路径校验在 `python/app/hub/paths.py` 会拒绝非法字符。
- 把模型放到一个纯英文无空格路径（如 `D:\AI\models\`）最稳。

### 8.3 杀毒软件误报

- 便携 Python 运行时与引擎下载器会被部分国产杀毒软件误判为「可疑脚本」。
- 解决：把 `%APPDATA%\KevraiOmni\` 加入杀毒白名单；下载的引擎二进制（`.exe` / `.so`）同理。
- Windows SmartScreen 对未签名 `.exe` 会弹蓝色提示，点「更多信息 → 仍要运行」。

### 8.4 Windows 上找不到 Python

- 安装包形态：不需要系统 Python，App 会自动引导下载便携版到 `python-runtime/`。
- 从源码运行：确保 `python` / `python3` 在 PATH 里；Windows 上建议从 [python.org](https://www.python.org/downloads/) 安装时勾选 **「Add python.exe to PATH」**。
- 若你有多个 Python 版本，用环境变量 `KEVRAI_PYTHON=C:\path\to\python.exe` 显式指定 sidecar 解释器。

### 8.5 下载慢 / 失败

- 设置 → 多源镜像：确认 `hf-mirror.com` 已启用；App 会对每个源测速并自动选最快的。
- 大文件下载支持**断点续传**：下载器写 `.partial` 文件，中断后下次自动从 `Range: bytes=<已传字节>-` 续传（见 `python/app/downloader.py`）。
- gated 模型（如 LTX-2.5）需要先在 HuggingFace 仓库页面接受许可，再在 App 设置里填 HF Token。

---

更多问题见 [FAQ.md](./FAQ.md) 与 [TROUBLESHOOTING.md](./TROUBLESHOOTING.md)。
