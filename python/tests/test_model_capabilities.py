"""Every catalog model carries its own capability manifest.

The market UI and generation forms must be driven by these manifests rather
than by a category-wide assumption.  These tests are deliberately about
structure and known edge cases, not about the exact wording of UI labels.
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CATALOG = ROOT / "catalog" / "models.json"


def _models() -> list[dict]:
    return json.loads(CATALOG.read_text(encoding="utf-8"))["models"]


def _by_id() -> dict[str, dict]:
    return {m["id"]: m for m in _models()}


def test_every_model_has_capability():
    missing = [m["id"] for m in _models() if not m.get("capability")]
    assert not missing, f"models without capability: {missing[:10]}"


def test_capability_structure():
    allowed_inputs = {"text", "image", "audio", "video"}
    allowed_outputs = {"image", "video", "audio", "3d", "text", "mask", "labels", "embedding"}
    for m in _models():
        cap = m["capability"]
        assert isinstance(cap.get("tasks"), list) and cap["tasks"], m["id"]
        assert isinstance(cap.get("inputs"), dict) and cap["inputs"], m["id"]
        assert isinstance(cap.get("outputs"), dict) and cap["outputs"], m["id"]
        assert set(cap["inputs"]).issubset(allowed_inputs), m["id"]
        assert set(cap["outputs"]).issubset(allowed_outputs), m["id"]
        assert isinstance(cap.get("controls"), list), m["id"]
        assert cap.get("source"), m["id"]
        assert cap.get("confidence") in {
            "inferred", "official-pipeline", "official-readme",
            "official-pipeline+standard-cfg", "curated-override",
        }, m["id"]


def test_prompt_control_requires_text_input():
    for m in _models():
        cap = m["capability"]
        if "prompt" in cap["controls"]:
            assert "text" in cap["inputs"], m["id"]


def test_required_image_has_image_input():
    for m in _models():
        cap = m["capability"]
        image = cap["inputs"].get("image")
        if image and image.get("required"):
            assert "image" in cap["controls"] or m["category"] == "vision", m["id"]


def test_known_video_models_are_not_generic():
    m = _by_id()
    assert "image" not in m["wan2.2-t2v"]["capability"]["inputs"]
    assert m["wan2.2-i2v"]["capability"]["inputs"]["image"]["required"] is True
    assert "prompt" not in m["svd-xt"]["capability"]["controls"]
    assert m["svd-xt"]["capability"]["inputs"]["image"]["required"] is True
    assert m["wan2.2-s2v"]["capability"]["inputs"]["audio"]["required"] is True
    assert m["wan2.2-s2v"]["capability"]["outputs"]["video"]["audio"] is True
    assert m["ltx-2.3"]["capability"]["outputs"]["video"]["audio"] is True


def test_known_audio_and_3d_models_are_not_generic():
    m = _by_id()
    assert "image" not in m["musicgen-large"]["capability"]["inputs"]
    assert m["musicgen-large"]["capability"]["outputs"] == {"audio": {"format": "wav"}}
    assert m["shap-e"]["capability"]["outputs"]["3d"]["format"] == "glb"
    assert "image" not in m["trellis-text-xlarge"]["capability"]["inputs"]
    assert m["instant-mesh"]["capability"]["inputs"]["image"]["required"] is True


def test_vision_models_do_not_claim_image_output():
    m = _by_id()
    for model_id in ("sam3", "clip-vit-l", "yolov10", "pp-ocrv6-det", "pp-ocrv6-rec"):
        assert "image" not in m[model_id]["capability"]["outputs"], model_id

def test_catalog_model_dump_keeps_capability():
    import sys
    sys.path.insert(0, str(ROOT / "python"))
    from app.catalog import load_catalog

    catalog, _engines = load_catalog(ROOT / "catalog")
    dumped = catalog.models[0].model_dump()
    assert dumped.get("capability"), "capability was dropped by the API model schema"
    assert dumped["capability"]["tasks"]
