# Kevrai Omni v4.17.9 发布说明

> 基线：v4.17.8。本版是**模型目录真实性修正**，不改推理运行时代码。
> 适用平台：Windows x64、macOS Intel / Apple Silicon、Linux x64。

## 一句话总结

模型市场只保留**真实存在、可访问的开放权重仓库**：删除 4 条无权重/闭源占位，
补入 NVIDIA/MIT `Open-H3-Regenerate-2K` 和 MiniMax `H3-Image`，修正 demucs、
`apple/Sharp`、Piper 的失效仓库地址；341 个 HF 仓库逐个审计，0 个失效。

## 目录变化

- 条目：261 → **259**；分类：9 → **8**，移除空的“待官方开源”页。
- 移除（没有可下载权重，保留只会产生 404）：
  - `minimax-2k-pending` — MiniMax 官方 H3-Regenerate-2K 仍仅 API；
  - `glm-5-code`、`wan3.0`、`happyshrimp-1.0` — 官方未发布权重。
- 新增（真实开放权重）：
  - `open-h3-regenerate-2k` — NVIDIA + MIT 的开放 2K/4K 再生成实现，165 GB 权重，
    官方 SoL-Engine 单卡约 150 GB 显存；**不是 MiniMax 官方模块**；
  - `minimax-h3-image` — MiniMax 2026-11-10 开放的图像生成/编辑模型，约 16 MP，
    BF16 DiT 68.65 GB、整仓约 204 GB。
- 修正：
  - `htdemucs` / `htdemucs-ft` / `htdemucs-6s` → `iBoostAI/Demucs-v4`，并声明各自权重文件；
  - `apple-sharp` → `apple/Sharp`；
  - 3 个 Piper voice → `rhasspy/piper-voices`，并声明具体 `.onnx` / `.onnx.json`。
- H3 相关描述不再写“2K 只能 API”：本地 768p 可接 Open-H3 开放实现；官方 2K 仍仅 API。

## 仓库审计

对目录引用的 **341 个唯一 Hugging Face 仓库**逐个调用 Hub API：

- **0 个 401/404**（修正前 4 个：`apple/ml-sharp`、3 个 `demucs/*`）。
- 316 个公开，25 个 gated（Llama、Gemma、FLUX、SD3.5、LTX-2.5 等）。
  gated 属于开放权重，接受许可并配置 Token 后可下载。
- 所有仓库均含权重；`Phonon-2` 为 `.bps.tar.zst` 打包，`Demucs-v4` 为 `.th`。

## 验证

- Python：**2685 passed**；唯一失败 `test_missing_engine_raises` 是本机预装 RapidOCR
  导致环境假设不成立，干净 CI 通过。
- 目录专项：`test_catalog` / `test_catalog_schema` / `test_v241_fixes` /
  `test_v292_catalog_factfields` / `test_v391_dedup_primary_repo` 共 157 passed。
- Renderer、JS 语法、ruff、版本门禁与 smoke 结果见 Release 构建日志。

## 边界

“开放权重”不等于“当前应用能一键本地运行”。vLLM/SGLang-only、厂商专用 3D、
尚未接入的 TTS 等条目会继续如实保留，但运行状态必须由引擎路由单独判断；
下一阶段才做逐模型“下载 → 加载 → 最小推理”的 smoke 矩阵。
