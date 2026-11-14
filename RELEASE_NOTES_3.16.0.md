# Kevrai Omni v3.16.0 — 语音识别引擎（Faster Whisper）

## 主要变化

### 新增语音识别能力
- 新增 **Faster Whisper** 引擎（CTranslate2 后端），在 CPU 与 CUDA 下进行语音转写，相比原版 Whisper 推理更快、占用显存更少。
- 侧边栏新增「语音转写」工作台：选择音频文件、模型与语言后即可转写，支持 99 种语言、VAD 静音过滤、词级时间戳。
- 输出格式包含纯文本、JSON、详细 JSON、SRT 与 VTT，便于生成字幕或对接其他流程。
- 提供与 OpenAI 兼容的音频接口：`/v1/audio/transcriptions`（转写）与 `/v1/audio/translations`（翻译为英文）。

### 模型市场
- 模型数量由 v3.15.0 的 238 个扩充至 **240 个**；引擎数量由 32 个增加至 **33 个**。
- 新增语音模型：
  - **Faster Whisper Large v3**：多语言语音识别与翻译（CTranslate2 格式）。
  - **Faster Whisper Large v3 Turbo**：速度更快、体积更小的版本。

### 安装与依赖
- 引擎随模型市场一键安装；安装前可查看所需显存、内存与磁盘空间。
- 固定了与该引擎匹配的音频解码依赖版本，避免新版本接口变动导致的兼容问题。

## 验证
- Python 后端全量测试通过（1533 项，较上一版本新增 30 项语音相关测试）。
- JavaScript 语法检查通过（47 个文件）。
- 冒烟测试通过；在真实桌面环境中确认引擎就绪并完成语音转写。

## 安装
- Linux：`.AppImage` 与 `.deb`（amd64）。
- macOS：arm64 与 x64 的 `.dmg`。
- Windows：`.exe` 安装包与 `.zip`。
