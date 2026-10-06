"""Regression tests for SD 1.5 and Stable Video Diffusion XT."""
from __future__ import annotations

import json
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_BY_ID = {
    m["id"]: m
    for m in json.loads((_ROOT / "catalog" / "models.json").read_text(encoding="utf-8"))["models"]
}


def test_total_grew():
    assert len(_BY_ID) >= 238


def test_sd15():
    m = _BY_ID["sd15-base"]
    assert m["repo"] == "stable-diffusion-v1-5/stable-diffusion-v1-5"
    assert m["license"] == "creativeml-openrail-m"
    assert m["category"] == "image" and m["modality"]["generate"] == ["image"]


def test_svd_xt():
    m = _BY_ID["svd-xt"]
    assert m["repo"] == "stabilityai/stable-video-diffusion-img2vid-xt"
    assert m["category"] == "video" and m["modality"]["generate"] == ["video"]
    assert "image" in m["modality"]["understand"]
