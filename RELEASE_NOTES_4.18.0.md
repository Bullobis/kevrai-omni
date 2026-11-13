# Kevrai Omni v4.18.0 源码更新

> 这是源码版本说明。v4.18.0 没有打包、没有创建 GitHub Release；最近一次已发布的安装包仍是 v4.17.9。

## Agent 工作区重构

- Agent 页改为单一会话壳：顶部标题 / 状态 / 会话操作，中间 transcript，底部 docked composer。
- 用户 turn 用左侧细线区分，不再用高饱和聊天气泡；助手回复保持正文，工具过程默认折叠为过程组。
- 空态改为内容型，只保留标题、说明和三个可执行入口：检测这台电脑、推荐可跑模型、查看本地模型。点击只填入输入框，不自动发送。
- 状态从填充式徽标改为 quiet status line（圆点 + 文本），基础模式 / 就绪状态使用语义色。
- 移除 Agent UI 内的 emoji 操作标签和 emoji 技能名，统一使用字母 glyph。
- 审批卡改为检查点：
  - 显示工具名与“批准后仅执行这一次”；
  - 参数默认折叠，可按需展开编辑；
  - 焦点在卡片时 Enter 批准，Esc 拒绝；
  - 批准 / 拒绝后按钮锁定。
- 技能库展开不再把 transcript 压成窄条，页面可增长并保留可用的会话区域。

## 本轮修复的真实问题

- 审批轮询此前每秒重建 `innerHTML`，会清空用户正在编辑的 JSON 参数；现在按 pending request id 集合判断，集合未变化时保留已挂载卡片。
- 追加消息时没有移除 `.agent-welcome`，导致空态残留在 transcript 顶部；现在追加任何消息都会移除空态。
- 技能库展开时会话区被压缩到不可用宽度；现在页面可增长，布局在 1360×768、1440×900 下均可用。
- 空态 starter 点击后正确填入 composer，并聚焦输入框，不自动发送。

## 参考文档

新增 `docs/REFERENCE-UPSTREAM.md`，记录实际下载的 Cherry Studio、Codex、DeepSeek Harness 仓库 HEAD、读到的关键源码路径与许可证边界。只借鉴信息架构、交互模型和设计 token 思路，不复制 Cherry 的 AGPL 源码。

## 验证

- `npm run test:js`：72 个 JS 文件语法通过。
- `npm run test:renderer`：441/441 通过。
- `npm run smoke`：9/9 步通过；pytest `2763 passed`。
- Agent 定向测试：34/34 通过，其中审批测试 7/7。
- 实机：1360×768、1440×900，浅色 / 深色主题，空态、有消息 transcript、技能库展开、审批卡预览均检查。

## 未包含

- 没有打包、没有发布、没有 GitHub Release。
- 模型市场目录与 20 条应用适配器没有在本版本逐条重跑；不把“目录存在”写成“本机已能跑”。
