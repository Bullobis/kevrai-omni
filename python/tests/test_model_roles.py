"""Tests for model capability tags + role slots (app.model_roles).

Cherry Studio's lesson under test: capabilities must come from *structured*
fields (never regex-guessed from an ID), unknown must stay unknown, and role
slots must degrade honestly when nothing is installed.
"""
from __future__ import annotations

import pytest

from app.model_roles import (
    CAPABILITIES,
    RAG_DEFAULTS,
    ROLES,
    UNKNOWN,
    OverrideStore,
    assign_roles,
    capabilities_or_unknown,
    derive_capabilities,
    fits_budget,
    pick_for_role,
)


def _m(mid, category="llm", tags=None, size=8.0, vram=8, min_vram=None,
       multimodal=False, generate=None, trending=False):
    hw = {"vram_gb": vram}
    if min_vram is not None:
        hw["min_vram_gb"] = min_vram
    return {
        "id": mid, "category": category, "name": mid.upper(),
        "tags": tags or [], "size_gb": size, "hardware": hw,
        "modality": {"multimodal": multimodal, "generate": generate or []},
        "trending": trending,
    }


# ---------------------------------------------------------------------------
# capability derivation
# ---------------------------------------------------------------------------

def test_llm_gets_chat():
    assert "chat" in derive_capabilities(_m("a", "llm"))


def test_image_category_gets_image_gen():
    assert derive_capabilities(_m("b", "image")) == ["image_gen"]


def test_3d_category_gets_three_d():
    assert derive_capabilities(_m("c", "3d")) == ["three_d"]


def test_video_category_gets_video_gen():
    assert derive_capabilities(_m("d", "video")) == ["video_gen"]


def test_embedding_category():
    assert derive_capabilities(_m("e", "embedding")) == ["embedding"]


def test_audio_category_is_not_guessed():
    """audio 是宽泛大类，细分不出就保持为空——不猜。"""
    assert derive_capabilities(_m("f", "audio")) == []


def test_audio_with_asr_tag():
    assert derive_capabilities(_m("g", "audio", tags=["ASR"])) == ["asr"]


def test_audio_with_chinese_music_tag():
    assert derive_capabilities(_m("h", "audio", tags=["音乐"])) == ["music"]


def test_pending_has_no_capabilities_even_with_tags():
    """官方未发布权重 → 明确无能力，不能被 tags 加回来。"""
    assert derive_capabilities(_m("i", "pending", tags=["chat", "asr"])) == []


def test_multimodal_flag_grants_vision():
    assert "vision" in derive_capabilities(_m("j", "llm", multimodal=True))


def test_modality_generate_image():
    assert "image_gen" in derive_capabilities(_m("k", "llm", generate=["image"]))


def test_modality_generate_video():
    assert "video_gen" in derive_capabilities(_m("l", "llm", generate=["video"]))


def test_tag_reasoning():
    assert "reasoning" in derive_capabilities(_m("m", "llm", tags=["推理"]))


def test_tag_function_calling_aliases():
    for tag in ("tool", "function-calling", "工具调用", "agent"):
        assert "function_calling" in derive_capabilities(_m(tag, "llm", tags=[tag]))


def test_tag_rerank():
    assert "rerank" in derive_capabilities(_m("n", "llm", tags=["重排"]))


def test_derived_caps_are_sorted_and_unique():
    caps = derive_capabilities(_m("o", "llm", tags=["chat", "推理", "tool"]))
    assert caps == sorted(set(caps))
    assert len(caps) == len(set(caps))


def test_all_derived_caps_are_known_vocabulary():
    caps = derive_capabilities(_m("p", "llm", tags=["chat", "推理", "tool", "多模态"]))
    assert all(c in CAPABILITIES for c in caps)


def test_unknown_tag_is_ignored_not_invented():
    caps = derive_capabilities(_m("q", "llm", tags=["totally-made-up"]))
    assert caps == ["chat"]  # 只保留 category 推出的，未知 tag 不产生新能力


def test_capabilities_or_unknown_never_empty():
    assert capabilities_or_unknown(_m("r", "pending")) == [UNKNOWN]


def test_missing_optional_fields_do_not_crash():
    assert derive_capabilities({"id": "s", "category": "llm"}) == ["chat"]
    assert derive_capabilities({}) == []


def test_no_id_regex_guessing():
    """核心回归：名字里带 'vision' 不会凭空获得看图能力。

    Cherry Studio 明确反对用 ID 正则猜能力——这里守住这条线。
    """
    m = _m("super-vision-3000", "llm")
    assert "vision" not in derive_capabilities(m)
    assert derive_capabilities(m) == ["chat"]


