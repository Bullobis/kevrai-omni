# Kevrai Omni v2.6.0 发布说明

发布日期：2026-09-04

## 主题：MiniMax-Music3 接入、2026 年 5 月后新模型上架、全目录事实核验

本次更新先对 `catalog/models.json` / `engines.json` 中**每一个** HuggingFace / GitHub
仓库做了联网实查（存在性、许可证、体积、创建/更新时间、gated 状态），再据此修正错误、
补齐新模型，避免重蹈 v2.4.1「不存在的 Hailuo-H3 幽灵仓库」覆辙。

## 1. MiniMax-Music3 全家桶（核验后接入）
官方权重 `MiniMaxAI/MiniMax-Music3` 于 2026-08 开放，本目录共 8 个条目，全部实查可达：

- `minimax-music3`：官方全精度，架构 **8B Global LLM + 0.6B Local LLM + 2.4B Flow
  Matching + 123M Flow-VAE**；32kHz/16bit 立体声 WAV、单首最长 5 分钟、5000 token
  歌词/描述；全精度 24GB+ 显存，CPU offload 约 22GB，分层流式最低 8GB（需 CUDA）
- `minimax-music3-comfyui`：Comfy-Org 官方重打包（社区最主流，HF 下载量 71 万+）
- `minimax-music3-gguf`：社区 GGUF 量化（38 万+ 下载，多档量化按需下载）
- `minimax-music3-turbo-fp8`：Turbo FP8 加速版；`minimax-music3-w4a8-comfyui`：W4A8 量化
- `minimax-music3-lora-fiona-crapple`：风格 LoRA；`minimax-music3-latent-refiner`：潜空间精修
- `minimax-music3-mlx`：Apple Silicon MLX 版
- 配套引擎：`sglang-omni`（官方 SGLang-Omni 推理框架）与 `comfyui-fl-minimaxmusic3` 专用节点
- 许可证核实为 **MiniMax-Music3 Community License**（需署名「MiniMax-Music3」；
  年收入超 2000 万美元需单独授权）——部分二手资料所称 CC BY-NC / CC-BY-SA 并不准确

## 2. 新增 13 个 2026 年 5 月后开放权重的高质量模型
均通过 HuggingFace API 实查（发布时间、许可、体积、热度）后入库：

| 分类 | 模型 | 仓库 | 许可 | 发布 |
|---|---|---|---|---|
| LLM | Granite 4.2 8B / 30B | ibm-granite/granite-4.2-{8b,30b} | Apache-2.0 | 2026-08 |
| LLM | MiniMax M3（428B/23B 稀疏 MoE，多模态） | MiniMaxAI/MiniMax-M3 | 社区许可 | 2026-06 |
| LLM | Meta Muse Glimmer 30B（本地 Agent） | meta-models/Muse-Glimmer-30B | Apache-2.0 | 2026-08 |
| LLM | DeepSeek-V4-Flash-Vision-Exp | deepseek-ai/DeepSeek-V4-Flash-Vision-Exp | MIT | 2026-08 |
| LLM | GLM-5.3（由「待开源」转正，1M 上下文） | zai-org/GLM-5.3 | GLM 许可 | 2026-08 |
| LLM | LFM2.5-VL-3B（端侧 VLM，可 WebGPU） | LiquidAI/LFM2.5-VL-3B | LFM 许可 | 2026-08 |
| TTS | MOSS-TTS v1.5（长文本/多说话人/流式） | OpenMOSS-Team/MOSS-TTS-v1.5 | Apache-2.0 | 2026-05 |
| TTS | dots.tts-soar（MeanFlow 少步加速） | dots-studio/dots.tts-soar | Apache-2.0 | 2026-06 |
| 音频 | Stable Audio 3 Medium（gated） | stabilityai/stable-audio-3-medium | Stability 许可 | 2026-05 |
| 音频 | Google Magenta Realtime 2（实时交互音乐） | google/magenta-realtime-2 | CC-BY-4.0 | 2026-05 |
| 音频 | MOSS-SoundEffect v2.0（环境音效） | OpenMOSS-Team/MOSS-SoundEffect-v2.0 | Apache-2.0 | 2026-05 |
| 图像 | Krea 2 Turbo（文生图/编辑，gated） | krea/Krea-2-Turbo | Krea 许可 | 2026-06 |

