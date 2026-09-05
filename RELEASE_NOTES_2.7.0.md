# Kevrai Omni v2.7.0 — Kevrai Agent 通用 AI 助手

**发布日期**: 2026-09-05
**版本**: 2.7.0
**代号**: Agent

---

## 概述

v2.7.0 引入 **Kevrai Agent**——一个运行在本地 Python sidecar 内的通用 AI 助手，基于 ReAct（Reasoning + Acting）循环架构，用自然语言管理本地 AI 模型。用户可以直接对话："我的硬件能跑什么模型？"、"搜索音乐生成模型"、"推荐适合8GB显存的图像模型"，Agent 会自动调用工具完成检测、搜索、推荐和下载规划。

架构设计借鉴 OpenClaw 2.0（MIT 开源个人 AI Agent 框架，2026-09-01 发布）的 Gateway+Runtime 分离、本地持久记忆、可插拔工具、模型无关路由理念，但不完全照抄——Agent 深度集成 Kevrai Omni 现有子系统，无需额外网关进程。

---

## 新增功能

### 1. Kevrai Agent 核心引擎 (`python/app/agent/`)

- **ReAct 循环**: Thought → Action → Observation，最多 12 步迭代（`MAX_ITERATIONS=12`）
- **双模式推理**:
  - **LLM 模式**: 加载 MNN LLM 模型后，由模型生成思考和工具调用，支持复杂多步推理
  - **Rule-based 回退模式**: 未加载模型时自动启用，基于关键词匹配调用工具（硬件查询→check_hardware、搜索查询→search_models），零依赖可用
- **系统提示词**: 中文提示词，包含工具使用准则（推荐模型前必须先 check_hardware）、输出格式规范
- **Step callback**: 每步思考/动作/观察可通过回调实时推送（用于 WebSocket 流式和前端思考状态）

### 2. 11 个内置工具

| 工具 | 类别 | 功能 |
|---|---|---|
| `search_models` | catalog | 按关键词/类别搜索模型，支持中文关键词→类别自动映射 |
| `model_info` | catalog | 获取模型详细信息 |
| `recommend_models` | catalog | 基于硬件配置智能推荐模型 |
| `list_installed` | catalog | 列出已安装的本地模型 |
| `list_categories` | catalog | 列出所有模型类别 |
| `check_hardware` | system | 检测 CPU/GPU/VRAM/RAM/磁盘，返回摘要 |
| `list_engines` | system | 列出 AI 引擎及安装状态 |
| `download_model` | system | 生成下载计划（不直接启动，避免绕过 UI 队列） |
| `generate_text` | system | 调用已加载的 MNN LLM 生成文本 |
| `get_preferences` | system | 读取用户偏好 |
| `set_preference` | system | 设置用户偏好 |

### 3. SQLite 持久记忆 (`python/app/agent/memory.py`)

- **sessions 表**: 会话元数据（ID、创建时间、消息数）
- **messages 表**: 消息记录（角色、内容、时间戳、工具调用元数据）
- **preferences 表**: 用户偏好键值存储
- **task_history 表**: 任务执行历史
- 数据库路径: `APP_ROOT/agent/memory.sqlite3`

### 4. 完整 API 层

**HTTP 端点** (`python/app/main.py`):
- `GET /api/agent/status` — Agent 状态（LLM 就绪/模式/工具数）
- `GET /api/agent/tools` — 工具列表
- `POST /api/agent/chat` — 非流式对话（含 steps 摘要、tools_used）
- `GET /api/agent/sessions` — 会话列表
- `GET /api/agent/sessions/{id}/messages` — 会话消息
- `DELETE /api/agent/sessions/{id}` — 删除会话
- `GET /api/agent/preferences` — 读取偏好
- `PUT /api/agent/preferences` — 设置偏好

**WebSocket**:
- `WS /ws/agent/{session_id}` — 实时流式 step 事件（event: step/final/error）

