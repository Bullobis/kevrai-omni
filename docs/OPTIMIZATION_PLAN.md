# Kevrai Omni — 代码强化与优化方案（审计报告 + 实施路线图）

> 版本：v1.0（审计稿）
> 审计对象：`Bullobis/kevrai-omni` @ `main` / `5156e9c`，`package.json` version = `2.7.0`
> 审计：Kevrai Omni Team
> 报告性质：**只出方案，不改源码**。本文档未修改任何现有源码文件。

---

## 0. 审计方法与口径说明

### 0.1 我做了什么

| 动作 | 说明 |
|---|---|
| 全量静态阅读 | 完整读完 `python/app/main.py`（2154 行）、`electron/main.js`（1150 行）、`electron/preload.js`（421 行）、`python/app/catalog.py`、`downloader.py`、`search.py`、`importer.py`、`converter.py`、`settings.py`、`agent/{agent,memory}.py`；抽样读了其余 Python 模块、全部 renderer 模块 |
| 交叉检索 | 对 `innerHTML` / `subprocess` / `to_thread` / `except Exception` / 全局变量 / 定时器 / `hf_token` / `assert` 等做了全仓定向检索 |
| 实测运行 | 在仓库内实际执行 `pytest`（**421 passed**）、`pytest --cov=app`（**TOTAL 57%**）、并按 `ci.yml` 同款脚本复算覆盖率门槛（**57.3%**）；用 `node` 实测 `assert` 是否为全局量 |
| 数据核对 | `catalog/models.json` 121 模型 / 30 引擎 / 159 KB；`python/app/main.py` 共 **67 个路由装饰器** |

### 0.2 严禁虚构原则的执行方式

- 本报告中每一条论断都带 **`文件:行号`**。凡是我没有读到、无法从代码推断的，一律标注 **「待确认」**，不做推测性结论。
- 与需求描述不一致的既有事实，我在文中显式更正（例如：根目录实际是 **6 个** `RELEASE_NOTES_*.md` 而非 8 个；测试实际是 **421 passed** 而非 420）。
- 仅在**本地沙箱缺失依赖**导致的现象（如未装 `pytest-asyncio`）不作为仓库缺陷上报，只作为工程建议。

### 0.3 与需求描述的事实更正

| 需求中的说法 | 实际核查结果 | 出处 |
|---|---|---|
| 根目录 8 个 `RELEASE_NOTES_*` | 实际 **6 个**（2.2.0 / 2.4.1 / 2.4.2 / 2.5.0 / 2.6.0 / 2.7.0），另有 `RELEASE.md` | `ls` |
| README 声称 420 passed | 实测 **421 passed**（28 文件 / 367 个测试函数 / 含 parametrize 后 421 项） | 本仓 `pytest -q` |
| 28 个 pytest 文件 | 确认 28 个 | `ls python/tests` |
| main.py 60+ 路由 | 确认 **67 个** 路由装饰器（65 HTTP + 2 WebSocket） | `main.py` grep |

---

## 1. 现状评估

### 1.1 模块规模表（Python sidecar）

健康度定义：**A**=职责单一、有测试、无全局可变状态；**B**=可用但有可维护性问题；**C**=明显上帝对象/无测试/全局状态严重；**D**=死代码或覆盖率极低且无测试。

| 文件 | 行数 | 职责 | 覆盖率 | 健康度 | 主要问题 |
|---|---:|---|---:|:---:|---|
| `python/app/main.py` | 2154 | HTTP 控制面：67 路由 + 中间件 + 限流 + 生命周期 + WS + OpenAI 兼容层 | **45%** | **C** | 上帝文件；模块级全局 `_HW_CACHE` `_MNN_DL` `_AGENT_SINGLETON`；路由内塞业务逻辑；存在未 await 协程（P0-2） |
| `python/app/engines.py` | 793 | 引擎安装/卸载/更新/清单 | 75% | B | 函数式 + `EngineManager` 类混用；`subprocess` 直接调用 |
| `python/app/converter.py` | 667 | 模型格式转换（subprocess 编排） | **20%** | C | 全局 `_TASKS`/`_ACTIVE` 无清理；`pip install` 无镜像/超时 |
| `python/app/drama.py` | 622 | 短剧剧本 LLM 编排 | **12%** | D | 几乎无测试 |
| `python/app/ltx_runtime.py` | 547 | LTX-2.5 视频生成运行时 | 61% | B | 线程 + 全局状态，结构尚可 |
| `python/app/search.py` | 542 | 加权模糊搜索 | **94%** | **A** | 有 Corpus 缓存键缺陷（P1-6） |
| `python/app/importer.py` | 496 | 本地模型导入 + 注册表 | 73% | B | 同步阻塞大文件哈希（被 async 路由直接调用） |
| `python/app/downloader.py` | 446 | 可续传下载 | 74% | B | httpx client 泄漏；尾部 fsync 代码失效 |
| `python/app/agent/agent.py` | 443 | ReAct 循环 | 90% | B | `router.chat` 同步阻塞事件循环 |
| `python/app/mnn_runtime.py` | 414 | MNN 推理运行时 | **14%** | D | 几乎无测试，且是 `/v1/chat/completions` 的核心依赖 |
| `python/app/catalog.py` | 408 | 目录加载/校验/缓存 | 65% | B | 缓存路径分支基本未测 |
| `python/app/gpu.py` | 328 | GPU 探测 | 47% | C | 多平台分支无测试 |
| `python/app/mnn_catalog.py` | 323 | MNN 市场清单 | **19%** | D | `list_mnn_files` 网络路径无测试 |
| `python/app/env.py` | 314 | 运行时环境检测/pip 安装 | 73% | B | — |
| `python/app/hardware.py` | 257 | 硬件快照 | 71% | B | — |
| `python/app/settings.py` | 234 | 设置持久化（原子写） | 88% | **A** | — |
| `python/app/sources.py` | 224 | 多源测速 | 90% | **A** | — |
| `python/app/recommend.py` | 166 | 硬件推荐 | **7%** | D | 几乎无测试 |
| `python/app/runner.py` | 21 | `spawn_llama_server` | **0%** | **D** | **全仓无任何引用 → 死代码** |

### 1.2 模块规模表（Electron + renderer）

| 文件 | 行数 | 职责 | 健康度 | 主要问题 |
|---|---:|---|---|---|
| `electron/main.js` | 1150 | 窗口 + sidecar 生命周期 + ~70 个 `ipcMain.handle` | C | 设置白名单漏字段（P0-3）；`openExternal` 未走白名单（P1-8）；sidecar 重启后不发健康事件 |
| `electron/preload.js` | 421 | `contextBridge` 暴露 ~95 个通道 | C | **`assert` 未定义导致 agentChat 全量崩溃（P0-1）** |
| `renderer/app.js` | 300 | 入口编排 + 视图切换 + 健康轮询 | B | 健康轮询 `setInterval` 无清理路径 |
| `renderer/modules/mnn.js` | 579 | MNN 引擎页（含市场/下载/对话/转换） | C | 单文件过大；轮询定时器多处；重复 `esc()` |
| `renderer/modules/drama.js` | 361 | 短剧四步流程 | B | 18 处 `innerHTML`；流程状态用模块级 `let` |
| `renderer/modules/models.js` | 325 | 模型卡片 + 详情面板 | B | — |
| `renderer/modules/search.js` | 304 | 搜索控制器 + 高亮 | B | — |
| `renderer/styles.css` | 751 | 全局样式 | B | 无模块化，但可接受（最小改动原则：不动） |

### 1.3 架构分层现状

```
┌──────────────────────────────────────────────────────────────────────┐
│  renderer/  (原生 JS ES Module, 无框架)                                │
│    app.js ──► modules/api.js ──► window.kevrai (preload bridge)        │
└──────────────────────────┬───────────────────────────────────────────┘
                           │ ipcRenderer.invoke (约 95 通道)
┌──────────────────────────▼───────────────────────────────────────────┐
│  electron/main.js  ——  进程管理 + IPC 校验 + 设置存储(JSON) + 下载门控   │
└──────────────────────────┬───────────────────────────────────────────┘
                           │ http://127.0.0.1:17890  (sidecarFetch)
┌──────────────────────────▼───────────────────────────────────────────┐
│  python/app/main.py  ——  67 路由（FastAPI）                            │
│     ├── 领域模块：catalog / engines / importer / downloader / converter │
│     │             / mnn_* / ltx_runtime / drama / search / recommend /  │
│     │             gpu / hardware / env / sources / settings              │
│     └── agent/ (agent.py + memory.py + model_router.py + tool_registry) │
└──────────────────────────────────────────────────────────────────────┘
```

**分层评价：**

| 维度 | 现状 | 评价 |
|---|---|---|
| 横向分层（renderer / main / sidecar） | 清晰，`contextIsolation:true` + `nodeIntegration:false` + `sandbox:true`（`electron/main.js:484-492`）已锁死 | ✅ 好 |
| 纵向分层（sidecar 内部） | **半分层**：领域模块已下沉（catalog/engines/downloader…），但**编排逻辑没有独立层**——`main.py` 同时承担「HTTP 适配」+「业务流程编排」+「进程内全局状态托管」三件事 | ⚠️ 主要病灶 |
| 依赖方向 | 基本单向：`main.py → 领域模块`。但有 **3 处反向/旁路耦合**：<br>① `main.py:104` `engines_module.is_installed = EngineManager.is_installed`（运行时给模块打补丁）<br>② `main.py:305-318` 运行时替换 `_JsonFormatter.format`（猴子补丁）<br>③ `main.py:795` 从 `downloader` 导入私有函数 `_check_url` | ⚠️ 有破口 |
| 领域模块之间 | 干净，无循环导入（未发现环） | ✅ 好 |
| 前端分层 | `modules/*.js` 按页面切分，共享 `api.js`/`toast.js`/`state.js`；**但无统一的 DOM 安全工具层**（`escapeHtml`/`esc` **重复定义 10 份**） | ⚠️ 中 |

