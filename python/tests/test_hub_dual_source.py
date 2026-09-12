"""Dual-source hub tests — smoke (S) + extreme (E) + P0 regression assertions.


**100% offline.** The only HTTP that happens is against the in-process
``FakeHubServer`` (port 0). No real hostname is ever contacted. Assertions are
split into four layers (design §6.3): pure functions, adapter parsing, route
layer via ``TestClient``, and the P0 hardening guards.
"""
from __future__ import annotations

import asyncio
import json
import sys
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from hub_fake_server import FakeHubServer, hf_item, ms_item  # noqa: E402

from app.hub import (  # noqa: E402
    HUB_CURATED,
    HUB_HF,
    HUB_MODELSCOPE,
    HubRegistry,
    SearchSpec,
    build_registry,
    cross_source_candidates,
)
from app.hub.base import (  # noqa: E402
    MODEL_ID_RE,
    SourceCursor,
    as_int,
    as_str_list,
    synth_id,
)
from app.hub.curated import CuratedAdapter  # noqa: E402
from app.hub.hf import HuggingFaceAdapter  # noqa: E402
from app.hub.modelscope import ModelScopeAdapter  # noqa: E402
from app.hub.net import (  # noqa: E402
    CircuitBreaker,
    TokenBucket,
    TTLCache,
    request_with_retry,
    timeout_for,
)
from app.hub.paths import UnsafePathError, safe_extract, safe_join  # noqa: E402
from app.hub.registry import (  # noqa: E402
    BadCursor,
    allocate_quota,
    decode_cursor,
    dedupe,
    encode_cursor,
    merge_rank,
)
from app.hub.taxonomy import (  # noqa: E402
    canonical_license,
    infer_engines,
    known_engine_ids,
    map_category,
    size_gb_from_bytes,
)

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def _hf(fake: FakeHubServer) -> HuggingFaceAdapter:
    """HF adapter pinned to the fake server *only* (no real-mirror fallback).

    ``mirrors=()`` is essential: without it the adapter would rotate to the
    real ``huggingface.co`` / ``hf-mirror.com`` whenever the fake returns
    garbage, which would (a) make the suite non-offline and (b) mask the
    degradation behaviour we are asserting.
    """
    return HuggingFaceAdapter(base_url=f"{fake.base_url}/api", mirrors=())


def _ms(fake: FakeHubServer) -> ModelScopeAdapter:
    """ModelScope adapter pinned to the fake server (single-base, mirrors N/A)."""
    return ModelScopeAdapter(base_url=f"{fake.base_url}/api/v1/models")


def _registry(fake: FakeHubServer, settings=None) -> HubRegistry:
    return HubRegistry(
        curated=CuratedAdapter(settings=settings),
        hf=_hf(fake),
        modelscope=_ms(fake),
        settings=settings,
    )


# ===========================================================================
# S01–S12  Smoke
# ===========================================================================


async def test_S01_curated_single_source():
    reg = build_registry(None)
    page = await reg.search(SearchSpec(q="", sources=[HUB_CURATED], page_size=5))
    assert page.items, "curated catalog should return items"
    assert all(m.hub == HUB_CURATED for m in page.items)
    assert page.has_more is True


async def test_S02_hf_single_source(fake_hub):
    reg = _registry(fake_hub)
    page = await reg.search(SearchSpec(q="qwen", sources=[HUB_HF], page_size=10))
    assert page.items
    assert all(m.hub == HUB_HF for m in page.items)
    assert all("/" in m.repo for m in page.items)


async def test_S03_modelscope_single_source(fake_hub):
    reg = _registry(fake_hub)
    page = await reg.search(SearchSpec(q="qwen", sources=[HUB_MODELSCOPE], page_size=10))
    assert page.items
    assert all(m.hub == HUB_MODELSCOPE for m in page.items)


async def test_S04_three_source_merge(fake_hub):
    reg = _registry(fake_hub)
    page = await reg.search(
        SearchSpec(q="qwen", sources=[HUB_CURATED, HUB_HF, HUB_MODELSCOPE], page_size=30)
    )
    hubs = {m.hub for m in page.items}
    assert HUB_HF in hubs and HUB_MODELSCOPE in hubs


