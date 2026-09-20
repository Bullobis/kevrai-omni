"""v2.9.0 — model-market field parsing (company / heat / type / size / time).

**100% offline.** Every upstream shape asserted here was first confirmed by a
live probe of the real API (recorded in the adapter module docstrings), then
frozen into the in-process ``FakeHubServer`` fixtures.

The two hard invariants this file guards:
1. ``Organization`` may be a dict / a bare string / ``None`` — all three must
   parse without raising (a crash here would take down a whole page of results).
2. ``RemoteModel.to_dict()`` must stay a **superset** of ``ModelEntry`` so the
   renderer keeps working unchanged.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from hub_fake_server import hf_item, ms_item  # noqa: E402

from app.hub.base import (  # noqa: E402
    RemoteModel,
    SearchSpec,
    SourceCursor,
    as_bool,
    as_int,
    as_iso,
    flatten_rich_text,
    split_owner,
    strip_markdown,
)
from app.hub.hf import HuggingFaceAdapter  # noqa: E402
from app.hub.modelscope import ModelScopeAdapter  # noqa: E402

# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


class TestAsIso:
    """Timestamps arrive as Unix seconds (魔搭) OR ISO strings (HF)."""

    def test_unix_seconds_are_converted(self):
        assert as_iso(1_745_834_265) == "2025-04-28T09:57:45Z"

    def test_iso_string_keeps_second_precision(self):
        assert as_iso("2026-08-05T08:22:59.000Z") == "2026-08-05T08:22:59Z"

    def test_iso_without_zone_is_accepted(self):
        assert as_iso("2026-08-05T08:22:59") == "2026-08-05T08:22:59Z"

    @pytest.mark.parametrize("bad", [None, "", 0, -1, False, "not-a-date", [], {}])
    def test_unusable_values_become_empty_string(self, bad):
        # Never "None"/"Invalid Date"/"1970-..." leaking into the UI.
        assert as_iso(bad) == ""

    def test_infinity_does_not_raise(self):
        # json.loads accepts Infinity; fromtimestamp(inf) raises OverflowError.
        assert as_iso(float("inf")) == ""
        assert as_iso(float("nan")) == ""


class TestFlattenRichText:
    """魔搭 `Organization.Description` is a Slate AST, not prose."""

    def test_slate_ast_is_flattened(self):
        ast = ["root", {}, ["p", {}, ["span", {}, ["span", {}, "欢迎来到 Qwen 👋"]]],
               ["p", {}, ["span", {}, ["span", {}, "来自阿里的大模型家族。"]]]]
        assert flatten_rich_text(ast) == "欢迎来到 Qwen 👋 来自阿里的大模型家族。"

    def test_plain_string_passes_through(self):
        assert flatten_rich_text("普通文本") == "普通文本"

    def test_flat_list_of_strings(self):
        assert flatten_rich_text(["a", "b"]) == "a b"

    @pytest.mark.parametrize("bad", [None, [], {}, 123, False])
    def test_non_text_values_do_not_raise(self, bad):
        assert isinstance(flatten_rich_text(bad), str)

    def test_deep_nesting_is_bounded(self):
        # A recursive upstream payload must not blow the stack.
        node: object = "leaf"
        for _ in range(60):
            node = [node]
        assert isinstance(flatten_rich_text(node), str)


class TestStripMarkdown:
    def test_yaml_front_matter_is_removed(self):
        src = "---\nlibrary_name: transformers\nlicense: apache-2.0\n---\n\n# Title\n\nBody text."
        out = strip_markdown(src)
        assert "library_name" not in out
        assert "Body text." in out

    def test_html_badges_are_stripped(self):
        src = '<a href="https://x"><img alt="Chat" src="y"/></a>\n\nReal prose.'
        out = strip_markdown(src)
        assert "<a" not in out and "<img" not in out
        assert "Real prose." in out

    def test_code_fences_are_dropped(self):
        src = "Before.\n\n```python\nprint(1)\n```\n\nAfter."
        out = strip_markdown(src)
        assert "print(1)" not in out
        assert "Before." in out and "After." in out

    @pytest.mark.parametrize("bad", [None, "", 123, [], {}])
    def test_non_text_does_not_raise(self, bad):
        assert isinstance(strip_markdown(bad), str)


class TestSplitOwner:
    @pytest.mark.parametrize("repo,want", [
        ("Qwen/Qwen3-8B", "Qwen"),
        ("meta-llama/Llama-3.1-8B", "meta-llama"),
        ("solo-model", ""),
        ("", ""),
        (None, ""),
    ])
    def test_split(self, repo, want):
        assert split_owner(repo) == want


# ---------------------------------------------------------------------------
# ModelScope adapter
# ---------------------------------------------------------------------------


class TestModelScopeFields:
    def _adapter(self, srv) -> ModelScopeAdapter:
        return ModelScopeAdapter(base_url=f"{srv.base_url}/api/v1/models")

    @pytest.mark.asyncio
    async def test_organization_dict_is_parsed(self, fake_hub):
        fake_hub.ms_items = [ms_item("Qwen", "Qwen3-8B", organization={
            "Name": "Qwen", "FullName": "千问",
            "Avatar": "https://resources.modelscope.cn/avatar/x.jpg",
        })]
        ad = self._adapter(fake_hub)
        try:
            m = (await ad.search(SearchSpec(q='x', page_size=10), SourceCursor())).items[0]
        finally:
            await ad.aclose()
        d = m.to_dict()
        assert d["owner"] == "Qwen"
        assert d["owner_full_name"] == "千问"
        assert d["owner_url"].startswith("https://")

    @pytest.mark.asyncio
    async def test_organization_as_bare_string(self, fake_hub):
        # Some payloads carry just the org name; must not raise.
        fake_hub.ms_items = [ms_item("Qwen", "A-Model", organization="Qwen")]
        ad = self._adapter(fake_hub)
        try:
            m = (await ad.search(SearchSpec(q='x', page_size=10), SourceCursor())).items[0]
        finally:
            await ad.aclose()
        assert m.to_dict()["owner"] == "Qwen"

    @pytest.mark.asyncio
    async def test_organization_none_falls_back_to_namespace(self, fake_hub):
        fake_hub.ms_items = [ms_item("alice", "user-model", organization=None)]
        ad = self._adapter(fake_hub)
        try:
            m = (await ad.search(SearchSpec(q='x', page_size=10), SourceCursor())).items[0]
        finally:
            await ad.aclose()
        d = m.to_dict()
        assert d["owner"] == "alice"
        assert d["owner_full_name"] == ""

    @pytest.mark.asyncio
    async def test_non_http_avatar_is_discarded(self, fake_hub):
        # A relative/garbage avatar must never reach the renderer's <img src>.
        fake_hub.ms_items = [ms_item("Qwen", "M", organization={
            "Name": "Qwen", "Avatar": "javascript:alert(1)"})]
        ad = self._adapter(fake_hub)
        try:
            m = (await ad.search(SearchSpec(q='x', page_size=10), SourceCursor())).items[0]
        finally:
            await ad.aclose()
        assert m.to_dict()["owner_url"] == ""

    @pytest.mark.asyncio
    async def test_heat_and_time_and_frameworks(self, fake_hub):
        fake_hub.ms_items = [ms_item("Qwen", "Qwen3-8B", downloads=7_568_998,
                                      stars=362)]
        ad = self._adapter(fake_hub)
        try:
            m = (await ad.search(SearchSpec(q='x', page_size=10), SourceCursor())).items[0]
        finally:
            await ad.aclose()
        d = m.to_dict()
        assert d["downloads"] == 7_568_998
        assert d["likes"] == 362
        assert d["created_at"] == "2025-04-28T09:57:45Z"
        assert d["updated_at"] == "2025-07-26T16:12:33Z"
        assert d["frameworks"] == ["pytorch"]
        assert d["architectures"] == ["LlamaForCausalLM"]
        assert d["library"] == "pytorch"
        assert d["nickname"] == "测试用户"
        assert d["name_cn"] == "文本生成模型"

    @pytest.mark.asyncio
    async def test_empty_framework_strings_are_filtered(self, fake_hub):
        # Verified live: real payloads contain `Frameworks: [""]`.
        item = ms_item("Qwen", "M")
        item["Frameworks"] = [""]
        item["Libraries"] = ["pytorch", "safetensors", ""]
        fake_hub.ms_items = [item]
        ad = self._adapter(fake_hub)
        try:
            m = (await ad.search(SearchSpec(q='x', page_size=10), SourceCursor())).items[0]
        finally:
            await ad.aclose()
        d = m.to_dict()
        assert d["frameworks"] == []
        # `library` still resolves from the Libraries list.
        assert d["library"] == "pytorch"

    @pytest.mark.asyncio
    async def test_is_new_true_is_preserved(self, fake_hub):
        item = ms_item("Qwen", "M")
        item["IsNewModel"] = True
        item["IsHot"] = 1
        fake_hub.ms_items = [item]
        ad = self._adapter(fake_hub)
        try:
            m = (await ad.search(SearchSpec(q='x', page_size=10), SourceCursor())).items[0]
        finally:
            await ad.aclose()
        d = m.to_dict()
        assert d["is_new"] is True
        assert d["is_hot"] is True

    @pytest.mark.asyncio
    async def test_chinese_name_is_the_list_description(self, fake_hub):
        # The list endpoint has no prose; the Chinese name is the stand-in.
        item = ms_item("Qwen", "M", chinese_name="千问3-8B")
        item["Description"] = ""
        fake_hub.ms_items = [item]
        ad = self._adapter(fake_hub)
        try:
            m = (await ad.search(SearchSpec(q='x', page_size=10), SourceCursor())).items[0]
        finally:
            await ad.aclose()
        assert m.to_dict()["description"] == "千问3-8B"


# ---------------------------------------------------------------------------
# HuggingFace adapter
# ---------------------------------------------------------------------------


class TestHuggingFaceFields:
    def _adapter(self, srv) -> HuggingFaceAdapter:
        # `mirrors=()` keeps the adapter pinned to the fake server — without it
        # the rotation would fall through to the real huggingface.co.
        return HuggingFaceAdapter(base_url=f"{srv.base_url}/api", mirrors=())

    @pytest.mark.asyncio
    async def test_owner_comes_from_author_when_present(self, fake_hub):
        fake_hub.hf_items = [hf_item("Qwen", "Qwen3-8B", author="Qwen")]
        ad = self._adapter(fake_hub)
        try:
            m = (await ad.search(SearchSpec(q='x', page_size=10), SourceCursor())).items[0]
        finally:
            await ad.aclose()
        assert m.to_dict()["owner"] == "Qwen"

    @pytest.mark.asyncio
    async def test_owner_falls_back_to_id_split_when_author_is_none(self, fake_hub):
        # Verified live: the list endpoint returns `author: null`.
        fake_hub.hf_items = [hf_item("Qwen", "Qwen3-8B", author=None)]
        ad = self._adapter(fake_hub)
        try:
            m = (await ad.search(SearchSpec(q='x', page_size=10), SourceCursor())).items[0]
        finally:
            await ad.aclose()
        assert m.to_dict()["owner"] == "Qwen"

    @pytest.mark.asyncio
    async def test_trending_score_and_library_and_dates(self, fake_hub):
        item = hf_item("Qwen", "Qwen3-8B", downloads=7_331_932, likes=15_827)
        fake_hub.hf_items = [item]
        ad = self._adapter(fake_hub)
        try:
            m = (await ad.search(SearchSpec(q='x', page_size=10), SourceCursor())).items[0]
        finally:
            await ad.aclose()
        d = m.to_dict()
        assert d["trending_score"] == item["trendingScore"]
        assert d["library"] == "transformers"
        assert d["frameworks"] == ["transformers"]
        assert d["created_at"] == "2025-01-01T00:00:00Z"
        assert d["updated_at"] == "2026-01-01T00:00:00Z"

    @pytest.mark.asyncio
    async def test_trending_score_drives_is_hot(self, fake_hub):
        hot = hf_item("Qwen", "Hot", downloads=50_000)      # score 500
        cold = hf_item("Qwen", "Cold", downloads=100)       # score 1
        fake_hub.hf_items = [hot, cold]
        ad = self._adapter(fake_hub)
        try:
            items = (await ad.search(SearchSpec(q='x', page_size=10), SourceCursor())).items
        finally:
            await ad.aclose()
        by_name = {m.name: m.to_dict() for m in items}
        assert by_name["Hot"]["is_hot"] is True
        assert by_name["Cold"]["is_hot"] is False

    @pytest.mark.asyncio
    async def test_search_sends_full_true(self, fake_hub):
        # `full=true` is what makes `author`/`lastModified` non-null upstream.
        fake_hub.hf_items = [hf_item("Qwen", "M")]
        ad = self._adapter(fake_hub)
        try:
            await ad.search(SearchSpec(q='x', page_size=10), SourceCursor())
        finally:
            await ad.aclose()
        assert any("full=true" in p for p in fake_hub.requests)


# ---------------------------------------------------------------------------
# Contract: to_dict() stays a superset + serializable
# ---------------------------------------------------------------------------


class TestToDictContract:
    LEGACY_KEYS = {
        "id", "name", "description", "category", "license", "size_gb", "engine",
        "trending", "repo", "hardware", "tags", "modality", "files", "size_bytes",
        "source", "sources", "primary_url", "gguf_repo", "mnn_repo", "hub",
        "size_known", "downloads", "likes", "revision", "task", "also_on",
        "import_only", "engine_confidence", "hub_display",
    }
    V290_KEYS = {
        "owner", "owner_url", "owner_full_name", "nickname", "name_cn",
        "trending_score", "library", "frameworks", "architectures",
        "created_at", "updated_at", "is_hot", "is_new", "remote",
    }

    def test_all_legacy_and_new_keys_present(self):
        d = RemoteModel(repo="a/b", hub="hf").to_dict()
        assert set(d) >= self.LEGACY_KEYS
        assert set(d) >= self.V290_KEYS

    def test_remote_flag_distinguishes_hubs(self):
        assert RemoteModel(repo="a/b", hub="hf").to_dict()["remote"] is True
        assert RemoteModel(repo="a/b", hub="modelscope").to_dict()["remote"] is True
        assert RemoteModel(repo="a/b", hub="curated").to_dict()["remote"] is False

    def test_defaults_are_json_safe(self):
        import json
        d = RemoteModel(repo="a/b", hub="hf").to_dict()
        json.dumps(d)  # must not raise

    @pytest.mark.parametrize("hub", ["hf", "modelscope", "curated"])
    def test_lists_are_copied_not_aliased(self, hub):
        m = RemoteModel(repo="a/b", hub=hub)
        d = m.to_dict()
        d["tags"].append("mutated")
        assert "mutated" not in m.tags


class TestAsBoolMixedTypes:
    """`IsHot` is an int while `IsNewModel` is a real bool (verified live)."""

    @pytest.mark.parametrize("val,want", [
        (True, True), (False, False), (1, True), (0, False),
        ("true", True), ("false", False), ("1", True), ("0", False),
        (None, False), ("", False),
    ])
    def test_coercion(self, val, want):
        assert as_bool(val, False) is want


class TestAsIntStillRobust:
    @pytest.mark.parametrize("bad", [None, "", "abc", [], {}, float("nan"), float("inf")])
    def test_defaults_without_raising(self, bad):
        assert as_int(bad, 0) == 0


# ---------------------------------------------------------------------------
# Source availability probe (v2.9.0) — drives the UI's "hide dead source"
# ---------------------------------------------------------------------------


class TestProbeSources:
    """`probe_sources()` must be fast, bounded, and never raise."""

    def _registry(self, fake, *, hf_mirrors=()):
        from app.hub.curated import CuratedAdapter
        from app.hub.registry import HubRegistry
        return HubRegistry(
            curated=CuratedAdapter(),
            hf=HuggingFaceAdapter(base_url=f"{fake.base_url}/api", mirrors=list(hf_mirrors)),
            modelscope=ModelScopeAdapter(base_url=f"{fake.base_url}/api/v1/models"),
        )

    @pytest.mark.asyncio
    async def test_reachable_sources_are_online(self, fake_hub):
        fake_hub.ms_items = [ms_item("Qwen", "M")]
        fake_hub.hf_items = [hf_item("Qwen", "M")]
        reg = self._registry(fake_hub)
        try:
            out = await reg.probe_sources(timeout_s=5.0)
        finally:
            await reg.aclose()
        assert out["sources"]["hf"]["online"] is True
        assert out["sources"]["modelscope"]["online"] is True
        assert out["enabled"]  # non-empty

    @pytest.mark.asyncio
    async def test_unreachable_source_is_offline_not_an_exception(self):
        from app.hub.curated import CuratedAdapter
        from app.hub.registry import HubRegistry
        # Port 1 is never listening → immediate connection refused.
        reg = HubRegistry(
            curated=CuratedAdapter(),
            hf=HuggingFaceAdapter(base_url="http://127.0.0.1:1/api", mirrors=()),
        )
        try:
            out = await reg.probe_sources(timeout_s=1.0)
        finally:
            await reg.aclose()
        assert out["sources"]["hf"]["online"] is False
        assert out["sources"]["hf"]["code"] in {"network", "timeout"}

    @pytest.mark.asyncio
    async def test_no_remote_adapters_yields_empty_map(self):
        from app.hub.curated import CuratedAdapter
        from app.hub.registry import HubRegistry
        reg = HubRegistry(curated=CuratedAdapter())
        out = await reg.probe_sources()
        assert out["sources"] == {}
        assert "curated" in out["enabled"]

    @pytest.mark.asyncio
    async def test_curated_is_never_probed(self, fake_hub):
        reg = self._registry(fake_hub)
        try:
            out = await reg.probe_sources(timeout_s=5.0)
        finally:
            await reg.aclose()
        # A local source cannot be "unreachable", so it is not in the probe map.
        assert "curated" not in out["sources"]


class TestHubHealthRoute:
    """`GET /api/hub/health` is additive — `/api/hub/sources` is untouched."""

    def test_route_exists_and_is_shaped(self, hub_client, monkeypatch):
        from app import main as app_main

        async def fake_probe(self, *, timeout_s=12.0):
            return {
                "sources": {"hf": {"hub": "hf", "online": True, "latency_ms": 12,
                                   "code": "ok"}},
                "enabled": ["curated", "hf"],
            }

        monkeypatch.setattr(app_main, "_get_hub",
                            lambda request: type("H", (), {
                                "probe_sources": fake_probe,
                                "enabled_sources": lambda self: ["curated", "hf"],
                            })())
        r = hub_client.get("/api/hub/health")
        assert r.status_code == 200
        body = r.json()
        assert body["online"] == ["hf"]
        assert body["sources"]["hf"]["online"] is True
        assert body["degraded"] is False

    def test_probe_failure_still_returns_200(self, hub_client, monkeypatch):
        from app import main as app_main

        async def boom(self, *, timeout_s=12.0):
            raise RuntimeError("upstream exploded")

        monkeypatch.setattr(app_main, "_get_hub",
                            lambda request: type("H", (), {
                                "probe_sources": boom,
                                "enabled_sources": lambda self: ["curated"],
                            })())
        r = hub_client.get("/api/hub/health")
        assert r.status_code == 200
        assert r.json()["online"] == []

    def test_sources_route_semantics_unchanged(self, hub_client):
        # The pre-existing route must keep its exact response shape.
        r = hub_client.get("/api/hub/sources")
        assert r.status_code == 200
        body = r.json()
        assert "sources" in body and "enabled" in body
