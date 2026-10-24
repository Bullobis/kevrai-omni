"""Tests for the opt-in web search agent tool.

Fully offline: every test replaces :func:`app.agent.tools.web_tools.http_get`
with a stub, so nothing touches the network. The stubs double as fixtures for
real SearXNG JSON / HTML shapes and real DuckDuckGo HTML.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.agent.tool_registry import ToolContext  # noqa: E402
from app.agent.tools import web_tools as wt  # noqa: E402

# If a test ever forgets to stub the transport, fail loudly instead of leaking
# a real request. Autouse + a sentinel keeps the suite hermetic.
_NO_STUB = object()


@pytest.fixture(autouse=True)
def no_real_http(monkeypatch):
    monkeypatch.setattr(
        wt, "http_get",
        lambda *a, **k: pytest.fail("network access is not allowed in tests"),
    )
    monkeypatch.delenv(wt.SEARXNG_URL_ENV, raising=False)
    yield


def stub(monkeypatch, handler):
    """Install ``handler(url, **kw) -> (status, body)`` as the transport."""
    monkeypatch.setattr(wt, "http_get", lambda url, **kw: handler(url, **kw))


def ctx(**extra) -> ToolContext:
    return ToolContext(extra=dict(extra))


SEARXNG_JSON = """{"results": [
  {"title": "Example One", "url": "https://one.example.com/a", "content": "snippet one"},
  {"title": "Example Two", "url": "https://two.example.com/b", "content": "snippet two"}
]}"""

SEARXNG_HTML = """<html><body>
<article class="result"><a href="https://one.example.com/a">Example One</a>
<p class="content">snippet one</p></article>
<article class="result"><a href="https://two.example.com/b">Example Two</a>
<p class="content">snippet two</p></article>
</body></html>"""

DDG_HTML = """<html><body>
<a class="result__a" href="/l/?uddg=https%3A%2F%2Fone.example.com%2Fa">Example One</a>
<a class="result__snippet">snippet one</a>
<a class="result__a" href="/l/?uddg=https%3A%2F%2Ftwo.example.com%2Fb">Example Two</a>
<a class="result__snippet">snippet two</a>
</body></html>"""


# --------------------------------------------------------------------------
# Parsers
# --------------------------------------------------------------------------
def test_parse_searxng_html_extracts_all_fields():
    rows = wt._parse_html(SEARXNG_HTML, wt._SearXngParser())
    assert rows[0] == {"title": "Example One",
                       "url": "https://one.example.com/a",
                       "snippet": "snippet one"}
    assert len(rows) == 2


def test_parse_duckduckgo_html_decodes_redirect_links():
    rows = wt._parse_html(DDG_HTML, wt._DuckDuckGoParser())
    assert rows[0]["url"] == "https://one.example.com/a"
    assert rows[0]["title"] == "Example One"
    assert rows[0]["snippet"] == "snippet one"


def test_decode_duckduckgo_href_passthrough_and_empty():
    assert wt._decode_duckduckgo_href("https://x.example.com") == "https://x.example.com"
    assert wt._decode_duckduckgo_href("") == ""
    assert "example" in wt._decode_duckduckgo_href("/l/?uddg=https%3A%2F%2Fexample.com")


def test_parsers_survive_empty_and_malformed_html():
    assert wt._parse_html("", wt._SearXngParser()) == []
    assert wt._parse_html("<html><body>nothing</body></html>",
                          wt._DuckDuckGoParser()) == []


# --------------------------------------------------------------------------
# Result cleaning
# --------------------------------------------------------------------------
def test_clean_results_drops_non_http_and_duplicates():
    rows = [
        {"title": "ok", "url": "https://a.example.com/", "snippet": "s"},
        {"title": "bad scheme", "url": "javascript:alert(1)", "snippet": ""},
        {"title": "dup", "url": "https://a.example.com", "snippet": "s"},
        {"title": "second", "url": "https://b.example.com", "snippet": ""},
    ]
    out = wt._clean_results(rows, limit=10)
    assert [r["url"] for r in out] == ["https://a.example.com/",
                                       "https://b.example.com"]


def test_clean_results_respects_limit_and_falls_back_title_to_url():
    rows = [{"title": "", "url": f"https://x{i}.example.com", "snippet": ""}
            for i in range(10)]
    out = wt._clean_results(rows, limit=3)
    assert len(out) == 3
    assert out[0]["title"] == "https://x0.example.com"


# --------------------------------------------------------------------------
# Engines
# --------------------------------------------------------------------------
def test_searxng_prefers_json_payload(monkeypatch):
    seen: list[str] = []

    def handler(url, **kw):
        seen.append(kw.get("params", {}).get("format", "html"))
        return 200, SEARXNG_JSON

    stub(monkeypatch, handler)
    rows = wt.search_searxng("http://searx.local", "q", 5)
    assert [r["url"] for r in rows] == ["https://one.example.com/a",
                                        "https://two.example.com/b"]
    assert seen == ["json"]  # JSON succeeded, no HTML fallback needed


def test_searxng_falls_back_to_html_when_json_disabled(monkeypatch):
    calls: list[str] = []

    def handler(url, **kw):
        fmt = kw.get("params", {}).get("format", "html")
        calls.append(fmt)
        # The instance has JSON disabled: 403 for format=json.
        return (403, "forbidden") if fmt == "json" else (200, SEARXNG_HTML)

    stub(monkeypatch, handler)
    rows = wt.search_searxng("http://searx.local", "q", 5)
    assert calls == ["json", "html"]
    assert len(rows) == 2


def test_searxng_rejects_non_http_base():
    assert wt.search_searxng("ftp://nope", "q", 5) is None
    assert wt.search_searxng("", "q", 5) is None


def test_searxng_returns_none_when_unreachable(monkeypatch):
    def handler(url, **kw):
        raise OSError("connection refused")

    stub(monkeypatch, handler)
    assert wt.search_searxng("http://searx.local", "q", 5) is None


def test_searxng_json_garbage_falls_back_to_html(monkeypatch):
    def handler(url, **kw):
        fmt = kw.get("params", {}).get("format", "html")
        return (200, "<html>not json</html>") if fmt == "json" else (200, SEARXNG_HTML)

    stub(monkeypatch, handler)
    assert len(wt.search_searxng("http://searx.local", "q", 5)) == 2


def test_duckduckgo_success(monkeypatch):
    stub(monkeypatch, lambda url, **kw: (200, DDG_HTML))
    rows = wt.search_duckduckgo("q", 5)
    assert [r["url"] for r in rows] == ["https://one.example.com/a",
                                        "https://two.example.com/b"]


def test_duckduckgo_403_returns_none(monkeypatch):
    stub(monkeypatch, lambda url, **kw: (403, "forbidden"))
    assert wt.search_duckduckgo("q", 5) is None


def test_duckduckgo_timeout_returns_none(monkeypatch):
    import httpx

    def handler(url, **kw):
        raise httpx.TimeoutException("too slow")

    stub(monkeypatch, handler)
    assert wt.search_duckduckgo("q", 5) is None


# --------------------------------------------------------------------------
# Tool handler
# --------------------------------------------------------------------------
def test_tool_requires_query():
    out = wt.web_search.handler({}, ctx())
    assert out["error"]


def test_tool_returns_results_with_engine(monkeypatch):
    stub(monkeypatch, lambda url, **kw: (200, DDG_HTML))
    out = wt.web_search.handler({"query": "kevrai"}, ctx())
    assert out["engine"] == "duckduckgo"
    assert out["count"] == 2
    assert out["results"][0]["url"] == "https://one.example.com/a"


def test_tool_clamps_limit(monkeypatch):
    stub(monkeypatch, lambda url, **kw: (200, DDG_HTML))
    out = wt.web_search.handler({"query": "q", "limit": 1}, ctx())
    assert out["count"] == 1
    # Absurd / non-numeric limits must never crash the agent.
    assert wt.web_search.handler({"query": "q", "limit": "abc"}, ctx())["count"] == 2
    assert wt.web_search.handler({"query": "q", "limit": 999}, ctx())["count"] == 2


def test_tool_prefers_configured_searxng(monkeypatch):
    def handler(url, **kw):
        return (200, SEARXNG_JSON) if "searx.local" in url else (200, DDG_HTML)

    stub(monkeypatch, handler)
    out = wt.web_search.handler({"query": "q"},
                                ctx(**{wt.SEARXNG_URL_SETTING: "http://searx.local"}))
    assert out["engine"] == "searxng"
    assert out["count"] == 2


def test_tool_falls_back_when_searxng_empty(monkeypatch):
    def handler(url, **kw):
        # SearXNG answers but has nothing to show; DuckDuckGo then refuses.
        return (200, "<html><body>no results</body></html>") if "searx.local" in url else (403, "no")

    stub(monkeypatch, handler)
    out = wt.web_search.handler({"query": "q"},
                                ctx(**{wt.SEARXNG_URL_SETTING: "http://searx.local"}))
    assert out["count"] == 0
    assert out["error_type"] == "WebSearchUnavailable"
    assert out["notices"], "a fallback attempt must be reported"


def test_tool_structured_error_when_everything_fails(monkeypatch):
    def handler(url, **kw):
        raise OSError("no network")

    stub(monkeypatch, handler)
    out = wt.web_search.handler({"query": "q"}, ctx())
    assert out["results"] == []
    assert out["count"] == 0
    assert "error" in out and "error_type" in out
    # Crucially: no exception escapes, so the agent run survives.


def test_tool_never_raises_on_transport_failure(monkeypatch):
    stub(monkeypatch, lambda url, **kw: (_ for _ in ()).throw(RuntimeError("boom")))
    out = wt.web_search.handler({"query": "q"}, ctx())
    assert out["count"] == 0


def test_tool_uses_environment_searxng(monkeypatch):
    monkeypatch.setenv(wt.SEARXNG_URL_ENV, "http://env.local:8080")
    seen: list[str] = []

    def handler(url, **kw):
        seen.append(url)
        return 200, SEARXNG_JSON

    stub(monkeypatch, handler)
    out = wt.web_search.handler({"query": "q"}, ctx())
    assert out["engine"] == "searxng"
    assert any("env.local" in u for u in seen)


# --------------------------------------------------------------------------
# Skill registration contract
# --------------------------------------------------------------------------
def test_web_skill_is_opt_in():
    from app.agent.tools import BUILTIN_SKILLS

    skill = next(s for s in BUILTIN_SKILLS if s.id == "web_search")
    assert skill.default_enabled is False, "network access must be opt-in"
    assert skill.required is False
    assert [t.name for t in skill.tools] == ["web_search"]


def test_web_skill_guidance_forbids_inventing_sources():
    from app.agent.tools import BUILTIN_SKILLS

    skill = next(s for s in BUILTIN_SKILLS if s.id == "web_search")
    assert "严禁编造" in skill.guidance
    assert "如实" in skill.guidance


def test_web_skill_not_enabled_by_default():
    from app.agent.tools import build_skill_manager

    sm = build_skill_manager(state_path=None)
    assert "web_search" not in sm.active_tool_names()


def test_web_skill_enables_when_turned_on():
    from app.agent.tools import build_skill_manager

    sm = build_skill_manager(state_path=None)
    sm.set_enabled("web_search", True)
    assert "web_search" in sm.active_tool_names()
