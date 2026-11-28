# Claw Code → Kevrai Agent 参考清单

> 基线：`ultraworkers/claw-code`，commit `0492eb3aa54312eab5ca338b5e7a5d554f86a648`，tag `v0.1.16`（2026-11-27）。
> 本地位置：`references/claw-code`。完整工作树，非 sparse；`git fetch --unshallow` 后含完整历史。
> 许可：MIT。本文只记录**结构和约束**，不复制 Rust 源码；如后续真要复用代码片段，需在 Kevrai 内保留 MIT 版权声明。

## 0. 结论

Claw Code 是一个 **coding CLI harness**，Kevrai Agent 是 **本地 AI 工作站助手**。两者不应该整体合并。

值得带走的是它的**不变量和边界设计**，不是 57 个工具、Rust 运行时或 CLI 表面。

最终建议：

- **P0 先做 5 件事**：文件工作区工具、工具级权限与写操作强制确认、pair-safe 上下文压缩、会话级工作区绑定、工具结果完整性。
- **P1 再做 3 件事**：只读子 agent、会话 fork/steer、项目记忆字符预算。
- **P2 按需**：Trident 三段压缩、provider 重试/能力报告。
- **不搬**：57 个工具、bash/PowerShell、MCP 生命周期、插件市场、Team/Cron、Worker 终端控制面、ACP、Rust CLI/TUI。

### 0.1 PHILOSOPHY 的提醒

`PHILOSOPHY.md` 明确说：**代码是证据，真正的产品教训是背后的协作系统**。

它强调的不是“一个更聪明的单 agent”，而是：

- 人给方向，agent 执行
- 计划、执行、审查、重试分开
- 通知路由放在 agent 上下文之外，避免状态日志吃掉上下文
- 多个 agent 协作时，要有明确的交接和收敛机制

这直接约束 Kevrai 的落地方式：

- 不要把长进度日志塞进模型上下文，进度应由现有 Agent 事件/UI 承载
- 子 agent 不是“再开一个聊天”，而是有明确角色、工具白名单和返回格式
- 并行 worker 是后话；先把单 agent 的文件边界、审批、压缩做稳

## 1. 源码基线

| 项目 | 数值 |
|---|---|
| 工作树文件 | 432 个（不含 `.git`） |
| Rust 源文件 | 121 个 |
| Rust 代码行 | 133,212 行 |
| Rust crate | 12 个 |
| 工作树大小 | 约 16 MB；含完整 `.git` 约 40 MB |
| 主 CLI | `rust/crates/rusty-claude-cli/src/main.rs`，约 787 KB |
| 工具注册表 | `rust/crates/tools/src/lib.rs`，`mvp_tool_specs()` 共 57 个工具 |

阅读顺序建议：

1. `README.md` / `PHILOSOPHY.md`：先确认它的自我定位
2. `rust/crates/runtime/src/conversation.rs`：工具循环
3. `rust/crates/runtime/src/permissions.rs`：权限模型
4. `rust/crates/runtime/src/compact.rs`：上下文压缩
5. `rust/crates/tools/src/lib.rs` + `runtime/src/file_ops.rs`：工具与路径边界
6. `rust/crates/tools/src/lib.rs` 的 `Agent`：子 agent
7. `rust/crates/runtime/src/session.rs` / `session_control.rs`：会话状态
8. `rust/crates/api/src/providers/`：模型适配
9. 各模块 `#[cfg(test)]`：不变量测试

## 2. 阅读地图

