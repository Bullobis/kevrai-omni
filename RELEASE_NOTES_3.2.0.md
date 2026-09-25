# Kevrai Omni v3.2.0 — UX 整合 + Remotion 滚动演示 + 发布加固

## 主要变化

- 设置与导航：标题栏设置齿轮、`Ctrl/Cmd + ,` 快捷键、设置三段分组、导航当前状态与悬停提示。
- 详情体验：详情栏新增关闭按钮；窄窗口下以右侧抽屉呈现，小屏弹窗全屏显示。
- Agent 首次使用：新增空状态、建议问题卡片，点击建议自动填入输入框。
- Remotion：新增隔离 `video/` 工程和 7 秒滚动演示；GitHub Actions 每 6 小时刷新 `rolling-demo` Release 资产。
- Electron 安全：权限请求默认拒绝；CSP 根据 sidecar host/port 动态生成；继续保留上下文隔离、预加载白名单和导航限制。
- 打包加固：Windows/Linux 打包排除测试、虚拟环境、缓存和构建产物。
- 主线整合：合入 v3.1.0 的 MNN spawn 子进程隔离、资源泄漏修复、真实 sidecar 冒烟和自维护工作流。

## 验证结果

- JavaScript：39 个 tracked JS 文件 `node --check` 通过。
- Renderer：27 个 Node 渲染层单元测试通过。
- Ruff：All checks passed。
- Mypy（当前 Python 3.12 基线）：Success, no issues found in 42 source files。
- Python：1085 passed, 1 warning。
- Electron UI 冒烟：主界面、设置三段结构、Agent 空状态、建议问题填入、命令面板均通过。
- Windows zip：压缩完整性通过，关键运行文件存在，未包含测试/虚拟环境/缓存。
- Linux AppImage：免 FUSE 抽取后关键运行文件存在。
- Linux deb：包元数据与关键运行文件校验通过。

## 下载资产

- Windows：`Kevrai-Omni-3.2.0-x64.zip`（便携包；当前构建环境无 Wine，NSIS 安装器需在 Windows 或具备 Wine 的环境补充）。
- Linux：`Kevrai-Omni-3.2.0-x86_64.AppImage`、`Kevrai-Omni-3.2.0-amd64.deb`。
- 更新元数据：`latest-linux.yml`。
- 校验文件：`SHA256SUMS.txt`。

## SHA256

```text
2c40821b4d72035fa27238ca0f68cf3079b26264f43cf6ce567778a1e76acc4e  Kevrai-Omni-3.2.0-x64.zip
73b32584cfa06a509fefbfd6e35eea8eff684190d5ee703a4fe51720da876150  Kevrai-Omni-3.2.0-x86_64.AppImage
f26873ba677027ae8a6d7b8d5ec2cf613fbec306f93f2be42d0eed56641709ea  Kevrai-Omni-3.2.0-amd64.deb
6da7586e527a1813848133d7022179ceaf00ae41047622a7462648cbb4210bef  latest-linux.yml
```

## 说明

当前环境没有 Authenticode 签名材料，Windows 资产为未签名便携包；下载后可依据本文件中的 SHA256 校验完整性。