### 1.4 耦合热点（main.py 内部）

| 行段 | 内容 | 性质 |
|---|---|---|
| 90-132 | 常量、路径、`ALLOWED_ORIGINS` | 配置 |
| 140-318 | JSON 日志、`_lifespan`、`_TokenBucket`、request-id 中间件、猴子补丁 | 基础设施（其中 305-318 是补丁） |
| 326-364 | Pydantic 请求模型 | 契约 |
| 406-570 | 早期路由（health/categories/models/engines/progress） | 路由 |
| 578-697 | gpu / env / settings | 路由 |
| **729-842** | **`download_start`：110 行，内含选源测速、URL 校验、gated token 判定** | **路由内业务编排（最重）** |
| 878-909 | WebSocket 下载 | 传输 |
| 917-960 | 硬件缓存 + 推荐 | 全局状态 + 路由 |
| **1064-1180** | **MNN 下载：全局 `_MNN_DL` dict + 线程 worker + 内联 httpx 重试（117 行）** | **全局状态 + 业务下沉在路由文件** |
| 1265-1415 | 转换路由 | 路由 |
| **1514-1759** | **OpenAI 兼容层（246 行），含 `_media_to_local` SSRF/本地文件读取面** | **独立协议，应独立模块** |
| 1766-1808 | 搜索路由 | 路由 |
| 1815-1915 | LTX 路由 | 路由 |
| **1929-2154** | **Agent：单例 + 8 路由 + WS（226 行）** | **应独立模块** |

### 1.5 质量基建实测

| 指标 | 声明值 | 实测值 | 结论 |
|---|---|---|---|
| pytest 通过数 | README：**420**；CI badge：372；README 另处：323 / 303 | **421 passed**（59s） | README 数字**互相矛盾且过期**（`README.md:20/34/43/91/132/205/265`） |
| 覆盖率门槛 | `ci.yml`：**≥ 80%** | 按 `ci.yml` 同款脚本复算：**57.3%**（3174/5539）；`pytest-cov` TOTAL：**57%** | **门槛与实际严重不符**，见 P1-11 |
| JS 语法检查 | `node --check` 全量 | `preload.js` 语法通过 | ⚠️ 语法通过 ≠ 运行正确（P0-1 就是语法正确但运行崩溃） |
| 覆盖率缺口 Top | — | `runner.py` 0% / `recommend.py` 7% / `drama.py` 12% / `mnn_runtime.py` 14% / `mnn_catalog.py` 19% / `converter.py` 20% / `main.py` 45% / `gpu.py` 47% | 见 §5.5 |

---

## 2. 问题清单

> 分级：**P0** = 必修（影响正确性/安全/崩溃，用户可感知）｜**P1** = 应修（可维护性/性能/健壮性）｜**P2** = 可选（锦上添花）
> 每条格式：**位置 → 问题 → 影响 → 改法 → 风险与回归验证**

---

### P0-1　`electron/preload.js:300` 使用了未定义的 `assert` —— v2.7.0 旗舰功能 Agent 对话 100% 崩溃

- **位置**：`electron/preload.js:300`
  ```js
  agentChat: (opts) => {
    assert(opts && typeof opts === "object", "opts: invalid");   // ← assert 未定义
  ```
  `preload.js` 全文只在 `:16` 有 `const { contextBridge, ipcRenderer } = require("electron");`，**没有** `require("node:assert")`。
- **问题**：`assert` 不是 Node/Electron 的全局量。实测（真实 `.js` 文件，非 REPL）：
  ```
  $ node t.js
  typeof assert = undefined
  assert ERROR: ReferenceError assert is not defined
  ```
  因此任何一次 `window.kevrai.agentChat(...)` 调用都会**同步抛 ReferenceError**——连 IPC 都发不出去。
- **影响面**：`renderer/modules/agent.js:174` → `api.agentChat` → 崩溃。也就是说 **v2.7.0 的整个「Kevrai Agent 通用 AI 助手」主对话入口完全不可用**。因为 `window.kevrai` 缺失该方法还会连带触发 `api.js:22` 之外的异常分支。同类写法在 `main.js:538` 是**本地定义的** `function assert(cond,msg)`（`:538`），preload 里没有对应定义，明显是复制粘贴遗漏。
- **改法（二选一，推荐①）**：
  1. **在 preload 内本地定义**，与 `main.js:538` 对齐：
     ```js
     function assert(cond, msg) { if (!cond) throw new Error(msg); }
     ```
     放在 `preload.js:52` 的 `invoke()` 之前，并把 `:300` 的调用改为与 `main.js` 同语义。
  2. 改为 `if (!(opts && typeof opts === "object")) throw new Error("opts: invalid");`
  顺带：把 preload 的 assert 家族（`assertString/assertEnum/assertObject/assertOptionalString`，`:30-50`）与 `main.js:534-545` 的校验器合并为一份共享 `electron/validate.js`（两侧 `require` 同一份），从根上消除"两边各写一套、漏一个就崩"的问题。
- **风险**：**极低**。纯新增一个本地函数，不改任何既有调用路径。
- **回归验证**：
  1. 新增 `python/tests/` 之外的 JS 冒烟：在 `package.json` 的 `test:js` 里增加一条静态检查脚本，扫描 `electron/preload.js` 中调用的所有标识符是否都有定义（或直接用 `eslint no-undef`）。
  2. 人工验证：启动 app → 打开 Agent 页 → 发送任意消息 → 应看到回复或 sidecar 错误 toast，**而不是** `ReferenceError`。
  3. 对照验证 `main.js:538` 已有本地 `assert`，两处行为一致。

---

### P0-2　`python/app/main.py:2001` 未 await 协程 —— Agent 的硬件上下文被写成协程对象

- **位置**：
  - `python/app/hardware.py:225`：`async def detect_hardware(data_root: Path) -> dict[str, Any]:`
  - `python/app/main.py:2001`（在 `async def agent_chat` 内部）：
    ```python
    agent.ctx.hardware_info = detect_hardware(Path(s.resolved_model_dir()))   # 缺 await
    ```
- **问题**：`detect_hardware` 是 `async def`，这里**没有 `await`**，于是 `ctx.hardware_info` 被赋值为一个 **coroutine 对象**（而不是 dict）。该调用还被包在 `try/except Exception: pass`（`:1998-2003`）里，赋值本身不会抛异常 → **静默失败**，只留一条 `RuntimeWarning: coroutine ... was never awaited`。
  对照：`main.py:929` 与 `:949` 都正确地写了 `await detect_hardware(...)`。
- **影响面**：
  1. `agent.py:150` `hw = self.ctx.hardware_info or {}` 拿到协程对象 → `:152-156` `hw.get(...)` 直接 `AttributeError`；
  2. 这段代码被 `agent.py:279` 的 `hasattr(self.ctx.hardware_info, "send")` 特判"兜住"了——**说明作者已经发现了这个脏值，但选择在下游打补丁而不是修源头**；
  3. 下游连锁：Agent 首次对话的 system prompt 硬件块会缺失/异常，规则模式的硬件分支可能走空。
- **改法**：
  ```python
  # main.py:1997-2003
  if not agent.ctx.hardware_info:
      try:
          from .settings import load_settings as _ls
          s = _ls()
          agent.ctx.hardware_info = await detect_hardware(Path(s.resolved_model_dir()))
      except Exception:
          pass
  ```
  同时把 `_HW_CACHE`（`:917`）一并写入，避免与 `/api/hardware` 各算各的（见 P1-5）。
  修好后**保留** `agent.py:279` 的 `hasattr(..., "send")` 守卫 1~2 个版本作为兼容，再在 v2.7.2 移除，并在注释里写明移除条件。
- **风险**：**低**。只改一处表达式；`agent_chat` 本身是 async 上下文，`await` 合法。
- **回归验证**：
  1. 新增测试：`test_v270_agent.py` 中断言 `agent.ctx.hardware_info` 是 `dict` 类型（当前测试 `test_hardware_query_triggers_check` 未覆盖此路径，因为 `:1996` 之后才跑）。
  2. 运行 pytest 时用 `-W error::RuntimeWarning`，确保不再出现 "coroutine was never awaited"。
  3. 手动：清空 `_HW_CACHE` 后直接 POST `/api/agent/chat`，观察日志无 RuntimeWarning。

---

### P0-3　HF Token 无法从 UI 写入 —— gated 模型下载链路端到端断裂（三处独立缺陷叠加）

- **位置（三处，缺一不可）**：
  1. `renderer/modules/settings.js:71-86` `readForm()` 的返回对象里**没有 `hfToken`**；
  2. `electron/main.js:150-151` `saveSettings` 的持久化白名单：
     ```js
     const allowed = ["theme","hardwareAccel","telemetry","allowlistAdvanced","allowlist","modelDir","engineDir"];
     ```
     **不含 `hfToken`**（但 `DEFAULT_SETTINGS` 在 `:135` 定义了 `hfToken: ""`）；
  3. **没有任何 JS 调用 sidecar 的 `/api/settings`**：全仓检索 `hfToken|hf_token|api/settings`，renderer/electron 侧只有 `settings.js:52` 读取 `s.hfToken` 用于**回显**，而 sidecar 侧真正被下载逻辑读取的是 `python/app/settings.py:106` 的 `hf_token`（消费点 `main.py:813`）。
- **问题**：用户在设置页（输入框存在，`renderer/index.html:368` `id="set-hf-token"` type=password）填入 Token → 点保存 → `readForm()` 丢弃 → 即便不丢弃，`main.js` 白名单也会丢弃 → 即便都写进去了，sidecar 也**根本读不到**（两套设置系统：Electron `userData/settings.json` vs Python `default_data_root()/settings.json`，字段命名与存储位置都不同，无任何同步）。
- **影响面**：
  - `main.py:812-825`：所有 `gated: true` 的下载（README 中点名的 LTX-2.5）在**没有 token 时直接 422 `gated_requires_token`** → **gated 模型永远下载不了**；
  - v2.4.1 发布说明（`README.md:62`）声称"Token 配置链路端到端打通"——实际只在 HTTP 层（`/api/settings` PUT）打通，**UI 层从未打通**；
  - 用户可见：LTX-2.5 视频生成功能不可用。
