# Kevrai-omni v4.17.0

本版聚焦"让生图和 Agent 看得见进度、让引擎真能装上"，并修掉一批实机测试里暴露的对接问题。

## 新功能

### 图片生成：采样进度实时可见（参考 Local Dream）
- 新增流式 WebSocket 通道 `/ws/comfyui/generate`：提交生图后，逐帧把 ComfyUI 的采样步数（`step/total`）推回界面，实时显示进度条和百分比，不再转圈干等。
- 连不上 ComfyUI 或流式通道时自动回退原来的同步等待，不会坏。

### Agent：工具执行过程实时可见（参考 Operit）
- ReAct 循环里工具一被调用就立即向前端推送 `tool_start` 事件，界面立刻显示"正在执行：xxx"，而不是等工具跑完才知道在做什么。

## 引擎安装修复

- **llama.cpp 现在真能装上了**：之前 catalog 里写的是 releases 列表页地址（HTML，被当成 zip 解压必然报 "File is not a zip file"）。安装时会实时查询最新 release，按当前操作系统自动挑选对应的 CPU 二进制资产（Linux `llama-bN-bin-ubuntu-x64.tar.gz`、Windows `llama-bN-bin-win-cpu-x64.zip`、macOS x64/arm64）。
- 引擎卸载失败现在会弹出具体错误，不再静默无反应。

## 稳定性与易用性

- **魔搭（ModelScope）不再被误隐藏**：源探测超时从 12s 放宽到 20s，国内冷启动较慢时也能正确探到，不会"次次跳过"魔搭。
- **检查更新报错不再打扰用户**：网络被拒（`ERR_NETWORK_ACCESS_DENIED`）等离线场景下静默处理，只有真正的错误才提示。
- 图片生成种子：留空或填 `-1` 表示随机种子（与主流生图软件一致），界面加了说明提示。

## 工程

- 清理 32 个已合并进 main 的陈旧分支。
- 全量后端测试 2588 项通过，前端 71 个脚本语法检查通过。

## 实机验证
- 在 Linux 上实机启动 sidecar 验证健康检查、新 WebSocket 握手、llama.cpp 资产解析、魔搭搜索均正常。