# ---------------------------------------------------------------------------
# VRAM budget
# ---------------------------------------------------------------------------

def test_fits_budget_uses_min_vram_first():
    m = _m("t", vram=24, min_vram=10)
    assert fits_budget(m, 12) is True
    assert fits_budget(m, 8) is False


def test_fits_budget_falls_back_to_nominal():
    m = _m("u", vram=24)  # 无 min_vram
    assert fits_budget(m, 24) is True
    assert fits_budget(m, 12) is False


def test_unknown_vram_always_fits():
    """未标注显存不该被预算排除——宁可展示，不要藏起来。"""
    m = {"id": "v", "category": "llm", "hardware": {}}
    assert fits_budget(m, 1) is True


def test_none_budget_means_no_constraint():
    assert fits_budget(_m("w", vram=80), None) is True


# ---------------------------------------------------------------------------
# role selection
# ---------------------------------------------------------------------------

def test_unknown_role_raises():
    with pytest.raises(KeyError):
        pick_for_role("nope", [], installed_ids=[])


def test_no_installed_models_leaves_slot_unfilled():
    c = pick_for_role("chat", [_m("x")], installed_ids=[])
    assert c.model_id == ""
    assert c.to_dict()["filled"] is False
    assert "没有已安装" in c.reason


def test_chat_picks_largest_within_budget():
    models = [_m("small", size=4), _m("big", size=30, vram=24)]
    c = pick_for_role("chat", models, installed_ids=["small", "big"])
    assert c.model_id == "big"


def test_chat_respects_vram_budget():
    models = [_m("small", size=4, vram=8), _m("big", size=30, vram=48)]
    c = pick_for_role("chat", models, vram_budget_gb=8, installed_ids=["small", "big"])
    assert c.model_id == "small"


def test_fast_picks_smallest():
    """Cherry Studio：后台小任务就该挑最小最快的。"""
    models = [_m("small", size=2), _m("mid", size=8), _m("big", size=30)]
    c = pick_for_role("fast", models, installed_ids=["small", "mid", "big"])
    assert c.model_id == "small"
    assert "最小的" in c.reason


def test_role_requires_matching_capability():
    models = [_m("textonly", "llm")]
    c = pick_for_role("image", models, installed_ids=["textonly"])
    assert c.model_id == ""
    assert c.candidates == 0


def test_vision_role_needs_vision_capability():
    plain = _m("plain", "llm")
    vis = _m("vis", "llm", multimodal=True)
    c = pick_for_role("vision", [plain, vis], installed_ids=["plain", "vis"])
    assert c.model_id == "vis"


def test_pending_model_never_serves_a_slot():
    p = _m("p", "pending")
    c = pick_for_role("chat", [p], installed_ids=["p"])
    assert c.model_id == ""


def test_all_roles_present_in_assignment():
    out = assign_roles([], installed_ids=[])
    assert set(out) == set(ROLES)
    assert all("filled" in v for v in out.values())


def test_assign_roles_marks_filled_correctly():
    out = assign_roles([_m("a", "llm")], installed_ids=["a"])
    assert out["chat"]["filled"] is True
    assert out["fast"]["filled"] is True
    assert out["image"]["filled"] is False


def test_trending_breaks_size_ties():
    a = _m("a", size=10, trending=False)
    b = _m("b", size=10, trending=True)
    c = pick_for_role("chat", [a, b], installed_ids=["a", "b"])
    assert c.model_id == "b"


# ---------------------------------------------------------------------------
# user override layer
# ---------------------------------------------------------------------------

def test_pin_overrides_automatic_choice():
    store = OverrideStore()
    store.pin("chat", "small")
    out = store.resolve([_m("small", size=2), _m("big", size=30)],
                        installed_ids=["small", "big"])
    assert out["chat"]["model_id"] == "small"
    assert out["chat"]["pinned"] is True
    assert "手动指定" in out["chat"]["reason"]


def test_pin_unknown_role_raises():
    with pytest.raises(KeyError):
        OverrideStore().pin("nope", "x")


def test_unpin_restores_automatic():
    store = OverrideStore()
    store.pin("chat", "small")
    store.unpin("chat")
    out = store.resolve([_m("small", size=2), _m("big", size=30)],
                        installed_ids=["small", "big"])
    assert out["chat"]["pinned"] is False if "pinned" in out["chat"] else True
    assert out["chat"]["model_id"] == "big"


