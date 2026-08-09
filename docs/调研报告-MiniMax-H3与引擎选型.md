# MiniMax H3 模型调研与推理引擎选型报告

> 调研日期：2026-08-07 · 所有数据均经联网核实（官方页面 / 仓库 API / 源码），核实来源附于文末。

---

## 一、MiniMax H3 模型核实信息

### 1. 时间线

| 事件 | 日期 | 来源 |
|---|---|---|
| 正式发布 | 2026-07-31 | MiniMax 官方博客、亿欧网 |
| 权重开源 | 2026-08-03 | 上海证券报、百度百科、智东西 |
| 纳入港股通 | 2026-08-06 | ITBear |

### 2. 开源范围（重要）

- **已开源**：H3-Base，含两个任务分区
  - **FL2VA**：文生视频、首帧/尾帧/首尾帧引导生成（最多 2 张关键帧图）
  - **Ref2VA**：全模态参考生成（最多 **9 图 + 3 视频 + 3 音频，合计 ≤12 个文件**）、视频编辑、动作迁移
- **未开源（仅官方 API）**：H3-Context-IR（复杂输入编排）、H3-Regenerate-2K（2K 再生成）
  - ⚠️ 因此**开源版最高输出为短边 768 像素**（默认档），2K 只能通过官方 API。用户所说"只开源 720P 级别"属实。

### 3. 输出规格（DiffSynth 官方文档 + 官方发布页交叉核实）

- 时长 **4~15 秒**，帧率**固定 24 FPS**，音频 **32kHz 原生立体声**
- 宽高必须是 **32 的倍数**；默认 768×1344（16:9）
- 帧数规则：`num_frames` 向上对齐到 **17n+5**（如 5 秒 → 124 帧）
- 比例支持：21:9 / 16:9 / 4:3 / 1:1 / 3:4 / 9:16
- 稳定支持 11 种语言（中、英、日、韩、法、德等）
- 模型为 CFG 蒸馏：**负向提示词默认不起作用**（cfg_scale 默认 1.0）

### 4. 模型规模与仓库（文件大小逐一经 files/tree API 核实）

官方仓库：`huggingface.co/MiniMaxAI/MiniMax-H3`（280 文件）与 `modelscope.cn/models/MiniMax/MiniMax-H3`（结构相同）

| 组件（单分区） | BF16 大小 |
|---|---|
| DiT 主干（transformer，13 分片） | 66.28 GB |
| Qwen3-VL 32B 文本编码器（14 分片） | 66.73 GB |
| 视频 VAE | 10.42 GB |
| 音频 VAE | 0.61 GB |
| **单分区合计** | **≈144 GB** |

### 5. 协议（必读）

**MiniMax H3 Community License Agreement**：明确**排除美国、欧盟、英国、韩国**的使用权限。商用前必须阅读协议全文。

---

## 二、量化版 / 社区版全景（全部核实真实存在 + 下载量）

### 官方量化（DiffSynth-Studio 出品，魔搭 + HF 双上架）

`DiffSynth-Studio/MiniMax-H3-NF4` —— bitsandbytes 4-bit（NF4）量化：

| 文件 | 大小 |
|---|---|
| minimax-h3-fl2va-nf4.safetensors | 17.16 GB |
| minimax-h3-ref2va-nf4.safetensors | 17.16 GB |
| minimax-h3-text-encoder-nf4.safetensors | 15.33 GB |
| video_vae_nf4.safetensors | 1.61 GB |
| audio_vae_nf4.safetensors | 0.28 GB |

配合 DiffSynth 显存管理：**最低约 7~8GB 显存可运行**；全磁盘直载模式下 8GB 内存也能跑（速度慢）。支持在 NF4 权重上做单阶段 LoRA 训练。

### Comfy-Org 社区量化套件（HF 229 万下载，魔搭同步镜像）

`Comfy-Org/MiniMax-H3`：