- **改法（最小改动，分两步）**：
  - **步骤 A（止血，~20 行）**：让"设置"真正落到 sidecar。
    1. `renderer/modules/api.js` 新增 `sidecarGetSettings: () => k().getSidecarSettings()` / `sidecarPutSettings`；
    2. `electron/preload.js` 新增两个通道 `kevrai:sidecar-get-settings` / `kevrai:sidecar-put-settings`（透传到 `/api/settings`）；
    3. `renderer/modules/settings.js` 的 `openSettings()` 同时拉取两套设置，`readForm()` 增加 `hfToken` 字段并在保存时**分别**写入（Electron 存 UI 项，sidecar 存 `hf_token`）；
    4. `electron/main.js:150` 白名单补 `"hfToken"`（防再次静默丢弃）。
  - **步骤 B（收敛，建议 v2.8）**：把 Electron 侧设置精简为纯 UI 项（theme/telemetry/allowlist），把与业务相关的（`model_dir/engine_dir/download_dir/hf_token/max_concurrent_downloads`）**统一以 sidecar 为唯一真源**，Electron 只做转发。
- **风险**：**中**。涉及跨进程数据流，但不改 sidecar 契约（`/api/settings` GET/PUT 已存在且有测试 `test_v241_api.py:42-50`）。风险点在于设置页要同时处理两套数据，需在 UI 上明确"哪些设置需要 sidecar 在线"。
- **回归验证**：
  1. 新测试：`PUT /api/settings {"hf_token":"hf_x"}` → `GET /api/settings` 回显 `hf_x`（已有，保留）；
  2. **新增端到端测试**：`POST /api/download/start` 带 `gated:true` + `settings.hf_token=""` → 期望 422 `gated_requires_token`；带 token → 不再 422（可 mock 网络层）；
  3. 新测试：`saveSettings({hfToken})` 后 `loadSettingsSync().hfToken === 值`（Electron 侧，需补一个 JS 测试运行器，见 P2-14）；
  4. 手工：设置页填 token → 保存 → 重启 app → 重新打开设置页应回显（当前回显依赖 Electron 存储，若改步骤 B 则依赖 sidecar）。

---

### P0-4　`python/app/main.py:1615-1667` `_media_to_local`：服务端 SSRF + 任意本地文件读取 + 临时文件泄漏

- **位置**：`main.py:1615-1667`（`_media_to_local`），被 `main.py:1585`（`image_url`）与 `:1594`（`audio`）调用，入口是 `POST /v1/chat/completions`（`main.py:1551`）。
- **问题**：
  1. **SSRF**：`:1644-1663` 对任意 `http://` / `https://` 发起 `httpx.Client.get(url, follow_redirects=True)`，**无 host 白名单、无内网地址拦截、无响应大小上限**。虽然 `catalog.py:29-70` 已有一份 `DEFAULT_MODEL_HOSTS` 白名单、`downloader.py:57` 有 `_check_url`，但**这条路径完全绕过了它们**。
  2. **任意本地文件读取**：`:1665-1666` `if os.path.exists(url): return url` —— 只要传入本地绝对路径（如 `/etc/passwd`、Windows `C:\...\settings.json`）就原样返回给 MNN 多模态推理。**注意：Electron 侧 `main.js:996-1002` 的 `open-path` 是做了 `safePathWithin` 限制的，说明团队知道要限制路径；但 sidecar 这条路径没有**。
  3. **无大小/类型限制**：`data:` base64 分支（`:1622-1641`）解码后直接写盘；HTTP 分支 `r.content` 全量读入内存，无 `Content-Length` 上限 → 可被用于内存放大。
  4. **临时文件泄漏**：`tempfile.mkstemp`（`:1636`、`:1658`）创建的临时文件**从不删除**，长跑进程会持续堆积在 `%TEMP%`/`/tmp`。
- **影响面（需精确界定）**：sidecar 绑定 `127.0.0.1`（`electron/main.js:361` `--host 127.0.0.1`），且 renderer CSP（`main.js:55`）允许 `connect-src http://127.0.0.1:17890`。因此可达者 = 本机任意进程 + renderer 自身 + 任何能诱导本机发请求的东西。**不是公网 RCE，但是本机权限内的任意文件读 + SSRF，属于 P0 而非 P1**，因为修复成本极低（几行白名单）。
- **改法**：
  1. 抽 `python/app/media.py`，统一处理 media 解析，并加：
     - `data:` 分支：限制大小（如 20 MB）、校验 magic bytes；
     - `http(s)` 分支：**复用 `catalog.DEFAULT_MODEL_HOSTS` 或新增 `MEDIA_HOSTS` 白名单** + 显式拒绝 `127.0.0.1/::1/10./172.16-31./169.254./metadata.google.internal` + `max_bytes` 流式上限 + 禁 `follow_redirects` 或对重定向目标重新校验；
     - 本地路径分支：改为 **`Path(url).resolve().is_relative_to(allowed_root)`**，其中 `allowed_root` 取 `settings.resolved_model_dir()` / 数据根 / 一个专门的 `uploads` 目录；**不允许任意绝对路径**；
  2. 临时文件：改用 `tempfile.TemporaryDirectory()` 上下文，或在任务结束后 `os.unlink`；
  3. 路由层：`/v1/chat/completions` 增加与 `/api/models/import` 同款的 `_TokenBucket` 限流（`main.py:205` 已有现成实现，可复用）。
- **风险**：**中**。属于**行为收紧**，如果有外部 Agent（README 提到的 OpenClaw）依赖传本地绝对路径，会被拒绝。建议：加 `Settings.allow_local_media_paths: list[str]`（默认空，用户显式配置才放行），兼顾安全与可用。
- **回归验证**：
  1. 新测试（`python/tests/test_media_security.py`）：`file:///etc/passwd` → 400；`http://169.254.169.254/latest/meta-data/` → 400；`http://evil.example.com/a.png` → 400（不在白名单）；`https://huggingface.co/x.png` → 放行；`data:image/png;base64,<20MB+1>` → 400；
  2. 新测试：连续调用 100 次后 `/tmp` 下 `kevrai-image-*` 文件数为 0；
  3. 手工：Postman 打 `/v1/chat/completions` 传本地路径，确认返回结构化 400 而不是把内容喂给模型。

---

### P0-5　`renderer/modules/settings.js:71-86` + `electron/main.js:150-151` 与 P0-3 同源，但**独立成因**——设置保存静默丢字段

> 说明：这条与 P0-3 高度相关但**应单独立项**，因为它是一个通用缺陷，不只影响 `hfToken`：`saveSettings` 的白名单机制会让**任何新增字段默认被静默丢弃**，且前端 `readForm()` 与后端白名单**两处都必须同步修改**，任何一处漏改都没有任何报错。

- **位置**：`electron/main.js:148-157`、`renderer/modules/settings.js:71-86`
- **问题**：`fillForm`（`:40-53`）会读取并回显 `hfToken`，但 `readForm`（`:71-86`）不返回它 —— **用户看到输入框有值、点保存、值消失、且无任何提示**。这是最坏的一类"静默失败"。
- **改法**：
  1. `readForm()` 补齐 `hfToken`；
  2. `main.js` 白名单补 `"hfToken"`；
  3. **结构性修正**：把白名单从 `main.js` 移到与 `DEFAULT_SETTINGS`（`:127-136`）**同一个对象**上，用 `Object.keys(DEFAULT_SETTINGS)` 派生白名单，杜绝两处不同步；
  4. `saveSettings` 对**被丢弃的键**返回一次 `logWarn`，让静默失败变成可观测。
- **风险**：**低**。
- **回归验证**：新增 JS 单测（见 P2-14 引入 runner）：`readForm()` 的 key 集合 ⊇ `DEFAULT_SETTINGS` 的 key 集合；`saveSettings({unknown:1, theme:'dark'}).theme === 'dark'` 且 `unknown` 被丢弃并在日志中告警。

---

### P1-6　`python/app/search.py:323-334` Corpus 缓存以 `id(list)` 为键 —— 缓存永不命中 + 潜在脏数据

- **位置**：`search.py:319-334`
  ```python
  def get_corpus(models: list[dict[str, Any]]) -> Corpus:
      key = id(models)          # ← 以对象身份为键
  ```
- **问题**：唯一调用方 `main.py:1793` 每次请求都**新建列表**：
  ```python
  models = [m.model_dump() for m in CATALOG.models]
  result = run_search(models, sq)
  ```
  新列表 → 新 `id()` → **缓存 100% miss**，每次搜索都对 121 个模型重做一次全量分词（`Corpus.__init__` 里 `_field_text` + `_tokens` + CJK bigram）。同时 `_CORPUS_CACHE` 还会累积到 8 份 Corpus（每份持有全量模型 dict），常驻内存可达 8× 目录体积。更糟的是 `id()` 在对象被 GC 后**会被复用**，理论上有极小概率命中一个属于**不同列表**的旧 Corpus（正确性隐患）。
- **影响面**：`/api/search` 每次输入都重新分词；随目录增长线性劣化。
- **改法**：
  1. **首选**：在 `main.py` 启动时**构建一次** `models_dump = [m.model_dump() for m in CATALOG.models]` 并缓存到 `app.state.catalog_dump`，`api_search` 复用同一对象；`get_corpus` 改为以 `id(同一对象)` 为键即可命中；
  2. **更稳**：把 `Corpus` 挂到一个显式注册表上，键用 `(catalog_version, len(models))` 或直接用 `app.state.corpus` 单例，彻底摆脱 `id()`；
  3. `main.py:1794` 之后 `compute_facets(models)`（每次请求全目录遍历 4 个 Counter）同样应在 `app.state` 预计算并缓存（catalog 是静态的， facets 也是静态的）。
