"""v3.1.0 catalog fact-field regression tests.

Locks in model size_gb / hardware / license values verified against the
HuggingFace API (blobs=true total repo size) on 2026-09-25. Guards against
parameter-count-based estimates drifting from actual repo sizes.
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


# ---------------------------------------------------------------------------
# Corrected size_gb (HF API actual repo size, rounded to nearest GB)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("mid,expected_size", [
    ("glm-4.5", 668),
    ("deepseek-v4-pro", 805),
    ("kimi-k3", 1454),
    ("qwen3.8-max", 4556),
    ("dots3-note-preview", 537),
    ("minimax-m3", 796),
    ("magi2-preview", 286),
    ("minimax-music3-gguf", 55),
    ("krea-2-turbo", 58),
    ("hunyuanimage-3.0", 157),
    ("glm-5.3", 704),
    ("deepseek-v4-flash", 149),
    ("kimi-k2.6", 554),
    ("minimax-m2.7", 214),
    ("glm-5.2", 1403),
    ("qwen3.5-397b-a17b", 751),
    ("hy3", 557),
    # Round 2 (2026-09-25)
    ("deepseek-r1", 641),
    ("qwen3-235b-a22b", 438),
    ("inkling", 1774),
    ("deepseek-v4-flash-vision", 156),
    ("ltx-2.3", 145),
    ("ltx-2.5", 187),
    ("minimax-h3", 459),
    ("minimax-h3-omni", 464),
    ("wan2.2-t2v", 118),
    ("wan2.2-i2v", 118),
    ("flux.2-dev", 165),
    ("gpt-oss-120b", 182),
    ("pixal3d", 43),
    ("4danyone", 20),
    ("muse-glimmer-30b", 56),
    # Round 3 (2026-09-25)
    ("glm-5", 1404),
    ("ltx-video", 237),
    ("supir", 60),
    ("bonsai-27b", 64),
    ("sd3.5-large", 67),
    ("flux1-dev", 54),
    ("flux1-schnell", 54),
    ("qwen-image", 54),
    ("mistral-small-24b", 88),
    ("keye-vl-2.0", 58),
    ("qwen3.6-35b-a3b", 67),
    ("granite-4.2-30b", 55),
    ("qwen3.5-27b", 52),
    ("qwen3.6-27b", 52),
    ("qwen3.8-27b", 52),
    ("musicgen-large", 19),
    ("stable-audio-open", 15),
    ("zamba2-vl-7b", 15),
    ("trellis2", 15),
    ("moss-tts-1.5", 16),
    ("lfm2.5-8b-a1b", 16),
    ("granite-4.2-8b", 16),
    ("gpt-oss-20b", 38),
    ("kolors", 27),
    ("stable-audio-3-medium", 10),
    ("moss-soundeffect-2", 10),
    ("chatterbox", 13),
    ("lingbot-video", 11),
    ("ace-step-1.5", 9),
    ("seedvr2", 14),
    ("qwen3.5-9b", 18),
    ("clip-vit-l", 6),
    ("f5-tts", 6),
    ("indextts", 6),
    ("triposg", 7),
    ("audioldm2", 8),
    ("cosyvoice2", 5),
    ("dots-tts-soar", 5),
    ("gpt-sovits", 5),
    ("spark-tts", 4),
    ("zamba2-vl-1.2b", 4),
    ("controlnet-canny", 4),
    ("visionpsy-nano-460m", 2),
    ("zamba2-vl-2.7b", 6),
    ("lfm2.5-vl-3b", 6),
    ("opensora-2.0", 9),
    ("trellis-image", 3),
])
def test_model_size_matches_hf_api(mid, expected_size):
    """size_gb must be within 5% of HF API verified repo size."""
    m = _models()
    assert mid in m, f"{mid} missing from catalog"
    actual = m[mid].get("size_gb", 0)
    tolerance = max(1, expected_size * 0.05)
    assert abs(actual - expected_size) <= tolerance, \
        f"{mid} size_gb={actual}, expected ~{expected_size} (HF API, 2026-09-25)"


# ---------------------------------------------------------------------------
# Hardware sanity: ram_gb must be at least 20% of size_gb (quantized running)
# ---------------------------------------------------------------------------
def test_ram_gb_reasonable_vs_size_gb():
    """ram_gb should be >= 20% of size_gb (Q4 quantization ~25% of BF16)."""
    m = _models()
    violations = []
    for mid, model in m.items():
        if model.get("category") == "pending":
            continue
        size = model.get("size_gb", 0) or 0
        ram = model.get("hardware", {}).get("ram_gb", 0) or 0
        if size > 20 and ram > 0 and ram < size * 0.2:
            violations.append(f"{mid}: ram={ram}GB < 20% of size={size}GB")
    assert not violations, f"ram_gb too low vs size_gb: {violations}"


# ---------------------------------------------------------------------------
# vram_gb must not be 0 for non-tiny, non-MLX models
# ---------------------------------------------------------------------------
def test_vram_gb_not_zero_for_large_models():
    """Models > 5GB (excluding MLX/Apple-Silicon) should declare vram_gb."""
    m = _models()
    violations = []
    for mid, model in m.items():
        if model.get("category") == "pending":
            continue
        if "mlx" in mid.lower() or "mlx" in model.get("repo", "").lower():
            continue  # MLX uses Apple unified memory, vram=0 is intentional
        size = model.get("size_gb", 0) or 0
        vram = model.get("hardware", {}).get("vram_gb", 0) or 0
        if size > 5 and vram == 0:
            violations.append(f"{mid}: size={size}GB but vram=0")
    assert not violations, f"vram_gb=0 for large models: {violations}"


# ---------------------------------------------------------------------------
# License must not be empty/未标注 for released models
# ---------------------------------------------------------------------------
def test_license_not_unmarked():
    """Non-pending models must have a real license, not '未标注' or empty."""
    m = _models()
    violations = []
    for mid, model in m.items():
        if model.get("category") == "pending":
            continue
        lic = model.get("license", "")
        if not lic or lic in ("未标注", "unknown", "Unknown", ""):
            violations.append(f"{mid}: license={lic!r}")
    assert not violations, f"missing license: {violations}"


# ---------------------------------------------------------------------------
# disk_gb should not be >3x size_gb for pure LLM/text models
# (video/image/3D models legitimately need extra space for VAEs/dependencies)
# ---------------------------------------------------------------------------
def test_disk_gb_reasonable_vs_size_gb():
    """disk_gb should not be >3x size_gb for text-only LLM models."""
    m = _models()
    violations = []
    for mid, model in m.items():
        if model.get("category") == "pending":
            continue
        cat = str(model.get("category", "")).lower()
        mod = str(model.get("modality", "")).lower()
        # Skip non-text models that need extra dependencies
        if any(k in cat for k in ["video", "image", "3d", "audio", "tts", "music", "vision", "multimodal", "omni"]):
            continue
        if any(k in mod for k in ["video", "image", "3d", "audio", "vision"]):
            continue
        size = model.get("size_gb", 0) or 0
        disk = model.get("hardware", {}).get("disk_gb", 0) or 0
        if size > 10 and disk > size * 3:
            violations.append(f"{mid}: disk={disk}GB > 3x size={size}GB")
    assert not violations, f"disk_gb > 3x size_gb: {violations}"


# ---------------------------------------------------------------------------
# Corrected licenses (verified against HF API license tags)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("mid,expected_license_keyword", [
    ("kimi-k2.6", "other"),
    ("minimax-m2.7", "other"),
    ("minimax-music3-gguf", "other"),
    ("glm-4.5", "MIT"),
    ("deepseek-v4-pro", "MIT"),
    ("deepseek-v4-flash", "MIT"),
    ("glm-5.2", "MIT"),
    ("qwen3-omni", "other"),
    # Round 3 license corrections
    ("audioldm2", "CC-BY-NC-ND"),
    ("f5-tts", "CC-BY-NC"),
    ("spark-tts", "CC-BY-NC-SA"),
    ("chatterbox", "MIT"),
])
def test_license_matches_hf_api(mid, expected_license_keyword):
    """License field must contain the HF-API-verified license keyword."""
    m = _models()
    assert mid in m
    lic = m[mid].get("license", "")
    assert expected_license_keyword.lower() in lic.lower(), \
        f"{mid} license={lic!r}, expected to contain {expected_license_keyword!r}"


# ---------------------------------------------------------------------------
# ModelScope URLs must not use /resolve/master/ without a filename (returns 500)
# ---------------------------------------------------------------------------
def test_modelscope_urls_no_bare_resolve_master():
    """ModelScope source URLs must be model pages, not bare /resolve/master/ (500)."""
    m = _models()
    violations = []
    for mid, model in m.items():
        for url in model.get("sources", []):
            if "modelscope.cn" in url and "/resolve/master/" in url:
                violations.append(f"{mid}: {url}")
    assert not violations, f"ModelScope /resolve/master/ URLs (return 500): {violations[:5]}"


def test_modelscope_urls_well_formed():
    """ModelScope URLs must start with https://modelscope.cn/models/."""
    m = _models()
    violations = []
    for mid, model in m.items():
        for url in model.get("sources", []):
            if "modelscope.cn" in url:
                if not url.startswith("https://modelscope.cn/models/"):
                    violations.append(f"{mid}: {url}")
    assert not violations, f"Malformed ModelScope URLs: {violations[:5]}"


