"""Tests for the super search engine (app.search)."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.search import (  # noqa: E402
    Corpus,
    SearchQuery,
    _edit_distance,
    compute_facets,
    get_corpus,
    search,
)
from app import search as search_mod  # noqa: E402


SAMPLE = [
    {"id": "ltx-2.5", "category": "video", "name": "LTX-2.5 (最新一代)",
     "repo": "Lightricks/LTX-2.5", "engine": ["diffusers", "comfyui"],
     "license": "LTX-Open", "size_gb": 95, "trending": True,
     "description": "2026 最新一代 LTX 世界模型：文生视频 / 图生视频 / LoRA",
     "tags": ["video", "t2v", "i2v"]},
    {"id": "wan2.2-t2v", "category": "video", "name": "Wan 2.2 T2V A14B",
     "repo": "Wan-AI/Wan2.2-T2V-A14B-Diffusers", "engine": ["diffusers"],
     "license": "Apache-2.0", "size_gb": 40, "trending": True,
     "description": "Wan 2.2 文生视频 14B 模型"},
    {"id": "qwen3-32b", "category": "llm", "name": "Qwen3 32B",
     "repo": "Qwen/Qwen3-32B", "engine": ["llama.cpp", "mnn"],
     "license": "Apache-2.0", "size_gb": 20, "trending": False,
     "description": "Qwen3 32B 大语言模型"},
    {"id": "kokoro", "category": "tts", "name": "Kokoro 82M",
     "repo": "hexgrad/Kokoro-82M", "engine": ["kokoro-engine"],
     "license": "Apache-2.0", "size_gb": 0.3, "trending": False,
     "description": "Kokoro 82M TTS 语音合成"},
    {"id": "flux-dev", "category": "image", "name": "FLUX.1 [dev]",
     "repo": "black-forest-labs/FLUX.1-dev", "engine": ["diffusers", "comfyui"],
     "license": "FLUX-1-dev", "size_gb": 24, "trending": True,
     "description": "FLUX.1 dev 图像生成模型"},
]


# ---------- tokenization / edit distance ----------

def test_edit_distance_basic():
    assert _edit_distance("koko", "koko") == 0
    assert _edit_distance("koko", "kok") == 1
    assert _edit_distance("koko", "koku") == 1
    assert _edit_distance("abc", "xyz") > 2


def test_edit_distance_capped():
    assert _edit_distance("a", "zzzzzzzz", cap=2) == 3  # cap+1


# ---------- basic search ----------

def test_empty_query_returns_all():
    r = search(SAMPLE, SearchQuery(q=""))
    assert r["count"] == 5
    assert r["elapsed_ms"] >= 0


def test_exact_name_match_ranks_first():
    r = search(SAMPLE, SearchQuery(q="LTX-2.5"))
    assert r["count"] >= 1
    assert r["items"][0]["id"] == "ltx-2.5"


def test_case_insensitive():
    r = search(SAMPLE, SearchQuery(q="ltx"))
    assert any(i["id"] == "ltx-2.5" for i in r["items"])


def test_chinese_query_matches():
    r = search(SAMPLE, SearchQuery(q="视频"))
    ids = [i["id"] for i in r["items"]]
    assert "ltx-2.5" in ids
    assert "wan2.2-t2v" in ids


def test_chinese_tts_matches():
    r = search(SAMPLE, SearchQuery(q="语音"))
    ids = [i["id"] for i in r["items"]]
    assert "kokoro" in ids


def test_substring_in_description():
    r = search(SAMPLE, SearchQuery(q="世界模型"))
    assert any(i["id"] == "ltx-2.5" for i in r["items"])


def test_typo_tolerance():
    # "kokro" is edit distance 1 from "kokoro"
    r = search(SAMPLE, SearchQuery(q="kokro"))
    assert any(i["id"] == "kokoro" for i in r["items"])


def test_multi_token_and():
    r = search(SAMPLE, SearchQuery(q="wan video"))
    ids = [i["id"] for i in r["items"]]
    # "wan" matches wan2.2, "video" matches category/description of video models
    assert "wan2.2-t2v" in ids


def test_no_results_gives_suggestions():
    # A query that matches nothing still returns a well-formed response with
    # a suggestions list (may be empty when nothing is close enough).
    r = search(SAMPLE, SearchQuery(q="zzzzzznomatch"))
    assert r["count"] == 0
    assert isinstance(r["suggestions"], list)


def test_corpus_suggest_near_miss():
    corpus = Corpus(SAMPLE)
    # "kokro" is edit distance 1 from the name token "kokoro"
    sug = corpus.suggest("kokro")
    assert "kokoro" in sug


# ---------- filters ----------

def test_category_filter():
    r = search(SAMPLE, SearchQuery(q="", category="video"))
    assert r["count"] == 2
    assert all(i["category"] == "video" for i in r["items"])


def test_engine_filter():
    r = search(SAMPLE, SearchQuery(q="", engine="mnn"))
    assert r["count"] == 1
    assert r["items"][0]["id"] == "qwen3-32b"


def test_trending_filter():
    r = search(SAMPLE, SearchQuery(q="", trending_only=True))
    assert r["count"] == 3
    assert all(i["trending"] for i in r["items"])


def test_size_bucket_filter():
    r = search(SAMPLE, SearchQuery(q="", size_bucket="15–40 GB"))
    assert r["count"] >= 1
    for i in r["items"]:
        assert 15 <= float(i["size_gb"]) < 40


# ---------- sorting ----------

def test_sort_name():
    r = search(SAMPLE, SearchQuery(q="", sort="name_asc"))
    names = [i["name"] for i in r["items"]]
    assert names == sorted(names, key=str.lower)


def test_sort_size_desc():
    r = search(SAMPLE, SearchQuery(q="", sort="size_desc"))
    sizes = [float(i["size_gb"]) for i in r["items"]]
    assert sizes == sorted(sizes, reverse=True)


def test_sort_trending():
    r = search(SAMPLE, SearchQuery(q="", sort="trending"))
    assert r["items"][0]["trending"] is True


# ---------- pagination ----------

def test_pagination():
    r = search(SAMPLE, SearchQuery(q="", page=1, page_size=2))
    assert len(r["items"]) == 2
    r2 = search(SAMPLE, SearchQuery(q="", page=2, page_size=2))
    assert len(r2["items"]) == 2
    assert r["items"][0]["id"] != r2["items"][0]["id"]


def test_page_size_capped():
    r = search(SAMPLE, SearchQuery(q="", page_size=99999))
    assert r["page_size"] == 200


# ---------- facets ----------

def test_facets_present():
    r = search(SAMPLE, SearchQuery(q=""))
    f = r["facets"]
    assert "engines" in f and "licenses" in f and "categories" in f and "sizes" in f
    eng_ids = [e["value"] for e in f["engines"]]
    assert "diffusers" in eng_ids
    assert "mnn" in eng_ids


# ---------- highlights ----------

def test_highlights_returned():
    r = search(SAMPLE, SearchQuery(q="LTX"))
    item = r["items"][0]
    assert "_highlights" in item
    assert item["_matched_fields"]
    assert any(h["field"] == "name" for h in item["_highlights"])


# ---------- corpus memoization ----------

def test_corpus_memoized():
    c1 = get_corpus(SAMPLE)
    c2 = get_corpus(SAMPLE)
    assert c1 is c2


# ---------- recent searches ----------

def test_recent_searches_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    # re-import to pick up patched settings path
    import importlib
    importlib.reload(search_mod)
    search_mod.push_recent("ltx video")
    search_mod.push_recent("qwen")
    recent = search_mod.recent_searches()
    assert recent[0] == "qwen"  # most recent first
    assert "ltx video" in recent
    # dedupe
    search_mod.push_recent("qwen")
    assert search_mod.recent_searches().count("qwen") == 1


# ---------- injection / extreme inputs ----------

def test_regex_special_chars_in_query():
    # Queries containing regex metacharacters must not crash and must not
    # execute as regex (they are treated as literals).
    r = search(SAMPLE, SearchQuery(q=".*+?^${}()|[]\\"))
    assert r["count"] == 0


def test_long_query_does_not_crash():
    r = search(SAMPLE, SearchQuery(q="a" * 5000))
    assert r["count"] == 0


def test_empty_unicode_query():
    r = search(SAMPLE, SearchQuery(q="   "))
    assert r["count"] == 5


def test_emoji_query():
    r = search(SAMPLE, SearchQuery(q="🔥"))
    assert isinstance(r["items"], list)


def test_html_in_query_escaped_by_consumer():
    # The search engine itself does not escape; it returns raw offsets.
    # Ensure no crash on HTML-like input.
    r = search(SAMPLE, SearchQuery(q="<script>alert(1)</script>"))
    assert r["count"] == 0


def test_null_bytes_in_query():
    r = search(SAMPLE, SearchQuery(q="\x00\x00"))
    assert isinstance(r["items"], list)


def test_missing_fields_do_not_crash():
    weird = [{"id": "x"}, {"name": "no id"}, {}]
    r = search(weird, SearchQuery(q=""))
    assert r["count"] == 3


def test_cjk_bigram_does_not_match_unrelated_models():
    """Regression: '视频' must not match every model via single-char
    tokenization + loose edit-distance."""
    data = [
        {"id": "video-model", "name": "LTX 视频生成", "category": "video",
         "tags": ["视频", "t2v"], "description": "文生视频"},
        {"id": "llm-model", "name": "Qwen3 32B", "category": "llm",
         "tags": ["大模型", "对话", "语言模型"],
         "description": "通义千问 大语言模型，推理+Agent"},
        {"id": "tts-model", "name": "ChatTTS", "category": "tts",
         "tags": ["语音合成"], "description": "文字转语音"},
    ]
    r = search(data, SearchQuery(q="视频"))
    ids = [it["id"] for it in r["items"]]
    assert "video-model" in ids
    assert "llm-model" not in ids
    assert "tts-model" not in ids


def test_cjk_bigram_ranks_exact_match_first():
    data = [
        {"id": "a", "name": "视频生成模型", "category": "video"},
        {"id": "b", "name": "视觉频率工具", "category": "other"},
    ]
    r = search(data, SearchQuery(q="视频"))
    assert r["items"][0]["id"] == "a"