- **风险**：**低**（目录是只读静态数据）。注意：若未来支持"本地模型也进搜索"，需提供失效钩子。
- **回归验证**：
  1. 新测试：连续两次 `search(models, sq)`（同一 list 对象）→ 断言 `get_corpus(models) is get_corpus(models)`；
  2. 新测试：断言 `/api/search?q=x` 第二次响应的 `elapsed_ms` 不高于第一次（宽松断言，避免 CI 抖动）；
  3. 手工：搜索框连续输入，Observer 无重复 `Corpus` 构建日志。

---

### P1-7　`python/app/main.py:504-525` `import_model` 在 async 路由中同步哈希/拷贝大模型，阻塞事件循环

- **位置**：`main.py:504`（`async def import_model`）→ `:514` `import_local(...)`（同步；`importer.py:242` 会对整个文件/目录做 SHA-256，`:283-289` 做 `copytree`/`os.link`）。
- **问题**：导入一个几十 GB 的模型时，SHA-256 计算（+ 拷贝）会**完全占住 asyncio 事件循环**。期间 `/api/health`、`/api/download/{id}`、`/ws/*` 全部无响应——而 Electron 侧 `main.js:264` 的健康轮询和 `pollDownloadProgress`（`:1040-1062`）会误判 sidecar 死亡。
- **改法**：`await asyncio.to_thread(import_local, src, MODELS_DIR, max_size_bytes=...)`。与文件里已有的模式一致（`main.py:624/648/680/1022/1048` 都已用 `asyncio.to_thread`）。
- **风险**：**低**。`import_local` 内部已有 `threading.RLock`（`importer.py:248`）保护注册表读写；改为 to_thread 后并发安全性不变，只是不再阻塞 loop。注意：`_TokenBucket` 的 `take()` 必须在 `to_thread` **之前**完成（现状已满足）。
- **回归验证**：现有 `test_importer.py` / `test_importer_edge.py` / `test_concurrency.py` 全绿即可；新增一条：导入进行中 `GET /api/health` 仍能在 1s 内返回（可用大临时文件 + 短断言）。

---

### P1-8　`electron/main.js:636-642` `shell:openExternal` 未走 host 白名单（与 preload 注释不符）

- **位置**：`main.js:636-642`
  ```js
  ipcMain.handle("shell:openExternal", async (_e, url) => {
    assert(isString(url, 2048), "url: invalid");
    let u; try { u = new URL(url); } catch (_) { throw err("url: not a valid URL"); }
    assert(u.protocol === "https:" || u.protocol === "http:", "url: only http(s) allowed");
    await shell.openExternal(u.toString());      // ← 没有 isHostAllowed
  });
  ```
  而 `isHostAllowed`（`main.js:550-556`）与 `ALLOW_DEFAULT`（`:39`）**只为 `kevrai:start-download`（`:950`）服务**。
- **问题**：`preload.js:399` 的注释写着 *"Very tight allowlist at the bridge too; main re-validates."* —— **但 main 并没有 re-validate host**。preload 侧也只校验了 `^https?://`（`preload.js:401`）。结果是：renderer 里任何一处拼出 `data-action="open-external"` 的字符串（例如 `models.js:263` 的 `https://huggingface.co/${escapeHtml(m.repo)}`），都能打开**任意 http(s) 站点**。虽然 renderer 目前是自家代码，但这与项目自身的安全模型不一致（下载路径都做了白名单，外链却没有）。
- **改法**：在 `main.js:636-642` 加一行 `assert(isHostAllowed(u.hostname.toLowerCase(), loadSettingsSync()), 'url: host not allowed')`。若担心影响"打开 GitHub 仓库页"等体验，把 `ALLOW_DEFAULT` 扩到业务需要的 host 集合，并让 `renderer/modules/settings.js` 的 allowlist 编辑 UI 明确作用于两者。
- **风险**：**中低**。可能挡住某些当前能打开的链接（例如 `models.js:263` 若 `m.repo` 指向非 HF 域名）。建议先加白名单 + 加日志观察一个版本，再决定是否强制拒绝。
- **回归验证**：新测试（JS）：`openExternal("https://evil.example.com")` 抛错；`openExternal("https://huggingface.co/x")` 通过；手工点详情页"在 Hugging Face 查看"仍需正常打开。

---

### P1-9　`main.py:1064-1226` MNN 下载：无锁的跨线程全局 dict + fire-and-forget 线程

- **位置**：`main.py:1064-1077`（`_MNN_DL` 全局 dict）、`:1080-1081`、`:1089-1115`（worker）、`:1118-1180`（单文件下载）、`:1225`：
  ```python
  asyncio.get_running_loop().run_in_executor(None, _mnn_download_worker, entry, dest)
  ```
- **问题**：
  1. **无锁**：worker 在线程池里写 `_MNN_DL[...]`，`_mnn_dl_state()`（`:1080`）在事件循环里读 → 读到的可能是撕裂状态（如 `files_done` 已 ++ 但 `bytes_done` 未更新）；
  2. **Future 被丢弃**：`run_in_executor` 返回的 future 没有保存引用，异常**完全不可观测**（worker 内部 `except Exception` 只写进 dict，没人看）；
  3. **取消靠轮询全局 flag**：`_MNN_DL["cancel"]`（`:1104/1148/1167`），与 `Downloader`（`downloader.py:91` 用 `asyncio.Event`）两套机制并存；
  4. **单文件下载内联了第二套 HTTP 重试**（`:1118-1180`）——与 `Downloader` 功能重叠，`downloader.py` 里的 BUG-01 修复（`downloader.py:330-349` 关于 Range/206 的处理）**没有同步到这里**（`:1158-1162` 的处理逻辑不同）。
- **改法（最小改动）**：
  - 短期：给 `_MNN_DL` 配一个 `threading.Lock`，读写都走 `_mnn_dl_state_locked()`；保存 `run_in_executor` 的 Future 到模块级列表并加 `add_done_callback` 记录异常。
  - 中期（推荐）：让 MNN 多文件下载**复用 `Downloader`**——把 `list_mnn_files()` 得到的每个文件作为 `dl.start()` 的一个任务，进度由聚合器汇总。这样取消/续传/SHA/并发上限全部统一，也顺带消灭重复代码。
- **风险**：**中**。MNN 下载是用户高频路径。建议**先加锁 + 加异常回调（低风险）**，复用 Downloader 放到下一批。
- **回归验证**：并发压测：`POST /api/mnn/download` 后立刻高频 `GET /api/mnn/download` 轮询 500 次，断言返回的 `bytes_done <= bytes_total` 且 `files_done <= files_total`（现版本在竞态下可能出现 `bytes_done > bytes_total`）；取消后断言状态为 `cancelled` 且工作线程在 5s 内退出。

---

### P1-10　`python/app/main.py:1929-1961` / `:2100-2132` Agent 单例 + 全局 step_callback 在多会话下互相踩踏

- **位置**：
  - `main.py:1929` `_AGENT_SINGLETON: dict[str, Any] = {}`（模块级）；
  - `main.py:2100-2102` WS 里用 `class _FakeReq: app = websocket.app` 伪造 Request 去取 app.state（类型作弊）；
  - `main.py:2132` `agent.set_step_callback(_on_step)` + `:2149` `finally: agent.set_step_callback(None)`。
- **问题**：Agent 是**进程级单例**，`set_step_callback` 也是单例字段。两个浏览器窗口 / 一个 WS + 一个 HTTP `/api/agent/chat` 同时请求时：后设置的 callback 覆盖前者，先者的 `finally` 又把 callback 置空 → **步骤流事件丢失或串到另一个会话**。
- **改法**：
  1. 把 Agent 移到 `app.state.agent`（`_lifespan` 里创建），去掉模块级 `_AGENT_SINGLETON`（顺带消除测试间的全局污染）；
  2. `agent.run(...)` 增加 `step_callback` **参数**（而不是 setter）：`async def run(self, message, session_id, on_step=None)`；`set_step_callback` 保留但标记 deprecated；
  3. WS 里的 `_FakeReq` hack 随之消失（直接传 `websocket.app.state.agent`）。
- **风险**：**中**。涉及 `agent.py` 签名变更，但 `agent.py` 覆盖率 90%、测试充分。
- **回归验证**：新测试：两个 `asyncio.Task` 并发 `agent.run()` 且各自带 callback → 断言每个 callback 只收到自己那次 run 的步骤（当前实现会串）；现有 `test_v270_agent.py::TestAgentReAct::test_step_callback_called` 必须保持通过。

---

### P1-11　CI 覆盖率门槛（≥80%）与实测 57% 不符 —— 门槛形同虚设或 CI 已红

- **位置**：`.github/workflows/ci.yml`（`python` job 的 "Coverage gate (>= 80%)" 步骤）
- **实测**：
  ```
  $ python3 -m pytest tests/ -q --cov=app --cov-report=term-missing
  TOTAL                               5539   2365    57%
  421 passed in 59.00s

  $ # 按 ci.yml 同款脚本复算
  Coverage: 57.3% (3174.0/5539.0)
  ```
- **问题**：门槛写 80%，实测 57%，且脚本依赖 `coverage` 的**私有 API** `cov._analyze(fname)`（新版 coverage 可能变更/移除）。
- **改法**：
  1. 把门槛脚本换成官方支持的写法：`python -m coverage report --fail-under=80`（或 `pytest --cov=app --cov-fail-under=80`），去掉私有 API；
  2. **先把门槛设成"当前实测值 + 缓冲"**（例如 `--fail-under=60`），并在 PR 模板里要求"新增代码不得拉低整体覆盖率"，然后按 §5.5 的补测计划逐步抬到 70 → 80；
  3. 在 README 的 badge 里显示真实覆盖率，避免"声称 80% 实际 57%"的落差。
