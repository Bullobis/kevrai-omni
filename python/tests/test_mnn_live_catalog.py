"""Regression: MNN market must not stop at the 15 hand-picked rows.

The live org listing is filtered to loadable chat repos and merged behind the
curated rows (which keep their stable entry ids and richer metadata).
"""
from __future__ import annotations

from app import mnn_catalog as mc


def _row(repo: str, pipeline: str = "text-generation") -> dict:
    return {"id": f"taobao-mnn/{repo}", "pipeline_tag": pipeline,
            "createdAt": "2026-11-01T00:00:00.000Z"}


def test_live_rows_filters_non_chat_and_duplicate_repos():
    data = [
        _row("Qwen3.5-0.8B-MNN"),
        _row("Qwen3.5-4B-Base-MNN"),          # base checkpoint
        _row("MNN-Pocket-0.3B-GGUF"),          # GGUF, not MNN runtime
        _row("bge-large-zh-MNN"),              # embedding
        _row("Qwen2-Audio-7B-Instruct-MNN"),   # audio
        _row("stable-diffusion-v1-5-mnn-opencl"),
        _row("Qwen3.6-27B-MNN"),                # already curated
        _row("Qwen2.5-VL-3B-Instruct-MNN", "image-text-to-text"),
    ]
    rows = mc._live_rows(data)
    repos = [r["repo"] for r in rows]
    assert "taobao-mnn/Qwen3.5-0.8B-MNN" in repos
    assert "taobao-mnn/Qwen2.5-VL-3B-Instruct-MNN" in repos
    assert "taobao-mnn/Qwen3.5-4B-Base-MNN" not in repos
    assert "taobao-mnn/MNN-Pocket-0.3B-GGUF" not in repos
    assert "taobao-mnn/bge-large-zh-MNN" not in repos
    assert "taobao-mnn/Qwen2-Audio-7B-Instruct-MNN" not in repos
    assert "taobao-mnn/stable-diffusion-v1-5-mnn-opencl" not in repos
    assert "taobao-mnn/Qwen3.6-27B-MNN" not in repos
    assert rows[0]["live"] is True
    assert rows[0]["dest_id"] == "Qwen3.5-0.8B-MNN"


def test_market_list_merges_live_rows_after_curated(monkeypatch):
    dynamic = mc._live_rows([_row("Qwen3.5-0.8B-MNN")])
    monkeypatch.setattr(mc, "live_market_list", lambda: dynamic)
    items = mc.market_list(live=True)
    assert len(items) == len(mc.MARKET) + 1
    assert all(e["live"] is False for e in items[:len(mc.MARKET)])
    assert items[-1]["live"] is True
    assert items[-1]["modality"]["generate"] == ["text"]


def test_market_list_offline_is_static_only():
    items = mc.market_list(live=False)
    assert len(items) == len(mc.MARKET)
    assert all(e["live"] is False for e in items)