**与 OpenClaw 等外部 Agent 的互操作**:
- 项目已有 `/v1/models`、`/v1/chat/completions` OpenAI 兼容端点（含 SSE 流式、多模态 content）
- 外部 Agent 框架（OpenClaw 等）可直接通过这些端点调用 Kevrai 本地模型
- Kevrai Agent 本身是独立的本地 Agent 层，与外部 Agent 互补

### 5. 前端对话面板 (`renderer/modules/agent.js`)

- 聊天式 UI：用户/助手消息气泡、工具调用标签
- 思考状态指示（spinner + 当前工具名）
- 会话下拉切换 + 新建会话按钮
- Enter 发送 / Shift+Enter 换行
- 模式徽章（LLM 就绪 / 规则模式）
- 暗色主题适配，响应式布局（移动端优化）
- 侧边栏新增「🤖 AI Agent」标签页

### 6. Electron 桥接层

- `electron/preload.js`: 新增 6 个 Agent 方法（agentStatus/agentChat/agentSessions/agentSessionMessages/agentGetPreferences/agentSetPreference）
- `electron/main.js`: 新增 6 个 IPC handler，通过 sidecarFetch 转发到 Python sidecar
- `renderer/modules/api.js`: 新增对应 API 封装

---

## 架构设计

### 与 OpenClaw 的关系

| 维度 | OpenClaw 2.0 | Kevrai Agent |
|---|---|---|
| 协议 | MIT | Kevrai Omni Community License v1.0 |
| 语言 | TypeScript / Swift | Python (sidecar) + JS (前端) |
| 架构 | Gateway (控制面) + Agent Runtime (执行面) | 单进程 sidecar 内 ReAct 循环 |
| 记忆 | Markdown / SQLite | SQLite (4 张表) |
| 工具 | ClawHub 5700+ 可插拔 Skills | 11 个内置工具，包装 Kevrai 子系统 |
| 模型 | 模型无关路由 (OpenAI 兼容) | MNN 本地 LLM + rule-based 回退 |
| 部署 | 多渠道接入 (CLI/桌面/移动端) | Electron 桌面应用内嵌 |
| 定位 | 通用个人 Agent 框架 | 本地 AI 模型管理专用 Agent |

**设计决策**: 不完全照抄 OpenClaw 的多进程 Gateway 架构，因为 Kevrai Omni 已经是 Electron + Python sidecar 双进程架构，Agent 直接嵌入 sidecar 可减少网络跳转和延迟。工具系统不采用远程 Skill 市场，而是直接包装现有 catalog/hardware/download/engine 子系统，确保工具调用的可靠性和数据一致性。

---

## 测试

### 新增测试 (`python/tests/test_v270_agent.py`)

48 项测试，覆盖：
- **ToolRegistry** (8): 注册、执行、参数校验、未知工具、重复注册
- **parse/extract** (5): 两种工具调用格式解析、最终答案提取
- **AgentMemory** (10): 会话创建/查询/删除、消息追加/分页、偏好读写、任务历史
- **ModelRouter** (2): 未就绪状态、is_ready/chat 接口
- **CatalogTools** (6): 搜索（英文/中文/类别）、模型详情、推荐、已安装列表、类别列表
- **SystemTools** (6): 硬件检测（正常/异常路径）、引擎列表、下载计划、文本生成、偏好
- **AgentRuleBased** (6): 硬件查询触发 check_hardware、搜索查询触发 search_models、默认引导、空消息、多轮会话、工具记录
- **AgentReAct** (8): MockModelRouter 测试完整 ReAct 循环（思考→工具→观察→答案）、多步迭代、工具错误处理、最大迭代数、记忆持久化

### 全量测试

```
420 passed in 76.10s
```

### 实机测试