- **风险**：**低**（只改 CI）。但要注意：若 CI 当前其实是"红"的，这一步会让 CI 变绿——这是**正确**的结果。
- **回归验证**：本地 `coverage report --fail-under=60` 应通过；`--fail-under=58` 通过；`--fail-under=80` 应失败（证明门槛真的生效了）。

---

### P1-12　`python/app/downloader.py:238-246` httpx 客户端泄漏 + `:380-385` 尾部 fsync 代码失效

- **位置**：
  - `downloader.py:219-224` `aclose()` 关闭的是 `self._client`：
    ```python
    if self._client_owned and self._client is not None:
        await self._client.aclose()
    ```
  - 但真正创建的客户端存在 `self._own_client`（`:241-245`）。构造时 `self._client = client`（`:149`）默认为 `None` → **`aclose()` 永远什么都不关**，每次 `start()` 后残留的 TCP 连接池/线程不会被回收（`_lifespan` 在 `main.py:215-217` 调用了 `dl.aclose()`）。
  - `downloader.py:380-385`：
    ```python
    with partial.open(mode) as fh:
        ...
    # Final fsync
    try:
        fh.flush()          # ← fh 已关闭，抛 ValueError
        os.fsync(partial.open("rb").fileno())   # ← 打开的文件对象从未 close → fd 泄漏
    except (OSError, ValueError):
        pass
    ```
    这段"最终 fsync"**实际上从不生效**（`fh.flush()` 先抛异常），且每次下载泄漏一个文件描述符。
- **改法**：
  1. 统一客户端字段：删除 `self._own_client`，只保留 `self._client`，`_get_client()` 惰性创建并置 `self._client`；`aclose()` 逻辑不变即可生效；
  2. 删除 `:380-385` 整段（前面的 chunk 循环里已经有 `fh.flush()` + `os.fsync`，`:365-369`），或改为在 `with` 块内做最后一次 fsync；
  3. 顺带：`_tasks`（`:141`）永不清理 → 加一个终端状态任务的 LRU 淘汰（保留最近 200 条）。
- **风险**：**低**。`aclose` 修复后行为更正确；删除死代码不改变成功路径。
- **回归验证**：新测试：创建 Downloader → 发起一次下载 → `await dl.aclose()` → 断言 `dl._client.is_closed is True`；新测试：完成一次下载后 `len(os.listdir('/proc/self/fd'))` 不增长（Linux CI）。

---

### P1-13　Sidecar 崩溃重启后不重新探活、不通知 renderer，且重启预算永不复位

- **位置**：`electron/main.js:385-400`
  ```js
  sidecarProc.on("exit", (code, signal) => {
    sidecarReady = false;
    if (sidecarManualStop) return;
    if (sidecarRestartCount >= SIDECAR_RESTART_MAX) { /* 放弃 */ }
    const delay = Math.min(30_000, 1000 * Math.pow(2, sidecarRestartCount));
    sidecarRestartCount += 1;
    setTimeout(() => { try { startSidecar(); } catch (_) {} }, delay);
  });
  ```
- **问题**：
  1. 重启后**没有 `waitForSidecar()`**，也没有 `notifyRenderer("sidecar:health", ...)` → UI 的健康灯（`renderer/app.js:264-267` 轮询）可能恢复，但 `sidecar:health` 订阅者（`preload.js:414`）收不到事件，且若启动慢于 15s 会先闪红；
  2. `sidecarRestartCount` **只在 `relaunchAfterBootstrap()`（`main.js:342`）里复位**。正常使用中 sidecar 因偶发原因重启 3 次后，即使已稳定运行数小时，**计数仍为 3** → 下次崩溃直接放弃重启，只能重开 app；
  3. `startSidecar()` 返回值在 `setTimeout` 里被丢弃（`:398`），spawn 失败无从得知。
- **改法**：
  1. 抽出 `async function restartSidecar()`：spawn → `await waitForSidecar()` → `notifyRenderer("sidecar:health", {ok:true, info})`；失败则 `notifyRenderer("sidecar:down", {...})`；
  2. 引入"稳定运行复位"：sidecar 就绪且持续存活 > 5 分钟后 `sidecarRestartCount = 0`（用 `setTimeout` + 标记）；
  3. 把 `startSidecar()` 失败的分支（`:1097-1100` `app.quit()`）与重启路径统一。
- **风险**：**中**。改的是进程生命周期，改动后必须在真机（Win/Mac/Linux）各测一次"kill sidecar 进程"场景。
- **回归验证**：手工：启动 app → 任务管理器 kill `uvicorn` 子进程 → 观察 ① 自动重启 ② 健康灯由红转绿 ③ `sidecar:health` 事件被触发（renderer console）；连续 kill 3 次后等待 6 分钟再 kill 第 4 次 → 应仍能重启（当前会放弃）。

---

### P1-14　`main.py:1016` / `:1361` 路径越界校验用字符串前缀比较，可被"同前缀兄弟目录"绕过

- **位置**：
  - `main.py:1014-1019`（`/api/mnn/load`）：
    ```python
    allowed_roots = [Path(settings.resolved_model_dir()), APP_ROOT]
    ok = any(str(d).startswith(str(r)) for r in allowed_roots)
    ```
  - `main.py:1361`（`/api/convert/start`）：
    ```python
    if not str(dst_resolved).startswith(str(data_root)):
    ```
- **问题**：`str.startswith` 是**字符串**比较，`/data/models-evil` 会通过 `/data/models` 的检查。此外 `main.py:1009` 的 `d = Path(req.model_dir).expanduser()` **没有 `.resolve()`**，而 `:1016` 拿 `str(d)` 去比，软链接/相对路径可绕过。
- **改法**：统一用一个工具函数（放 `python/app/paths.py`）：
  ```python
  def is_within(child: Path, root: Path) -> bool:
      try:
          child.resolve().relative_to(root.resolve())
          return True
      except ValueError:
          return False
  ```
  两处都改用 `is_within()`，并在比较前一律 `.resolve()`。
- **风险**：**低**。收紧校验。注意 Windows 上大小写与 8.3 短名，`.resolve()` 已能处理大部分。
- **回归验证**：新测试（`test_security.py` 追加）：`model_dir = <model_dir>_evil` 的绝对路径 → 400；`<model_dir>/../x` → 400；`<model_dir>/sub` → 200。现有 `test_security.py`（16 项）保持通过。

---

### P1-15　`main.py:117-121` 目录加载失败被静默吞掉，退化为"空目录"继续运行

- **位置**：
  ```python
  try:
      CATALOG, ENGINES = load_catalog(CATALOG_DIR)
  except Exception:
      CATALOG, ENGINES = Catalog(version="0", models=[]), {}
  ```
- **问题**：`models.json` schema 校验失败、JSON 损坏、字段非法时，进程**照常启动**并对外提供一个"空市场"。用户看到的是"没有模型"，而不是"目录加载失败"。这是最贵的那类 bug（排障成本极高）。
- **改法**：
  1. `except Exception as e:` → `log.critical("catalog load failed", extra={"err": str(e), "dir": str(CATALOG_DIR)})`；
  2. 让 `/api/health`（`main.py:406-408`）返回 `catalog_ok: bool` 与 `catalog_error: str|null`，Electron 与 renderer 据此显示明确的错误横幅；
  3. **是否 fail-fast 待确认**：考虑到离线可用性，建议**不 fail-fast**，但必须可观测。
- **风险**：**低**。
- **回归验证**：新测试：monkeypatch `load_catalog` 抛异常 → 断言 `GET /api/health` 的 body 含 `catalog_ok: false` 且日志出现 `catalog load failed`。

---

### P2-16　`main.py:305-318` / `:104` 运行时猴子补丁

- **位置**：`main.py:104` `engines_module.is_installed = EngineManager.is_installed`；`main.py:305-318` 替换 `_JsonFormatter.format`。
- **问题**：前者为兼容旧调用方给模块打补丁，后者为注入 request_id 替换类方法。两者都让"代码里看到的"≠"运行时执行的"，静态分析（mypy/ruff）与 IDE 跳转失效。
- **改法**：
  1. `is_installed` 别名：在 `engines.py` 里显式定义 `def is_installed(engine_id, root=...)` 作为**正式公开函数**（内部调用 `EngineManager.is_installed`），导入方改为正常 import，`main.py:104` 删除；
  2. request_id：改为在 `_JsonFormatter.format` 内部直接读 `_RidToken.get()`（把 contextvar 定义移到 formatter 之前），删掉运行时替换。
- **风险**：**低**。
- **回归验证**：全量 pytest + 确认没有其它模块 import `engines.is_installed`（`grep -rn "is_installed" python/` 已确认仅 `engines.py` 内部与 `main.py:104`）。

---

### P2-17　`renderer/modules/environments.js:31` `el()` 的 `html` 属性是直连 innerHTML 的"后门"

- **位置**：`environments.js:25-39` 的 `el(tag, props, ...children)`：`else if (k === "html") e.innerHTML = props[k];`
- **问题**：当前仓库中**未发现** `html:` 的实际调用点（全仓 grep 无 `html:` 字面量传入），因此**不是现存漏洞**，但它是"最容易长出 XSS 的口子"——后续任何人传 `{html: serverData}` 就直接注入。
- **改法**：删除 `html` 分支（YAGNI），或改名为 `unsafeHTML` 并加注释 + ESLint 规则禁止在业务代码中调用。
- **风险**：**极低**（无调用点）。
- **回归验证**：`grep -rn "html:" renderer/` 为空 + 全量 `node --check`。

---

### P2-18　`python/app/runner.py` 为死代码（0% 覆盖、零引用）

