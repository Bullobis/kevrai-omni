# Kevrai v4.1.0 — 魔搭模型广场（ModelScope）源补充

## 4.1.0（2026-10-08）

### Added — 国内下载提速 & 免令牌

- **为 72 个 HF-only 模型自动补充魔搭模型广场（modelscope.cn）直链**。
  - 覆盖热门模型如 `Lightricks/LTX-2`、`XiaomiMiMo/MiMo-V2.6-Pro`、
    `google/gemma-4-31B-it`、`microsoft/phi-4`、`Qwen/Qwen2.5-VL-72B-Instruct`、
    `deepseek-ai/DeepSeek-R1-Distill-*`、`mistralai/*`、`stabilityai/*`、
    `stabilityai/stable-diffusion-xl-base-1.0`、`openbmb/MiniCPM-V-4.6`、
    `HiDream-ai/HiDream-I1-Fast` 等 72 个（见下方验证清单）。
  - **国内用户下载这些模型时无需 HF 令牌**（HF gated repo 依旧可能需 token，
    但大多数开源模型可直接下载）。
  - 下载器会自动做 best-source 测速，选最快源；`sources[]` 数组是有序的
    fallback 链，任何一路失败自动切下一路。

### 关键校验

- 所有新加的 MS URL 都通过了
  `GET https://www.modelscope.cn/api/v1/models/{owner}/{repo}`
  返回 `Success: true` 的严格验证。
  - 初次尝试直接按 `huggingface.co/{owner}/{repo}` →
    `www.modelscope.cn/models/{owner}/{repo}` 映射，但**魔搭和 HF 的 slug 并不总是一致**
    （魔搭常用中文 slug 或不同命名，例如 GLM-4.5、CosyVoice、Llama、
    HunyuanImage 等在魔搭存在但 slug 不同），因此 128 个候选中只有
    72 个（56 个 404）被保留，其余自动剔除，避免出现死链。
- 现有 123 个已带魔搭源的模型（多为 v3.x 时手工添加）不动。

### 统计

| 项 | 数量 |
|---|---|
| 模型总数 | 257 |
| 带 HuggingFace 源 | 301 |
| 带 hf-mirror 源 | 278 |
| 带 ModelScope 源（模型数） | **195**（原 123 + 新增 72） |
| 带 ModelScope 源（URL 数） | 216（部分模型多重映射） |
| 带至少一个国内源（HF-mirror 或 MS） | ~250+ |
| 总源数 | 810 |

### Version bump

- `package.json` → 4.1.0
- `python/app/__init__.py` → 4.1.0
- `catalog/models.json` → 4.1.0
- `catalog/engines.json` → 4.1.0

### 未做的（后续可迭代）

- **补齐其他 56 个 HF-only 模型的魔搭源**：需要在魔搭侧搜索对应的中文/
  重命名 slug，或等待社区同步。目前这 56 个模型仍可走 HF + hf-mirror
  下载，不影响使用。
- 用户提到"很多模型魔搭和 HF 都有"——大部分是**国内大厂官方模型**
  （Qwen / DeepSeek / Hunyuan / GLM / Wan 等），这些其实**已有 123 个
  带 MS 源**，本次新增的是**海外开源模型在魔搭被镜像的部分**。