def test_pin_to_vanished_model_is_reported_not_silently_replaced():
    """用户钉的模型不在目录里 → 必须说出来，不能偷偷换一个。"""
    store = OverrideStore()
    store.pin("chat", "ghost")
    out = store.resolve([_m("real")], installed_ids=["real"])
    assert out["chat"]["model_id"] == "ghost"
    assert out["chat"]["filled"] is False
    assert "不在模型目录" in out["chat"]["reason"]


def test_capability_override_wins():
    store = OverrideStore()
    store.set_capabilities("a", ["chat", "rerank"])
    assert store.capabilities_for(_m("a", "llm")) == ["chat", "rerank"]


def test_capability_override_rejects_unknown():
    with pytest.raises(ValueError):
        OverrideStore().set_capabilities("a", ["telepathy"])


def test_capability_override_falls_back_to_derived():
    store = OverrideStore()
    assert store.capabilities_for(_m("b", "image")) == ["image_gen"]


def test_pins_do_not_leak_across_roles():
    store = OverrideStore()
    store.pin("chat", "x")
    out = store.resolve([_m("x", size=5), _m("y", size=50)],
                        installed_ids=["x", "y"])
    assert out["chat"]["model_id"] == "x"
    assert out["fast"]["model_id"] == "x" or out["fast"]["model_id"] == "y"


# ---------------------------------------------------------------------------
# RAG defaults (Cherry Studio baseline)
# ---------------------------------------------------------------------------

def test_rag_defaults_match_cherry_baseline():
    assert RAG_DEFAULTS["top_k"] == 6
    assert RAG_DEFAULTS["score_threshold"] == 0.0
    assert RAG_DEFAULTS["rerank_enabled"] is False
    assert RAG_DEFAULTS["mode"] == "bm25"
    assert RAG_DEFAULTS["chunk_size_tokens"] == 1024
    assert RAG_DEFAULTS["chunk_overlap_tokens"] == 200


def test_rag_defaults_note_mentions_the_escalation_path():
    assert "BM25" in RAG_DEFAULTS["note"]
    assert "Rerank" in RAG_DEFAULTS["note"]


# ---------------------------------------------------------------------------
# HTTP routes (/api/roles/*)
# ---------------------------------------------------------------------------

@pytest.fixture
def client(tmp_path, monkeypatch):
    from starlette.testclient import TestClient

    monkeypatch.setenv("KEVRAI_DATA_ROOT", str(tmp_path))
    from app.main import app
    from app.model_roles import OverrideStore

    with TestClient(app) as c:
        # lifespan 启动后重置，避免上个测试的钉选串味
        c.app.state.role_pins = OverrideStore()
        yield c


def test_route_roles_index_shape(client):
    r = client.get("/api/roles")
    assert r.status_code == 200
    body = r.json()
    assert set(body["roles"]) == set(ROLES)
    assert "vocabulary" in body
    assert "slots" in body
    assert isinstance(body["installed"], list)


def test_route_roles_unfilled_when_nothing_installed(client):
    body = client.get("/api/roles").json()
    assert all(v["filled"] is False for v in body["roles"].values())


def test_route_roles_fills_when_installed(client, monkeypatch):
    from app import main as main_mod

    monkeypatch.setattr(main_mod, "_installed_ids", lambda: {"qwen3-32b"})
    body = client.get("/api/roles").json()
    assert body["roles"]["chat"]["filled"] is True
    assert body["roles"]["chat"]["model_id"] == "qwen3-32b"


def test_route_roles_vram_budget_applied(client, monkeypatch):
    from app import main as main_mod

    monkeypatch.setattr(main_mod, "_installed_ids", lambda: {"qwen3-32b"})
    body = client.get("/api/roles", params={"vram_budget": 1}).json()
    assert body["roles"]["chat"]["filled"] is False
    assert body["vram_budget"] == 1


def test_route_roles_bad_budget_400(client):
    assert client.get("/api/roles", params={"vram_budget": 0}).status_code == 400
    assert client.get("/api/roles", params={"vram_budget": 9999}).status_code == 400


def test_route_pin_and_unpin(client):
    assert client.post("/api/roles/pin", json={
        "role": "chat", "model_id": "qwen3-32b"}).status_code == 200
    body = client.get("/api/roles").json()
    assert body["roles"]["chat"]["model_id"] == "qwen3-32b"
    assert body["roles"]["chat"]["pinned"] is True

    assert client.delete("/api/roles/pin/chat").status_code == 200
    body = client.get("/api/roles").json()
    assert "pinned" not in body["roles"]["chat"]


def test_route_pin_unknown_role_400(client):
    r = client.post("/api/roles/pin", json={"role": "nope", "model_id": "x"})
    assert r.status_code == 400
    assert "unknown role" in r.json()["detail"]