- **位置**：`python/app/runner.py`（21 行，`spawn_llama_server`）
- **问题**：全仓 grep 无任何其他文件 import 或调用；`docs/ARCHITECTURE.md` 的依赖图里还把它列为活跃模块。
- **改法**：删除文件（配合 archive/分支保留），并从 `docs/ARCHITECTURE.md` 的依赖图中移除。若团队计划用它做 llama-server 托管，则在 `main.py` 中真正接入并补测试——二选一，**不要留悬空**。
- **风险**：**极低**。
- **回归验证**：删除后 `pytest` 仍 421 passed；`grep -rn "runner" python/` 仅剩 `downloader.py:428` 的局部函数名。

---

### P2-19　renderer 中 `escapeHtml`/`esc` 重复定义 10 份

- **位置**：`app.js:145`、`modules/agent.js:10`、`modules/downloads.js:140`、`modules/drama.js:9`、`modules/engines.js:129`、`modules/generation-wait.js:202`、`modules/hardware.js:8`、`modules/mnn.js:8`、`modules/models.js:321`、`modules/search.js:299`
- **问题**：10 份实现，其中 `app.js:146` / `downloads.js:141` / `generation-wait.js:203` 用 `String(s || "")`（`0`、`false` 会变空串），`hardware.js:9` / `agent.js:11` / `drama.js:10` / `mnn.js:9` 用 `String(s == null ? "" : s)`（更正确），`search.js:300` 用 `String(s ?? "")`——**行为不一致**，且未来修一个 XSS 边界要改 10 处。
- **改法**：新建 `renderer/modules/dom.js` 导出 `escapeHtml` / `escapeAttr` / `setText` / `el`，10 处改为 import。**不改调用点语义**，只换实现来源。这是"最小改动原则"下最安全的收敛方式（不引入框架、不改渲染方式）。
- **风险**：**低**。逐文件替换，每个文件替换后跑一次对应页面的手工冒烟。
- **回归验证**：`grep -c "function escapeHtml\|function esc(" renderer/` 应从 10 降到 0；手工遍历 8 个页面确认渲染无差异；建议同时给 `dom.js` 补一组纯函数单测（见 P2-14）。

---

### P2-20　文档与仓库卫生

| 项 | 位置 | 问题 | 改法 |
|---|---|---|---|
| README 测试数自相矛盾 | `README.md:20`(372) / `:34`(420) / `:43`(372) / `:91`(323) / `:132`(303) / `:205`(372) / `:265`(303) | 7 处数字，5 个不同值 | 改为**由 CI 生成的单一真源**（在 ci.yml 里 `pytest -q | tail -1` 写回 badge JSON），或直接改成"见 CI" |
| `docs/ARCHITECTURE.md` 过期 | 全文 272 行 | 标题仍是 **"Kevrai Studio"**（项目已更名 Kevrai Omni）；称 "models come from huggingface.co only"（现在是多镜像）；依赖图含死模块 `app.runner`；**0 处**提及 Agent / v2.7 | 随 §4 的 main.py 拆分同步重写；至少先修名称、删 runner、补 Agent 层 |
| 发布说明散落 | 根目录 6 个 `RELEASE_NOTES_*.md` + `RELEASE.md` | 根目录被 7 个发布文档污染；且缺 2.3.0 / 2.4.0 两版 | 全部移入 `docs/releases/`，按 `vX.Y.Z.md` 命名；根目录只留 `RELEASE.md` 作为索引；`README.md` 的"版本历史"改为链接 |
| `RELEASE.md` 过期 | `RELEASE.md:1` | 标题 "v1.0.0"，含占位 token `ghp_xxxx...` | 更新为 2.7.x runbook，或删除（内容已由 release.sh 覆盖） |
| 仓库体积 | `docs/kevrai-omni-promo.mp4` **15.9 MB**；`.git` 20 MB | 二进制进 git，clone 变慢 | 用 Git LFS 托管，或改为 README 外链；`git filter-repo` 清理历史（**需团队确认，会改历史**） |
| `.gitignore` | 无 `coverage.xml` / `.coverage` | 实测运行后产生了未跟踪的 `python/.coverage` | 补 `.coverage`、`coverage.xml`、`htmlcov/` |

---

## 3. 专项方案一：`main.py` 拆分

### 3.1 拆分边界选择

**结论：按「HTTP 适配层 / 领域路由分组」拆，不按 DDD 领域模块重划。**

理由（基于最小改动原则）：
- 现有领域模块（`catalog/engines/downloader/...`）已经下沉得不错，**不需要动**；
- `main.py` 真正的病是"**路由 + 编排 + 全局状态**混在一起"，把它们按路由前缀切到 `api/` 包即可，业务代码基本是**整段搬迁**，不是重写；
- 不引入 service 层抽象——那会要求同时改测试与调用方，风险远超收益。

### 3.2 目标结构

```
python/app/
├── main.py                 # 仅剩：app 工厂 + lifespan + 中间件 + include_router（目标 ≤ 250 行）
├── deps.py                 # NEW: 共享依赖与校验（_get_settings/_get_downloader/_get_engine_manager/
│                           #      _validate_model_id/_MODEL_ID_RE/_REPO_RE/_TokenBucket）
├── logging_setup.py        # NEW: _JsonFormatter + _configure_logging + request-id contextvar
├── paths.py                # NEW: is_within() 路径越界校验（P1-14）
├── media.py                # NEW: _media_to_local（P0-4 加固后）
├── state.py                # NEW: AppState（catalog/engines/settings/downloader/ltx/agent/corpus 的容器）
└── api/
    ├── __init__.py         # NEW: 汇总 routers（控制挂载顺序！）
    ├── meta.py             # /api/health, /api/categories, /api/progress
    ├── models.py           # /api/models*, /api/gguf-repos, /api/models/import
    ├── engines.py          # /api/engines*, /api/gpu
    ├── env.py              # /api/env/*
    ├── settings.py         # /api/settings (GET/PUT)
    ├── downloads.py        # /api/download/*, /ws/download/*, /api/sources/measure
    ├── hardware.py         # /api/hardware, /api/recommend
    ├── mnn.py              # /api/mnn/* (+ MNN 下载 worker 迁到 domain 层 mnn_downloads.py)
    ├── convert.py          # /api/convert/*
    ├── drama.py            # /api/drama/*
    ├── search.py           # /api/search*
    ├── ltx.py              # /api/ltx/*
    ├── agent.py            # /api/agent/*, /ws/agent/*
    └── openai_compat.py    # /v1/models, /v1/chat/completions
```

### 3.3 迁移顺序（**每一步都必须能独立通过全量测试**）

| 步 | 迁出内容 | 行数 | 关键约束 | 验证 |
|---|---|---:|---|---|
| S0 | 先建 `deps.py` / `logging_setup.py`（**原样搬运**，不改逻辑） | ~180 | 从 `main.py` **剪切**而非复制；`main.py` 改为 `from .deps import ...` | 路由清单快照测试通过（见 3.4） |
| S1 | `meta.py` + `models.py` | ~120 | **`/api/models/local` 必须在 `/api/models/{model_id}` 之前**——同文件内保持相对顺序即可 | 同上 + `/api/models/local` 返回 200 而非被 `{model_id}` 吞掉 |
| S2 | `engines.py` + `settings.py` + `env.py` | ~130 | — | 同上 |
| S3 | `downloads.py`（含 WS） | ~180 | WS 路由用 `router.websocket` | 同上 + 一次真实下载进度流 |
| S4 | `hardware.py` + `search.py` | ~70 | `_HW_CACHE` 迁到 `state.py` | 同上 |
| S5 | `mnn.py` + 抽 `mnn_downloads.py` | ~200 | 顺带修 P1-9（加锁 + 保存 Future） | 同上 + MNN 下载/取消 |
| S6 | `convert.py` + `drama.py` + `ltx.py` | ~200 | — | 同上 |
| S7 | `agent.py`（含 WS）+ `openai_compat.py` | ~330 | 顺带修 P0-2、P1-10 | 同上 + Agent 对话 |

**每步完成后**：`git commit`，跑 `pytest`（421 passed）+ 路由清单快照 diff 为空，才进入下一步。

### 3.4 如何保证不破坏现有 67 个 API 路径与行为

**核心手段：路由清单快照测试（在动第一行之前就先建）**

```python
# python/tests/test_route_manifest.py  (NEW, 第一步就写)
import json, pathlib
from app.main import app

def _manifest():
    return sorted(
        (getattr(r, "path", ""), tuple(sorted(getattr(r, "methods", []) or [])))
        for r in app.routes if getattr(r, "path", "").startswith(("/api", "/v1", "/ws"))
    )

def test_route_manifest_unchanged():
    expected = json.loads(pathlib.Path(__file__).with_name("route_manifest.json").read_text())
    assert _manifest() == [tuple(x) for x in expected]
```

流程：
1. **拆分前**：生成 `route_manifest.json`（67 条）并提交；
2. **每步拆分后**：跑该测试。只要 diff 非空，说明路由顺序/前缀被改坏了，**立即回滚该步**；
3. 若确需新增/删除路由，**必须同步更新 manifest 并在 PR 中说明**。

**补充保障：**
- 所有 `response_model` / 返回 dict 结构 **一个字段都不改**（只在新增字段，如 P1-15 的 `catalog_ok`）；
- `include_router` 的**挂载顺序**必须与当前 `main.py` 中的声明顺序一致（尤其 `models` 早于其它可能冲突的通配路由）；
- 现有 421 个测试中凡是走 `TestClient` 的（`test_v24_api.py` / `test_v241_api.py` / `test_security.py` / `test_smoke.py` / `test_download_start.py`）天然就是契约回归网，**不需要新写**；
- 拆分期间**禁止**同时做其它重构（比如改 Pydantic 模型字段），一次只动一件事。

---

## 4. 专项方案二：安全性加固清单

