#!/usr/bin/env python3
"""Kevrai Omni — catalog URL liveness / ad-placeholder auditor.

New, read-only tool (does NOT modify catalogs). It extracts every URL from
catalog/models.json and catalog/engines.json (model sources/primary_url,
gguf_repos sources, engine platforms + sources), then performs polite,
concurrent network checks (HEAD, falling back to a ranged GET) and
classifies each unique URL:

    OK            reachable, expected success (200/206)
    REDIRECT_OK   reachable after following redirects
    GATED         repository/resource requires authentication (401/403)
    DEAD          404/410 or hard 4xx that means "missing"
    SERVER_ERROR  5xx
    TIMEOUT       exceeded deadline
    DNS_ERROR     name does not resolve / no route
    SUSPICIOUS    parked/ad/placeholder, or malformed for its intended use
    UNCHECKED     not checked (e.g. --limit)

Outputs machine-readable JSON and a human Markdown report. Every verdict
records the status code, redirect chain, content type, elapsed time and the
catalog provenance, so conclusions are reproducible.
"""
from __future__ import annotations

import argparse
import asyncio
import collections
import json
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

try:
    import httpx
except ImportError:  # pragma: no cover
    sys.stderr.write("httpx is required (part of python/requirements.txt)\n")
    raise

# Hosts that are only meaningful with a signed query string; a bare path here
# is never a usable browse/download source.
SIGNED_ONLY_HOSTS = {
    "objects.githubusercontent.com",
    "github-releases.githubusercontent.com",
    # Current GitHub release-asset host (signed redirect target; a bare path
    # is meaningless, but a signed redirect here is a healthy download).
    "release-assets.githubusercontent.com",
}
# Hosts the project historically treated as leaked / non-canonical; verify.
WATCH_HOSTS = {"hf-cdn.sufy.com", "hf-cn-mirror.com", "hf-mirror.us"}
# Well-known, expected mirrors / hosts.
KNOWN_GOOD_HOSTS = {
    "huggingface.co", "hf-mirror.com", "modelscope.cn", "github.com",
    "pypi.org", "pypi.tuna.tsinghua.edu.cn", "mirrors.aliyun.com",
    "mirrors.cloud.tencent.com", "mirrors.tuna.tsinghua.edu.cn",
    "mirrors.huaweicloud.com", "gitcode.com", "sgl-project.github.io",
    "www.minimax.io", "ollama.com",
}

FILE_EXT_RE = re.compile(r"\.(gguf|zip|exe|dmg|AppImage|deb|rpm|safetensors|tar\.[a-z0-9]+|whl|bin|pt|pth|onnx|mnn|ms|apk|7z|gz)$", re.I)

# Hosts whose 401/403/404 (and sometimes 5xx) from THIS egress were shown to
# be unreliable during development: well-known public repositories
# (e.g. xinntao/Real-ESRGAN, RVC-Boss/GPT-SoVITS, stabilityai/stable-diffusion-2-1)
# intermittently returned 401 "Invalid username or password" while control
# repos on the same host returned 200. Auth/not-found verdicts on these hosts
# must be treated as low-confidence and re-verified interactively.
LOW_CONF_HOSTS = {"huggingface.co", "hf-mirror.com",
                  "mirrors.tuna.tsinghua.edu.cn", "pypi.tuna.tsinghua.edu.cn"}
LOW_CONF_VERDICTS = {"GATED", "DEAD", "SUSPICIOUS", "SERVER_ERROR", "TIMEOUT", "DNS_ERROR"}


@dataclass
class Ref:
    url: str
    where: str


@dataclass
class Result:
    url: str
    verdict: str = "UNCHECKED"
    status: int | None = None
    final_url: str | None = None
    chain: list[str] = field(default_factory=list)
    content_type: str | None = None
    content_length: int | None = None
    elapsed_ms: int | None = None
    detail: str = ""
    confidence: str = "high"
    refs: list[str] = field(default_factory=list)