| 主题 | 关键文件 / 符号 | 读到的机制 | 对 Kevrai 的价值 |
|---|---|---|---|
| 工具循环 | `runtime/src/conversation.rs` → `run_turn` | 每个 assistant 轮先落库；提取所有 `ToolUse`；逐个执行并写入 `ToolResult`；工具执行完再进入下一轮 | 高：补 Kevrai 的工具结果完整性 |
| 中断处理 | `conversation.rs` → `run_turn` 的 interrupted 分支 | 每个未执行的 pending tool 也合成 `ToolResult`，内容为 `Interrupted by user...` | 高：避免取消后留下非法历史 |
| 权限等级 | `runtime/src/permissions.rs` → `PermissionMode` / `covers` | `read-only` < `workspace-write` < `danger-full-access`；`prompt` 永远问，`allow` 永远不问 | 高：给 Kevrai 的工具加“所需权限” |
| 权限执行 | `runtime/src/permission_enforcer.rs` → `check_file_write` / `check_bash` | 文件写入按工作区边界分类；bash 按命令是否只读分类；无 prompter 时自动拒绝 | 高：路径与命令分类 |
| 工具规格 | `tools/src/lib.rs` → `ToolSpec` / `mvp_tool_specs` | 每个工具带 `required_permission` 和 JSON Schema | 高：Kevrai 的 `Tool` 缺这个字段 |
| 路径校验 | `runtime/src/file_ops.rs` → `validate_workspace_boundary` / `resolves_within` | 处理 `..`、symlink、缺失父目录、Windows 大小写/8.3 名称 | 高：不能只做字符串前缀判断 |
| 压缩 | `runtime/src/compact.rs` → `CompactionConfig` / `compact_session` | 默认保留最近 4 条、估算上限 10,000 token；边界回退，避免拆 tool pair | 高：Kevrai 当前只有单条裁剪 |
| 自动压缩 | `conversation.rs` → `maybe_auto_compact` | 累计 input token 超阈值后，在 assistant 轮之后压缩 | 中高：Kevrai 可按 scratchpad 字符预算触发 |
| 压缩后健康检查 | `conversation.rs` → `run_session_health_probe` | 压缩过的会话先做一次非破坏性工具探测，失败则要求新会话 | 中：可作为 Kevrai 压缩后的自检 |
| 子 agent | `tools/src/lib.rs` → `Agent` / `execute_agent_with_spawn` / `allowed_tools_for_subagent` | 独立 `Session::new()`、独立 runtime、工具白名单、后台线程、manifest/输出文件 | 中高：Kevrai 可做只读子 agent |
| 子 agent 边界 | `SubagentToolExecutor` | 白名单外的工具在执行前直接拒绝 | 高：白名单必须是执行器边界，不是提示词 |
| 会话工作区 | `runtime/src/session.rs` → `Session.workspace_root` | 每个会话绑定 worktree，避免并行 lane 写错目录 | 高：Kevrai 现在是全局 `ToolContext` |
| 会话 fork/steer | `session_control.rs` → `SessionStore` / `fork_session`；`conversation.rs` → `append_queued_steer` | 可从历史 fork 分支；运行中可插入 steering 消息 | 中：适合以后做“分叉试验/中途改方向” |
| Worker 生命周期 | `runtime/src/worker_boot.rs` → `WorkerRegistry` / `WorkerStatus` | `Spawning` / `TrustRequired` / `ToolPermissionRequired` / `ReadyForPrompt` / `Running` / `Finished`；检测 trust 提示和 prompt 误投递 | 低中：终端 worker 专用，不是 Kevrai 的通用调度器 |
| 提示词预算 | `runtime/src/prompt.rs` | 单文件 4,000 字符、总 12,000 字符、git diff 50,000 字符上限 | 中：Kevrai 若加载项目记忆，需要硬上限 |
| provider | `api/src/providers/openai_compat.rs` / `providers/mod.rs` | 重试、`Retry-After`、请求体大小上限、Ollama/OpenAI/xAI/DashScope 能力报告 | 低中：Kevrai 主要是本地 MNN/Transformers |
| 三阶段压缩 | `runtime/src/trident.rs` | supersede 过期文件读、collapse 工具链、cluster 相似消息 | 低：复杂度高，先不做 |

## 3. 可借鉴清单

