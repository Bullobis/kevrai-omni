"""Agent tool: web search (opt-in, offline-safe).

Gives the local agent a way to answer questions about anything that is *not*
already on the machine — release notes, current prices, documentation. It is
deliberately the smallest possible surface:

* **No new dependencies.** HTTP goes through ``httpx`` (already required by the
  app) and HTML is parsed with the standard-library :class:`html.parser`.
* **Two engines, one contract.** SearXNG first (self-hostable, no key, no
  per-query billing), then DuckDuckGo's HTML endpoint (no key) as a fallback.
* **Never raises.** Every network/parse failure degrades into a structured
  error dict so the agent can say "I could not reach the network" instead of
  crashing a run — and, critically, so it never invents a URL.
* **Offline by default.** The containing skill is opt-in; nothing here runs
  unless the user enabled it.
"""
from __future__ import annotations

import json
import logging
import os
from html.parser import HTMLParser
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from ..tool_registry import Tool, ToolContext

log = logging.getLogger("kevrai.web_tools")

# Environment override for a self-hosted SearXNG instance, e.g.
# "http://127.0.0.1:8080". Takes effect when the UI has not configured one.
SEARXNG_URL_ENV = "KEVRAI_SEARXNG_URL"
# Settings key (in ``Settings.extra``) holding the same value.
SEARXNG_URL_SETTING = "searxng_url"

DEFAULT_TIMEOUT = 8.0
MAX_LIMIT = 20
_USER_AGENT = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
               "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")


# ---------------------------------------------------------------------------
# HTTP — isolated so tests can replace it without touching the network
# ---------------------------------------------------------------------------
def http_get(url: str, *, params: dict[str, Any] | None = None,
             headers: dict[str, str] | None = None,
             timeout: float = DEFAULT_TIMEOUT) -> tuple[int, str]:
    """GET ``url`` and return ``(status_code, body_text)``.

    Raises only for transport-level failures; HTTP error statuses are returned
    so callers can decide (e.g. 403 from DuckDuckGo is a known, reportable
    condition rather than an exception).
    """
    import httpx

    resp = httpx.get(url, params=params, headers=headers, timeout=timeout,
                     follow_redirects=True)
    return resp.status_code, resp.text


def _safe_http_get(url: str, **kw) -> tuple[int, str] | None:
    """``http_get`` that returns ``None`` instead of raising."""
    try:
        return http_get(url, **kw)
    except Exception as e:  # noqa: BLE001 — network is best-effort by design
        log.info("web request failed url=%s err=%s", url, e)
        return None


# ---------------------------------------------------------------------------
# HTML parsing
# ---------------------------------------------------------------------------
class _SearXngParser(HTMLParser):
    """Extract result blocks from SearXNG's default HTML theme."""

    def __init__(self) -> None:
        super().__init__()
        self.results: list[dict[str, str]] = []
        self._in_result = False
        self._in_link = False
        self._in_snippet = False
        self._href = ""
        self._title: list[str] = []
        self._snippet: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        cls = dict(attrs).get("class") or ""
        if tag == "article" and "result" in cls:
            self._in_result = True
            self._href = ""
            self._title = []
            self._snippet = []
        elif self._in_result and tag == "a":
            if "url_header" in cls or "url_wrapper" in cls:
                return
            href = dict(attrs).get("href") or ""
            if href.startswith(("http://", "https://")) and not self._href:
                self._href = href
                self._in_link = True
        elif self._in_result and tag == "p" and "content" in cls:
            self._in_snippet = True

    def handle_endtag(self, tag: str) -> None:
        if tag == "article" and self._in_result:
            self._in_result = False
            if self._href:
                self.results.append({
                    "title": " ".join("".join(self._title).split()),
                    "url": self._href,
                    "snippet": " ".join("".join(self._snippet).split()),
                })
        elif tag == "a":
            self._in_link = False
        elif tag == "p":
            self._in_snippet = False

    def handle_data(self, data: str) -> None:
        if self._in_link:
            self._title.append(data)
        elif self._in_snippet:
            self._snippet.append(data)