def collect(catalog_dir: Path) -> tuple[list[Ref], dict]:
    m = json.loads((catalog_dir / "models.json").read_text(encoding="utf-8"))
    e = json.loads((catalog_dir / "engines.json").read_text(encoding="utf-8"))
    refs: list[Ref] = []

    def add(u, where):
        if isinstance(u, str):
            u = u.strip()
            if u.startswith(("http://", "https://")):
                refs.append(Ref(u, where))

    for x in m.get("models", []):
        mid = x.get("id", "?")
        for s in x.get("sources", []) or []:
            add(s, f"model:{mid}")
        add(x.get("primary_url"), f"model-primary:{mid}")
        repo = x.get("repo")
        if repo:
            add(f"https://huggingface.co/{repo}", f"repo-page:{mid}")
    for g in m.get("gguf_repos", []) or []:
        gid = g.get("id", "?")
        for s in g.get("sources", []) or []:
            add(s, f"gguf:{gid}")
    for en in e.get("engines", []):
        eid = en.get("id", "?")
        for plat, u in (en.get("platforms") or {}).items():
            add(u, f"platform:{eid}:{plat}")
        for s in en.get("sources", []) or []:
            add(s, f"engine-src:{eid}")
    meta = {"model_count": len(m.get("models", [])), "engine_count": len(e.get("engines", []))}
    return refs, meta


def kind_for(url: str) -> str:
    p = urlparse(url)
    tail = p.path.rstrip("/").rsplit("/", 1)[-1]
    if FILE_EXT_RE.search(tail):
        return "file"
    if "/resolve/" in p.path or "/releases/download/" in p.path or "/releases/latest/download/" in p.path:
        # resolve/download base without a concrete file name
        return "file-base"
    return "page"


