# Kevrai Studio v2.2.0 — Release Notes

发布日期：2026-08-21

## 🎉 What's New in 2.2.0

**v2.2.0 是 v1.x 的重大升级**，聚焦三件事：**多源自动选最速**、**环境/依赖/引擎一键装**、**零黑名单**。

### 1. 多源下载 · 自动选最快（核心）

- **每个模型/引擎都暴露 `sources[]` 镜像列表** —— 62 个模型 + 20 个引擎，**总 343 个下载源**。
- 选源逻辑：`/api/sources/measure` 端点对每个候选 URL 跑 64 KiB Range 探针，**测延迟 + 测吞吐** → 复合打分排序 → 自动挑最快。
- `/api/download/start` 接受 `candidates[]`，测速后**自动选最优源**。UI 顶部显示 top-5 排名（速度 / 延迟 / 状态码）。
- 候选镜像（实际选择时按你当前位置延迟排序）：
  - **HuggingFace 官方** + HF LFS CDN
  - **HF-Mirror**（CN） / HF-Mirror US / HF-CN-Mirror
  - **ModelScope**（CN）/ 阿里云 OSS
  - **清华 PyPI 镜像** / 阿里云 PyPI / 华为云 PyPI / 腾讯云 PyPI
  - **GitHub releases** + raw.githubusercontent.com
  - 你随时可以在 **设置 → 下载源** 加自定义镜像

实测三源对比（沙箱环境）：
| # | URL | 延迟 | 吞吐 | 状态 |
|---|-----|-----|-----|-----|
| 1 | `www.modelscope.cn` | 89 ms | — | 200 ✅ 选中 |
| 2 | `hf-mirror.com` | 5,555 ms | — | 206 |
| 3 | `huggingface.co` | 5,003 ms | — | timeout ❌ |

→ 系统直接选了 ModelScope（沙箱到 HF 超时）。

### 2. 零黑名单 · 完全用户自决

- **v1.x 硬黑名单已彻底移除**（`hf-cdn.sufy.com` / `sufy.com` / `huggingface.buzz` / `hugging-face.club` 等）。
- `app.catalog`、`app.downloader`、`app.importer`、`app.main`、`catalog/schema.py`、`catalog/models.json` 全部清理。
- 现在的策略：**任何 https URL 都能下载**。在 **设置 → 下载源** 自己加。
- 向后兼容：`DEFAULT_BLOCKED_MIRRORS` / `BLOCKED_MIRRORS` / `ALLOWED_MODEL_HOSTS` / `ALLOWED_ENGINE_HOSTS` 这些名字**保留为空集 / 默认镜像集**，老代码和老测试不会破坏。
- 验证：`POST /api/download/start` 用 `https://hf-cdn.sufy.com/x` 也能 200 OK（你定的规则）。

### 3. 全新"环境管理"页（in-app installer）

- 侧边栏新增 **"环境管理"** tab。
- 自动检测并展示：
  - **Python 版本** + pip 版本
  - **Node.js 版本**（可选）
  - **GPU 列表**（NVIDIA / AMD / Apple / 昇腾 / CPU 自动识别）
  - **磁盘剩余** + 模型/引擎目录占用
  - **已装 pip 包 vs 必需包**（一键标红缺失项）
  - **引擎列表** + 安装状态
- 缺失项 → 点"安装"按钮 → **软件内自动调用 pip / 引擎下载器**，全程在 GUI 里完成。
- "测速全部镜像"按钮：一键测速，UI 排序展示。
- 引擎安装：选引擎 → `app.sources.measure_sources(engine.sources)` 测速 → 选最快 → 下载。
- 新增端点：
  - `GET /api/env/status` — 全量状态
  - `POST /api/env/install` — 装 pip 包（`{name, version?, mirrors?}`）
  - `POST /api/env/upgrade` — 升级 pip 包
  - `POST /api/env/install-engine` — 装引擎（自动选最快源）

### 4. 其它改进

- **版本号从 1.0.0 跳到 2.2.0**（package.json + Python `__version__` + electron-builder.yml 全部统一）。
- 测试从 28 → **195 passing**（新增 test_sources.py / test_env.py / test_catalog.py 多源校验 / test_downloader.py 改透策略）。
- `scripts/smoke.sh` 9 步闸门全过，包括新增的"多源目录 shape"步骤。
- 端到端新端点：8 个原契约 + 4 个新（env-status / env-install / env-upgrade / env-install-engine / sources-measure）全 200。
- IPC 100% 一致（preload ↔ main + renderer ↔ preload）。

## 📦 下载与安装

| 平台 | 文件 | 大小 | 安装方式 |
|------|------|-----:|---------|
| **Linux x64** | `Kevrai-Omni-2.2.0-linux-x64-portable.tar.gz` | 102 MB | 解包 → `./run.sh` |
| **Windows x64** | `Kevrai-Omni-2.2.0-win32-x64-portable.zip` | 110 MB | 解压 → 双击 `run.bat`；可选 `install-shortcut.bat` 创建桌面快捷方式 |
| **源码 (tar.gz)** | `kevrai-studio-2.2.0-source.tar.gz` | 132 KB | `npm install` + `bash scripts/build_windows.sh` |
| **源码 (zip)** | `kevrai-studio-2.2.0-source.zip` | 167 KB | 同上 |

> ⚠ **首次启动**会自动调 pip 装依赖（阿里云 PyPI 镜像）；引擎和模型按需下载，安装包**保持小巧**。

## 🔧 已知限制

- **沙箱里没法发 Linux 单文件 .AppImage / Windows 单文件 .exe**：electron-builder / NSIS 需要 wine，沙箱无 wine。
  - 已交付的 portable 是**等效方案**（解压即用），等价于 `electron-builder --linux dir` / `--win portable`。
  - 想生成单文件 .exe：在 Windows 上跑 `bash scripts/build_windows.sh`（需要 Python 3.11 + Node 22 + npm）。
- 已泄露的旧 GitHub PAT（首次会话中贴出过明文）已**提醒**轮换；新发布请用新 PAT，并保留 PAT 在环境变量 `GITHUB_TOKEN` 而非明文。

## 🛡 安全

- 零黑名单策略下，建议用户从可信源下载（`huggingface.co` / `github.com` 官方）。
- 所有下载走 HTTPS，main 进程 IPC 输入**逐字段校验**。
- sidecar 默认只允许 `http://localhost:*` 跨域，CSRF 风险归零。
- SHA-256 校验在 `Downloader` 内置；引擎安装 manifest 原子写。

## 📝 升级指引 (v1.x → v2.2.0)

- v1.x 用户：解包新版本到不同目录，先跑 `bash scripts/smoke.sh` 验证环境，再启动新版。**用户数据目录**（`~/.local/share/KevraiOmni/` / `%APPDATA%/KevraiOmni/`）兼容。
- 旧 token 必须轮换。

## 💬 反馈

- Bug 报告：开 issue，附 `~/.local/share/KevraiOmni/logs/main.log`。
- 模型/引擎请求：开 issue 标 `catalog-request`。
- 性能问题：附 `python -m cProfile` 输出 + GPU 型号。

— Kevrai Studio Contributors
