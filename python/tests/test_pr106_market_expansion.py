"""PR #106 market-expansion regression tests (K-Catalog QA).

Locks in the 53 models added by k-nexus/market-expand. Every slug was
verified to exist on the HuggingFace API on 2026-10-06; this guard ensures
a later merge cannot silently revert them (as happened with the v3.0.0
catalog rollback).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

PYDIR = Path(__file__).resolve().parents[1]
REPO = PYDIR.parent
sys.path.insert(0, str(PYDIR))


def _models():
    data = json.loads((REPO / "catalog" / "models.json").read_text(encoding="utf-8"))
    return {m["id"]: m for m in data["models"]}


FACTS = [
    ('acestep-v15-base', 'ACE-Step/acestep-v15-base', 'MIT'),
    ('apple-sharp', 'apple/Sharp', 'Apple Research License (apple-amlr，研究/非商业)'),
    ('aurasr-v2', 'fal/AuraSR-v2', 'Apache-2.0'),
    ('cloudflare-clef', 'Cloudflare/clef', 'Apache-2.0'),
    ('cloudflare-clef-flash', 'Cloudflare/clef-flash', 'Apache-2.0'),
    ('cogvideox-5b', 'zai-org/CogVideoX-5b', 'other'),
    ('deepseek-v3', 'deepseek-ai/DeepSeek-V3', 'other'),
    ('deepseek-v3.1', 'deepseek-ai/DeepSeek-V3.1', 'MIT'),
    ('deepseek-v4.1-flash', 'deepseek-ai/DeepSeek-V4.1-Flash', 'MIT'),
    ('fish-speech-s2-pro', 'fishaudio/s2-pro', 'other'),
    ('flux1-kontext-dev', 'black-forest-labs/FLUX.1-Kontext-dev', 'FLUX.1-Kontext-dev-Non-Commercial (gated)'),
    ('flux2-klein-4b', 'black-forest-labs/FLUX.2-klein-4B', 'Apache-2.0'),
    ('framepack-i2v', 'lllyasviel/FramePackI2V_HY', 'other'),
    ('indextts-2.5', 'IndexTeam/IndexTTS-2.5', 'other'),
    ('instant-mesh', 'TencentARC/InstantMesh', 'Apache-2.0'),
    ('jev-27b-vl', 'autotrust/JEV-27B-VL', 'Apache-2.0'),
    ('llama-3.1-8b', 'meta-llama/Llama-3.1-8B-Instruct', 'Llama3.1'),
    ('llama-3.2-3b', 'meta-llama/Llama-3.2-3B-Instruct', 'Llama3.2'),
    ('midi-3d', 'VAST-AI/MIDI-3D', 'Apache-2.0'),
    ('mochi-1', 'genmo/mochi-1-preview', 'Apache-2.0'),
    ('nemotron3-diarization', 'nvidia/Nemotron-3-Diarization', 'OpenMDW-1.1'),
    ('orpheus-3b', 'canopylabs/orpheus-3b-0.1-ft', 'Apache-2.0'),
    ('paddleocr-vl', 'PaddlePaddle/PaddleOCR-VL', 'Apache-2.0'),
    ('phi-4', 'microsoft/phi-4', 'MIT'),
    ('phonon-2', 'FermionResearch/Phonon-2', 'CC-BY-4.0'),
    ('pp-ocrv6-det', 'PaddlePaddle/PP-OCRv6_medium_det_onnx', 'Apache-2.0'),
    ('pp-ocrv6-rec', 'PaddlePaddle/PP-OCRv6_medium_rec_onnx', 'Apache-2.0'),
    ('pyramid-flow', 'rain1011/pyramid-flow-sd3', 'MIT'),
    ('qwen-image-2.1', 'Qwen/Qwen-Image-2.1', 'Qwen Research License (other)'),
    ('qwen-image-2.1-viggle-turbo', 'Viggle/Qwen-Image-2.1-viggle-turbo', 'Qwen Research License（衍生自 Qwen-Image-2.1）'),
    ('qwen-image-edit-2511', 'Qwen/Qwen-Image-Edit-2511', 'Apache-2.0'),
    ('qwen2.5-7b', 'Qwen/Qwen2.5-7B-Instruct', 'Apache-2.0'),
    ('qwen2.5-coder-32b', 'Qwen/Qwen2.5-Coder-32B-Instruct', 'Apache-2.0'),
    ('qwen3-0.6b', 'Qwen/Qwen3-0.6B', 'Apache-2.0'),
    ('qwen3-4b', 'Qwen/Qwen3-4B', 'Apache-2.0'),
    ('qwen3-8b', 'Qwen/Qwen3-8B', 'Apache-2.0'),
    ('qwen3-coder-30b-a3b', 'Qwen/Qwen3-Coder-30B-A3B-Instruct', 'Apache-2.0'),
    ('qwen3-tts-1.7b-customvoice', 'Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice', 'Apache-2.0'),
    ('qwen3.8-flash-next', 'Qwen/Qwen3.8-Flash-Next', 'Qwen 社区许可（other, qwen-community-1.0）'),
    ('qwq-32b', 'Qwen/QwQ-32B', 'Apache-2.0'),
    ('sam3', 'facebook/sam3', 'Meta 自定义许可（other）'),
    ('shap-e', 'openai/shap-e', 'MIT'),
    ('smollm2-1.7b', 'HuggingFaceTB/SmolLM2-1.7B-Instruct', 'Apache-2.0'),
    ('stable-fast-3d', 'stabilityai/stable-fast-3d', 'Stability AI Community License（other，非商业/受限商用）'),
    ('stable-spa3d', 'stabilityai/stable-point-aware-3d', 'Stability AI Community License（other，非商业/受限商用）'),
    ('triposplat', 'VAST-AI/TripoSplat', 'MIT'),
    ('voxcpm2', 'openbmb/VoxCPM2', 'Apache-2.0'),
    ('wan2.2-animate', 'Wan-AI/Wan2.2-Animate-14B-Diffusers', 'Apache-2.0'),
    ('wan2.2-animate2', 'Wan-AI/Wan2.2-Animate-2-14B', 'Apache-2.0'),
    ('wan2.2-s2v', 'Wan-AI/Wan2.2-S2V-14B', 'Apache-2.0'),
    ('yue2-3b', 'm-a-p/YuE2-3B', 'CC-BY-NC-4.0'),
    ('z-image-turbo', 'Tongyi-MAI/Z-Image-Turbo', 'Apache-2.0'),
    ('zdtaichu5.0-9b', 'TaichuAI/ZDTaichu5.0-9B', '未在 HF 卡声明（other）'),
]


def test_pr106_total_model_count():
    assert len(_models()) >= 180


@pytest.mark.parametrize("mid,repo,license", FACTS)
def test_model_present_with_repo_and_license(mid, repo, license):
    m = _models().get(mid)
    assert m is not None, mid
    assert m["repo"] == repo
    assert m["license"] == license


@pytest.mark.parametrize("mid,repo,license", FACTS)
def test_model_has_two_sources_and_positive_size(mid, repo, license):
    m = _models()[mid]
    srcs = {s.rstrip("/") for s in m.get("sources", [])}
    assert len(srcs) >= 2, mid
    assert m["size_gb"] > 0
    assert m["hardware"]["disk_gb"] >= m["size_gb"]