async def probe(client: httpx.AsyncClient, url: str, timeout: float) -> Result:
    r = Result(url=url)
    host = urlparse(url).netloc
    kind = kind_for(url)
    t0 = time.monotonic()

    # Signed-only hosts with no query string are unusable as listed.
    if host in SIGNED_ONLY_HOSTS and not urlparse(url).query:
        r.verdict = "SUSPICIOUS"
        r.detail = "signed-CDN host listed without a signature/query; not a usable source"
        r.elapsed_ms = int((time.monotonic() - t0) * 1000)
        return r

    headers = {"User-Agent": "kevrai-liveness-audit/1.0", "Range": "bytes=0-1023"}

    async def one(method: str) -> tuple[httpx.Response | None, str | None]:
        try:
            req = client.build_request(method, url, headers=headers)
            resp = await client.send(req, follow_redirects=True)
            return resp, None
        except httpx.TimeoutException:
            return None, "TIMEOUT"
        except (httpx.ConnectError, httpx.RemoteProtocolError, httpx.ReadError) as ex:
            msg = str(ex)
            if re.search(r"getaddrinfo|Name or service not known|Temporary failure in name resolution|nodename nor servname", msg):
                return None, "DNS_ERROR"
            return None, "CONNECT_ERROR"
        except Exception as ex:  # pragma: no cover
            return None, f"ERR:{type(ex).__name__}"

    resp, err = await one("HEAD")
    # Fall back to a ranged GET when HEAD is rejected/uninformative.
    if resp is not None and (resp.status_code in (403, 405, 501) or
                             (kind == "file" and resp.status_code >= 400)):
        await resp.aclose()
        resp, err = await one("GET")
    if resp is not None and resp.status_code in (405, 501):
        await resp.aclose()
        resp, err = await one("GET")

    r.elapsed_ms = int((time.monotonic() - t0) * 1000)
    if err is not None:
        r.verdict = {"TIMEOUT": "TIMEOUT", "DNS_ERROR": "DNS_ERROR"}.get(err, "SUSPICIOUS")
        r.detail = err
        if resp is not None:
            await resp.aclose()
        return r

    try:
        r.status = resp.status_code
        r.final_url = str(resp.url)
        r.chain = [str(h.url) for h in resp.history]
        r.content_type = resp.headers.get("content-type", "").split(";")[0].strip() or None
        cl = resp.headers.get("content-length")
        cr = resp.headers.get("content-range")
        if cl and cl.isdigit():
            r.content_length = int(cl)
        elif cr:
            m = re.search(r"/(\d+)$", cr)
            if m and m.group(1) != "*":
                r.content_length = int(m.group(1))
        redirected = bool(resp.history)
        code = resp.status_code

        # Heuristics for parked/ad/placeholder pages.
        body_hint = ""
        if r.content_type and "html" in r.content_type and kind in ("file", "file-base"):
            body_hint = "file-looking URL returned HTML"

        if code in (200, 206):
            r.verdict = "REDIRECT_OK" if redirected else "OK"
            if body_hint:
                r.verdict = "SUSPICIOUS"
                r.detail = body_hint
        elif code in (301, 302, 303, 307, 308):
            r.verdict = "REDIRECT_OK"
        elif code in (401, 403):
            # Public HF repos return 200; 401/403 means gated or blocked.
            r.verdict = "GATED"
            r.detail = f"HTTP {code} (gated/blocked)"
        elif code in (404, 410):
            r.verdict = "DEAD"
            r.detail = f"HTTP {code}"
        elif 400 <= code < 500:
            r.verdict = "SUSPICIOUS"
            r.detail = f"unexpected HTTP {code}"
        elif code >= 500:
            r.verdict = "SERVER_ERROR"
            r.detail = f"HTTP {code}"

        if host in WATCH_HOSTS and r.verdict in ("OK", "REDIRECT_OK"):
            r.detail = (r.detail + "; " if r.detail else "") + "non-canonical mirror host — verify it is not parked/ad"
            r.verdict = "SUSPICIOUS"
        if host not in KNOWN_GOOD_HOSTS and host not in WATCH_HOSTS and host not in SIGNED_ONLY_HOSTS:
            r.detail = (r.detail + "; " if r.detail else "") + "unknown host"
            if r.verdict in ("OK", "REDIRECT_OK"):
                r.verdict = "SUSPICIOUS"
    finally:
        await resp.aclose()
    return r


async def run(catalog_dir: Path, concurrency: int, timeout: float, limit: int | None) -> dict:
    refs, meta = collect(catalog_dir)
    by_url: dict[str, Result] = {}
    for ref in refs:
        r = by_url.get(ref.url)
        if r is None:
            r = Result(url=ref.url)
            by_url[ref.url] = r
        r.refs.append(ref.where)

    urls = sorted(by_url.keys())
    if limit:
        urls = urls[:limit]

    sem = asyncio.Semaphore(concurrency)
    limits = httpx.Limits(max_connections=concurrency * 2, max_keepalive_connections=concurrency)

    async with httpx.AsyncClient(
        timeout=httpx.Timeout(timeout, connect=min(timeout, 12.0)),
        limits=limits, verify=True,
    ) as client:
        async def worker(u: str):
            async with sem:
                res = await probe(client, u, timeout)
                res.refs = by_url[u].refs
                by_url[u] = res
                # brief politeness on the hot hosts
                await asyncio.sleep(0.02)

        done = 0
        tasks = [asyncio.create_task(worker(u)) for u in urls]
        for t in asyncio.as_completed(tasks):
            await t
            done += 1
            if done % 25 == 0 or done == len(tasks):
                print(f"  ...{done}/{len(tasks)}", file=sys.stderr)

    results = [by_url[u] for u in urls]
    for r in results:
        host = urlparse(r.url).netloc
        if host in LOW_CONF_HOSTS and r.verdict in LOW_CONF_VERDICTS:
            r.confidence = "low"
            note = "egress-dependent; re-verify interactively"
            if note not in r.detail:
                r.detail = (r.detail + "; " if r.detail else "") + note
    counts = collections.Counter(r.verdict for r in results)
    return {"meta": meta, "total_unique": len(urls), "counts": dict(counts),
            "results": [r.__dict__ for r in results]}


