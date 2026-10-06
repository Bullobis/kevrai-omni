"""Regression tests for the Laya non-autoregressive decision model addition."""
from __future__ import annotations

import json
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_MODELS = json.loads((_ROOT / "catalog" / "models.json").read_text(encoding="utf-8"))["models"]
_BY_ID = {m["id"]: m for m in _MODELS}


def test_total_grew_to_208():
    assert len(_MODELS) >= 208


def test_laya_present_and_repo():
    m = _BY_ID["laya-decision"]
    assert m["repo"] == "convaiinnovations/laya"
    assert m["license"] == "Apache-2.0"


def test_laya_is_classification_not_generation():
    m = _BY_ID["laya-decision"]
    mod = m["modality"]
    assert mod["multimodal"] is False
    assert "text" in mod["understand"]


def test_laya_runs_on_python_cpu():
    m = _BY_ID["laya-decision"]
    assert "python" in m["engine"]
    assert m["hardware"]["vram_gb"] == 0


def test_laya_sources_two_distinct_hosts():
    m = _BY_ID["laya-decision"]
    hosts = {s.split("/")[2] for s in m["sources"]}
    assert len(hosts) >= 2
