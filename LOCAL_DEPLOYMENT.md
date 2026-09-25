# 本地部署指南 — Kevrai Omni v3.0.0

本文面向需要**自己动手部署/排障**的用户与开发者：从源码运行、只跑 Python sidecar、
离线/内网部署、GPU 加速选型，以及常见问题排查。

面向普通用户的「下载安装包 + 三步上手」见 [INSTALL.md](INSTALL.md)；
开发者侧的完整架构与测试见 [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md)、
[docs/API.md](docs/API.md)、[docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md)。

> 本文所有命令均与仓库实际一致（核对自 `package.json`、`electron/main.js`、
> `python/app/settings.py`、`python/app/main.py`、`scripts/setup/bootstrap.sh`）。
> 文中标注「待核实」的条目表示未在仓库内找到直接证据，需自行验证。

---

## 1. 系统要求

| 项目 | 要求 | 说明 |
|---|---|---|
| 操作系统 | Windows 10+ (x64)、Linux (x86_64)、macOS 12+（Intel 与 Apple Silicon） | 预编译包形态见 INSTALL.md |
| Node.js | **≥ 20.x**（推荐 20 LTS） | 仓库未声明 `engines`、无 `.nvmrc`；`scripts/setup/bootstrap.sh` 强制检测 ≥20。Electron 33 内置 Node 20 |
| Python | **≥ 3.10**（推荐 3.11 或 3.12） | `python/pyproject.toml` 要求 `>=3.10`；CI 实测 3.11；Windows 应用内一键引导下载的是 3.12.7 embeddable |
| 内存 | 最低 8 GB；流畅跑 7B 级量化模型建议 16 GB+；更大模型 32 GB+ | 经验值，非仓库硬性要求 |
| 磁盘 | 安装包本身很小；模型/引擎按需下载，预留 **20 GB 起步**，多模型/视频模型建议 100 GB+ | 应用内 `max_model_size_gb` 默认上限 200 GB |
| GPU | 可选；无 GPU 时自动回退 CPU | 详见第 3 节 |

> Windows 下源码构建脚本为 PowerShell/Git Bash 环境；`bootstrap.sh` 在 Git Bash/WSL 中运行，
> 原生 cmd 不保证可用。

---

## 2. 三种安装路径

### 路径 ① Release 预编译包（普通用户）

直接见 [INSTALL.md](INSTALL.md)：Windows `.exe`、Linux AppImage/deb、macOS `.dmg`。
引擎与模型首次使用时在软件内按需下载，本文不再重复。

### 路径 ② 从源码运行（开发者）

```bash
git clone https://github.com/Bullobis/kevrai-omni.git
cd kevrai-omni

# 1) 安装前端/Electron 依赖
npm install

# 2) 安装 Python sidecar 依赖
cd python
pip install -r requirements.txt
cd ..

# 3) 启动
npm start          # 等价于 electron .
# 或开发模式（开启日志与 DevTools）
npm run dev
```

**要点：**

- `npm start` / `npm run dev` 会由 Electron 主进程**自动拉起 Python sidecar**，无需手动开第二个终端。
- sidecar 实际启动命令（核对自 `electron/main.js`）为：
  ```
  python -X utf8 -u -m uvicorn app.main:app --host 127.0.0.1 --port 17890 --log-level info
  ```
  工作目录为仓库的 `python/`。
- sidecar 监听 **`127.0.0.1:17890`**，仅本机可访问。
- Electron 启动 sidecar 时会**自动生成一个随机 Bearer secret**（32 字节 hex）通过环境变量
  `KEVRAI_SIDECAR_SECRET` 传给 sidecar；你不需要也不应该手写这个值。
- Python 解释器解析顺序：环境变量 `KEVRAI_PYTHON` > 应用托管的 `python-runtime` > 系统 `python3`/`python`。
  如需指定解释器：`KEVRAI_PYTHON=/usr/bin/python3 npm start`。

仓库还提供一键环境引导脚本（检测 Node/Python、装依赖、跑自检、查端口占用）：

```bash
bash scripts/setup/bootstrap.sh                 # 检测 + 装依赖 + 自检
bash scripts/setup/bootstrap.sh --dry-run       # 只检测不安装
bash scripts/setup/bootstrap.sh --mirror ustc   # pip 镜像：tencent(默认)/aliyun/ustc/official
```

常用脚本（核对自 `package.json`）：

