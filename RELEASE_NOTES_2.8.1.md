# Kevrai Omni v2.8.1 — 全面细化检查 + 引擎/模型对接加固

发布日期：2026-09-18
上一版本：v2.8.0

本次为**细化检查与加固版本**，无破坏性变更。重点解决 DIY 本地导入链路断点、
目录数据实测清洗、版本/UA 漂移与并发单例竞态。

---

## 1. DIY 导入链路打通（此前只能导入、无法运行）

| 项 | 变更 |
|---|---|
| 引擎自动标注 | `/api/models/local` 与 `/api/models/{id}` 新增 `compatible_engines` 字段（读时计算、不落盘）：`.gguf`→`llama.cpp`；`config.json` + `*.mnn`→`mnn`；`model_index.json`→`diffusers`+`transformers`；HF 目录→`transformers` |
| llama.cpp 运行通道 | Electron 新增 `kevrai:llm-start` / `llm-stop` / `llm-status`：定位已安装 `llama-server`（含 zip 布局递归查找）→ 随机端口 spawn → `/health` 轮询（30s 超时）→ 单实例互斥 → 退出时树杀回收 |
| MNN 页合并 | `/api/mnn/local` 合并 DIY 注册表中的 MNN 目录模型，按绝对路径去重并标注 `diy: true` |
| 前端 | 本地页显示「可用引擎」标注；GGUF 条目一键启动/停止并显示运行端口。修复 `renderer/modules/api.js` 未导出 `llm*` 导致按钮运行时崩溃的契约缺口 |
| 文档修正 | `runner.py` 中指向不存在函数 `startLLMServer()` 的失实注释已更正 |

## 2. 引擎目录与模型源

- 新增引擎 **`transformers`**（pip，huggingface/transformers，6 个 PyPI 镜像源）
- 新增引擎 **`gpt-sovits`**（source，RVC-Boss/GPT-SoVITS）
- 修复 `kokoro-engine` 的 GitHub 死链（`hexgrad/Kokoro-82M` 不存在 → `hexgrad/Kokoro`）
- `models.json` 中 `kokoro` 引擎引用归一为 `kokoro-engine`（与 `taxonomy.py` 一致）
- 引擎目录 32 项；模型目录 121 项引擎引用 100% 可解析

## 3. 下载与镜像

- 引擎安装器：多候选回退（`ghfast.top` / `gh-proxy.com` 前缀）、HTTP Range 断点续传、200-对-Range 自动重置、进度遥测
- 注入 98 个经 ModelScope 实测验证的下载源，移除 240 条实测不可达镜像条目
- `extra_model_mirrors` 默认值仅保留存活的 `hf-mirror.com`（移除 4 个实测死镜像）

## 4. 版本单一来源与并发加固

- 新增 `app.USER_AGENT`（由 `__version__` 派生），统一此前 6 处硬编码 UA：
  `engines.py`、`hub/hf.py`、`hub/modelscope.py`、`sources.py`、`hardware.py`、
  `importer.py`、`mnn_catalog.py`、`main.py`
  清除的历史漂移包括 `kevrai-studio/2.3.0`、`KevraiStudio/2.3.0`（含品牌名不一致）
- 修复 `hub.get_registry()` 与 `taxonomy.known_engine_ids()` 的 check-then-act 竞态，补 `threading.Lock`
- 版本号统一升级至 **2.8.1**：`python/app/__init__.py`、`package.json`、
  `package-lock.json`、`catalog/{engines,models}.json`；`build_windows.sh` 版本回退值同步

## 5. 测试

| 套件 | 结果 |
|---|---|
| `pytest`（全量） | **582 passed** |
| `node --check`（JS） | 14/14 |
| `scripts/smoke.sh`（官方 9 步） | 全绿 |
| 新增 `test_engines_extreme.py` | 7 项（真实 HTTP：续传/重置/取消/进度/回退/全周期） |
| 新增 `test_local_diy.py` | 10 项（引擎检测 + API 标注 + MNN 合并去重） |
| 新增 `test_v281_version_and_locks.py` | 9 项（版本一致性 + UA 无漂移 + 并发单例） |

实机验证：`/api/models/local` 与 `/api/mnn/local` 注入/合并/去重均符合预期；
80 路并发无失败；死源下载优雅失败且日志无异常栈。

## 6. 已知限制

- 沙箱构建环境无 Wine，NSIS 安装器不可生成；Windows 用户使用 `x64.zip` 便携版
  或在真实 Windows 环境执行 `scripts/build_windows.sh`
- 模型源放行策略沿用 v2.2.0 既定设计（允许任意 http(s) 源，用户自主选源），
  边缘由 CORS 白名单 + sidecar 仅监听回环地址防护
- llama.cpp 的 GUI 交互链路（启动按钮 → 对话）建议在真实桌面环境端到端手工验证