### P0-1 文件工作区工具

**源码依据**

- `tools/src/lib.rs`：`read_file` / `write_file` / `edit_file` / `glob_search` / `grep_search`
- `runtime/src/file_ops.rs`：`validate_workspace_boundary`、`resolves_within`
- 读取上限 10 MB；忽略 `.git`、`node_modules`、`target`、`dist` 等目录

**Kevrai 落点**

- 新增 `python/app/agent/tools/workspace_tools.py`
- 新增内置技能 `coding_workspace`，默认关闭，用户在技能库中手动启用
- 工作区固定为 `<DATA_ROOT>/agent/workspace`，不开放任意路径选择
- 工具集：`read_file`、`list_dir`、`search_text`、`write_file`、`edit_file`、`edit_lines`
- 执行分三档：`none` / `test_runner`（白名单）/ `approval_shell`（逐条确认、无隔离）；沙箱 Shell 暂不做

**必须有的边界**

- 绝对路径、`..`、symlink escape 全部拒绝
- 写文件允许父目录不存在，但父目录必须在工作区内
- 读文件拒绝二进制、超过大小上限、工作区外路径
- 所有错误返回 `ok: false` 的 tool result，不抛出到 Agent 循环

**验收**

- 工作区外读取/写入被拒绝
- `../../` 与 symlink 逃逸被拒绝
- 工作区内新建目录/文件成功
- 超过 10 MB 的文件被拒绝
- 技能关闭时工具不在注册表中

---

### P0-2 工具级权限与写操作强制确认

**源码依据**

- `permissions.rs`：`PermissionMode` 五种模式；`covers()` 明确 `prompt` 永远问、`allow` 永远不问
- `permission_enforcer.rs`：`check_file_write` 区分只读/工作区写/全权限；无 prompter 时拒绝
- `tools/src/lib.rs`：`classify_file_path_permission` / `classify_read_path_permission` 按实际路径动态判定

**Kevrai 落点**

- `tool_registry.py` 的 `Tool` 增加 `required_permission`（`read-only` / `workspace-write` / `danger-full-access`）
- `approval.py` 的 `gate` 增加 `force_prompt` 或 `requires_confirmation`
- 写工具注册时标记 `required_permission="workspace-write"` 且 `requires_confirmation=True`
- 保留 Kevrai 现有 `auto` / `confirm` / `deny`；不要直接引入 Claw 的五模式 UI

**语义**

- 全局 `auto` 下，`write_file` / `edit_file` / `edit_lines` 仍然必须弹确认
- 全局 `deny` 下，写工具直接拒绝，不弹确认
- 用户显式把该工具设为 `auto` 时，才允许跳过确认
- `read-only` 作为预设：写工具直接 `deny`

**验收**

- `auto` + 写工具 → 有 pending 请求
- `deny` + 写工具 → 不产生 pending 请求，返回拒绝结果
- 编辑参数后执行的是编辑后的参数
- `read-only` 下写工具不可执行
- 现有 71 个 Agent 测试不回归

---

### P0-3 pair-safe 上下文压缩

**源码依据**

- `compact.rs`：`preserve_recent_messages=4`、`max_estimated_tokens=10_000`
- 压缩边界从尾部回退，直到不把 `ToolUse` 和 `ToolResult` 拆开
- `conversation.rs`：`maybe_auto_compact` 在 assistant 轮后触发
- 压缩后 `run_session_health_probe`

**Kevrai 现状**

- `history.py` 只有 `content[:…]` 的头尾裁剪，默认 700 字符
- `agent.py` 历史只取最近 10 条
- 单条 observation 上限 2,000 字符
- 没有 scratchpad 总量预算，也没有压缩后的健康检查

**Kevrai 落点**

