# Kevrai Omni v3.8.0 — 模型市场扩充与端侧多模态

## 主要变化

### 模型市场
- 模型数量由 v3.7.2 的约 190 个扩充至 **207 个**，覆盖 LLM、视觉、音频、TTS、视频、3D、超分等类别。
- 为 54 个 GGUF 模型补全逐量化信息（Q3 / Q4 / Q5 / Q6 / Q8 / F16 的文件体积、所需显存与推荐档位）。
- 新增小尺寸 Qwen3 系列、QwQ-32B、Nex N2.5 mini/Pro、Kolibri-1、FLUX.2-klein、Z-Image-Turbo、Breeze-TTS-2、VoxCPM2、Gemma 4 Edge E2B/E4B 等。

### 功能
- 安装模型前可选择版本 / 量化；GGUF 支持先选分支或标签、再选量化。
- Gemma 4 Edge E2B/E4B 端侧多模态（文本 + 图像 + 音频）。
- 新增说话人分离（pyannote）与命名实体识别（GLiNER 2.5）能力。
- 对话支持单条消息删除与用户消息编辑后重发。

### 修复
- 修正清华 PyPI 镜像的失效前缀与若干失效来源链接。
- 深色模式、自定义标题栏窗口按钮、应用图标等显示问题修复。

## 验证
- Python 后端全量测试通过（1434 项）。
- JavaScript 语法检查通过（46 个文件）。
- 冒烟测试通过；在 Xvfb 下实机启动核对模型市场、详情面板与窗口显示。

## 安装
- Linux：提供 `.AppImage` 与 `.deb`（amd64）。
- macOS：提供 arm64 与 x64 的 `.dmg`。
- Windows：提供 `.exe` 安装包与 `.zip`。
