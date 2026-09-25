# 本地开发环境搭建（Development Guide）

本文档面向想从源码运行、调试或打包 Kevrai Omni 的开发者。
应用由两部分组成：

- **Electron 主进程 + 渲染层**（Node.js / 原生前端，无构建框架，`renderer/`）
- **Python sidecar**（FastAPI，负责目录、下载、引擎、GPU 检测、Agent、LTX 等）

Electron 启动时会以子进程方式拉起 sidecar（`uvicorn app.main:app`，绑定
`127.0.0.1:17890`），并通过 HTTP / WebSocket 与之通信。

---

## 前置要求

| 工具 | 版本要求 | 说明 |
|---|---|---|
| **Node.js** | **22 LTS 及以上** | 提供 Electron 与 npm 脚本；推荐用 nvm / fnm 管理 |
| **Python** | **3.11+**（发行内嵌版为 3.12.7） | 运行 sidecar；`pyproject.toml` 要求 `>=3.10`，推荐 3.12 |
| **Git** | 任意较新版本 | 克隆仓库；技能市场 import-git 还会调用系统 `git` |
| 系统库 | 见下文各平台 | Linux 下 Electron 需要一组图形/沙箱运行库 |

> 不需要预装 CUDA / PyTorch 才能启动开发环境——sidecar 的核心控制面只依赖
> `requirements.txt` 里的轻量包（fastapi/uvicorn/httpx/pydantic 等）。
> torch / diffusers 等重型推理依赖是**按需**在使用对应引擎时才安装的。

---

## 获取源码

```bash
git clone https://github.com/Bullobis/kevrai-omni.git
cd kevrai-omni
git checkout kevrai-forge/v3.0.0     # 或你要开发的分支
```

---

## Windows 开发环境

1. **安装 Node.js 22 LTS**：从 <https://nodejs.org/> 下载 LTS 安装包，勾选
   "Add to PATH"。验证：
   ```powershell
   node -v    # 应输出 v22.x
   npm -v
   ```

2. **安装 Python 3.12**：从 <https://www.python.org/downloads/> 安装，
   安装时**务必勾选 "Add python.exe to PATH"**。验证：
   ```powershell
   python --version    # 应输出 Python 3.12.x
   ```

3. **创建并激活 venv**（推荐，避免污染系统 Python）：
   ```powershell
   python -m venv .venv
   .\.venv\Scripts\activate
   ```

4. **安装 JS 依赖**（仓库根目录）：
   ```powershell
   npm install
   ```

5. **安装 Python sidecar 依赖**（在已激活 venv 的终端里）：
   ```powershell
   cd python
   pip install -r requirements.txt
   cd ..
   ```

6. **启动开发模式**：
   ```powershell
   npm run dev
   ```
   Electron 会自动打开 DevTools 并尝试拉起 sidecar。若系统有多个 Python，
   可用环境变量指定用哪个解释器启动 sidecar：
   ```powershell
   set KEVRAI_PYTHON=C:\path\to\.venv\Scripts\python.exe
   npm run dev
   ```

### Windows 常见问题

- **`'python' 不是内部或外部命令`**：安装 Python 时没勾 "Add to PATH"，
  重新运行安装包选 Modify 勾上，或改用 `py -3.12`。
- **venv 激活脚本报 "无法加载文件…因为禁止运行脚本"**：PowerShell 默认禁止
  执行脚本，执行一次 `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`，
  或改用 CMD（`.\.venv\Scripts\activate.bat`）。
- **pip 下载慢**：临时加清华/腾讯镜像
  `-i https://mirrors.tencent.com/pypi/simple/`。

---

## Linux 开发环境（Ubuntu / Debian）

1. **系统依赖**（Electron 运行所需的图形/沙箱库）：
   ```bash
   sudo apt update
   sudo apt install -y \
     libgtk-3-0 libnss3 libgbm1 libasound2 \
     libxss1 libxshmfence1 libdrm2 libxtst6 \
     libatspi2.0-0 libcups2 libglib2.0-0
   ```
   > 这些是 Electron 在无头/桌面环境下正常开窗与 GPU 初始化所需的运行库；
   > 在 SSH 无显示环境下调试可配合 `xvfb-run`。

2. **Node.js 22**（推荐用 nvm）：
   ```bash
   curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.39.7/install.sh | bash
   nvm install 22
   nvm use 22
   ```

3. **Python 3.11+**：
   ```bash
   sudo apt install -y python3 python3-venv python3-pip
   ```

4. **创建 venv 并安装依赖**：
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   npm install
   cd python && pip install -r requirements.txt && cd ..
   ```

5. **启动**：
   ```bash
   npm run dev
   # 指定 sidecar 解释器：
   # KEVRAI_PYTHON=$PWD/.venv/bin/python npm run dev
   ```

### Linux 常见问题

- **Electron 报 `libgtk-3.so.0: cannot open shared object file`**：上面 apt
   依赖没装全，补装 `libgtk-3-0`。
- **沙箱报错 `No SUID sandbox` / `chrome-sandbox`**：在无特权容器里跑时，
   可临时 `npm run dev -- --no-sandbox`（仅开发调试用，勿在发布包这么做）。
- **GPU 初始化失败**：见 [TROUBLESHOOTING.md](TROUBLESHOOTING.md) 的 GPU 节。

---

## macOS 开发环境

1. **安装 Homebrew**（如未装）：
   ```bash
   /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
   ```

2. **用 brew 安装 Node 与 Python**：
   ```bash
   brew install node@22 python@3.12
   brew link --overwrite node@22 python@3.12
   ```

3. **创建 venv 并安装依赖**：
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   npm install
   cd python && pip install -r requirements.txt && cd ..
   ```