- 新增 `python/app/agent/compact.py`，但**按 Kevrai 的 text scratchpad 适配**，不要照搬 `Session.messages`
- 保留最近 4 组 `Action/Observation`
- 旧步骤生成确定性摘要：已用工具、关键文件、最近用户请求、未完成事项
- 压缩后检查：没有孤立 observation、没有超过单条上限、总字符数低于预算

**验收**

- 压缩后最后一条 observation 不孤立
- 重复压缩不会产生“摘要套摘要”
- 总字符数下降且仍保留最近任务上下文
- 压缩后 Agent 还能继续调用工具

---

### P0-4 会话级工作区绑定

**源码依据**

- `session.rs`：`Session.workspace_root` 绑定 worktree
- `session_control.rs`：`SessionStore` 持有 `workspace_root`，可在不同工作区建立独立会话
- 原因：全局 session store 并行时会写错 CWD

**Kevrai 现状**

- `_get_agent()` 创建单例 Agent
- `ToolContext` 是全局对象，没有 `workspace_root`
- 会话只有 SQLite 消息，没有工作区字段

**Kevrai 落点**

- `AgentSession` 增加 `workspace_root`
- `ToolContext` 增加 `workspace_root`，由每次 `run(session_id)` 注入
- 文件工具只读 `ctx.workspace_root`，不读进程 CWD
- 会话恢复时保留 workspace_root

**验收**

- 两个 session 指向两个 workspace，互不写入对方
- 恢复会话后仍使用原 workspace
- 工作区变更需要显式新会话，不静默切换

---

### P0-5 工具调用/结果完整性

**源码依据**

- `conversation.rs`：每个 `pending_tool_uses` 都要写入 `tool_result`
- 中断发生在工具前/工具后，都会合成错误 result
- 不允许出现“只有 tool_use、没有 tool_result”的历史

**Kevrai 现状**

- 当前是同步 ReAct 文本循环，`registry.execute` 总返回 dict，所以暂时不会出现结构化的缺失
- 但一旦加异步工具、子 agent、原生 tool calling，这个不变量必须显式化

**Kevrai 落点**

- 在 `Agent` 里加 `_execute_tool_safely`：异常、取消、未知工具都转换成一个错误 observation
- 在 session/step 记录中检查“每个 action 都有 observation”
- 为未来结构化 tool call 预留 pair id

**验收**

- 工具抛异常，循环仍能继续或体面结束
- 取消后不出现孤立 action
- 未知工具返回可读错误，不崩溃

## 4. P1 / P2

### P1-1 只读子 agent

源码：`Agent` 工具、`execute_agent_with_spawn`、`allowed_tools_for_subagent`、`SubagentToolExecutor`。

可借鉴：

- 独立 `Session::new()`
- 独立 runtime
- 明确工具白名单
- 输出 manifest + 最终结果
- 最大迭代 32

**不要照搬的关键点**

Claw Code 的 `agent_permission_policy()` 把子 agent 的活动权限设为 `DangerFullAccess`，实际边界是工具白名单。**白名单不是沙箱**。Kevrai 如果做子 agent，必须：

- 活动权限用 `read-only`
- 只注册 `read_file` / `list_dir` / `search_text`
- 不允许递归派生
- 不允许写文件
- 结果只返回 JSON，不回传完整子会话

### P1-2 会话 fork / 运行中 steer

源码：`session_control.rs` 的 `fork_session`、`conversation.rs` 的 `append_queued_steer`。

适合以后做：

- “从当前会话分叉一个试验分支”
- 长任务运行中插入一句新指令，不打断当前工具
- 子 agent 完成后把摘要回注父会话

当前 Kevrai 的取消是协作式，不做 preemptive interrupt；这块可以后置。

### P1-3 项目记忆字符预算

源码：`prompt.rs` 的三个常量：

- 单文件 4,000 字符
- 总指令 12,000 字符
- git diff 50,000 字符

Kevrai 目前没有项目级 `CLAUDE.md` / `AGENTS.md` 注入。如果以后加，必须同时加上限和可见的截断标记，不能把整份 README 塞进系统提示。

