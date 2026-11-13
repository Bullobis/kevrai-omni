"""Availability verdicts must stay honest and cover every catalog entry."""
from __future__ import annotations

import json
from pathlib import Path

from app import availability

ROOT = Path(__file__).resolve().parents[2]
MODELS = json.loads((ROOT / "catalog" / "models.json").read_text(encoding="utf-8"))["models"]


def test_every_catalog_entry_is_downloadable():
    for m in MODELS:
        verdict = availability.describe(m)
        assert verdict["downloadable"] is True, m["id"]
        assert verdict["status"] in {"native", "external", "unsupported"}


def test_llama_cpp_without_gguf_is_not_claimed_native():
    # GLiNER2.5 is an entity-linking model, not a causal LLM; llama.cpp is
    # listed as an upstream label but has no GGUF repo and no adapter.
    m = next(x for x in MODELS if x["id"] == "gliner2.5")
    v = availability.describe(m)
    assert "llama.cpp" in v["engines"]
    assert all(x["engine"] != "llama.cpp" for x in v["native"]), (
        "GLiNER2.5 has no GGUF repo; llama.cpp must not be advertised as native"
    )


def test_image_models_have_a_native_diffusers_path():
    images = [m for m in MODELS if m["category"] == "image"]
    assert images
    for m in images:
        v = availability.describe(m)
        assert v["status"] in {"native", "external"}, m["id"]
        assert any(x["engine"] == "diffusers" for x in v["native"]), m["id"]


def test_video_and_audio_models_have_a_generic_diffusers_path():
    for m in MODELS:
        if m["category"] not in ("video", "audio"):
            continue
        if "diffusers" not in (m.get("engine") or []):
            continue
        v = availability.describe(m)
        assert v["status"] == "native", m["id"]
        assert any(
            x["action"] in ("video", "audio") for x in v["native"]
        ), m["id"]


def test_unsupported_mnn_model_offers_conversion():
    m = next(x for x in MODELS if x["id"] == "real-esrgan")
    v = availability.describe(m)
    assert v["status"] == "unsupported"
    assert v["conversion"]["kind"] in ("hf-to-mnn", "torch-to-onnx")


def test_summary_counts_add_up():
    s = availability.summary(MODELS)
    assert s["total"] == len(MODELS)
    assert sum(s["counts"].values()) == len(MODELS)
    assert sum(sum(v.values()) for v in s["by_category"].values()) == len(MODELS)
