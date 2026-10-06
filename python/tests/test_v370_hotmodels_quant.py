"""v3.7.0 hot-models + quantizations regression tests.

Locks in the 11 newly added trending models and the catalog ``quantizations``
data structure that powers the install-time quantization picker. Every slug,
size_gb and license value was verified against the HuggingFace API
(blobs=true) on 2026-10-06; per-quantization sizes come from enumerating the
actual .gguf files in each GGUF repository (single-file and directory/multi-
shard layouts). No value here is a parameter-count estimate.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

PYDIR = Path(__file__).resolve().parents[1]
REPO = PYDIR.parent
sys.path.insert(0, str(_p := str(PYDIR)))


def _catalog():
    return json.loads((REPO / "catalog" / "models.json").read_text(encoding="utf-8"))


def _models():
    return {m["id"]: m for m in _catalog()["models"]}


# 15 newly added trending models
NEW_MODELS = [
    "qwen3-0.6b",
    "qwen3-1.7b",
    "qwen3-4b",
    "qwen3-8b",
    "qwen3-14b",
    "qwq-32b",
    "nex-n2.5-mini",
    "nex-n2.5-pro",
    "qwen3.8-flash-next",
    "kolibri-1",
    "hemmingway-1",
    "flux.2-klein-4b",
    "z-image-turbo",
    "breeze-tts-2",
    "voxcpm2",
]

# Expected (repo, license) verified via HF API
NEW_FACTS = {
    "qwen3-0.6b": ("Qwen/Qwen3-0.6B", "Apache-2.0"),
    "qwen3-1.7b": ("Qwen/Qwen3-1.7B", "Apache-2.0"),
    "qwen3-4b": ("Qwen/Qwen3-4B", "Apache-2.0"),
    "qwen3-8b": ("Qwen/Qwen3-8B", "Apache-2.0"),
    "qwen3-14b": ("Qwen/Qwen3-14B", "Apache-2.0"),
    "qwq-32b": ("Qwen/QwQ-32B", "Apache-2.0"),
    "nex-n2.5-mini": ("nex-agi/Nex-N2.5-mini", "Apache-2.0"),
    "nex-n2.5-pro": ("nex-agi/Nex-N2.5-Pro", "Apache-2.0"),
    "qwen3.8-flash-next": ("Qwen/Qwen3.8-Flash-Next", "Qwen 社区许可（other, qwen-community-1.0）"),
    "kolibri-1": ("Aleph-Alpha/Kolibri-1", "Apache-2.0"),
    "hemmingway-1": ("Altworld/Hemmingway-1", "CC-BY-NC-4.0"),
    "flux.2-klein-4b": ("black-forest-labs/FLUX.2-klein-4B", "Apache-2.0"),
    "z-image-turbo": ("Tongyi-MAI/Z-Image-Turbo", "Apache-2.0"),
    "breeze-tts-2": ("BreezeBlue/Breeze-TTS-2", "其他（other）"),
    "voxcpm2": ("openbmb/VoxCPM2", "Apache-2.0"),
}


def test_total_model_count_at_least_201():
    """201 after PR #98; later branches may grow further (lower bound)."""
    assert len(_catalog()["models"]) >= 201


@pytest.mark.parametrize("mid", NEW_MODELS)
def test_new_model_present(mid):
    assert mid in _models()


@pytest.mark.parametrize("mid", NEW_MODELS)
def test_new_model_repo_and_license(mid):
    m = _models()[mid]
    repo, lic = NEW_FACTS[mid]
    assert m["repo"] == repo
    assert m["license"] == lic


@pytest.mark.parametrize("mid", NEW_MODELS)
def test_new_model_has_two_distinct_sources(mid):
    m = _models()[mid]
    srcs = {s.rstrip("/") for s in m["sources"]}
    assert len(srcs) >= 2
    # at least one HuggingFace-family source
    assert any("huggingface.co" in s or "hf-mirror.com" in s for s in srcs)