| 命令 | 作用 |
|---|---|
| `npm start` | 启动应用 |
| `npm run dev` | 开发模式（开启日志 + DevTools） |
| `npm run test:python` | `cd python && python -m pytest -q tests/` |
| `npm run test:js` | 对 `electron/*.js`、`renderer` 做 `node --check` 语法冒烟 |
| `npm run smoke` / `npm run test:smoke` | `bash scripts/smoke.sh` |
| `npm run build:win` / `build:linux` / `build:mac` | electron-builder 出包 |
| `npm run release` | `bash scripts/release.sh` |

### 路径 ③ 仅运行 sidecar / 无头模式（不启动 Electron）

适合把推理编排层跑在服务器或容器里，自己用 HTTP/WebSocket 客户端对接。

```bash
cd python

# 必须自己指定一个 Bearer secret（替换成你自己的强随机串，不要用真实 token）
export KEVRAI_SIDECAR_SECRET='replace-with-your-own-strong-secret'

python -m uvicorn app.main:app --host 127.0.0.1 --port 17890 --log-level info
```

**鉴权约定（核对自 `python/app/main.py`）：**

- 除 `GET /api/health` 外，**所有**请求都必须带请求头：
  `Authorization: Bearer <KEVRAI_SIDECAR_SECRET>`。
- **若未设置 `KEVRAI_SIDECAR_SECRET`，sidecar 会对所有受保护路由直接返回 HTTP 500**
  （fail-closed），而不是开放控制面。这是故意的安全行为。
- 健康检查（免鉴权）：
  ```bash
  curl http://127.0.0.1:17890/api/health
  # → {"ok": true, "version": "3.0.0", "models_dir": "...", "app_root": "..."}
  ```
- 带鉴权访问其它路由示例：
  ```bash
  curl -H "Authorization: Bearer $KEVRAI_SIDECAR_SECRET" \
       http://127.0.0.1:17890/api/categories
  ```
- WebSocket 升级请求同样要求在握手时带 `Authorization: Bearer ...`。
- 改端口：`--port` 即可；Electron 拉起时端口固定为 17890，环境变量 `KEVRAI_PORT` 与之对应。

> 注意：sidecar 只是「编排层」，真正的推理引擎（llama.cpp/MNN/…）仍需按第 4、5 节
> 单独安装或下载到引擎目录。

---

## 3. GPU / NPU 厂商支持

应用内置硬件检测（`python/app/gpu.py`）通过以下命令识别设备，全部带超时与容错，
某一厂商探测失败不影响其它：

| 厂商 | 检测命令 |
|---|---|
| NVIDIA | `nvidia-smi`（查 `index,name,memory.total,driver_version,compute_cap,uuid`） |
| AMD | `rocm-smi`（`/opt/rocm/bin/rocm-smi`） |
| Apple Silicon | `system_profiler`（读 Metal 支持信息） |
| 华为昇腾 | `npu-smi`（`/usr/local/Ascend/driver/tools/npu-smi`） |

应用会把结果映射到 `hardware_acceleration` 设置（`auto/nvidia/amd/apple/ascend/cpu`）。
**下表区分「上游引擎支持」与「本应用随包下载的预编译形态」**，避免误解。

### 3.1 NVIDIA CUDA

- 应用随包下载的 llama.cpp 预编译二进制本身就是 CUDA 构建
  （核对自 `catalog/engines.json`：Windows `llama-bin-win-cuda-x64.zip`、
  Linux `llama-bin-linux-x64-cuda.zip`）。
- 需要系统装有**与 CUDA 运行时兼容的 NVIDIA 专有驱动**（开源 Nouveau 驱动不支持 CUDA 计算）。
- llama.cpp 通过 CMake `-DGGML_CUDA=ON` 开启 CUDA 后端；官方 Python 绑定声明支持
  CUDA 11.8 / 12.1–12.5 / 13.0 / 13.2，计算能力 6.0+（CUDA 12）。
  来源：llama-cpp-python 文档 https://llama-cpp-python.readthedocs.io/en/stable/
- MNN 也提供 CUDA 后端（CMake `MNN_CUDA=ON`，默认关闭），支持 Windows/Linux。
  来源：MNN 官方文档 https://mnn-docs.readthedocs.io/en/latest/intro/about.html

> 应用下载的 CUDA 二进制是否捆绑了 CUDA 运行时库、需要哪个最低驱动版本，
> 以 llama.cpp 对应 Release 说明为准（待核实）。

### 3.2 AMD ROCm

- **上游 llama.cpp 支持 ROCm（HIP 后端），但仅 Linux**；AMD 官方文档标注
  「Applies to Linux」，要求 Ubuntu 22.04/24.04 + ROCm 7.0.0（亦兼容 6.4.x）。
  来源：https://rocm.docs.amd.com/projects/llama-cpp/en/latest/install/llama-cpp-install.html