| 文件 | 大小 |
|---|---|
| fl2va/ref2va_bf16 | 各 66.28 GB |
| fl2va/ref2va_int8_convrot | 各 34.04 GB |
| fl2va/ref2va_pruned_bf16 | 各 40.23 GB |
| fl2va/ref2va_pruned_fp8_scaled | 各 20.96 GB |
| fl2va/ref2va_pruned_int8_convrot | 各 20.97 GB |
| qwen3vl_32b 文本编码器 bf16 / int8 / nvfp4_awq | 51.51 / 27.14 / 15.69 GB |
| video_vae_fp16 + audio_vae_fp32 | 5.21 + 0.61 GB |

（"pruned_int8 主干 21GB + NVFP4 文本编码器 15.7GB + VAE 5.8GB ≈ 42.5GB" 与媒体报道的"量化后 42.5GB"吻合 ✓）

### 社区 GGUF（ComfyUI-GGUF 插件用）

- `Abiray/MiniMax-H3-GGUF`（15.6 万下载）：FL2VA/Ref2VA 全系列 Q3_K_M(15.57GB) → Q8_0(36.04GB)，Q4_K_M=19.86GB；文本编码器 Q4_K_M GGUF 14.58GB
- 其他：realrebelai(6.6万)、molbal(2.7万)、vantagewithai(1.2万) 等 GGUF 仓库

### 社区微调 / 加速

- **InstantX/MiniMax-H3-Turbo-Lora-Diffusers**：4 步蒸馏 Turbo LoRA，851.5MB（官方 InstantX 团队出品）
- larryvrh/MiniMax-H3-Turbo-Lora：实验性全量权重 .bin（10.9GB/个，非 LoRA）
- disguisequence/MiniMax-H3-10Eros-Max-Quants：社区微调 10Eros_Max 的量化转换版
- Kijai/MiniMax-H3-TAE：微型自编码器（实验性）
- ostris/minimax_h3_training_adapter：ai-toolkit 训练适配
- ⚠️ 另有若干"uncensored/Heretic/NSFW"微调仓库，本软件不予收录

---

## 三、推理引擎选型（核心结论）

### 候选引擎对比（全部核实）

| 引擎 | 平台 | 最低显存 | H3 支持方式 | 维护状态 | 结论 |
|---|---|---|---|---|---|
| **DiffSynth-Studio** | Win/Linux/Mac | **~7-8GB**（NF4） | 官方 MiniMaxH3Pipeline，文档+示例+NF4 量化+LoRA 训练 | 12,862★，Apache-2.0，**调研当日仍有提交** | ✅ **选用** |
| **ComfyUI** | Win/Linux/Mac | ~12-16GB（INT8 pruned） | v0.30.0+ Day-0 原生支持，官方工作流模板 | 最活跃的节点生态 | ✅ 作为模型消费方（本软件下载 Comfy-Org 权重供其使用） |
| diffusers | 全平台 | 高（官方验证于 80GB 卡） | HF 官方 tag `diffusers:MiniMaxH3ModularPipeline` | 活跃 | 备选，低显存优化不足 |
| vLLM-Omni | **仅 Linux 服务器** | ≥48GB 多卡 | Day-0 适配 | 活跃 | ❌ 不适合 Windows 消费级 |
| SGLang(-Diffusion) | **仅 Linux** | ≥48GB 多卡 | Day-0 适配（壁仞/摩尔线程均基于它） | 活跃 | ❌ 不适合 Windows 消费级 |

### 选 DiffSynth-Studio 的理由（逐条对应产品需求）

1. **显存内存自动分配** → 内置三级显存管理（硬盘→内存→显存按计算顺序流转），`vram_limit` 自动预算，参数与官方示例完全一致
2. **小白可用** → 8GB 显存消费级显卡（RTX 3060/4060）即可跑 NF4 版
3. **Windows 原生** → 纯 PyTorch 栈；NF4 依赖的 bitsandbytes 自 0.43 起官方支持 Windows 11（README 明确列出）
4. **功能全覆盖** → 文生视频、首尾帧、多图/视频/音频参考、视频编辑（Retake 区间重生成）、音频驱动，全部是一个 pipeline 的参数
5. **社区微调可嵌入** → `pipe.load_lora(pipe.dit, lora_config=..., alpha=...)` 官方接口，支持 LoRA 热加载
6. **进度可控** → `progress_bar_cmd` 钩子可做实时步数进度
7. **下载体系兼容** → 魔搭官方同源（`MiniMax/MiniMax-H3`），国内下载天然快