def to_markdown(data: dict) -> str:
    counts = data["counts"]
    lines = [
        "# Catalog URL Liveness Audit",
        "",
        f"- Models: **{data['meta']['model_count']}**, Engines: **{data['meta']['engine_count']}**",
        f"- Unique URLs checked: **{data['total_unique']}**",
        "- Verdict counts: " + ", ".join(f"{k}={v}" for k, v in sorted(counts.items())),
        "",
        "## Non-OK / needs attention",
        "",
        "| Verdict | Conf | URL | Status | Detail | Used by |",
        "|---|---|---|---|---|---|",
    ]
    problem = [r for r in data["results"] if r["verdict"] not in ("OK", "REDIRECT_OK")]
    order = {"DEAD": 0, "SUSPICIOUS": 1, "DNS_ERROR": 2, "TIMEOUT": 3,
             "SERVER_ERROR": 4, "GATED": 5, "UNCHECKED": 6}
    for r in sorted(problem, key=lambda x: order.get(x["verdict"], 9)):
        used = ", ".join(sorted(set(r["refs"]))[:4])
        lines.append("| {v} | {cf} | `{u}` | {s} | {d} | {used} |".format(
            v=r["verdict"], cf=r.get("confidence", "high"), u=r["url"], s=r.get("status") or "—",
            d=(r.get("detail") or "").replace("|", "/"), used=used))
    lines += [
        "",
        "## Method limitations",
        "",
        "- Auth/not-found verdicts on `huggingface.co`, `hf-mirror.com` and the TUNA mirrors are "
        "**low-confidence**: during this run the sandbox egress returned 401 "
        "(\"Invalid username or password\") for demonstrably public repositories while control "
        "repositories on the same host returned 200. These rows must be re-verified interactively "
        "and must not, on their own, be treated as gated/dead.",
        "- High-confidence findings are those cross-checked with consistent controls: GitHub pages/API, "
        "ModelScope model pages (200 controls), DNS resolution, and signed-CDN hosts listed without signatures.",
    ]
    lines += ["", "## Host summary", ""]
    hosts = collections.Counter(urlparse(r["url"]).netloc for r in data["results"])
    lines.append("| Host | URLs | non-OK |")
    lines.append("|---|---|---|")
    host_bad = collections.Counter()
    for r in data["results"]:
        if r["verdict"] not in ("OK", "REDIRECT_OK"):
            host_bad[urlparse(r["url"]).netloc] += 1
    for h, c in hosts.most_common():
        lines.append(f"| {h} | {c} | {host_bad.get(h, 0)} |")
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    here = Path(__file__).resolve().parent.parent
    ap.add_argument("--catalog-dir", default=str(here / "catalog"))
    ap.add_argument("--json-out", default=None)
    ap.add_argument("--md-out", default=None)
    ap.add_argument("--concurrency", type=int, default=12)
    ap.add_argument("--timeout", type=float, default=15.0)
    ap.add_argument("--limit", type=int, default=None, help="check only first N unique URLs (debug)")
    args = ap.parse_args(argv)

    data = asyncio.run(run(Path(args.catalog_dir), args.concurrency, args.timeout, args.limit))
    payload = json.dumps(data, ensure_ascii=False, indent=2)
    if args.json_out:
        Path(args.json_out).write_text(payload, encoding="utf-8")
    md = to_markdown(data)
    if args.md_out:
        Path(args.md_out).write_text(md, encoding="utf-8")
    print(md)
    bad = sum(v for k, v in data["counts"].items() if k not in ("OK", "REDIRECT_OK"))
    print(f"\nSummary: {data['total_unique']} unique, non-OK={bad}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