### P2-1 Trident 三段压缩

源码：`trident.rs`

- supersede：同一文件旧读被新写覆盖后，删除旧读
- collapse：连续工具链折叠成摘要
- cluster：相似消息聚类

价值高，但复杂度和误删风险都高。**先做 P0-3 的单层 pair-safe 压缩，再评估 Trident。**

### P2-2 provider 重试与能力报告

源码：`api/src/providers/openai_compat.rs`、`providers/mod.rs`

- 指数退避、`Retry-After`、最大重试 8 次
- 请求体大小上限：OpenAI 100MB、xAI 50MB、DashScope 6MB
- provider 能力报告：tool_calls / streaming / prompt_cache / reasoning 等

Kevrai 当前是本地 MNN/Transformers，不需要马上引入。只有新增远程 provider 时才有意义。

## 5. 不建议照搬

| 项目 | 原因 |
|---|---|
| 57 个工具全量 | Kevrai 已有 35 个领域工具；全量搬会稀释技能库和权限面 |
| bash / PowerShell | 没有 OS 级沙箱；路径守卫不能替代进程隔离 |
| TeamCreate / Cron | `team_cron_registry` 主要是登记与生命周期，不是并行调度器；Kevrai 不应为了“看起来多 agent”而引入 |
| Worker* 终端控制面 | `worker_boot.rs` 是 tmux/终端 worker 的启动状态机，包含 trust 提示和 prompt 误投递恢复；与 Kevrai 的进程模型不匹配 |
| 插件市场 / MCP 生命周期 | Kevrai 已有自己的插件与 MCP 入口，不应再叠一套 |
| ACP / Zed JSON-RPC | Claw Code 自己也明确尚未真正提供 ACP daemon |
| Rust CLI/TUI | 与 Kevrai Electron + Python sidecar 的产品形态不同 |
| 原生 tool calling | Kevrai 当前本地小模型对 JSON Schema 的依从性未验证；先保持 ReAct 文本循环 |
| 完整 Session JSONL | Kevrai 已有 SQLite 会话；只在文件工作区、fork 等局部借鉴 |
| Trident 三阶段 | 先做 P0-3，避免一开始就引入高误删风险 |

## 6. 与 Kevrai 现状对账

| 能力 | Kevrai 现状 | Claw Code | 结论 |
|---|---|---|---|
| 工具循环 | `agent.py` ReAct，12 轮 | 结构化 tool blocks，可配置迭代 | 保留 Kevrai 循环，不重写 |
| 工具审批 | 三档 UI：全部确认 / 自动确认 / 无需确认；内部另有 deny，单工具策略，60s 超时 | 五模式 + 动态路径分类 + prompter | 补 `required_permission` 和写操作强制确认 |
| 上下文压缩 | `history.py` 单条裁剪；无总预算 | pair-safe session compaction | 补 scratchpad 预算与边界保护 |
| 文件工具 | 无 | read/write/edit/glob/grep | P0 新增，默认关闭 |
| 工作区隔离 | 无，`ToolContext` 全局 | `Session.workspace_root` | P0 增加 session/workspace 绑定 |
| 子 agent | 无 | `Agent` + 白名单 + manifest | P1 只读子 agent，不照搬 DangerFullAccess |
| 会话 fork | 无 | `fork_session` | P1 按需 |
| 项目记忆 | 无 `CLAUDE.md` 注入 | 4k/12k/50k 字符上限 | P1 若加则带上限 |
| 远程 provider | 无，本地 MNN/Transformers | Anthropic/OpenAI/xAI/DashScope | P2 按需，不先做 |
| 工具数量 | 43 个 / 11 技能（`coding_workspace` 默认关闭） | 57 个 | 不追求数量对齐 |

## 7. 建议的落地顺序