## 3. 修正 14 处错误 / 失效仓库 slug（实查为据）
- `deepseek-v4-pro`：`deepseek-ai/DeepSeek-V4`（不存在）→ `deepseek-ai/DeepSeek-V4-Pro`
- `qwen3.8-max`：`Qwen/Qwen3.8-Max`（不存在）→ `Qwen/Qwen3.8-2.4T-A95B`，体积/许可一并更正
- `qwen3.8-27b`：官方权重 `Qwen/Qwen3.8-27B`（2026-08-14 开放）转为正式 `repo`，
  原 JonathanColetti 去审查量化版保留为 `gguf_repo`
- `trellis2`：`microsoft/TRELLIS.2`（代码仓）→ 权重仓 `microsoft/TRELLIS.2-4B`，许可更正为 MIT
- `hunyuanimage-3.0`：`Tencent-Hunyuan/...` → 小写 `tencent/HunyuanImage-3.0`，体积更正为 83GB
- `seedvr2`：`ByteDance-Seed/SeedVR2`（不存在）→ `ByteDance-Seed/SeedVR2-3B`
- `direct3d-s2`：`thu-ml-lab/...` → 权重 `wushuang98/Direct3D-S2`、代码 `DreamTechAI/Direct3D-S2`，许可 MIT
- `triposr`：`VAST-AI/TripoSR` → `VAST-AI-Research/TripoSR`
- 引擎地址：Kokoro→`hexgrad/Kokoro`、Chatterbox→`resemble-ai/chatterbox`、
  IndexTTS→`index-tts/index-tts`、TripoSG→`VAST-AI-Research/TripoSG`
- `mistral-small-24b` 的 GGUF：bartowski 正确名带 `mistralai_` 前缀
- 补齐此前空仓库：`ace-step-1.5`→`ACE-Step/Ace-Step1.5`、`dots3-note-preview`→
  `dots-studio/dots3-note-prev`、`magi2-preview`→`sand-ai/MAGI-2-preview`、
  `lingbot-video`→`robbyant/lingbot-video-dense-1.3b`
- **移除** `meta-llama/Llama-4-Multilingual`：官方 Llama 4 仅有 Scout/Maverick，
  该仓库不存在（虚构条目）

## 4. 测试与验证
- 新增 `python/tests/test_v260_catalog.py`（49 项）：锁定全部修正 slug、新模型字段
  完整性与源一致性、Music3 架构事实、引擎地址、目录不变量
- 更新 v2.4.1 中已过时的 Qwen3.8-27B 身份测试
- **pytest 全量 372 项全绿**；23 个 JS 文件 `node --check` 全过；jsonschema 校验 0 错误
- 实机：真实启动 FastAPI sidecar，验证 health/categories/models/search/recommend/
  engines/download 等端点；真实下载新模型文件端到端落盘成功
- 极端测试：5000 字查询、SQL/路径穿越/XSS/全角字符、非法文件名、file:// scheme、
  gated 无 Token、不可达主机、越界/不存在资源——均无 500、无崩溃、错误码符合预期
- 硬件适配模拟：8GB 显存可跑 Granite-8B(量化)/LFM2.5-VL/全部新 TTS·音频/Krea2；
  24GB 增 Granite-30B/Muse Glimmer；80GB 增 GLM-5.3；集群级 M3/V4-Flash-Vision 正确隔离

## 5. 版本
- 应用 / sidecar / package 版本统一升至 **2.6.0**；模型条目 110 → **121**，引擎仍为 30

## 6. License Change (Important)
- The project's own license has been changed from **CC BY-NC-SA 4.0 (non-commercial)** to the **Kevrai Omni Community License v1.0** (full English text in root `LICENSE`).
- Core terms: **source code is public** and free for non-commercial use, modification, and distribution; **commercial use is permitted but requires prior written Commercial Authorization from the Licensor**; derivative works must be distributed under the same license.
- The Licensor expressly reserves the right to issue cease-and-desist letters, lawyer's letters (律师函), and pursue injunctive relief, damages, and account of profits for unauthorized commercial use or any other breach.
- Commercial authorization contact: **2671369836@qq.com** (see `LICENSE` Appendix A for application requirements).
- Synced references: `LICENSE` (rewritten in English), `README.md`, `INSTALL.md`, `NOTICE.md`, `package.json` (`SEE LICENSE IN LICENSE`), `electron-builder.yml` (copyright).
- Third-party models/engines/weights remain governed by their own upstream licenses, independent of the project's own license.
- This license is a legal document; consult a qualified lawyer before relying on it for commercial transactions.