### 引擎 API 核实清单（本软件开发依据，全部来自官方仓库源码/文档）

```python
from diffsynth.pipelines.minimax_h3_audio_video import MiniMaxH3Pipeline, ModelConfig
from diffsynth.utils.data.audio_video import write_video_audio, read_video_audio
from diffsynth.utils.data.audio import read_audio

pipe = MiniMaxH3Pipeline.from_pretrained(
    torch_dtype=torch.bfloat16, device="cuda",
    model_configs=[ModelConfig(path=...或model_id=..., offload/onload/preparing/computation=...)],
    processor_config=ModelConfig(...), vram_limit=float)

video, audio = pipe(prompt=..., height=..., width=..., num_frames=..., num_inference_steps=...,
    seed=..., keyframes=[PIL], keyframe_indices=[0,-1],
    references=[{"type":"image"|"video"|"audio"|"video_audio", ...}],
    retake_video=..., frame_regions_to_retake=[(a,b)],
    retake_audio=..., seconds_regions_to_retake=[(s,e)],
    progress_bar_cmd=...)
pipe.load_lora(pipe.dit, lora_config=path, alpha=1.0)
```

---

## 四、下载源核实

| 源 | 状态 | 说明 |
|---|---|---|
| 魔搭 ModelScope | ✅ 官方上架 MiniMax/MiniMax-H3、DiffSynth NF4、Comfy-Org 全套 | resolve URL 实测 200，支持 Range |
| HF-Mirror (hf-mirror.com) | ✅ HF 全量镜像 | 实测可用 |
| HuggingFace 原站 | ✅ 首发源 | 海外首选 |
| 魔乐 Modelers | ❌ **无法核实有 H3**（API 404）→ 已从软件移除 | 拒绝收录未证实信息 |
| GitHub | ❌ 官方权重不托管于 GitHub（仅代码）→ 不作为下载源 | 如实说明 |

测速方案：**真实 HTTP Range 采样**（默认 4MB），同时测 TTFB 延迟与真实吞吐，综合评分 = 速度 75% + 延迟 25%。**不使用任何伪造速度数字。**

---

## 五、硬件门槛建议（社区实测数据交叉参考）

| 显存 | 推荐版本 | 预期体验 |
|---|---|---|
| 8~12 GB | DiffSynth NF4 + 磁盘流式 | 480P/5s 约 8~25 分钟 |
| 16 GB | DiffSynth NF4 标准 | 480P~768P 可用 |
| 24 GB | DiffSynth NF4 全速 | 768P/5~10s |
| ≥48 GB | BF16 原版 | 全精度上限 |

系统内存建议 ≥32GB；模型目录建议 NVMe SSD。

---

## 核实来源索引

1. MiniMax 官方博客 https://www.minimaxi.com/blog/minimax-h3 （2026-07-31）
2. HuggingFace 模型卡 https://huggingface.co/MiniMaxAI/MiniMax-H3 （tree API 逐目录核实）
3. 魔搭 https://modelscope.cn/models/MiniMax/MiniMax-H3 （files API + resolve URL 实测）
4. DiffSynth-Studio 官方文档 docs/en/Model_Details/MiniMax-H3.md + examples/minimax_h3/* 示例源码 + core/loader/config.py 源码
5. NF4 仓库 https://modelscope.cn/models/DiffSynth-Studio/MiniMax-H3-NF4 （files API 核尺寸）
6. Comfy-Org https://huggingface.co/Comfy-Org/MiniMax-H3 （tree API 核尺寸）
7. ComfyUI 官方教程 docs.comfy.org/tutorials/video/minimax/minimax-h3（搜索快照）
8. bitsandbytes README（Windows 11 支持矩阵）
9. 上海证券报/智东西/百度百科/和讯（开源时间线与 16 家 Day-0 适配）
10. Abiray GGUF、InstantX Turbo LoRA（tree API 核尺寸）