class _DuckDuckGoParser(HTMLParser):
    """Extract results from ``html.duckduckgo.com`` (the no-JS endpoint)."""

    def __init__(self) -> None:
        super().__init__()
        self.results: list[dict[str, str]] = []
        self._in_link = False
        self._in_snippet = False
        self._href = ""
        self._title: list[str] = []
        self._snippet: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        cls = dict(attrs).get("class") or ""
        if tag == "a" and "result__a" in cls:
            self._in_link = True
            self._href = dict(attrs).get("href") or ""
            self._title = []
        elif tag in ("a", "div") and "result__snippet" in cls:
            self._in_snippet = True
            self._snippet = []

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._in_link:
            self._in_link = False
            self.results.append({
                "title": " ".join("".join(self._title).split()),
                "url": _decode_duckduckgo_href(self._href),
                "snippet": " ".join("".join(self._snippet).split()),
            })
        elif tag in ("a", "div") and self._in_snippet:
            self._in_snippet = False
            # The snippet follows its title link, so it belongs to the row
            # already emitted — write it back rather than appending a new one.
            if self.results:
                self.results[-1]["snippet"] = " ".join(
                    "".join(self._snippet).split()
                )
            self._snippet = []

    def handle_data(self, data: str) -> None:
        if self._in_link:
            self._title.append(data)
        elif self._in_snippet:
            self._snippet.append(data)


def _decode_duckduckgo_href(href: str) -> str:
    """Unwrap DuckDuckGo's ``/l/?uddg=<encoded>`` redirect link."""
    href = (href or "").strip()
    if not href:
        return ""
    parsed = urlparse(href)
    if "uddg" in parse_qs(parsed.query):
        target = parse_qs(parsed.query)["uddg"][0]
        return unquote(target)
    return href


def _parse_html(html: str, parser: HTMLParser) -> list[dict[str, str]]:
    parser.feed(html or "")
    parser.close()
    return list(parser.results)  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Engines
# ---------------------------------------------------------------------------
def _clean_results(rows: list[dict[str, str]], limit: int) -> list[dict[str, str]]:
    """Drop malformed / non-http rows, de-duplicate by URL, cap at ``limit``."""
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for row in rows:
        url = (row.get("url") or "").strip()
        if not url.startswith(("http://", "https://")):
            continue
        # Normalise away trivial duplicates (trailing slash / fragment).
        key = url.split("#", 1)[0].rstrip("/").lower()
        if key in seen:
            continue
        seen.add(key)
        out.append({
            "title": (row.get("title") or "").strip() or url,
            "url": url,
            "snippet": (row.get("snippet") or "").strip(),
        })
        if len(out) >= limit:
            break
    return out


def search_searxng(base: str, query: str, limit: int,
                   timeout: float = DEFAULT_TIMEOUT) -> list[dict[str, str]] | None:
    """Query a SearXNG instance; ``None`` when it is unreachable or unusable.

    ``format=json`` is attempted first because it is far more robust than
    scraping; when the instance has JSON disabled (the default) we fall back to
    parsing the HTML theme.
    """
    base = (base or "").strip().rstrip("/")
    if not base.startswith(("http://", "https://")):
        return None

    headers = {"User-Agent": _USER_AGENT, "Accept": "application/json, text/html"}

    got = _safe_http_get(f"{base}/search",
                         params={"q": query, "format": "json", "language": "zh-CN"},
                         headers=headers, timeout=timeout)
    if got is not None:
        status, body = got
        if status == 200 and body.lstrip().startswith(("{", "[")):
            try:
                payload = json.loads(body)
                rows = [
                    {"title": r.get("title", ""), "url": r.get("url", ""),
                     "snippet": r.get("content", "")}
                    for r in payload.get("results", [])
                ]
                cleaned = _clean_results(rows, limit)
                if cleaned:
                    return cleaned
            except (ValueError, TypeError, AttributeError) as e:
                log.info("searxng json payload unusable: %s", e)

    got = _safe_http_get(f"{base}/search", params={"q": query},
                         headers=headers, timeout=timeout)
    if got is None:
        return None
    status, body = got
    if status != 200:
        return None
    return _clean_results(_parse_html(body, _SearXngParser()), limit)


