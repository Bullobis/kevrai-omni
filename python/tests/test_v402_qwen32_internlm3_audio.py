"""Regression tests for Qwen2.5-32B, InternLM3-8B, Qwen2-Audio-7B."""
from __future__ import annotations

import json
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_BY_ID = {
    m["id"]: m
    for m in json.loads((_ROOT / "catalog" / "models.json").read_text(encoding="utf-8"))["models"]
}
_ORDER = ["Q2_K", "Q3_K_M", "IQ4_NL", "Q4_K_M", "Q5_K_M", "Q8_0", "F16"]
_RANK = {k: i for i, k in enumerate(_ORDER)}


def test_total_grew():
    assert len(_BY_ID) >= 234


def test_repos_and_gguf():
    assert _BY_ID["qwen2.5-32b"]["gguf_repo"] == "bartowski/Qwen2.5-32B-Instruct-GGUF"
    assert _BY_ID["internlm3-8b"]["repo"] == "internlm/internlm3-8b-instruct"


def test_qwen2audio_transformers_audio():
    m = _BY_ID["qwen2-audio-7b"]
    assert "transformers" in m["engine"]
    assert m["modality"]["multimodal"] and "audio" in m["modality"]["understand"]
    assert "gguf_repo" not in m


def test_all_apache():
    for mid in ("qwen2.5-32b", "internlm3-8b", "qwen2-audio-7b"):
        assert _BY_ID[mid]["license"] == "apache-2.0"


def test_gguf_quants_ordered_monotonic():
    for mid in ("qwen2.5-32b", "internlm3-8b"):
        q = _BY_ID[mid]["quantizations"]
        ranks = [_RANK[x["id"]] for x in q]
        assert ranks == sorted(ranks), mid
        sizes = [x["size_gb"] for x in q]
        assert sizes == sorted(sizes), mid
        assert len([x for x in q if x["recommended"]]) == 1
        for x in q:
            assert x["vram_gb"] >= x["size_gb"]
