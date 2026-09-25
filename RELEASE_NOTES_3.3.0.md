# Kevrai Omni v3.3.0 — Nova UX 整合 + Remotion 滚动演示 + 发布加固

发布日期：2026-09-25　·　上一版本：v3.2.0

## 主要变化

- 设置与导航：标题栏设置齿轮、`Ctrl/Cmd + ,` 快捷键、设置三段分组、导航当前状态与悬停提示。
- 详情体验：详情栏新增关闭按钮；窄窗口下以右侧抽屉呈现，小屏弹窗全屏显示。
- Agent 首次使用：新增空状态、建议问题卡片，点击建议自动填入输入框。
- Remotion：新增隔离 `video/` 工程和 7 秒滚动演示；GitHub Actions 每 6 小时刷新 `rolling-demo` Release 资产。
- Electron 安全：权限请求默认拒绝；CSP 根据 sidecar host/port 动态生成；继续保留上下文隔离、预加载白名单和导航限制。
- 打包加固：Windows/Linux 打包排除测试、虚拟环境、缓存和构建产物。
- 主线整合：合入 v3.2.0 的 GGUF 并发枚举与 TTL 缓存、命令面板/主题/数据目录改进，以及 v3.1.0 的 MNN spawn 子进程隔离和资源泄漏修复。

## 验证结果

- JavaScript：37 个 tracked JS 文件 `node --check` 通过。
- Renderer：27 个 Node 渲染层单元测试通过。
- Ruff：All checks passed。
- Mypy（当前 Python 3.12 基线）：Success, no issues found in 42 source files。
- Python：1085 passed, 1 warning。
- Electron UI 冒烟：主界面、设置三段结构、Agent 空状态均通过。
- Windows zip：压缩完整性通过，关键运行文件存在，未包含测试/虚拟环境/缓存。
- Linux AppImage：免 FUSE 抽取后关键运行文件存在。
- Linux deb：包元数据与关键运行文件校验通过。

## 下载资产

- Windows：`Kevrai-Omni-3.3.0-x64.zip`（便携包；当前构建环境无 Wine，NSIS 安装器需在 Windows 或具备 Wine 的环境补充）。
- Linux：`Kevrai-Omni-3.3.0-x86_64.AppImage`、`Kevrai-Omni-3.3.0-amd64.deb`。
- 更新元数据：`latest-linux.yml`。
- 校验文件：`SHA256SUMS.txt`。

## SHA256

```text
ce5f149c4bb82953f4daabbce754ba63af1c1a7bec6e067466b54c40c4b908f7  Kevrai-Omni-3.3.0-x64.zip
6c3743f7bd154d275f048e3fb208e48831eb748945d58c0feb5dbc59f94f4178  Kevrai-Omni-3.3.0-x86_64.AppImage
2eee8c2eda8cf2d7084b4297db031b5ccbfab96b83d9dc82da3bcfa09dba7f04  Kevrai-Omni-3.3.0-amd64.deb
918c8599f2d79d2b0ac3fcd90301f75be705002367d954b962f7ddb628a5ed36  latest-linux.yml
```

## 说明

当前环境没有 Authenticode 签名材料，Windows 资产为未签名便携包；下载后可依据本文件中的 SHA256 校验完整性。
