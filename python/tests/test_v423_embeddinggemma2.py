"""Regression: google/embeddinggemma-2 catalog entry (HF + ModelScope)."""
from __future__ import annotations

from pathlib import Path

from app.catalog import load_catalog

CATALOG_DIR = Path(__file__).resolve().parent.parent.parent / "catalog"


def _model():
    catalog, _ = load_catalog(CATALOG_DIR)
    hits = [m for m in catalog.models if m.id == "embeddinggemma-2"]
    assert hits, "embeddinggemma-2 missing from catalog"
    return hits[0]


def test_embeddinggemma2_basic_fields():
    m = _model()
    assert m.category == "embedding"
    assert m.repo == "google/embeddinggemma-2"
    assert m.license == "apache-2.0"
    assert "sentence-transformers" in m.engine


def test_embeddinggemma2_sources_distinct_platforms():
    m = _model()
    hosts = sorted({u.split("/")[2] for u in m.sources})
    assert hosts == ["huggingface.co", "modelscope.cn"]
    assert len(m.sources) >= 2


def test_embeddinggemma2_size_and_modality():
    m = _model()
    # weights verified at 1,488,915,288 bytes (~1.49 GB)
    assert 1.3 <= m.size_gb <= 1.7
    assert m.hardware["min_vram_gb"] >= 2
    assert "embedding" in m.modality["generate"]
    assert "text" in m.modality["understand"]