def search_duckduckgo(query: str, limit: int,
                      timeout: float = DEFAULT_TIMEOUT) -> list[dict[str, str]] | None:
    """Query DuckDuckGo's HTML endpoint (no API key). ``None`` on failure."""
    got = _safe_http_get(
        "https://html.duckduckgo.com/html/",
        params={"q": query},
        headers={"User-Agent": _USER_AGENT,
                 "Accept": "text/html",
                 "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"},
        timeout=timeout,
    )
    if got is None:
        return None
    status, body = got
    if status != 200:
        log.info("duckduckgo returned status %s", status)
        return None
    return _clean_results(_parse_html(body, _DuckDuckGoParser()), limit)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
def searxng_base_url(ctx: ToolContext | None = None) -> str:
    """Resolve the SearXNG base URL: settings → environment → none."""
    if ctx is not None:
        try:
            extra = getattr(ctx, "extra", None) or {}
            configured = extra.get(SEARXNG_URL_SETTING)
            if not configured:
                from ...settings import load_settings

                configured = (load_settings().extra or {}).get(SEARXNG_URL_SETTING)
            if isinstance(configured, str) and configured.strip():
                return configured.strip().rstrip("/")
        except Exception as e:  # noqa: BLE001 — config is optional
            log.debug("读取 SearXNG 配置失败，回退到环境变量: %s", e)
    env = os.environ.get(SEARXNG_URL_ENV, "").strip().rstrip("/")
    return env


# ---------------------------------------------------------------------------
# Tool
# ---------------------------------------------------------------------------
def _web_search(params: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    query = str(params.get("query") or "").strip()
    if not query:
        return {"error": "query is required"}
    try:
        limit = int(params.get("limit") or 5)
    except (TypeError, ValueError):
        limit = 5
    limit = max(1, min(limit, MAX_LIMIT))
    timeout = float(params.get("timeout") or DEFAULT_TIMEOUT)

    engine_used = ""
    results: list[dict[str, str]] = []
    notices: list[str] = []

    base = searxng_base_url(ctx)
    if base:
        rows = search_searxng(base, query, limit, timeout=timeout)
        if rows:
            engine_used = "searxng"
            results = rows
        else:
            notices.append("自托管 SearXNG 无结果或不可达，已回退 DuckDuckGo。")

    if not results:
        rows = search_duckduckgo(query, limit, timeout=timeout)
        if rows:
            engine_used = "duckduckgo"
            results = rows

    if not results:
        return {
            "query": query,
            "results": [],
            "count": 0,
            "error": ("未能获取搜索结果：网络不可达或搜索引擎拒绝了请求。"
                      "若使用 DuckDuckGo，建议自托管 SearXNG 后重试。"),
            "error_type": "WebSearchUnavailable",
            "notices": notices,
        }

    return {
        "query": query,
        "engine": engine_used,
        "count": len(results),
        "results": results,
        "notices": notices,
    }


web_search = Tool(
    name="web_search",
    description=(
        "Search the public web and return titles, URLs and snippets. Use it only "
        "for information that is not on this machine (recent releases, prices, "
        "documentation). Prefer a configured self-hosted SearXNG; falls back to "
        "DuckDuckGo. Returns an error instead of results when the network is "
        "unreachable — in that case say so plainly and never invent URLs or facts."
    ),
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string",
                      "description": "The search query."},
            "limit": {"type": "integer",
                      "description": f"Max results to return (1-{MAX_LIMIT}, default 5)."},
            "timeout": {"type": "number",
                        "description": "Per-request timeout in seconds (default 8)."},
        },
        "required": ["query"],
    },
    handler=_web_search,
    category="web",
)


WEB_TOOLS = [web_search]
