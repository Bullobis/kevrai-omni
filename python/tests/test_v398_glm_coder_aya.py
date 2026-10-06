"""Regression tests for GLM-4-9B, Qwen2.5-Coder-7B, Aya-Expanse-8B."""
from __future__ import annotations

import json
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_MODELS = json.loads((_ROOT / "catalog" / "models.json").read_text(encoding="utf-8"))["models"]
_BY_ID = {m["id"]: m for m in _MODELS}
_ORDER = ["Q2_K", "Q3_K_M", "IQ4_NL", "Q4_K", "Q4_K_M", "Q5_K_M", "Q8_0", "F16"]
_RANK = {k: i for i, k in enumerate(_ORDER)}
_NEW = ("glm-4-9b", "qwen2.5-coder-7b", "aya-expanse-8b")


def test_total_grew():
    assert len(_MODELS) >= 231


def test_repos_and_gguf():
    assert _BY_ID["glm-4-9b"]["gguf_repo"] == "legraphista/glm-4-9b-chat-IMat-GGUF"
    assert _BY_ID["qwen2.5-coder-7b"]["repo"] == "Qwen/Qwen2.5-Coder-7B-Instruct"
    assert _BY_ID["aya-expanse-8b"]["gguf_repo"] == "bartowski/aya-expanse-8b-GGUF"


def test_licenses_with_caveats():
    assert _BY_ID["glm-4-9b"]["license"] == "other"
    assert _BY_ID["qwen2.5-coder-7b"]["license"] == "apache-2.0"
    # Aya is non-commercial
    assert _BY_ID["aya-expanse-8b"]["license"] == "cc-by-nc-4.0"
    assert "非商业" in _BY_ID["aya-expanse-8b"]["hardware"].get("notes", "")


def test_quants_ordered_monotonic_one_recommended():
    for mid in _NEW:
        q = _BY_ID[mid]["quantizations"]
        ranks = [_RANK[x["id"]] for x in q]
        assert ranks == sorted(ranks), mid
        sizes = [x["size_gb"] for x in q]
        assert sizes == sorted(sizes), mid
        assert len([x for x in q if x["recommended"]]) == 1, mid
        for x in q:
            assert x["vram_gb"] >= x["size_gb"]
