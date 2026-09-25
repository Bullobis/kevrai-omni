# Kevrai Omni v3.3.1 — 关键修复：Agent 聊天恢复

发布日期：2026-09-25　·　上一版本：v3.3.0

本次为**关键修复版本**，无新功能、无破坏性变更。

## 修复

### 1. Agent 聊天 / 技能切换失败（关键）
- preload 中 `agentChat`、`agentToggleSkill` 调用了未定义的 `assert()`（此前只定义了
  `assertString/assertEnum/assertObject`），导致在 Electronron 渲染进程中**每次 Agent 对话
  都抛出 `assert is not defined`**。
- 已补回 `assert(cond, msg)` 助手；Agent 聊天与技能切换恢复正常。

### 2. 存活核验工具判定（开发/CI 工具，不影响应用运行时）
- 修复对 Range 探测返回 `206 Partial Content` 的有效文件误判 SUSPICIOUS；
  将 GitHub 官方资产主机 `release-assets.githubusercontent.com` 与 `ollama.com`
  纳入可信清单。

## 质量门（真实数字）
- Python 全量测试：全绿（以最终整合分支实跑为准）。
- 跨平台安装包由 release workflow 在 Linux / Windows / macOS 上分别构建。