def test_route_unpin_unknown_role_400(client):
    assert client.delete("/api/roles/pin/nope").status_code == 400


def test_route_pin_empty_model_id_422(client):
    assert client.post("/api/roles/pin", json={
        "role": "chat", "model_id": ""}).status_code == 422


def test_route_capabilities_lists_all_models(client):
    r = client.get("/api/roles/capabilities")
    assert r.status_code == 200
    body = r.json()
    assert body["count"] > 0
    first = body["capabilities"][0]
    assert set(first) >= {"id", "name", "category", "capabilities", "derived", "overridden"}
    assert all(c in CAPABILITIES or c == UNKNOWN for c in first["capabilities"])


def test_route_capabilities_never_empty_list(client):
    """无法确定的能力必须显示 unknown，不能给空数组让前端没得渲染。"""
    body = client.get("/api/roles/capabilities").json()
    for item in body["capabilities"]:
        assert len(item["capabilities"]) > 0


def test_route_fit_filters_by_vram(client):
    r = client.get("/api/roles/fit", params={"vram_budget": 8})
    assert r.status_code == 200
    body = r.json()
    assert isinstance(body["fits"], list)
    assert body["count"] == len(body["fits"])


def test_route_fit_rejects_bad_budget(client):
    assert client.get("/api/roles/fit", params={"vram_budget": 0}).status_code == 400
    assert client.get("/api/roles/fit", params={"vram_budget": -5}).status_code == 400


# ---------------------------------------------------------------------------
# v4.11.0 修复：专用视觉模型不得被当成对话模型
# ---------------------------------------------------------------------------

def test_specialised_vision_models_have_no_chat():
    """OCR / 检测 / 分割 / 人脸模型只能做视觉，不能对话。

    回归：``vision`` 类目曾笼统带 chat，导致「后台小任务」槽位挑中最小的
    OCR 检测模型（0.06GB）去跑后台对话——完全不可用。
    """
    for mid, tags in [
        ("pp-ocrv6-det", ["vision", "OCR", "text-detection", "目标检测"]),
        ("pp-ocrv6-rec", ["vision", "OCR", "text-recognition", "文字识别"]),
        ("yolov10", ["vision", "detection", "检测"]),
        ("insightface", ["vision", "detection", "人脸"]),
        ("sam3", ["vision", "segmentation", "图像分割"]),
        ("clip-vit-l", ["vision", "识别"]),
    ]:
        caps = derive_capabilities(_m(mid, "vision", tags=tags))
        assert "chat" not in caps, f"{mid} 不该有 chat 能力"
        assert "vision" in caps, f"{mid} 应有 vision 能力"


def test_vlm_models_do_get_chat():
    """真正的多模态 LLM（带 VLM / 多模态标记）应该能对话。"""
    for tags in (["VLM"], ["多模态"], ["multimodal"], ["vision-language"], ["视觉语言"]):
        caps = derive_capabilities(_m("vlm-model", "vision", tags=tags))
        assert "chat" in caps, f"标记 {tags} 应让模型获得 chat"
        assert "vision" in caps


def test_fast_slot_never_picks_a_vision_only_model(monkeypatch):
    """后台小任务槽位绝不能选中一个不能对话的模型。"""
    from app import model_roles as mr

    pool = [
        _m("tiny-ocr", "vision", tags=["OCR", "检测"], size=0.06),
        _m("small-llm", "llm", size=2.0),
        _m("big-llm", "llm", size=30.0),
    ]
    monkeypatch.setattr(mr, "_size", lambda m: float(m.get("size_gb", 0)))
    c = pick_for_role("fast", pool, installed_ids=["tiny-ocr", "small-llm", "big-llm"])
    assert c.model_id == "small-llm", f"应挑最小的真 LLM，实际挑了 {c.model_id}"


def test_vision_slot_still_finds_vision_models():
    """修掉 chat 之后，看图槽位依然要能找到模型。"""
    pool = [_m("ocr", "vision", tags=["OCR"]), _m("llm-only", "llm")]
    c = pick_for_role("vision", pool, installed_ids=["ocr", "llm-only"])
    assert c.model_id == "ocr"


def test_diarization_is_a_known_capability():
    assert "diarization" in CAPABILITIES


@pytest.mark.parametrize("tag", ["diarization", "说话人分离"])
def test_diarization_tags(tag):
    assert derive_capabilities(_m("d", "audio", tags=[tag])) == ["diarization"]


def test_audio_category_still_unguessed_for_unknown_tags():
    """回归保护：修 diarization 的同时不能让 audio 类目开始乱猜。"""
    assert derive_capabilities(_m("x", "audio", tags=["随便什么"])) == []