async def test_S05_cross_source_dedupe(fake_hub):
    """The same owner/name on three sources collapses to one card."""
    fake_hub.hf_items = [hf_item("Qwen", "Qwen2.5-7B-Instruct")]
    fake_hub.ms_items = [ms_item("Qwen", "Qwen2.5-7B-Instruct")]
    reg = _registry(fake_hub)
    page = await reg.search(
        SearchSpec(q="qwen", sources=[HUB_HF, HUB_MODELSCOPE], page_size=30)
    )
    repos = [m.repo.lower() for m in page.items]
    assert repos.count("qwen/qwen2.5-7b-instruct") == 1


async def test_S06_cursor_pagination_no_overlap(fake_hub):
    reg = build_registry(None)  # curated-only pagination is deterministic
    p1 = await reg.search(SearchSpec(q="", sources=[HUB_CURATED], page_size=10))
    assert p1.has_more and p1.next_cursor
    p2 = await reg.search(SearchSpec(q="", sources=[HUB_CURATED], page_size=10), p1.next_cursor)
    ids1 = {m.id for m in p1.items}
    ids2 = {m.id for m in p2.items}
    assert ids1.isdisjoint(ids2)


async def test_S07_hf_detail(fake_hub):
    reg = _registry(fake_hub)
    model = await reg.detail(HUB_HF, "Qwen/Qwen2.5-7B-Instruct")
    assert model.repo == "Qwen/Qwen2.5-7B-Instruct"
    assert model.hub == HUB_HF


def test_S08_engine_inference():
    gguf, only = infer_engines(["model.gguf"], repo="a/b", category="llm")
    assert "llama.cpp" in gguf and only is False
    mnn, _ = infer_engines(["model.mnn"], repo="a/b")
    assert "mnn" in mnn


async def test_S09_files_and_layout(fake_hub):
    reg = _registry(fake_hub)
    listing = await reg.files(HUB_MODELSCOPE, "Qwen/Qwen2.5-7B-Instruct")
    paths = [f["path"] for f in listing["files"]]
    assert "config.json" in paths
    assert listing["engines"], "safetensors should infer engines"


async def test_S10_full_outage_graceful(fake_hub):
    """All remote sources 503 → curated-only, degraded, still valid."""
    fake_hub.force = {"boom": "2"}
    bad_hf = _hf(fake_hub)
    bad_ms = _ms(fake_hub)
    reg = HubRegistry(curated=CuratedAdapter(), hf=bad_hf, modelscope=bad_ms)
    page = await reg.search(
        SearchSpec(q="", sources=[HUB_CURATED, HUB_HF, HUB_MODELSCOPE], page_size=10)
    )
    assert page.items  # curated still there
    assert page.degraded is True
    assert page.warnings
    await bad_hf.aclose()
    await bad_ms.aclose()


# ===========================================================================
# E01–E30  Extreme
# ===========================================================================


async def test_E04_garbage_json_degrades_not_500(fake_hub):
    """HTML body faking application/json → degrade, never crash."""
    fake_hub.force = {"bad": "1"}
    reg = _registry(fake_hub)
    page = await reg.search(SearchSpec(q="x", sources=[HUB_HF], page_size=5))
    assert page.degraded is True
    assert page.items == []
    assert page.warnings


async def test_E05_data_is_string_not_iterated(fake_hub):
    """ModelScope Data.Models as a string must NOT yield char-by-char items."""
    fake_hub.force = {"bad": "2"}
    reg = _registry(fake_hub)
    page = await reg.search(SearchSpec(q="x", sources=[HUB_MODELSCOPE], page_size=5))
    assert page.items == []
    assert page.degraded is True


async def test_E06_missing_fields_ok(fake_hub):
    """Dropping downloads/likes/tags must still yield items."""
    fake_hub.force = {"bad": "3"}
    reg = _registry(fake_hub)
    page = await reg.search(SearchSpec(q="x", sources=[HUB_HF], page_size=5))
    assert page.items
    assert page.items[0].downloads == 0


def test_E07_page_size_clamped():
    assert SearchSpec(page_size=100000).normalized().page_size == 100


