"""Regression tests for SDXL Base and HiDream-I1-Fast."""
from __future__ import annotations

import json
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_BY_ID = {
    m["id"]: m
    for m in json.loads((_ROOT / "catalog" / "models.json").read_text(encoding="utf-8"))["models"]
}


def test_total_grew():
    assert len(_BY_ID) >= 236


def test_sdxl():
    m = _BY_ID["sdxl-base"]
    assert m["repo"] == "stabilityai/stable-diffusion-xl-base-1.0"
    assert m["license"] == "openrail++"
    assert "diffusers" in m["engine"]
    assert m["modality"]["generate"] == ["image"]


def test_hidream_fast():
    m = _BY_ID["hidream-i1-fast"]
    assert m["repo"] == "HiDream-ai/HiDream-I1-Fast"
    assert m["license"] == "mit"
    assert m["size_gb"] > 40