| # | 位置 | 现状 | 建议 | 优先级 |
|---|---|---|---|---|
| S-1 | `preload.js:300` | `assert` 未定义 | 本地定义 / 统一 `electron/validate.js` | **P0**（P0-1） |
| S-2 | `main.py:1615-1667` | SSRF + 任意本地文件读 + tmp 泄漏 | 白名单 host + `is_within` + 大小上限 + 临时目录上下文 | **P0**（P0-4） |
| S-3 | `main.js:636-642` | `openExternal` 无 host 白名单 | 复用 `isHostAllowed` | P1（P1-8） |
| S-4 | `main.py:1016` / `:1361` | 字符串前缀路径校验 | `is_within()`（P1-14） | P1 |
| S-5 | renderer `innerHTML` | 13 个文件共 ~70 处，`esc()` 已覆盖绝大多数 | ① 收敛 10 份重复 `esc` 到 `dom.js`（P2-19）；② 给 `dom.js` 补单测；③ 删 `environments.js:31` 的 `html` 后门（P2-17）；④ **不引入框架、不重写渲染层** | P2 |
| S-6 | `main.js:996-1002` `open-path` | 限制在 `<userData>/models` | **同时修对端**：Python 的模型根目录是 `default_data_root()/models`（`settings.py:21-32`），Electron 用的是 `app.getPath("userData")/models`（`main.js:998`）——**两个根不一致**，导致 `renderer/app.js:221` 的"定位"按钮大概率失败。建议以 sidecar 的 `resolved_model_dir()` 为唯一真源 | P1（**待确认**两个路径在目标平台的实际取值） |
| S-7 | IPC 通道面 | `preload.js` 暴露 ~95 个通道 | 建议加一条**静态检查**：扫描 `preload.js` 中所有 `invoke("...")` 的第一参数，与 `main.js` 中所有 `ipcMain.handle("...")` 求差集，双向都必须为空（防"preload 调了 main 没注册"和"main 注册了没人用") | P2 |
| S-8 | 依赖与密钥 | `.github/workflows/ci.yml` 用腾讯镜像；`scripts/smoke.sh:Step4` 有密钥扫描 | 现状良好。建议把 smoke 的密钥扫描**前移到 pre-commit**（`.pre-commit-config.yaml` 已有 local hook 先例） | P2 |

---

## 5. 专项方案三：健壮性 / 性能 / 测试 / 工程质量

### 5.1 健壮性

| 项 | 位置 | 建议 |
|---|---|---|
| 统一异常响应 | `main.py` 无全局 `@app.exception_handler` | 加 `HTTPException` / `RequestValidationError` / `Exception` 三个 handler，统一输出 `{"code":<http_status>, "message":..., "request_id":...}`，**并保留现有 `detail` 字段**以免破坏前端 |
| 统一 `log` 字段 | `main.py:263-286` | `getattr(locals().get("response"), "status_code", 0)` 这种写法（`:280`）在异常路径下取不到 response；改用显式变量 + `try/except/finally` 三段式 |
| 阻塞调用清单 | `main.py:514`（import）、`agent.py:336`（`router.chat`）、`main.py:1225`（已 to_thread） | 前两个改 `asyncio.to_thread` |
| WS 异常 | `main.py:878-909`（download）、`:2082-2154`（agent） | 当前只 catch `WebSocketDisconnect`。建议补 `asyncio.CancelledError` 与其它异常的 `close(code=1011)`；agent WS 的 `receive_json` 在收到非 JSON 时会抛异常并静默断开 → 应 catch 后发 `{"event":"error"}` 再继续 |
| 下载并发/取消 | `downloader.py` | 并发上限已有（`Semaphore`）；取消已有（`cancel_event`）。缺口是 `_tasks` 无界增长（P1-12） |
| 转换任务 | `converter.py:109` `_TASKS` | 同上，加 LRU 淘汰 |

### 5.2 性能

| 项 | 现状 | 建议 | 预期收益 |
|---|---|---|---|
| `catalog/models.json` | **159 KB / 5520 行 / 121 模型 / 30 引擎**，`main.py:118` 启动时一次性 `json.load` + pydantic 校验 | 现状**可接受**（159 KB 不算大，且有 mtime 缓存 `catalog.py:308-343`）。不建议引入数据库 | — |
| `GET /api/models`（`main.py:427-445`） | 每次请求对 121 个模型做 `model_dump()` + 字符串拼接过滤 | 启动时预计算 `app.state.models_dump`，请求只做 filter/sort | 消除 121 次 pydantic dump/请求 |
| `GET /api/search`（`main.py:1793-1794`） | 每次请求 `model_dump()` ×121 + **Corpus 全量重建**（P1-6）+ `compute_facets` 全目录遍历 | 三件事都在 `app.state` 预计算并复用 | 搜索响应显著下降，且随目录增长不再劣化 |
| `GET /api/recommend`（`main.py:940-941`） | 为校验 category 调用了 `categories()`（每次构造 9 个 dict） | 提为模块级常量 `CATEGORY_IDS` | 微小 |
| 前端渲染 | 已有 `virtual-grid.js`；`renderMnnPage` 每次切页全量重渲染（`app.js:171-177`） | 现状可接受。MNN 页 579 行建议拆分为 `mnn-engine.js` / `mnn-market.js` / `mnn-chat.js` / `mnn-convert.js` 四个模块（**纯搬迁**） | 可维护性 |
| 轮询 | `app.js:264` 15s 健康轮询；`mnn.js:57` 1.5s 下载轮询；`ltx.js:173` 1s 任务轮询 | 现状可接受；建议三处统一为一个 `renderer/modules/poller.js`（带 `start/stop/visibilitychange` 暂停），避免窗口隐藏后仍在轮询 | 省电/省 CPU |

### 5.3 前端工程质量

| 项 | 位置 | 建议 |
|---|---|---|
| 重复 `esc` ×10 | 见 P2-19 | 收敛到 `renderer/modules/dom.js` |
| 定时器清理 | `mnn.js:57`（`_pollTimer` 已清理，`:20`）、`mnn.js:469`（`_convertPollTimer` 已清理）、`app.js:264`（`healthTimer` **无清理路径**） | `app.js` 的 `healthTimer` 加 `beforeunload`/`destroy` 清理；切页时 `ltx.js:185` 已清理、`mnn.js` 已清理 |
| 类型标注 | renderer 全 JS，无 JSDoc | 只要求**新增的** `dom.js` / `poller.js` 写 JSDoc 类型；存量不动 |
| 死代码 | `wireModelGrid()`（`models.js:98-102`）函数体为空 | 标注为扩展点或删除 |
| 模块重复 | `ess` 之外，`$ = (s,r) => (r||document).querySelector(s)` 也在 8 个文件重复 | 一并收进 `dom.js` |

### 5.4 测试组织现状与重构建议

**现状（实测）**：28 个文件，367 个测试函数，421 个用例，**无 `conftest.py`**。

| 问题 | 证据 | 建议 |
|---|---|---|
| 无共享 fixture | `def client` 在 `test_download_start.py`、`test_security.py`、`test_smoke.py`、`test_v241_api.py`、`test_v24_api.py` **各定义一遍**（5 份）；13 个文件手写 `import sys` 做 path 处理 | 新建 `python/tests/conftest.py`，提供 `client`（`TestClient(app)`）、`tmp_settings`、`tmp_models_dir` 三个 fixture，5 处重复定义改为使用 |
| 按版本堆砌命名 | `test_v24_api.py`、`test_v241_api.py`、`test_v241_fixes.py`、`test_v260_catalog.py`、`test_v270_agent.py` | **不重命名已有文件**（避免丢失 git 追溯），改为：新测试一律按**被测模块**命名（`test_api_search.py` / `test_api_agent.py`）；在 `docs/` 维护一张"版本 → 文件"映射表；`test_v241_api.py` 与 `test_v241_fixes.py` 有**同名的** `test_settings_hf_token_roundtrip`（分别在 `:42` 和 `:90`），建议后者改名为 `test_settings_hf_token_persistence_roundtrip` 以消除歧义 |
| 覆盖率缺口 | `runner.py` 0% / `recommend.py` 7% / `drama.py` 12% / `mnn_runtime.py` 14% / `mnn_catalog.py` 19% / `converter.py` 20% / `main.py` 45% / `gpu.py` 47% | 见 §5.5 补测计划 |
| 断言偏弱 | `test_security.py` 等大量 `assert r.status_code == 200` | 逐步补充 body 结构断言（配合 §3.4 的快照思路） |
| 缺 JS 测试 | 仅有 `node --check`（语法） | 见 P2-14 |

### 5.5 覆盖率补测计划（按 ROI 排序）

| 优先级 | 模块 | 现覆盖 | 补测内容 | 预计新增用例 |
|---|---|---:|---|---:|
| 1 | `app/mnn_runtime.py` | 14% | 用 `unittest.mock` 伪造 `MNN.llm.Llm`，覆盖 `load_model/status/chat/chat_stream/chat_multimodal` 的成功与失败分支（不装真实 MNN） | ~25 |
| 2 | `app/converter.py` | 20% | mock `subprocess.Popen` 与 `shutil.which`，覆盖 6 个 `_worker_*` 的参数拼装与失败路径 | ~20 |
| 3 | `app/recommend.py` | 7% | 纯函数，喂固定 `hardware` dict 断言推荐结果/排序/过滤 | ~15 |
| 4 | `app/mnn_catalog.py` | 19% | mock `httpx` 返回，覆盖 `list_mnn_files` 的镜像回退与解析 | ~8 |
| 5 | `app/drama.py` | 12% | mock LLM 返回固定 JSON，覆盖 `generate_script/build_storyboard/render_plan` | ~15 |
| 6 | `app/main.py` | 45% | 随 §3 拆分**同步**为各 `api/*.py` 补路由级测试（拆分后每路由文件更容易测） | ~40 |
| 7 | `app/gpu.py` | 47% | mock `asyncio.create_subprocess_exec`，覆盖 nvidia-smi / rocm-smi / sysctl 各分支 | ~10 |

