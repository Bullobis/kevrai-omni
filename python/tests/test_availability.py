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


def test_gliner_is_claimed_through_its_own_adapter():
    # GLiNER2.5 is a classification head, not a causal LLM; it must be
    # native through gliner2, never through llama.cpp.
    m = next(x for x in MODELS if x["id"] == "gliner2.5")
    v = availability.describe(m)
    assert v["engines"] == ["gliner2"]
    assert [x["engine"] for x in v["native"]] == ["gliner2"]


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


def test_all_catalog_entries_have_a_runtime_or_external_path():
    for m in MODELS:
        v = availability.describe(m)
        assert v["status"] in {"native", "external"}, m["id"]


def test_conversion_is_offered_for_a_model_without_runtime():
    from types import SimpleNamespace

    fake = SimpleNamespace(
        id="fake-conversion", category="llm", repo="owner/repo",
        engine=["mnn"], mnn_repo="", name="fake",
    )
    v = availability.describe(fake)
    assert v["status"] == "unsupported"
    assert v["conversion"]["kind"] == "hf-to-mnn"


def test_missing_engine_moves_to_external_not_unsupported():
    # AuK's official path is SGLang-Omni; it must be reported as external,
    # not as an unsupported model that cannot be used.
    m = next(x for x in MODELS if x["id"] == "auk")
    v = availability.describe(m)
    assert v["status"] == "external"
    assert any(x["engine"] == "sglang-omni" for x in v["external"])


def test_kokoro_and_chatterbox_use_in_process_tts_not_external_service():
    # piper_runtime has real Kokoro / Chatterbox adapters; calling them
    # "external" made the UI tell users to start a service that does not exist.
    for model_id in ("kokoro-82m", "chatterbox"):
        m = next(x for x in MODELS if x["id"] == model_id)
        v = availability.describe(m)
        assert v["status"] == "native", model_id
        assert not v["external"], model_id
        assert any(x["action"] == "tts" for x in v["native"]), model_id


def test_diffusers_3d_models_use_the_local_three_d_runtime():
    # Shap-E / InstantMesh / MIDI-3D are diffusers-native; they must not be
    # reported as external-only just because they also list ComfyUI.
    for model_id in ("shap-e", "instant-mesh", "midi-3d"):
        m = next(x for x in MODELS if x["id"] == model_id)
        v = availability.describe(m)
        assert v["status"] == "native", model_id
        assert any(x["action"] == "3d" for x in v["native"]), model_id


def test_summary_counts_add_up():
    s = availability.summary(MODELS)
    assert s["total"] == len(MODELS)
    assert sum(s["counts"].values()) == len(MODELS)
    assert sum(sum(v.values()) for v in s["by_category"].values()) == len(MODELS)