1. **P0-1 + P0-4**：先有工作区和会话绑定，否则文件工具没有安全边界。
2. **P0-2**：给写工具加 required_permission 和强制确认，再开放给用户。
3. **P0-3**：加 scratchpad 预算与 pair-safe 压缩，防止长任务把本地小模型上下文打爆。
4. **P0-5**：补工具异常/取消/未知工具的统一结果。
5. **P1-1**：只读子 agent，独立 registry，不递归。
6. **P1-2/P1-3**：按产品需要做 fork/steer 和项目记忆预算。
7. **P2**：Trident、远程 provider、更多会话控制。

## 8. 验收清单

- [ ] 工作区外路径、`..`、symlink escape 全部拒绝
- [ ] `read-only` 下写工具不可执行
- [x] `auto` 下写工具仍弹确认，`deny` 下不弹确认，`no_confirm` 显式跳过强制确认
- [ ] 工具异常、取消、未知工具都产生可读 tool result
- [ ] 压缩后没有孤立 observation / tool pair
- [ ] 压缩后总字符数下降，最近 4 组工具调用保留
- [ ] 两个 session 使用不同 workspace 时不串写
- [x] 技能关闭时 workspace 工具不在注册表中
- [ ] Kevrai 现有 Agent 测试 + 新增工作区/权限/压缩测试全部通过
- [x] 不默认启用 shell；test_runner / approval_shell 都不承诺 OS 级沙箱

## 9. 已知风险与未决问题

- **Kevrai 的 ReAct 是文本格式**，不是结构化 tool blocks。Claw Code 的 pair 守卫不能直接套到消息数组，必须先映射到 Kevrai 的 `AgentStep` / scratchpad。
- **Kevrai Agent 是单例**，`ToolContext` 当前全局。做 session workspace 绑定会触及 `_get_agent()`、`run()`、`memory` 和工具上下文，不能只改文件工具。
- **`skill_hub` 导入的技能是 guidance-only**，不能直接承载可执行文件工具；`coding_workspace` 必须是内置技能。
- **审批默认 `auto`**。如果直接把写工具注册进去而不加强制确认，会绕过用户预期；这是上线前必须解决的阻塞项。
- **Claw Code 的 `Agent` 权限是 `DangerFullAccess` + 白名单**。白名单是功能边界，不是安全沙箱；不能把这个结论复制到 Kevrai。
- **Claw Code README 明确说它不是严肃生产项目**，自称“museum exhibit”。参考它的不变量，不要把它当依赖或质量基线。
- **路径守卫不是 OS 沙箱**。即使做到 symlink/`..` 防护，也不要在文档里承诺“安全隔离”。

## 10. Kevrai 已落地（2026-11-28）

参考清单中的 P0 没有整体照搬，先落了最小可用的一层：

- `python/app/agent/workspace.py`：固定工作区、路径守卫、三档执行模式、命令执行（超时/输出上限/进程组 kill/环境变量清洗/审计）。
- `python/app/agent/tools/workspace_tools.py`：`read_file`、`list_dir`、`search_text`、`write_file`、`edit_file`、`edit_lines`、`run_test`、`run_command`。
- `coding_workspace` 内置技能默认关闭；用户开启后才把工具注册进 Agent。
- `Tool.requires_confirmation` + `ApprovalManager.gate(force_prompt=True)`：写文件/编辑文件/完整 Shell 在全局 `auto` 下也必须确认；显式 per-tool `auto` 仍可覆盖。
- `GET/PUT /api/agent/execution-mode`，以及 Electron IPC：执行模式只能由用户侧读写，模型没有修改工具。
- 执行状态条常驻 Agent 顶部；选择面板列出三档真实代价；完整 Shell 需勾选风险确认后才能保存。

明确没做：沙箱 Shell、OS 级隔离、并行子 agent、上下文压缩。`test_runner` 仍会执行项目代码，`approval_shell` 也没有网络/进程/文件系统隔离。
