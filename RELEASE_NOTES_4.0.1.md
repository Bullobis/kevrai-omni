# Kevrai Omni v4.0.1 — 小白友好的环境准备页

## 主要变化

### 环境准备页（Bootstrap）— 真正的"内置更新"

之前 sidecar（Python 推理后端）启动失败时，会弹出一个技术性的英文错误框（"Python sidecar failed to start. Reason: sidecar health timeout..."），小白用户根本看不懂怎么办。

**v4.0.1 起，所有 sidecar 启动失败都改为跳到软件内环境准备页**：

- **依赖缺失 / Python 未装 / 端口占用 / 解释器崩溃 / health timeout** 一律进入引导页
- 引导页永远可见"一键安装运行依赖"按钮（只要有 Python）
- Windows 独有"一键安装 Python 环境"按钮（自动下载官方便携版到用户目录）
- stderr 日志默认展开在下方，用户能直接看到实际失败原因
- 按钮文案更清晰："安装/重装运行依赖" / "一键安装 Python 环境（仅 Windows）"

### 其他

- `electron-builder.yml` copyright 行同步升级到 **Kevrai Omni Community License v2.5**
- `electron-builder.yml` publish 目标：`Bullobis/kevrai-omni`（保持主账号发布渠道）

## 版本
- 3.24.0 → 4.0.1
- 基于 commit `3b5c8de`（Bullobis/kevrai-omni main，含 v3.24.0 + 4 个后续修复）

## 说明
- 版本号 4.x 是发布渠道演进（协议 v2.5 + 引导页升级）的语义标记，无破坏性 API 变更
- 老版本 v3.6.0 ~ v3.24.0 保留完整，可作为 tag 查历史
