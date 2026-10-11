"""Regression for the catalog liveness auditor's byte-range handling.

Some servers (notably the aliyun pypi simple index, and some mirrors that do
not support range requests on every object) answer a ``Range`` request with
HTTP 416 even though the resource is fully available. The auditor must not
flag those as suspicious:

* page/index URLs must not carry a Range header at all;
* file URLs that get a 416 must be retried once with a range-free GET.

The probes are exercised deterministically with ``httpx.MockTransport`` so no
real network is used.
"""
from __future__ import annotations

import sys
from pathlib import Path

import httpx
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import audit_catalog_liveness as audit  # noqa: E402


def _transport() -> httpx.MockTransport:
    """416 for any ranged request, 200 otherwise."""

    def handler(request: httpx.Request) -> httpx.Response:
        ctype = ("application/octet-stream"
                 if request.url.path.endswith(".bin") else "text/html")
        if request.headers.get("range"):
            return httpx.Response(416, headers={"content-type": ctype})
        return httpx.Response(200, headers={"content-type": ctype})

    return httpx.MockTransport(handler)


async def _probe(url: str):
    async with httpx.AsyncClient(transport=_transport()) as client:
        return await audit.probe(client, url, 10.0)


@pytest.mark.asyncio
async def test_page_index_does_not_send_range_and_is_ok():
    # A pypi-style simple index page (kind == "page").
    r = await _probe("https://mirrors.aliyun.com/pypi/simple/mnn/")
    assert r.verdict == "OK"
    assert r.status == 200


@pytest.mark.asyncio
async def test_file_url_recovers_from_416_with_plain_get():
    # A file-looking URL whose server only rejects the byte-range.
    r = await _probe("https://hf-mirror.com/models/model.bin")
    assert r.verdict == "OK"
    assert r.status == 200


@pytest.mark.asyncio
async def test_genuinely_dead_file_still_flagged():
    """A real 404 must not be masked by the range retry."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        r = await audit.probe(client, "https://example.com/missing.bin", 10.0)
    assert r.verdict == "DEAD"
