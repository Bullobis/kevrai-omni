"""Connection-pool lifecycle for :class:`app.downloader.Downloader`.

Two pools exist and they must not be confused:

* an *injected* ``client`` — owned by the caller, so the downloader must never
  close it;
* ``_own_client`` — built lazily when no client was injected, owned by the
  downloader, and closed in :meth:`aclose`.

``_own_client`` used to be created only inside ``_get_client`` (guarded by
``hasattr``/``getattr``).  It is now declared in ``__init__`` so the attribute
always exists and its type is concrete, which let the ``# type: ignore`` and
the defensive attribute probing both go away.  These tests pin the behaviour
that refactor was supposed to preserve.
"""
from __future__ import annotations

import httpx

from app.downloader import Downloader


async def test_injected_client_is_used_and_not_owned():
    """A caller-supplied client is returned as-is and survives ``aclose``."""
    injected = httpx.AsyncClient()
    try:
        d = Downloader(client=injected)
        assert d._own_client is None

        assert await d._get_client() is injected
        # Nothing was built, because the injected client is used directly.
        assert d._own_client is None

        await d.aclose()
        # Closing the downloader must not close a pool it does not own —
        # the caller may still be using it.
        assert not injected.is_closed
    finally:
        await injected.aclose()


async def test_own_client_is_created_lazily_and_cached():
    """With no injected client, one pool is built on first use and reused."""
    d = Downloader()

    # Declared in __init__ -> present and None before any request is made.
    assert d._own_client is None
    assert d._client is None

    first = await d._get_client()
    assert isinstance(first, httpx.AsyncClient)
    assert d._own_client is first

    # Second call must reuse the same instance rather than leaking a pool.
    assert await d._get_client() is first


async def test_aclose_closes_and_clears_own_client():
    """``aclose`` releases the self-built pool and resets the attribute."""
    d = Downloader()
    own = await d._get_client()
    assert d._own_client is own

    await d.aclose()

    assert own.is_closed
    assert d._own_client is None


async def test_aclose_is_idempotent_without_any_client():
    """Closing an unused downloader is a no-op, not an error."""
    d = Downloader()
    await d.aclose()
    await d.aclose()
    assert d._own_client is None