@pytest.mark.parametrize("mid", NEW_MODELS)
def test_new_model_has_positive_size_and_hardware(mid):
    m = _models()[mid]
    assert m["size_gb"] > 0
    hw = m["hardware"]
    assert hw["disk_gb"] >= m["size_gb"]
    assert hw["ram_gb"] > 0


def test_hemmingway_noncommercial_license_noted():
    """CC-BY-NC is non-commercial; the entry must surface that fact."""
    m = _models()["hemmingway-1"]
    assert m["license"] == "CC-BY-NC-4.0"
    assert "非商用" in m["hardware"]["notes"]


def test_flash_next_is_multimodal():
    m = _models()["qwen3.8-flash-next"]
    assert m["modality"]["multimodal"] is True
    assert "image" in m["modality"]["understand"]


# ---------------------------------------------------------------------------
# quantizations data structure
# ---------------------------------------------------------------------------
QUANT_ORDER = ["PTQ1_0", "PQ2_0", "Q2_K", "Q3_K_M", "IQ4_NL", "Q4_K", "Q4_K_M", "Q5_K_M", "Q6_K", "Q8_0", "F16", "LOSSLESS"]


def test_at_least_30_models_declare_quantizations():
    n = sum(1 for m in _models().values() if m.get("quantizations"))
    assert n >= 40


@pytest.mark.parametrize("mid", NEW_MODELS)
def test_new_gguf_models_declare_quantizations(mid):
    m = _models()[mid]
    if m.get("gguf_repo"):
        q = m.get("quantizations")
        assert q, f"{mid} has gguf_repo but no quantizations"
        assert len(q) >= 2


def test_quantizations_well_formed():
    for mid, m in _models().items():
        q = m.get("quantizations")
        if not q:
            continue
        ids = [v["id"] for v in q]
        # unique ids
        assert len(ids) == len(set(ids)), mid
        # ordered by compression
        ranks = [QUANT_ORDER.index(i) for i in ids]
        assert ranks == sorted(ranks), mid
        # exactly one recommended
        recs = [v for v in q if v.get("recommended")]
        assert len(recs) == 1, mid
        for v in q:
            assert v["size_gb"] > 0, (mid, v["id"])
            assert v["vram_gb"] >= v["size_gb"], (mid, v["id"])


def test_quantization_sizes_monotonic():
    for mid, m in _models().items():
        q = m.get("quantizations")
        if not q:
            continue
        sizes = [v["size_gb"] for v in q]
        assert sizes == sorted(sizes), mid


def test_q4_km_recommended_when_available():
    for mid, m in _models().items():
        q = m.get("quantizations")
        if not q:
            continue
        ids = {v["id"] for v in q}
        rec = next(v for v in q if v.get("recommended"))
        if "Q4_K_M" in ids:
            assert rec["id"] == "Q4_K_M", mid


def _engines():
    return json.loads((REPO / "catalog" / "engines.json").read_text(encoding="utf-8"))


def _collect_urls(obj, out):
    if isinstance(obj, dict):
        for v in obj.values():
            _collect_urls(v, out)
    elif isinstance(obj, list):
        for v in obj:
            _collect_urls(v, out)
    elif isinstance(obj, str) and obj.startswith("http"):
        out.append(obj)


def test_no_dead_tuna_pypi_prefix():
    """mirrors.tuna.tsinghua.edu.cn/pypi/simple 404s; the working host is
    pypi.tuna.tsinghua.edu.cn/simple (verified 2026-10-06)."""
    urls: list[str] = []
    _collect_urls(_engines(), urls)
    dead = [u for u in urls if "mirrors.tuna.tsinghua.edu.cn/pypi" in u]
    assert dead == []
    # and the correct form is present
    good = [u for u in urls if "pypi.tuna.tsinghua.edu.cn/simple/" in u]
    assert len(good) >= 7


def test_small_qwen3_quant_sizes_locked():
    """Spot-check verified per-quant sizes for Qwen3-4B."""
    m = _models()["qwen3-4b"]
    q = {v["id"]: v["size_gb"] for v in m["quantizations"]}
    assert q["Q4_K_M"] == 2.33
    assert q["Q8_0"] == 3.99
    assert q["F16"] == 7.5