- **注意：本应用 catalog 为 Linux x64 下载的是 CUDA 构建，并未提供 ROCm 预编译包。**
  因此在 AMD 显卡上用随包引擎时，默认会走 **CPU 回退**；要拿到 ROCm 加速，
  需自行用 `-DGGML_HIP=ON` 编译 ROCm 版 llama.cpp 并放到引擎目录（待核实具体对接方式）。
- MNN 在 AMD Radeon 上可走 OpenCL 后端（跨平台），而非 ROCm。
  来源：https://deepwiki.com/alibaba/MNN/3.2.2-metal-backend-and-ios-integration

### 3.3 Apple Silicon (macOS arm64)

- 应用下载的 macOS arm64 llama.cpp 包为 `llama-bin-macos-arm64.zip`，走 **Metal** 加速。
- MNN 提供 Metal 后端（`MNN_METAL=ON`，macOS/iOS）。
- 另有 **MLX** 引擎（`ml-explore/mlx`，pip 安装），专为 Apple Silicon 统一内存设计。
  来源：MNN 后端矩阵 https://mnn-docs.readthedocs.io/en/latest/intro/about.html
- 无 GPU 加速时自动回退 CPU。

### 3.4 华为昇腾 NPU

- 应用能通过 `npu-smi` 识别昇腾设备，但 catalog 中**没有随包的 NPU 推理引擎**；
  NPU 加速路径主要走 pip 安装的 `transformers`/PyTorch 系引擎。
- 官方要求：先装配套的 **NPU 驱动固件 + CANN 软件（Toolkit、Kernels、NNAL）**，
  并在执行业务前 `source /usr/local/Ascend/ascend-toolkit/...` 设置脚本注入环境变量，
  否则 NPU 业务无法运行；再按版本匹配安装 **torch_npu（Ascend Extension for PyTorch）**。
  来源：昇腾社区《安装前必读》https://www.hiascend.com/document/detail/zh/Pytorch/600/configandinstg/instg/insg_0003.html
  与《安装 torch_npu 插件》https://www.hiascend.com/document/detail/zh/canncommercial/700/envdeployment/instg/instg_0048.html
- torch_npu 版本必须与 PyTorch / CANN 版本严格匹配（PyPI `torch-npu` 页面有版本矩阵）。
  来源：https://pypi.org/project/torch-npu/

---

## 4. 模型与引擎目录布局

用户数据根目录（核对自 `python/app/settings.py:default_data_root()`）：

| 系统 | 数据根 |
|---|---|
| Linux | `$XDG_DATA_HOME/KevraiOmni`（未设则 `~/.local/share/KevraiOmni`） |
| macOS | `~/Library/Application Support/KevraiOmni` |
| Windows | `%APPDATA%/KevraiOmni` |

缓存根（catalog 解析结果等非关键缓存）：Linux `$XDG_CACHE_HOME/KevraiOmni`、
macOS `~/Library/Caches/KevraiOmni`、Windows `%LOCALAPPDATA%/KevraiOmni`。

数据根下的典型结构：

```
<KevraiOmni>/
├── settings.json            # 用户设置（模型/引擎目录、镜像、HF token 等）
├── source_health.json      # 下载源健康状态（自动维护）
├── models/                 # 下载/导入的模型（GGUF 等），可在设置中改到别的盘
│   └── ...
├── engines/                # 随选下载的推理引擎（llama.cpp/MNN 二进制等）
├── downloads/              # 下载临时/中转站
└── agent/
    └── memory.sqlite3      # Kevrai Agent 的记忆库（SQLite）
```

- 上述子目录均可在「设置」里通过 `model_dir` / `engine_dir` / `download_dir` 改到其它路径
  （展开 `~`）；未设置时用数据根下的默认子目录。
- `catalog/`（仓库内，只读）随发行包提供模型/引擎目录；应用启动时加载它。
- 本地已有模型文件也可以直接拖入窗口导入，不必走模型市场。

---

## 5. 离线 / 内网部署

1. **模型镜像**：`settings.extra_model_mirrors` 默认已包含 `https://hf-mirror.com`，
   且默认开启 `auto_pick_best_source`（自动测速选最快源）。内网环境可在设置里
   把它换成你的内网 HuggingFace 镜像地址。
2. **pip 镜像**：`settings.pip_mirrors` 默认含阿里云、清华 TUNA、华为云；
   `bootstrap.sh --mirror` 也可指定。内网请换成私有 PyPI 源。
