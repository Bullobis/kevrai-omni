# Kevrai Omni v3.4.1 — 模型数据准确性修复

发布日期：2026-09-25　·　上一版本：v3.4.0

本次为**数据准确性修复版本**，无新功能、无破坏性变更。v3.4.0 的安装包在数据核验
合并前构建，本版本正式纳入经多源核实的目录数据。

## 修复

### 模型体积 / 参数量（79 个条目）
- 以 Hugging Face 仓库实际文件字节（`?blobs=true`）逐一核验，修正 **79 个 size_gb**。
- 此前偏差较大的示例：
  - GLM-5：22 → 1404 GB
  - LTX-Video：12 → 237 GB
  - SUPIR：3.5 → 60 GB
  - Bonsai-27B：4 → 64 GB
  - Inkling：550 → 1774 GB
- 模型详情中的内存 / 磁盘 / 显存建议随实际体积同步修正。

### 许可证（8 个条目）
- AudioLDM2：Apache-2.0 → CC-BY-NC-ND-4.0
- F5-TTS：MIT → CC-BY-NC-4.0
- Spark-TTS：Apache-2.0 → CC-BY-NC-SA-4.0
- Chatterbox：Apache-2.0 → MIT
- Kimi-K2.6 / MiniMax-M2.7 / MiniMax-Music3-GGUF / Qwen3-Omni：标注为 other。

## 质量门（真实数字）
- Python 全量测试：1216+ passed（含新增 test_v292 数据字段回归测试）。
- JS 语法检查、smoke 全绿；安装包由 release workflow 在 Linux / Windows / macOS 分别构建。