def test_E08_bad_page_size_defaults():
    for bad in (0, -1, "abc", None):
        assert 1 <= SearchSpec(page_size=bad).normalized().page_size <= 100


async def test_E09_empty_result_no_runaway(fake_hub):
    fake_hub.ms_items = []
    reg = _registry(fake_hub)
    page = await reg.search(SearchSpec(q="zzz", sources=[HUB_MODELSCOPE], page_size=30))
    assert page.items == []


async def test_E10_ttl_cache_collapses_upstream_calls(fake_hub):
    fake_hub.reset_requests()
    reg = _registry(fake_hub)
    # Sequential repeats must be served from the TTL cache (design §2.5).
    fake_hub.requests.clear()
    adapter = reg.adapter(HUB_HF)
    spec = SearchSpec(q="qwen", sources=[HUB_HF], page_size=10)
    for _ in range(20):
        await reg.search(spec)
    # Only the very first call should reach the upstream (subsequent are cached).
    assert fake_hub.count("GET /api/models") == 1

    # And the cache reports real hits.
    assert adapter._cache.hits >= 1


async def test_E12_circuit_breaker_short_circuits(fake_hub):
    """Five failures trip the breaker; subsequent calls skip the upstream."""
    fake_hub.force = {"boom": "1"}
    reg = _registry(fake_hub)
    for n in range(6):
        await reg.search(SearchSpec(q=f"x{n}", sources=[HUB_HF], page_size=5))
    fake_hub.reset_requests()
    page = await reg.search(SearchSpec(q="zzz-after-open", sources=[HUB_HF], page_size=5))
    assert page.degraded is True
    assert fake_hub.count("/api/models") == 0


def test_E19_traversal_rejected(tmp_path):
    with pytest.raises(UnsafePathError):
        safe_join(tmp_path, "../../../../tmp/evil")
    with pytest.raises(UnsafePathError):
        safe_join(tmp_path, "/etc/passwd")


def test_E20_nul_and_illegal_rejected(tmp_path):
    with pytest.raises(UnsafePathError):
        safe_join(tmp_path, "a\x00b")
    with pytest.raises(UnsafePathError):
        safe_join(tmp_path, "a" * 300)


def test_E21_windows_reserved_rejected(tmp_path):
    for name in ("CON", "NUL", "COM1", "LPT1", "CON.txt"):
        with pytest.raises(UnsafePathError):
            safe_join(tmp_path, name)


async def test_E22_401_degrades_public_still_ok(fake_hub):
    bad = ModelScopeAdapter(base_url=f"{fake_hub.base_url}/api/v1/models?auth=1")
    res = await bad.search(SearchSpec(q="x"), SourceCursor())
    assert res.degraded is True
    assert res.code in {"http_error", "not_found"}
    await bad.aclose()


def test_E23_empty_token_no_auth_header():
    class S:
        hf_token = "   "
        ms_token = ""
    hf = HuggingFaceAdapter(settings=S())
    ms = ModelScopeAdapter(settings=S())
    assert hf.auth_headers() == {}
    assert ms.auth_headers() == {}


async def test_E24_429_with_retry_after(no_sleep):
    calls = {"n": 0}

    class Resp:
        status_code = 429
        headers = {"Retry-After": "1"}
        def json(self):
            return {}

    class Client:
        async def request(self, *a, **k):
            calls["n"] += 1
            return Resp()

    out = await request_with_retry(
        Client(), "GET", "http://x/y", retries=2, sleep=no_sleep, jitter=0.0,
    )
    assert out.ok is False
    assert out.code == "rate_limited"
    assert calls["n"] <= 3


async def test_E25_429_no_retry_after_backs_off(no_sleep):
    class Resp:
        status_code = 429
        headers = {}
        def json(self):
            return {}

    class Client:
        async def request(self, *a, **k):
            return Resp()

    out = await request_with_retry(
        Client(), "GET", "http://x/y", retries=2, sleep=no_sleep,
        backoff=(0.4, 1.2), jitter=0.0,
    )
    assert out.code == "rate_limited"
    assert no_sleep.slept and no_sleep.slept[0] >= 0.4