- uvicorn sidecar 启动正常，`/api/health` 返回 version 2.7.0
- `/api/agent/status` 返回 `{llm_ready: false, mode: "rule_based", tool_count: 11}`
- `/api/agent/tools` 返回全部 11 个工具
- `POST /api/agent/chat` 硬件查询 → 触发 check_hardware，返回硬件摘要
- `POST /api/agent/chat` 中文搜索"音乐生成模型" → 触发 search_models，返回 8 个音频模型
- 会话创建/列表/消息加载正常
- 偏好读写正常（SQLite 持久化）

### 冒烟测试

`bash scripts/smoke.sh` 全绿（含 Python pytest、JS node --check、目录不变量、版本一致性）。

---

## 版本号变更

| 文件 | 旧值 | 新值 |
|---|---|---|
| `python/app/__init__.py` | 2.6.0 | 2.7.0 |
| `package.json` | 2.6.0 | 2.7.0 |
| `package-lock.json` | 2.6.0 | 2.7.0 |
| `catalog/models.json` (顶层 version) | 2.6.0 | 2.7.0 |
| `catalog/engines.json` (顶层 version) | 2.6.0 | 2.7.0 |

---

## 文件清单

### 新增
- `python/app/agent/__init__.py` — 包导出
- `python/app/agent/agent.py` — 核心 ReAct Agent（~450 行）
- `python/app/agent/memory.py` — SQLite 持久记忆
- `python/app/agent/model_router.py` — MNN 模型路由封装
- `python/app/agent/tool_registry.py` — 工具注册表/解析器
- `python/app/agent/tools/__init__.py` — 工具包导出
- `python/app/agent/tools/catalog_tools.py` — 5 个 catalog 工具
- `python/app/agent/tools/system_tools.py` — 6 个 system 工具
- `python/tests/test_v270_agent.py` — 48 项测试
- `renderer/modules/agent.js` — 前端对话面板
- `RELEASE_NOTES_2.7.0.md` — 本文件

### 修改
- `python/app/main.py` — 末尾追加 ~300 行 Agent API（9 HTTP + 1 WS）
- `renderer/index.html` — 新增「AI Agent」侧边栏按钮和 pane
- `renderer/app.js` — 导入并懒加载 initAgent
- `renderer/modules/api.js` — 新增 6 个 Agent API 方法
- `renderer/styles.css` — 新增 Agent 面板样式（~80 行）
- `electron/preload.js` — 新增 6 个 Agent 桥接方法
- `electron/main.js` — 新增 6 个 Agent IPC handler
- `python/app/__init__.py` — 版本号 2.7.0
- `package.json` / `package-lock.json` — 版本号 2.7.0
- `catalog/models.json` / `catalog/engines.json` — 顶层 version 2.7.0
- `python/tests/test_v260_catalog.py` — 版本断言更新为 2.7.0
- `README.md` — 新增 v2.7.0 亮点段，tagline 加入 Agent

---

## 已知限制

1. **LLM 模式需手动加载 MNN 模型**: Agent 不会自动下载和加载 LLM 模型，用户需先在「MNN 引擎」页加载模型。未加载时自动回退 rule-based 模式。
2. **WebSocket 流式为实验性**: `/ws/agent/{session_id}` 端点已实现，但前端当前使用非流式 POST 接口。流式 UI 将在后续版本完善。
3. **工具调用不可直接启动下载**: `download_model` 工具返回下载计划而非直接启动下载，避免绕过 UI 下载队列和进度跟踪。用户需在 UI 中确认后启动。
4. **沙箱环境无独显**: 实机测试在无 GPU 沙箱中进行，VRAM=0。在有 NVIDIA/AMD GPU 的机器上，check_hardware 会正确检测 VRAM 并影响推荐结果。

---

## 后续计划

- WebSocket 流式前端 UI（实时显示每步思考和工具调用）
- Agent 自动加载 MNN 模型（检测到已下载 LLM 时自动加载）
- 更多工具：模型转换、引擎安装、环境管理
- Agent 技能系统（用户可自定义工具链）
- 与 OpenClaw 的深度互操作（Kevrai 作为 OpenClaw 的本地模型 Provider）