# ---------------------------------------------------------------------------
# engines.json must not contain invalid objects.githubusercontent.com URLs
# ---------------------------------------------------------------------------
def test_engines_no_objects_githubusercontent():
    """engines.json sources must not use objects.githubusercontent.com (returns 400)."""
    data = json.loads((REPO / "catalog" / "engines.json").read_text(encoding="utf-8"))
    violations = []
    for e in data.get("engines", []):
        for url in e.get("sources", []):
            if "objects.githubusercontent.com" in url:
                violations.append(f"{e['id']}: {url}")
    assert not violations, f"Invalid objects.githubusercontent.com URLs: {violations[:5]}"


def test_engines_have_at_least_two_sources():
    """Every engine must have >=2 sources (multi-mirror guarantee)."""
    data = json.loads((REPO / "catalog" / "engines.json").read_text(encoding="utf-8"))
    violations = []
    for e in data.get("engines", []):
        if len(e.get("sources", [])) < 2:
            violations.append(f"{e['id']}: {len(e.get('sources',[]))} sources")
    assert not violations, f"Engines with <2 sources: {violations}"


# ---------------------------------------------------------------------------
# engines.json must not contain known-dead GitHub repos (404)
# ---------------------------------------------------------------------------
def test_engines_no_dead_github_repos():
    """engines.json sources must not use known 404 GitHub repos."""
    data = json.loads((REPO / "catalog" / "engines.json").read_text(encoding="utf-8"))
    dead_patterns = [
        "IndexTeam/IndexTTS-2",       # renamed to index-tts/index-tts
        "ResembleAI/chatterbox",      # renamed to resemble-ai/chatterbox
        "thu-ml-lab/Direct3D-S2",     # moved to DreamTechAI/Direct3D-S2
        "VAST-AI/TripoSR",            # org is VAST-AI-Research
        "VAST-AI/TripoSG",            # org is VAST-AI-Research
    ]
    violations = []
    for e in data.get("engines", []):
        for url in e.get("sources", []):
            for pat in dead_patterns:
                if pat in url:
                    violations.append(f"{e['id']}: {url}")
    assert not violations, f"Dead GitHub repo URLs in engines: {violations}"


def test_engines_no_release_asset_download_urls():
    """engines.json must not contain /releases/latest/download/ URLs (often 404)."""
    data = json.loads((REPO / "catalog" / "engines.json").read_text(encoding="utf-8"))
    violations = []
    for e in data.get("engines", []):
        for url in e.get("sources", []):
            if "/releases/latest/download/" in url:
                violations.append(f"{e['id']}: {url}")
    assert not violations, f"Release asset download URLs in sources: {violations}"