4. **启动**：
   ```bash
   npm run dev
   ```

> Apple Silicon（arm64）与 Intel（x64）均可。首次从源码打包 dmg 时，
> 内部构建未签名，需在「系统设置 → 隐私与安全性」允许打开。

---

## 从源码运行（标准流程）

在**仓库根目录**、且 Python venv 已激活的终端里：

```bash
# 1. 安装前端依赖
npm install

# 2. 安装 Python sidecar 依赖
cd python
pip install -r requirements.txt
cd ..

# 3. 一键启动（Electron 自动拉起 sidecar）
npm run dev
```

启动后：

- 渲染层为 Electron 窗口（`renderer/index.html`）
- sidecar 健康检查在 `http://127.0.0.1:17890/api/health`
- 可另开终端直接打 API 调试：
  ```bash
  curl http://127.0.0.1:17890/api/health
  # {"ok": true, "version": "3.0.0", ...}
  ```

---

## 运行测试

```bash
# Python 单元测试（pytest；默认排除需要真实网络的 live 标记）
npm run test:python
# 等价于：cd python && python -m pytest -q tests/

# JS 语法冒烟（对 electron/*.js、renderer/*.js、renderer/modules/*.js 逐个 node --check）
npm run test:js

# 渲染层单元测试（node --test，跑 renderer/__tests__/*.test.js；部分模块用 jsdom 在 Node 下测 DOM 逻辑）
npm run test:renderer

# 端到端冒烟脚本（拉起 sidecar、打关键健康/目录接口）
npm run smoke
# 等价于：bash scripts/smoke.sh
```

开发 Python sidecar 时也可直接在 `python/` 目录下用 pytest 跑单文件：

```bash
cd python
python -m pytest -q tests/test_security.py -x
```

---

## 打包

打包产物输出到 **`build/output/`**（见 `electron-builder.yml`）：

```bash
npm run build:win     # Windows NSIS .exe（需在 Windows 上；Linux 下可用 wine）
npm run build:linux   # Linux AppImage + .deb
npm run build:mac     # macOS .dmg（需在 macOS 上）
```

产物命名模板来自 `electron-builder.yml` 的 `artifactName`：
`Kevrai-Omni-${version}-${arch}.${ext}`。

> 不要在打包后手工改产物名；自动更新靠 `latest.yml` / `latest-linux.yml`
> 与文件名严格对应。发版流程见 [../RELEASE.md](../RELEASE.md)。

---

## 常见开发问题排查

### 1. 找不到 Python / sidecar 起不来

症状：启动后弹 "Python sidecar failed to start" 或 `no-python`。

- 确认 `python --version`（Windows）/ `python3 --version`（Linux/macOS）可用。
- 确认已 `pip install -r python/requirements.txt`。
- 用 `KEVRAI_PYTHON` 显式指向 venv 里的解释器再 `npm run dev`。
- 看 DevTools 控制台与终端里 `[sidecar-stderr]` 日志尾；若出现
  `ModuleNotFoundError: No module named 'xxx'`，就是缺依赖，补装即可。

### 2. 端口 17890 被占用

sidecar 固定监听 `127.0.0.1:17890`。若被别的进程占用，Electron 健康检查会超时：

```bash
# 查谁占用（Linux/macOS）
lsof -i :17890
# Windows PowerShell
netstat -ano | findstr :17890
```

结束占用进程，或改由该进程退出后再启动。开发期不要把 sidecar 暴露到
`0.0.0.0`——这会绕过 localhost 与 CORS 的安全边界。

### 3. Python 依赖冲突

- 始终在 venv 里安装依赖，不要 `pip install` 到系统环境。
- 若同时跑多个 Python 项目，确认激活的是本仓库 `.venv`。
- `pip install` 走默认 PyPI 慢时可用 `pip install -i https://mirrors.tencent.com/pypi/simple/ -r requirements.txt`。

### 4. GPU 驱动 / 显存

- sidecar 的 `/api/gpu` 只做**检测**，不要求你有 GPU 才能跑开发环境。
- 真正跑 LTX 视频生成 / 大模型推理时才需要 NVIDIA 驱动 + 对应 CUDA 版
  PyTorch；显存不足会在生成面板报显存错误，详见
  [TROUBLESHOOTING.md](TROUBLESHOOTING.md)。

### 5. Electron sandbox / 渲染层报错

- 本项目 preload 使用 `contextIsolation` + `sandbox`，**不要**为了图方便
  把 `nodeIntegration` 打开或关掉 sandbox。
- 无特权容器 / CI 里开窗失败时，临时加 `--no-sandbox` 仅用于本地调试。
- 渲染层请求 sidecar 必须走 preload 暴露的 `api:*` 通道，CORS 白名单见
  `python/app/main.py` 的 `ALLOWED_ORIGINS`。

---

## 相关文档

- API 参考：[docs/API.md](API.md)
- 用户/开发者故障排查：[docs/TROUBLESHOOTING.md](TROUBLESHOOTING.md)
- 发布流程：[../RELEASE.md](../RELEASE.md)
- 安全模型：[../SECURITY.md](../SECURITY.md)
