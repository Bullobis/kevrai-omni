# NOTICE — Third-party components distributed via Kevrai Omni

This file lists every third-party model, engine, and asset that Kevrai Omni can
download or bundle. **Each row's license governs that component, NOT the
CC BY-NC-SA 4.0 license that covers Kevrai Omni itself** (see `LICENSE`).

When you redistribute Kevrai Omni, you do NOT need to redistribute the
third-party weights — Kevrai Omni downloads them on demand from each upstream
repository. If you DO redistribute them (e.g. in a pre-seeded bundle), you
must comply with each upstream license, which usually requires attribution,
non-commercial use, or both.

The license column uses SPDX identifiers where one exists. Where no official
SPDX identifier exists, the upstream license name is used verbatim.

## Models

Each row links to the upstream repository where you can read the full license
text.

| Category | Name | Upstream repo | License | Commercial use |
|----------|------|---------------|---------|----------------|
| llm | Qwen3 32B (Instruct) | Qwen/Qwen3-32B | Apache-2.0 | yes |
| llm | Qwen3 235B A22B (MoE) | Qwen/Qwen3-235B-A22B | Apache-2.0 | yes |
| llm | Qwen3-VL 32B Instruct | Qwen/Qwen3-VL-32B-Instruct | Apache-2.0 | yes |
| llm | Qwen3-Omni 30B-A3B Instruct | Qwen/Qwen3-Omni-30B-A3B-Instruct | Apache-2.0 | yes |
| llm | Llama 3.3 70B Instruct | meta-llama/Llama-3.3-70B-Instruct | LLAMA-3.3-COMMUNITY | conditional (≤700M MAU free) |
| llm | DeepSeek-V3 (MoE 671B) | deepseek-ai/DeepSeek-V3 | DEEPSEEK-LICENSE | yes (with use restrictions) |
| llm | DeepSeek-R1 | deepseek-ai/DeepSeek-R1 | MIT | yes |
| llm | Kimi K2 Instruct (MoE) | moonshotai/Kimi-K2-Instruct | MODIFIED-MIT | yes |
| llm | GLM-4.5 (Air / Plus) | THUDM/glm-4-9b-chat | Apache-2.0 | yes |
| llm | Mistral Small 24B | mistralai/Mistral-Small-24B-Base-2501 | Apache-2.0 | yes |
| llm | Gemma 3 27B IT | google/gemma-3-27b-it | GEMMA-TERMS-OF-USE | conditional (Gemma prohibited-use policy) |
| llm | InternVL3 38B | OpenGVLab/InternVL3-38B | MIT | yes |
| llm | Qwen2.5 72B Instruct | Qwen/Qwen2.5-72B-Instruct | Apache-2.0 | yes |
| llm | Qwen2.5-VL 72B Instruct | Qwen/Qwen2.5-VL-72B-Instruct | Apache-2.0 | yes |
| llm | Qwen3 30B A3B (MoE) | Qwen/Qwen3-30B-A3B | Apache-2.0 | yes |
| image | Qwen-Image | Qwen/Qwen-Image | Apache-2.0 | yes |
| tts | CosyVoice 2 (0.5B) | FunAudioLLM/CosyVoice2-0.5B | Apache-2.0 | yes |
| tts | CosyVoice 3 | FunAudioLLM/CosyVoice-300M | Apache-2.0 | yes |
| tts | Fish Speech 1.5 | fishaudio/fish-speech-1.5 | Apache-2.0 | yes |
| tts | F5-TTS | SWivid/F5-TTS | MIT | yes |
| tts | Spark-TTS 0.5B | SparkAudio/Spark-TTS-0.5B | Apache-2.0 | yes |
| tts | Kokoro 82M | hexgrad/Kokoro-82M | Apache-2.0 | yes |
| tts | Chatterbox TTS | ResembleAI/chatterbox | Apache-2.0 | yes |
| tts | IndexTTS | IndexTeam/IndexTTS-2 | Apache-2.0 | yes |
| tts | GPT-SoVITS | RVC-Boss/GPT-SoVITS | MIT | yes |
| video | Wan 2.2 T2V A14B | Wan-AI/Wan2.2-T2V-A14B-Diffusers | Apache-2.0 | yes |
| video | Wan 2.2 I2V A14B | Wan-AI/Wan2.2-I2V-A14B-Diffusers | Apache-2.0 | yes |
| video | HunyuanVideo (T2V/I2V) | Tencent-Hunyuan/HunyuanVideo | TENCENT-HUNYUAN-COMMUNITY | conditional (territory restricted: not EU/UK/KR) |
| video | CogVideoX 5B | THUDM/CogVideoX-5b | Apache-2.0 | yes |
| video | CogVideoX 2B | THUDM/CogVideoX-2b | Apache-2.0 | yes |
| video | LTX-Video | Lightricks/LTX-Video | OPENRAIL-M | conditional (use restrictions; no facial-gen of public figures, etc.) |
| video | Open-Sora 2.0 | hpcaitech/Open-Sora | Apache-2.0 | yes |
| video | Step-Video | stepfun-ai/stepvideo | Apache-2.0 | yes |
| image | FLUX.1 [dev] | black-forest-labs/FLUX.1-dev | FLUX.1-DEV-NON-COMMERCIAL | **NO** (commercial license separate) |
| image | FLUX.1 [schnell] | black-forest-labs/FLUX.1-schnell | Apache-2.0 | yes |
| image | SDXL-Turbo | stabilityai/sdxl-turbo | SDXL-TURBO-NON-COMMERCIAL | **NO** (commercial license separate) |
| image | Stable Diffusion 3.5 Large | stabilityai/stable-diffusion-3.5-large | STABILITY-AI-NON-COMMERCIAL | **NO** (commercial license separate) |
| image | Kwai Kolors | Kwai-Kolors/Kolors | Apache-2.0 | yes |
| image | AuraFlow | fal/AuraFlow | Apache-2.0 | yes |
| image | HunyuanImage 3.0 | Tencent-Hunyuan/HunyuanImage-3.0 | TENCENT-HUNYUAN-COMMUNITY | conditional (territory restricted) |
| image | ControlNet (SD1.5 Canny) | lllyasviel/control_v11p_sd15_canny | OPENRAIL | conditional |
| superres | Real-ESRGAN x4plus | xinntao/Real-ESRGAN | BSD-3 | yes |
| superres | APISR (Anime/Photo) | Kiteretsu77/APISR | Apache-2.0 | yes |
| superres | SUPIR | Fanghua-Yu/SUPIR | Apache-2.0 | yes |
| superres | 4x-UltraSharp | Kim2091/UltraSharp | CC-BY-NC-SA-4.0 | **NO** |
| superres | SeedVR2 | ByteDance-Seed/SeedVR2 | Apache-2.0 | yes |
| audio | Stable Audio Open 1.0 | stabilityai/stable-audio-open-1.0 | STABILITY-AI-NON-COMMERCIAL | **NO** |
| audio | MusicGen Large | facebook/musicgen-large | CC-BY-NC-4.0 | **NO** |
| audio | MusicGen Stereo Large | facebook/musicgen-stereo-large | CC-BY-NC-4.0 | **NO** |
| audio | AudioLDM 2 | haoheliu/audioldm2 | Apache-2.0 | yes |
| audio | DiffRhythm | ASLP-lab/DiffRhythm | Apache-2.0 | yes |
| 3d | Hunyuan3D 2.1 | tencent/Hunyuan3D-2 | TENCENT-HUNYUAN-COMMUNITY | conditional (territory restricted) |
| 3d | TRELLIS-image (Large) | microsoft/TRELLIS-image-large | MIT | yes |
| 3d | TripoSR | VAST-AI/TripoSR | MIT | yes |
| 3d | Direct3D-S2 | thu-ml-lab/Direct3D-S2 | Apache-2.0 | yes |
| 3d | PartCrafter (NeurIPS'25) | wgsxm/PartCrafter | Apache-2.0 | yes |
| 3d | Trellis 2 | microsoft/TRELLIS.2 | MIT | yes |
| 3d | TripoSG | VAST-AI/TripoSG | MIT | yes |
| vision | CLIP ViT-L/14 | openai/clip-vit-large-patch14 | MIT | yes |
| vision | InsightFace (buffalo_l) | buffalo_l | INSIGHTFACE-NON-COMMERCIAL | **NO** (InsightFace license is NC) |
| vision | YOLOv10 | THU-MIG/yolov10 | AGPL-3.0 | conditional — **strong copyleft**: if you distribute a service that uses this, you must publish the complete corresponding source of the service under AGPL-3.0. Avoid using YOLOv10 in any Kevrai Omni build intended for redistribution. |
| pending | MiniMax Hailuo 2K | (not released) | UNKNOWN | n/a |

## Engines (inference backends)

| Engine | Upstream | License | Notes |
|--------|----------|---------|-------|
| llama.cpp | github.com/ggerganov/llama.cpp | MIT | yes |
| llama-cpp-python | abetlen/llama-cpp-python | MIT | yes |
| vLLM | vllm-project/vllm | Apache-2.0 | yes |
| MNN | alibaba/MNN | Apache-2.0 | yes |
| ONNX Runtime | microsoft/onnxruntime | MIT | yes |
| ComfyUI | comfyanonymous/ComfyUI | GPL-3.0 | yes (but ComfyUI custom nodes are GPL-3.0, copyleft) |
| Diffusers | huggingface/diffusers | Apache-2.0 | yes |
| Transformers | huggingface/transformers | Apache-2.0 | yes |
| PyTorch | pytorch/pytorch | BSD-3 | yes |
| Triton Inference Server | triton-inference-server/server | BSD-3 | yes |
| Kokoro (TTS) | hexgrad/kokoro | Apache-2.0 | yes |
| Fish Speech | fishaudio/fish-speech | Apache-2.0 | yes |
| F5-TTS engine | SWivid/F5-TTS | MIT | yes |
| CosyVoice engine | FunAudioLLM/CosyVoice | Apache-2.0 | yes |
| Spark-TTS engine | SparkAudio/Spark-TTS | Apache-2.0 | yes |
| Chatterbox engine | resemble-ai/chatterbox | Apache-2.0 | yes |
| IndexTTS engine | index-tts/index-tts | Apache-2.0 | yes |
| Hunyuan3D engine | tencent/Hunyuan3D-2 | TENCENT-HUNYUAN-COMMUNITY | conditional (territory restricted) |
| TRELLIS engine | microsoft/TRELLIS | MIT | yes |
| TripoSR engine | VAST-AI/TripoSR | MIT | yes |
| TripoSG engine | VAST-AI/TripoSG | MIT | yes |
| Direct3D-S2 engine | thu-ml-lab/Direct3D-S2 | Apache-2.0 | yes |
| PartCrafter engine | wgsxm/PartCrafter | Apache-2.0 | yes |
| InsightFace engine | deepinsight/insightface | INSIGHTFACE-NON-COMMERCIAL | **NO** |

## User-imported models

When a user imports a local model file (via "Import local"), Kevrai Omni
records the file's SHA-256 in `models/_local.json` but does NOT claim any
license over the imported file. **The user remains solely responsible** for
ensuring they have the right to use and redistribute whatever they import.

## A note on commercial use

Kevrai Omni itself is **non-commercial** (CC BY-NC-SA 4.0). However, many of
the third-party models and engines it can install are themselves under
non-commercial licenses. Before using any model/engine to produce content you
intend to commercialise, check the row above — anything marked `**NO**` in
"Commercial use" requires purchasing a separate commercial license from the
upstream rights-holder.

This file is updated as part of each Kevrai Omni release. If you spot an
inaccuracy or a new model whose license is missing, please open an issue at
the upstream Kevrai Omni repository.