补完后预计整体可从 **57% → 70%+**，届时再把 CI 门槛抬到 70。

### 5.6 P2-14：引入轻量 JS 测试

- 建议：`node --test`（Node 22 内置，**零新依赖**，CI 已装 Node 22）。
- 覆盖范围（只测纯逻辑，不碰 DOM）：`dom.js` 的 `escapeHtml/escapeAttr`、`electron/validate.js` 的断言器、`settings.js` 的 `readForm` 白名单一致性、`preload ↔ main` 通道清单双向 diff（S-7）。
- 挂到 `package.json` 的 `test:js` 后面，不改 CI 结构。

---

## 6. 实施路线图

### 总览

```
批次 0（止血，0.5 天）──┬──► 批次 1（加固，2-3 天）──► 批次 2（拆分，5-8 天）
  P0-1 preload assert    │      P0-2 await 协程           main.py → api/
  P0-3/P0-5 HF Token     │      P1-8 openExternal          （每步一次 commit）
  P0-4 media 加固         │      P1-14 is_within
  P1-11 修 CI 门槛        │      P1-12 downloader 泄漏
                         └──► 批次 3（质量，3-5 天）
                                conftest + 补测 + docs 整理
```

### 批次 0 — 止血（建议 1 个 PR，可拆成 4 个 commit）

| 任务 | 文件 | 验收标准 |
|---|---|---|
| P0-1 | `electron/preload.js`（+ 可选新建 `electron/validate.js`） | `node --check` 通过；手工点 Agent 发送消息不再 `ReferenceError` |
| P0-3 + P0-5 | `renderer/modules/settings.js`、`electron/main.js`（`:150`）、`renderer/modules/api.js`、`electron/preload.js` | 设置页填 HF Token → 保存 → sidecar `GET /api/settings` 能读到该 token；`gated:true` 下载不再 422 |
| P0-4 | 新建 `python/app/media.py`，改 `main.py:1615-1667` | 新增 `test_media_security.py` ≥ 6 条全绿；现有 421 全绿 |
| P1-15 | `main.py:117-121` + `/api/health` | 目录加载失败时 `/api/health` 返回 `catalog_ok:false` |
| P1-11 | `.github/workflows/ci.yml` | `coverage report --fail-under=60` 通过；`--fail-under=80` 失败 |

**批次 0 依赖**：无。可立即开始。
**批次 0 出口**：`pytest` 421+ passed，CI 全绿，Agent 可用，gated 下载可配置。

### 批次 1 — 加固（依赖批次 0 完成）

| 任务 | 文件 | 验收标准 |
|---|---|---|
| P0-2 | `main.py:2001` | 无 `RuntimeWarning: coroutine was never awaited`；新断言 `isinstance(ctx.hardware_info, dict)` |
| P1-6 | `search.py:323-334` + `main.py:1793` | 新测试：同一 list 两次 `get_corpus` 命中缓存；搜索响应不劣化 |
| P1-7 | `main.py:514` | `asyncio.to_thread`；导入中 `/api/health` 仍响应 |
| P1-8 | `electron/main.js:636-642` | 非白名单 host 打开被拒 |
| P1-9（第一阶段） | `main.py:1064-1226` | 加锁；保存 Future；异常可被记录 |
| P1-12 | `downloader.py` | `aclose` 真的关掉 client；删死 fsync 段；`_tasks` LRU |
| P1-13 | `electron/main.js:385-400` | kill sidecar 能自动恢复；稳定运行后重启预算复位 |
| P1-14 | 新建 `python/app/paths.py`；改 `main.py:1016`/`:1361` | `test_security.py` 新增 3 条边界用例通过 |
| P1-10 | `main.py:1929-2154` + `agent/agent.py` | 并发两路 run 的 callback 不串 |

**批次 1 出口**：P0 全部关闭；`pytest` ≥ 421 passed；覆盖率 ≥ 58%（未下降）。

### 批次 2 — `main.py` 拆分（依赖批次 1；**严格按 §3.3 的 S0→S7 逐步提交**）

| 前置 | 必须先建 `tests/test_route_manifest.py` + `route_manifest.json`（67 条） |
|---|---|
| 顺序 | S0 基础设施 → S1 meta/models → S2 engines/settings/env → S3 downloads → S4 hardware/search → S5 mnn → S6 convert/drama/ltx → S7 agent/openai |
| 每步验收 | ① `route_manifest` diff 为空；② `pytest` 全绿；③ 手工点一遍该组路由对应的页面 |
| 出口 | `main.py` ≤ 250 行；`api/` 各文件 ≤ 250 行；67 路由零变更；覆盖率不下降 |

### 批次 3 — 工程质量与文档（可与批次 2 的 S5 之后并行）

| 任务 | 验收标准 |
|---|---|
| 建 `python/tests/conftest.py`，收敛 5 份 `def client` | `pytest` 仍全绿；重复定义归零 |
| §5.5 补测（优先 1-5 项） | 覆盖率 57% → ≥ 65% |
| P2-18 删 `runner.py` | `pytest` 仍全绿 |
| P2-19 建 `renderer/modules/dom.js`，收敛 10 份 `esc` | `grep` 归零；8 个页面手工冒烟 |
| P2-17 删 `environments.js:31` 的 `html` 后门 | `grep "html:"` 为空 |
| P2-16 去猴子补丁 | `pytest` 全绿 + mypy 无新增 |
| P2-20 文档整理：README 数字统一、`docs/releases/`、`ARCHITECTURE.md` 重写 | 文档与实际一致 |
| P2-14 引入 `node --test` | `npm run test:js` 覆盖 `dom.js` 等纯逻辑 |
| `.gitignore` 补 `.coverage` / `coverage.xml` | `git status` 干净 |

---

## 7. 待确认清单（我无法从代码推断、需要 Bullobis 拍板）

| # | 问题 | 为什么需要确认 |
|---|---|---|
| 1 | `docs/kevrai-omni-promo.mp4`（15.9 MB）是否可以用 Git LFS 或外链替换 | 清理历史会改写 git history，影响所有 fork |
| 2 | 是否计划保留 `python/app/runner.py`（`spawn_llama_server`）的功能 | 决定是删除还是接入 |
| 3 | CI 的 `python` job 当前实际是绿还是红 | 我本地按同款脚本算出 57.3% < 80%。若 CI 是绿的，说明门槛脚本已失效（需按 P1-11 修）；若是红的，说明长期被忽略 |
| 4 | Electron `userData/models`（`main.js:998`）与 Python `default_data_root()/models`（`settings.py:131-134`）在目标平台实际是否指向同一目录 | 决定 S-6 是"确认 bug"还是"我的推断有误"。**建议工程师先在 Win/Mac/Linux 各打印一次两个路径再定方案** |
| 5 | `shell:openExternal` 收紧白名单后，是否会影响"打开第三方模型主页"等现有体验 | 决定 `ALLOW_DEFAULT`（`main.js:39`）需要扩到哪些 host |
| 6 | `/v1/chat/completions` 是否已有外部 Agent（OpenClaw）依赖"传本地绝对路径"的用法 | 决定 P0-4 的本地路径分支是硬拒绝还是加 `allow_local_media_paths` 开关 |
| 7 | 是否接受在 renderer 引入哪怕一个 devDependency（如 `node --test` 是内置的，但 ESLint 不是） | 决定 S-7 的静态检查用什么实现 |
| 8 | 补全覆盖率的目标是 70% 还是维持 80% 门槛但长期豁免 | 决定批次 3 的投入规模 |

---

## 附录 A：本次审计使用的关键命令（可复现）

```bash
# 1. 全量测试
cd python && python -m pytest tests/ -q
#   → 421 passed in 59.00s

# 2. 覆盖率
cd python && python -m pytest tests/ -q --cov=app --cov-report=term-missing
#   → TOTAL 5539 stmts, 2365 miss, 57%

# 3. 按 ci.yml 同款脚本复算门槛
cd python && python -c "
import coverage
cov = coverage.Coverage(); cov.load()
total=covered=0.0
for f in cov.get_data().measured_files():
    if not f.startswith('$(pwd)'): continue
    a = cov._analyze(f); total += len(a.statements)
    covered += len([s for s in a.statements if s in a.executed])
print(f'{(covered/total*100):.1f}%')"
#   → 57.3%

# 4. assert 是否为 Node 全局量
printf 'console.log(typeof assert);\ntry{assert(true)}catch(e){console.log(e.constructor.name)}\n' > /tmp/t.js && node /tmp/t.js
#   → undefined / ReferenceError

# 5. 路由清点
grep -c '@app\.\(get\|post\|put\|delete\|websocket\)' python/app/main.py   # → 67

# 6. 目录统计
python -c "import json;m=json.load(open('catalog/models.json'));print(len(m['models']))"  # → 121

# 7. escape 助手重复度
grep -rn "function escapeHtml\|function esc(" renderer/    # → 10 处

# 8. 死代码核查
grep -rn "runner\|spawn_llama_server" --include="*.py" python/   # → 仅 runner.py 自身
```

## 附录 B：核心指标基线（供后续对比）

| 指标 | 基线值（v2.7.0 @ 5156e9c） |
|---|---|
| Python 源码行数（`python/app`） | 5,539 语句 / 约 8,900 物理行 |
| `main.py` 行数 / 路由数 | 2,154 / 67 |
| `electron/main.js` + `preload.js` | 1,150 + 421 = 1,571 |
| renderer JS | 约 3,500 |
| 测试用例数 / 通过 | 421 / 421 |
| 覆盖率（pytest-cov） | 57% |
| 覆盖率（ci.yml 脚本） | 57.3% |
| P0 问题数 | **5** |
| P1 问题数 | **10** |
| P2 问题数 | **5** |