def test_E26_legacy_route_rejects_slash():
    from app.main import _validate_model_id
    from fastapi import HTTPException

    with pytest.raises(HTTPException):
        _validate_model_id("deepseek-ai/DeepSeek-V3")


def test_E27_curated_never_writes_source_enum():
    """`source` is a URL slot; provenance must live in `hub` (design §1.2)."""
    entry = {"id": "x", "name": "X", "source": "", "hub": "curated"}
    model = CuratedAdapter._to_remote(entry)
    assert model.source == ""
    assert model.hub == HUB_CURATED
    d = model.to_dict()
    assert d["source"] == ""
    assert d["hub"] == HUB_CURATED


def test_E28_unknown_size_flag():
    from app.hub.base import RemoteModel
    m = RemoteModel(repo="a/b", hub=HUB_HF, size_gb=0.0, size_known=False)
    d = m.to_dict()
    assert d["size_known"] is False
    assert d["size_gb"] == 0.0


def test_E29_settings_never_leak_tokens(hub_client):
    hub_client.put("/api/settings", json={"hf_token": "hf_SECRET", "ms_token": "ms_SECRET"})
    body = hub_client.get("/api/settings").text
    # 1) The plaintext secret values must never appear anywhere in the payload.
    assert "hf_SECRET" not in body
    assert "ms_SECRET" not in body
    # 2) The keys themselves must be absent (checking exact JSON keys, not
    #    substrings — ``hf_token_set`` legitimately contains "hf_token").
    j = hub_client.get("/api/settings").json()
    assert "hf_token" not in j and "ms_token" not in j
    # 3) Redaction must still advertise "configured-ness" via *_set booleans.
    assert j["hf_token_set"] is True
    assert j["ms_token_set"] is True


def test_E30_zip_slip_blocked(tmp_path):
    evil = tmp_path / "evil.zip"
    with zipfile.ZipFile(evil, "w") as zf:
        zf.writestr("../../../../tmp/kevrai-evil-file", "pwned")
        zf.writestr("good.txt", "ok")
    dest = tmp_path / "extract"
    with zipfile.ZipFile(evil) as zf:
        safe_extract(zf, dest)
    assert (dest / "good.txt").exists()
    assert not Path("/tmp/kevrai-evil-file").exists()


# ===========================================================================
# Route layer
# ===========================================================================


def test_route_hub_search_returns_200(hub_client):
    r = hub_client.get("/api/hub/search", params={"q": "", "sources": "curated", "page_size": 5})
    assert r.status_code == 200
    body = r.json()
    assert "items" in body and "next_cursor" in body


def test_route_hub_search_bad_cursor_400(hub_client):
    r = hub_client.get("/api/hub/search", params={"cursor": "!!!notbase64"})
    assert r.status_code == 400
    assert r.json()["detail"]["error"] == "bad_cursor"


def test_route_hub_sources_shape(hub_client):
    r = hub_client.get("/api/hub/sources")
    assert r.status_code == 200
    hubs = {s["hub"] for s in r.json()["sources"]}
    assert {HUB_CURATED, HUB_HF, HUB_MODELSCOPE} <= hubs


def test_route_hub_model_bad_repo_400(hub_client):
    r = hub_client.get("/api/hub/model", params={"hub": "hf", "repo": "../etc/passwd"})
    assert r.status_code == 400


def test_route_hub_model_unknown_hub_400(hub_client):
    r = hub_client.get("/api/hub/model", params={"hub": "bitbucket", "repo": "a/b"})
    assert r.status_code == 400


def test_route_categories_has_other(hub_client):
    ids = {c["id"] for c in hub_client.get("/api/categories").json()["categories"]}
    assert "other" in ids


def test_route_existing_models_unchanged(hub_client):
    """Backward-compat guard: /api/models response shape is untouched."""
    r = hub_client.get("/api/models")
    assert r.status_code == 200
    body = r.json()
    assert set(["count", "models", "gguf_repos"]) <= set(body.keys())


# ===========================================================================
# Pure functions (also cover the design's required unit assertions)
# ===========================================================================


def test_synth_id_matches_model_id_re_and_idempotent():
    a = synth_id(HUB_HF, "deepseek-ai/DeepSeek-V3")
    b = synth_id(HUB_HF, "deepseek-ai/DeepSeek-V3")
    assert a == b
    assert MODEL_ID_RE.fullmatch(a)