3. **离线放置模型**：在能联网的机器上把模型文件（如 `.gguf`）下载好，
   直接拷到目标机的 `<数据根>/models/`（或你设置的 `model_dir`），应用会识别；
   也可用「拖入窗口导入」。
4. **离线放置引擎**：引擎二进制放到 `<数据根>/engines/` 对应目录。
   具体子目录命名以应用「AI 引擎」页下载后生成的结构为准（待核实逐引擎布局）。
5. **HF gated 模型**（如 LTX-2.5）：需先在 HuggingFace 仓库页接受许可，
   再在设置里填入 HF token（仅存本机）；内网若无法访问 huggingface.co，
   需确保镜像代理可达 gated 仓库。
6. Python 依赖可先在联网机上 `pip download -r python/requirements.txt -d wheels/`，
   拷到内网机 `pip install --no-index --find-links=wheels/ -r python/requirements.txt`。

---

## 6. 常见问题排查

| 现象 | 排查 |
|---|---|
| sidecar 没起来 / 界面一直转圈 | 看 Electron 日志里 `[sidecar-stderr]`；常见原因：① Python 依赖没装全（`pip install -r python/requirements.txt`）；② 系统找不到 `python3`，用 `KEVRAI_PYTHON=/path/to/python3 npm start` 指定；③ 端口 17890 被占用（见下） |
| `curl /api/health` 通，但其它路由返回 500 | 手动跑 sidecar 时**没设 `KEVRAI_SIDECAR_SECRET`**，或请求头没带 `Authorization: Bearer <secret>`。这是 fail-closed 设计 |
| 其它路由返回 401 `unauthorized` | Bearer token 与 `KEVRAI_SIDECAR_SECRET` 不一致；注意首尾空格、`Bearer ` 前缀 |
| 端口占用 / sidecar 启动冲突 | 默认端口 **17890**。`lsof -iTCP:17890 -sTCP:LISTEN`（或 `ss -ltnp | grep 17890`）查占用方；手动无头跑时换 `--port`，Electron 场景下关掉占用进程 |
| 模型下载失败 / 超时 | 设置里确认镜像可达；试切 `auto_pick_best_source` 或锁定源；内网见第 5 节；gated 模型检查 HF token 与许可是否已接受 |
| GPU 没被用上 | 「设置 → 硬件加速」确认不是 `cpu`；NVIDIA 确认 `nvidia-smi` 可用且驱动为专有驱动；AMD 注意随包是 CUDA 构建（见 3.2）；昇腾确认已 `source` CANN 环境 |
| 权限报错（Linux/macOS） | `engines/` 里的二进制需要可执行权限：`chmod +x`；数据根目录需当前用户可写 |
| Windows 下 `npm run dev` 脚本失败 | 用 Git Bash / PowerShell；`.sh` 脚本不支持原生 cmd |

更多排障见 [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md)。

---

## 7. 升级与卸载

- **覆盖升级**：直接装新版安装包或 `git pull && npm install && pip install -r python/requirements.txt` 后重启即可；
  已下载的引擎与模型保留在数据根，不会重复下载。
- **数据保留**：卸载默认**保留** `<数据根>/KevraiOmni` 下已下载的引擎、模型与
  `agent/memory.sqlite3`（重装后自动识别）。
- **彻底清理**：手动删除数据根目录即可（路径见第 4 节）；
  如改过 `model_dir`/`engine_dir` 到自定义路径，需一并删除那些目录。
- 引擎可在「AI 引擎」页用「检查引擎更新」单独升级，不必整体升级应用。

---

## 附：信息来源（GPU/引擎支持）

- llama-cpp-python 后端/CUDA 版本要求：https://llama-cpp-python.readthedocs.io/en/stable/
- AMD 官方《llama.cpp on ROCm》（Linux / ROCm 7.0.0 / Ubuntu 22.04·24.04）：https://rocm.docs.amd.com/projects/llama-cpp/en/latest/install/llama-cpp-install.html
- MNN 后端矩阵（OpenCL/Vulkan/Metal/CUDA）：https://mnn-docs.readthedocs.io/en/latest/intro/about.html
- MNN CMake 后端开关：https://deepwiki.com/alibaba/MNN/4.1-cmake-build-configuration
- 昇腾社区《安装前必读》（驱动固件 + CANN + 环境变量）：https://www.hiascend.com/document/detail/zh/Pytorch/600/configandinstg/instg/insg_0003.html
- 昇腾社区《安装 torch_npu 插件》：https://www.hiascend.com/document/detail/zh/canncommercial/700/envdeployment/instg/instg_0048.html
- PyPI `torch-npu` 版本矩阵：https://pypi.org/project/torch-npu/
