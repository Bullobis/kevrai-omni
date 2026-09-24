# DEVELOPMENT.md — 开发者指南

> 面向想改代码、跑测试、提 PR 的工程师。终端用户装软件请见 [DEPLOYMENT.md](./DEPLOYMENT.md)；架构见 [ARCHITECTURE.md](./ARCHITECTURE.md)。

## 目录

- [1. 环境搭建](#1-环境搭建)
- [2. 安装依赖](#2-安装依赖)
- [3. 运行](#3-运行)
- [4. 测试](#4-测试)
- [5. 代码规范](#5-代码规范)
- [6. 提交规范](#6-提交规范)
- [7. 调试](#7-调试)
- [8. CI 概览](#8-ci-概览)

---

## 1. 环境搭建

| 工具 | 版本 | 说明 |
|---|---|---|
| Node.js | **20.x 或更高**（CI 在 22 上跑 `node --check`） | Electron 33 内置 Node 20.x；仓库 `package.json` 未声明 `engines`，但 CI 用 22。 |
| Python | **3.11**（pyproject 声明 `>=3.10`；CI 统一用 3.11） | sidecar 跑在这个版本上。 |
| git | 任意现代版 |  |
| （可选）pnpm | — | 仓库用 npm / package-lock.json，**未**锁 pnpm；用 npm 即可。 |
| 操作系统 | 任意能跑 Electron 33 的桌面 OS | 见 [DEPLOYMENT §1](./DEPLOYMENT.md#1-系统要求)。 |

一键检查环境是否就绪：

```bash
bash scripts/setup/bootstrap.sh --dry-run
```

脚本会检查 node / python3 / npm / git 的版本，并在自检阶段 import 关键 Python 包、确认 17890 端口空闲。

---

## 2. 安装依赖

```bash
git clone https://github.com/Bullobis/kevrai-omni.git
cd kevrai-omni

# 1) 前端依赖
npm install

# 2) Python sidecar 依赖（国内建议加镜像源）
python3 -m pip install -i https://mirrors.tencent.com/pypi/simple/ -r python/requirements.txt

# 3) 开发依赖（pytest / ruff / mypy / black / pytest-cov）
python3 -m pip install -i https://mirrors.tencent.com/pypi/simple/ \
    pytest pytest-asyncio pytest-cov ruff mypy black
```

> 也可以用 `pip install -e "python/[dev]"` 一键装运行 + 开发依赖（`python/pyproject.toml` 里定义了 `[project.optional-dependencies].dev`）。

**pip 镜像选择**（CI 默认用腾讯）：

| 镜像 | index URL |
|---|---|
| 腾讯（默认，CI 在用） | `https://mirrors.tencent.com/pypi/simple/` |
| 阿里云 | `https://mirrors.aliyun.com/pypi/simple/` |
| 清华 TUNA | `https://pypi.tuna.tsinghua.edu.cn/simple/` |
| 华为云 | `https://mirrors.huaweicloud.com/repository/pypi/simple/` |
| 官方 | `https://pypi.org/simple/` |

设为默认：`pip config set global.index-url <URL>`，或临时 `-i <URL>`。

---

## 3. 运行

```bash
# 生产模式启动（Electron 加载 renderer/index.html，spawn sidecar）
npm start

# 开发模式：开 DevTools、打开 Electron 日志
npm run dev
```

sidecar 默认绑 `127.0.0.1:17890`。如果你想单独调试 sidecar（不开 Electron）：

```bash
cd python
python3 -m uvicorn app.main:app --host 127.0.0.1 --port 17890 --reload
```

注意：Electron 主进程会自己 spawn 一个 sidecar；手动再开一个会因端口占用而失败。开发时二选一。

---

## 4. 测试

### 4.1 Python 测试

```bash
cd python
python -m pytest -q tests/
```

基线：**833 passed**（约 127s，v2.9.0）。pytest 配置在 `python/pyproject.toml`：

- `addopts = "-ra --strict-markers -m 'not live'"`——默认排除需要真实网络的 `live` 标记测试。
- `asyncio_mode = "auto"`。
- 覆盖率门槛在 CI 里：floor 68%、goal 80%（见 [docs/COVERAGE.md](./COVERAGE.md)）。

跑单个测试文件：

```bash
cd python
python -m pytest tests/test_gpu.py -q
```

### 4.2 JS 语法检查

```bash
npm run test:js
```

实质是对 `electron/*.js`、`renderer/*.js`、`renderer/modules/*.js` 逐个 `node --check`（当前 25 个文件）。

### 4.3 冒烟

```bash
npm run smoke
# 等价于
bash scripts/smoke.sh
```

9 步检查：catalog JSON 合法性 → jsonschema 校验 → URL 白名单 → 无泄露密钥 → pip 装依赖 → pytest → node --check → electron-builder.yml 合法性 → catalog 统计。CI 里 `smoke` job 跑的就是它。

---

## 5. 代码规范

### 5.1 Python

- **ruff** 选了 `E W F I B UP S SIM`（pycodestyle / pyflakes / isort / bugbear / pyupgrade / bandit / simplify），line-length 110。
- **mypy**：`python_version="3.10"`、`ignore_missing_imports=true`、`no_implicit_optional=true`。CI 硬门 **`MAX_ERRORS=0`**——任何新类型错误直接挂。需要压制时用窄作用域 `# type: ignore[<code>]` 并写注释。
- **black**：line-length 110，target py310/311/312。
- 测试目录放宽了 `S` / `B` 规则（`pyproject.toml` 里 `per-file-ignores`）。

本地跑：

```bash
cd python
ruff check .
mypy app/ --ignore-missing-imports
black --check .
```

### 5.2 JavaScript

- **没有 ESLint 配置**，唯一硬门是 `node --check` 语法通过。
- 风格上沿用现有：双引号、2 空格、`"use strict";`、ES Module（renderer 端）、CommonJS（electron 端）。
- 新增 IPC channel 时**必须**在 `electron/preload.js` 的白名单里登记，并做入参校验。

### 5.3 目录约定

```
electron/        # 主进程 + preload（CommonJS）
renderer/        # UI（ES Module，无打包器）
  modules/       # 21 个功能模块
python/
  app/           # sidecar 包
    agent/       # Agent 子包
    hub/         # 多源 hub 客户端
    tools/       # Agent 工具
  tests/         # pytest
catalog/         # 静态 models.json / engines.json / schema.py
scripts/         # 构建/发布/冒烟（别在这放临时脚本）
scripts/setup/   # 环境引导（bootstrap.sh）
docs/            # 本文档集合
```

---

## 6. 提交规范

沿用 Conventional Commits 前缀：

| 前缀 | 用途 |
|---|---|
| `feat` | 新功能 |
| `fix` | 修 bug |
| `docs` | 文档 |
| `chore` | 构建/脚本/杂项 |
| `refactor` / `test` / `style` | 视情况 |

关联 issue：commit message 里写 `(#123)` 或正文提 `Closes #123`。

分支约定（ADR-0002）：**只在 `cabinet/*` 分支提交**，开 PR 合回 `main`；不直接 push main、不 force push、不打 tag。

---

## 7. 调试

### 7.1 Electron DevTools

```bash
npm run dev
```

自动 `--enable-logging --devtools`，DevTools 打开。渲染进程的 console / network 可直接看。

### 7.2 sidecar 日志

- 主进程日志：`<userData>/logs/main.log`（Windows: `%APPDATA%\KevraiOmni\logs\main.log`）。
- sidecar 的 stdout/stderr 会被主进程前缀 `[sidecar]` / `[sidecar-stderr]` 写进同一个 `main.log`。
- sidecar stderr 尾部最多保留 80 行（`sidecarStderrTail`），崩溃诊断时用。
- 设置里打开 `debug_http_logs=true` 可以看到 sidecar 的 HTTP 请求日志。

### 7.3 Python 远程调试

sidecar 是主进程 spawn 的子进程。两种方式：

1. **不开 Electron，手动跑 uvicorn**（见 [§3](#3-运行)），在 IDE 里直接打断点。
2. 在 sidecar 代码里加 `import debugpy; debugpy.listen(5678); debugpy.wait_for_client()`，然后从 VS Code attach。注意这种方式下 `KEVRAI_PORT` 要和主进程期望的一致，或者干脆只手动跑 sidecar。

### 7.4 常用环境变量

| 变量 | 作用 |
|---|---|
| `KEVRAI_PYTHON` | 覆盖 sidecar Python 解释器路径。 |
| `KEVRAI_PORT` | sidecar 端口（默认 17890）。 |
| `KEVRAI_PIP_INDEX` | smoke.sh 用的 pip 镜像（默认腾讯）。 |
| `HF_ENDPOINT` | 覆盖 HF 端点（如 `https://hf-mirror.com`）。 |
| `HF_HUB_OFFLINE=1` | 离线模式。 |

---

## 8. CI 概览

`.github/workflows/ci.yml`，5 个 job：

| # | Job | Runner | 做什么 |
|---|---|---|---|
| 1 | `python` | ubuntu-latest, Python 3.11 | 装依赖（腾讯镜像）→ `pytest --cov=app` → 覆盖率门（floor 68%，goal 80%）；失败时把 pytest 尾部和 pip freeze 写进 Summary。 |
| 2 | `node` | ubuntu-latest, Node 22 | 不装依赖，直接对所有 `.js`（排除 node_modules/build/dist）跑 `node --check`。 |
| 3 | `smoke` | ubuntu-latest, Python 3.11 + Node 22 | 跑 `bash scripts/smoke.sh`。 |
| 4 | `lint` | ubuntu-latest, Python 3.11 | `ruff check .` + `mypy app/ --ignore-missing-imports`（**MAX_ERRORS=0**；mypy 退出码 >=2 视为硬失败）。 |
| 5 | `summary` | ubuntu-latest | `needs: [python, node, smoke, lint]`，永远跑；任一前置失败则本 job 失败。 |

触发：push 到 main/master、PR 到 main/master、手动 `workflow_dispatch`。同一 ref 上新 push 会取消在跑的旧 run。

> 覆盖率门槛刻意设在实测基线（约 69.6%）之下，用来抓**回归**而不是永久红；目标 80%，见 [docs/COVERAGE.md](./COVERAGE.md)。