def test_infer_engines_ids_are_real():
    known = known_engine_ids()
    for files in (["x.gguf"], ["x.safetensors"], ["x.bin"], ["x.onnx"], ["x.mnn"],
                  ["pytorch_model.bin"], ["x.ckpt"]):
        engines, _ = infer_engines(files, repo="a/b", category="llm")
        assert set(engines) <= known


def test_circuit_breaker_state_machine():
    clock = {"t": 0.0}
    cb = CircuitBreaker(fail_threshold=2, open_seconds=10, clock=lambda: clock["t"])
    assert cb.state == "closed"
    cb.record_fail(); cb.record_fail()
    assert cb.state == "open"
    assert cb.allow() is False
    clock["t"] = 11.0
    assert cb.state == "half-open"
    assert cb.allow() is True
    cb.record_ok()
    assert cb.state == "closed"


def test_allocate_quota_sums_to_page_size():
    q = allocate_quota(30, [HUB_CURATED, HUB_HF, HUB_MODELSCOPE])
    assert sum(q.values()) == 30
    assert q[HUB_CURATED] >= q[HUB_HF]


def test_cursor_roundtrip_and_sig_mismatch():
    token = encode_cursor("sig1", {HUB_HF: SourceCursor(page=3)})
    assert decode_cursor(token, expect_sig="sig1")[HUB_HF].page == 3
    assert decode_cursor(token, expect_sig="other") == {}
    with pytest.raises(BadCursor):
        decode_cursor("x" * 3000)


def test_dedupe_marks_also_on():
    from app.hub.base import RemoteModel
    a = RemoteModel(repo="a/b", hub=HUB_HF)
    b = RemoteModel(repo="a/b", hub=HUB_MODELSCOPE)
    out = dedupe([a, b])
    assert len(out) == 1
    assert HUB_MODELSCOPE in out[0].also_on


def test_cross_source_candidates_order():
    cands = cross_source_candidates(
        hub=HUB_MODELSCOPE, repo="a/b", path="model.safetensors",
        also_on=[HUB_HF], extra_mirrors=["https://hf-mirror.com"],
    )
    assert cands and "modelscope.cn" in cands[0]
    assert any("hf-mirror.com" in c for c in cands)


def test_map_category_and_license():
    assert map_category(pipeline_tag="text-generation") == "llm"
    assert map_category(pipeline_tag="") == "other"
    assert canonical_license("MIT") == "MIT"
    assert canonical_license("", ["license:apache-2.0"]) == "Apache-2.0"


def test_size_helpers():
    assert size_gb_from_bytes(0) == 0.0
    assert size_gb_from_bytes(-5) == 0.0
    assert size_gb_from_bytes("bad") == 0.0
    assert size_gb_from_bytes(1024 ** 3) == 1.0


def test_as_str_list_does_not_explode_strings():
    assert as_str_list("abc") == ["abc"]
    assert as_str_list(None) == []


def test_token_bucket_limits():
    tb = TokenBucket(rate=1.0, capacity=2.0, clock=lambda: 0.0)
    assert tb.take() and tb.take()
    assert tb.take() is False


def test_as_int_survives_non_finite_json_literals():
    """Regression: json.loads accepts Infinity/NaN, and int(float('inf'))
    raises OverflowError. One dirty field must not discard a whole page."""
    assert as_int(float("inf"), -1) == -1
    assert as_int(float("-inf"), -1) == -1
    assert as_int(float("nan"), -1) == -1
    assert as_int("Infinity", -1) == -1
    assert as_int("-inf", -1) == -1
    assert as_int("nan", -1) == -1
    # Normal paths must be unaffected.
    assert as_int("12,345") == 12345
    assert as_int("3.9") == 3
    assert as_int(7) == 7
    assert as_int(True) == 1
    assert as_int(10 ** 30) == 10 ** 30


def test_as_int_never_raises_on_dirty_values():
    for value in (None, [], {}, object(), "", "  ", "abc", "1e999"):
        assert isinstance(as_int(value, -1), int)
