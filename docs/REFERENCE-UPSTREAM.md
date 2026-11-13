# 上游开源项目对照记录

这份记录只记录**实际下载并读过的源码**，不引用二手介绍。三个仓库放在项目外：

```
/home/user/Doubao/chats/38453556993272066/references/
├── cherry-studio        # CherryHQ/cherry-studio, 89ed7aa
├── codex                # openai/codex, 69231e1
└── deepseek-harness     # deepseek-ai/deepseek-harness, 39163dd3
```

下载命令使用 `git clone --depth 1 --filter=blob:none --sparse`，再按目录 `git sparse-checkout set`，因此本地是真实工作树，不是网页摘要。

## 许可证边界

| 项目 | 许可证 | 本项目的使用方式 |
|---|---|---|
| Cherry Studio | AGPL-3.0 | 只借鉴信息架构、交互模式和设计原则；不复制其源码。 |
| Codex | Apache-2.0 | 可参考协议与组件边界；当前未直接复制 Rust/TUI 代码。 |
| DeepSeek Harness | MIT | 可参考插件、会话与审批的分层方式；当前未直接复制其包代码。 |

Kevrai Omni 自己的许可证与这三个项目不同。以下改动都是按这些项目的**结构原则**重写现有原生实现，不是整段搬运。

## 实际读到的关键实现

### Cherry Studio

- `src/renderer/components/chat/shell/ConversationShell.tsx`
  - 一个会话只有一个壳：`relative flex h-full min-h-0 flex-1 overflow-hidden`。
  - 顶部栏、中央 transcript、右侧 pane、overlay 分层，不把页面装饰成另一套窗口。
  - 顶栏是 shell 的一部分，不重复造第二套页面容器。
- `src/renderer/components/chat/primitives/EmptyState.tsx`
  - 空态是 `flex h-full w-full flex-col items-center justify-center gap-4`。
  - 由图标、标题、描述、可选操作组成，没有大幅插画或营销文案。
  - `compact` 尺寸为 40，普通尺寸为 56；本项目空态视觉标记用 38px，避免抢内容。
- `src/renderer/components/chat/primitives/ErrorState.tsx`
  - 错误态用统一的 Alert，而不是散落的 toast 或裸文本。
- `packages/ui/src/components/primitives` / `composites`
  - 组件按 primitives / composites 分层；页面只消费共享组件。
- `packages/ui/src/styles/tokens`
  - 颜色、间距、圆角由 token 驱动；`DESIGN.md` 要求 neutral first、semantic color、surface hierarchy、content before decoration、禁止页面随手硬编码颜色。

### Codex

- `codex-rs/tui/src/chatwidget.rs` 顶部注释写清了它的模型：
  - committed transcript cells（已完成的 `HistoryCell`）+ 一个可原地变化的 in-flight active cell。
  - 工具调用合并为一个过程组，不把每个 token/工具事件都变成第二条聊天流。
  - 底部只有一个 “task running” 指示器，由 `agent_turn_running` 和 `mcp_startup_status` 共同推导。
- `codex-rs/app-server/README.md`
  - `item/commandExecution/requestApproval` 有唯一 `approvalId`，只接受 `accept`、`decline`、`cancel`。
  - 审批是明确检查点，不是通用 toast；会话级/持久级批准不能替代单次批准。
- `codex-rs/tui/styles.md`
  - header bold、primary default、secondary 用语义样式、key hint 单独表达；成功/错误使用语义色，不随意上自定义前景色。

### DeepSeek Harness

- `packages/client/ui-chat/README.zh.md`
  - “工作步骤展示模式控制过程显隐，不隐藏 Assistant 回复。”
  - 轮次过程折叠、分组渲染、滚动归属分离；已完成轮次与进行中轮次不是同一层。
  - 运行状态和过程行共享节奏，避免重复进度指示。
- `packages/client/ui-approval/README.zh.md`
  - 聚焦审批详情后 Enter 批准、Esc 拒绝；输入框和输入法保留自己的按键。
  - 键盘与指针共用同一个 pending request 锁；已撤销/替换的请求不能再次作答。
  - 工具审批默认是单次；持久授权必须额外显式操作。
- `packages/boot/plugin-manager/README.zh.md` 与 `apps/`、`packages/`
  - “everything is a plugin”，但插件管理本身有 profile、lock、安装状态、失败恢复和明确的 `restart-required` 语义。
  - 安装失败要恢复 `package.json` / lockfile，不能留下半安装状态。

## 已落到本项目

### Agent 工作区（`renderer/styles/agent-workspace.css`、`renderer/modules/agent*.js`）

- 把 Agent 从“聊天气泡 + 浮动按钮 + emoji 标签”改成 **transcript-first 工作记录**：
  - 一个页面壳，顶部是标题、状态、会话操作；
  - 中间是单一 conversation surface，消息按 turn 排列，用户 turn 用左侧细线而不是彩色气泡；
  - 工具过程放在可折叠的 `<details>` 中，默认收起，最终回答始终可见；
  - 底部是 docked composer，含键盘提示、token 数、发送/停止。
- 状态从填充色 badge 改为 **quiet status line**：圆点 + 文本，就绪/基础模式用语义色，不再整块高饱和。
- 审批从裸卡片改成 **checkpoint**：
  - 标题“等待你的确认”，工具名、单次授权范围；
  - 参数默认折叠，需要时展开编辑；
  - Enter（卡片聚焦时）批准，Esc 拒绝；
  - 轮询请求集合不变时不再重建 DOM，避免每秒清空用户正在编辑的参数。
- 移除 Agent UI 内的 emoji 标签/按钮（`🗑`、`↻`、`⧉`、`⏸️`、`✅`、`✋` 等），技能名前的 emoji 也剥离，统一用字母 glyph + 语义色。
- 打开技能库时不再把会话区压成不可用的一条缝；页面改为可增长并保留可用 transcript 高度。

### 尚未落地的部分

- Codex app-server 的 JSON-RPC approval replay / `expectedRevision` 冲突语义没有在本项目 IPC 层照搬；当前审批锁仍由 Agent 后端的 pending request 集合承担。
- DeepSeek Harness 的 profile/plugin manager 的完整安装回滚、lockfile 恢复、HMR 语义没有整体移植；插件页仍使用现有 Git/目录导入能力。
- Cherry 的 AGPL 组件代码没有复制；只把 shell、empty state、token 分层原则映射到现有 CSS/JS。

## 后续可继续对照的点

1. 把审批请求改成 **单一 pending request 锁**，避免同一轮多个请求被并发点击；当前 UI 已按 id 分组，但后端需要确认串行语义。
2. 给 Agent transcript 增加 **stable turn key + 滚动锚点恢复**，对齐 DeepSeek 的 scroll ownership，而不是每次追加都无条件滚到底。
3. 插件页参考 DeepSeek `plugin-manager`，补“安装前 inspect / 失败恢复 / restart-required”状态，不把外部包安装伪装成一次成功。
4. 模型适配器继续按“引擎能力 + 格式转换”拆分，避免所有模型都落到 llama.cpp / MNN 两个实现。
