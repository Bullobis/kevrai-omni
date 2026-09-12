"""Tests for the v2.8.1 source scheduler (design §4.1).

Everything here is **offline**: probes are synthetic dicts and, where a real
``measure_sources`` call would occur, it is monkeypatched with a fake that
never touches the network. The clock / cache are injected so cooling and cache
behaviour are asserted deterministically.
"""
from __future__ import annotations

import pytest

import app.sources as sources_mod
from app.sources_registry import (
    PRESET_SOURCES,
    SourceMeta,
    SourceRegistry,
    build_gitcode_candidates,
    normalize_user_mirrors,
)
from app.source_scheduler import (
    NEG_INF,
    PROFILE_LARGE,
    PROFILE_SMALL,
    SourceScheduler,
    choose_profile_weights,
    ewma_blend,
)


# ---------------------------------------------------------------------------
# Fake clock + probe factory
# ---------------------------------------------------------------------------


class FakeClock:
    def __init__(self, t: float = 1000.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t

    def advance(self, dt: float) -> None:
        self.t += dt


def _probe(url: str, host: str, ok: bool, lat: float, spd: float,
           status: int = 200) -> dict:
    return {
        "url": url, "host": host, "ok": ok, "latency_ms": lat,
        "speed_mbps": spd, "status": status if ok else 0,
        "size_bytes": 65535 if ok else 0, "error": "" if ok else "fail",
        "source_id": "", "source_type": "", "from_cache": False,
    }


def _make_fake_measure(results: list[dict]):
    async def _fake(urls, **kwargs):
        wanted = {str(u) for u in urls}
        return [dict(r) for r in results if r["url"] in wanted]
    return _fake


@pytest.fixture()
def registry() -> SourceRegistry:
    return SourceRegistry(persist_path=None, clock=FakeClock())


# ---------------------------------------------------------------------------
# Pure functions
# ---------------------------------------------------------------------------


def test_profile_weights_small_vs_large():
    assert choose_profile_weights(5 * 1024 * 1024) == PROFILE_SMALL
    assert choose_profile_weights(8 * 1024 ** 3) == PROFILE_LARGE
    # explicit override wins
    assert choose_profile_weights(8 * 1024 ** 3, "latency") == PROFILE_SMALL
    assert choose_profile_weights(1, "speed") == PROFILE_LARGE
    assert choose_profile_weights(1, "engine")[1] >= 0.9


def test_ewma_blend_first_sample_unchanged():
    assert ewma_blend(100.0, 0.0, 0.4) == 100.0
    # blend with history
    assert ewma_blend(100.0, 50.0, 0.4) == pytest.approx(0.4 * 100 + 0.6 * 50)


def test_score_source_failed_is_neg_inf(registry):
    sch = SourceScheduler(registry)
    bad = _probe("https://hf-mirror.com/x", "hf-mirror.com", False, 0, 0)
    view = sources_mod.SourceProbe(
        url=bad["url"], host=bad["host"], ok=False, latency_ms=0,
        speed_mbps=0, status=0, size_bytes=0,
    )
    assert sch.score_source(view, None, 0) == NEG_INF


# ---------------------------------------------------------------------------
# Selection correctness
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_select_prefers_fast_source(registry, monkeypatch):
    results = [
        _probe("https://hf-mirror.com/f", "hf-mirror.com", True, 50, 20),
        _probe("https://modelscope.cn/s", "modelscope.cn", True, 200, 5),
        _probe("https://hf-mirror.us/d", "hf-mirror.us", False, 0, 0),
    ]
    monkeypatch.setattr(sources_mod, "measure_sources", _make_fake_measure(results))
    sch = SourceScheduler(registry)
    res = await sch.select([r["url"] for r in results], file_size=5 * 1024 * 1024)
    assert res.best_url == "https://hf-mirror.com/f"
    assert res.ranking[0]["ok"] is True


@pytest.mark.asyncio
async def test_small_file_latency_dominant(registry, monkeypatch):
    # low-latency/low-speed vs high-latency/high-speed
    results = [
        _probe("https://hf-mirror.com/ll", "hf-mirror.com", True, 30, 5),
        _probe("https://modelscope.cn/ht", "modelscope.cn", True, 300, 50),
    ]
    monkeypatch.setattr(sources_mod, "measure_sources", _make_fake_measure(results))
    sch = SourceScheduler(registry)
    res = await sch.select([r["url"] for r in results], file_size=2 * 1024 * 1024)
    assert res.best_url == "https://hf-mirror.com/ll"


@pytest.mark.asyncio
async def test_large_file_throughput_dominant(registry, monkeypatch):
    results = [
        _probe("https://hf-mirror.com/ll", "hf-mirror.com", True, 30, 5),
        _probe("https://modelscope.cn/ht", "modelscope.cn", True, 300, 50),
    ]
    monkeypatch.setattr(sources_mod, "measure_sources", _make_fake_measure(results))
    sch = SourceScheduler(registry)
    res = await sch.select([r["url"] for r in results], file_size=8 * 1024 ** 3)
    assert res.best_url == "https://modelscope.cn/ht"


# ---------------------------------------------------------------------------
# EWMA smoothing
# ---------------------------------------------------------------------------


def test_ewma_history_damps_repeated_probe(registry):
    sch = SourceScheduler(registry)
    sid = "hf-mirror-com"
    # First observation: slow (lat 400). Probe it via health.observe.
    p1 = sources_mod.SourceProbe(
        url="https://hf-mirror.com/a", host="hf-mirror.com", ok=True,
        latency_ms=400, speed_mbps=2, status=200, size_bytes=100,
    )
    h = registry.health_of(sid)
    h.observe(p1, alpha=0.4)
    assert h.ewma_latency_ms == pytest.approx(400.0)
    # Second observation: fast (lat 100). EWMA should land between the two.
    p2 = sources_mod.SourceProbe(
        url="https://hf-mirror.com/a", host="hf-mirror.com", ok=True,
        latency_ms=100, speed_mbps=20, status=200, size_bytes=100,
    )
    h.observe(p2, alpha=0.4)
    assert 100.0 < h.ewma_latency_ms < 400.0
    assert h.ewma_latency_ms == pytest.approx(0.4 * 100 + 0.6 * 400)


# ---------------------------------------------------------------------------
# Cooling / circuit breaker
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cooling_skips_dead_source(monkeypatch):
    clock = FakeClock()
    reg = SourceRegistry(persist_path=None, fail_threshold=3,
                         cooldown_seconds=300.0, clock=clock)
    sid = "hf-mirror-us"
    h = reg.health_of(sid)
    for _ in range(3):
        h.breaker.record_fail()
    assert h.available() is False

    async def _boom(urls, **kwargs):  # must never be called for the cooling URL
        raise AssertionError("cooling source should not be probed")
    monkeypatch.setattr(sources_mod, "measure_sources", _boom)

    sch = SourceScheduler(reg)
    res = await sch.select(["https://hf-mirror.us/x"], file_size=0)
    assert "https://hf-mirror.us/x" in res.skipped
    assert res.best_url == ""


def test_cooling_recovery_after_cooldown():
    clock = FakeClock()
    reg = SourceRegistry(persist_path=None, fail_threshold=3,
                         cooldown_seconds=300.0, clock=clock)
    h = reg.health_of("hf-mirror-us")
    for _ in range(3):
        h.breaker.record_fail()
    assert h.breaker.state == "open"
    assert h.available() is False
    clock.advance(300.0)
    assert h.breaker.state == "half-open"
    # half-open lets exactly one probe through
    assert h.available() is True


# ---------------------------------------------------------------------------
# Probe cache
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_probe_cache_hit_second_time(registry, monkeypatch):
    results = [_probe("https://hf-mirror.com/f", "hf-mirror.com", True, 50, 20)]
    calls = {"n": 0}

    async def _counting(urls, **kwargs):
        calls["n"] += 1
        return [dict(r) for r in results if r["url"] in set(urls)]
    monkeypatch.setattr(sources_mod, "measure_sources", _counting)
    sch = SourceScheduler(registry, cache_ttl_s=300.0)
    r1 = await sch.select(["https://hf-mirror.com/f"], file_size=0)
    assert calls["n"] == 1
    r2 = await sch.select(["https://hf-mirror.com/f"], file_size=0)
    assert calls["n"] == 1, "second select must hit the cache"
    assert r2.from_cache is True
    assert any(it.get("from_cache") for it in r2.ranking)


@pytest.mark.asyncio
async def test_force_bypasses_cache(registry, monkeypatch):
    results = [_probe("https://hf-mirror.com/f", "hf-mirror.com", True, 50, 20)]
    calls = {"n": 0}

    async def _counting(urls, **kwargs):
        calls["n"] += 1
        return [dict(r) for r in results if r["url"] in set(urls)]
    monkeypatch.setattr(sources_mod, "measure_sources", _counting)
    sch = SourceScheduler(registry)
    await sch.select(["https://hf-mirror.com/f"], file_size=0)
    await sch.select(["https://hf-mirror.com/f"], file_size=0, force=True)
    assert calls["n"] == 2


# ---------------------------------------------------------------------------
# All-sources-fail
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_all_sources_fail_returns_empty_best(registry, monkeypatch):
    results = [
        _probe("https://hf-mirror.us/a", "hf-mirror.us", False, 0, 0),
        _probe("https://hf-cn-mirror.com/b", "hf-cn-mirror.com", False, 0, 0),
    ]
    monkeypatch.setattr(sources_mod, "measure_sources", _make_fake_measure(results))
    sch = SourceScheduler(registry)
    res = await sch.select([r["url"] for r in results], file_size=0)
    assert res.best_url == ""
    assert len(res.ranking) == 2  # still returned for UI display


# ---------------------------------------------------------------------------
# Registry / GitCode / mirror normalisation
# ---------------------------------------------------------------------------


def test_preset_sources_have_expected_states():
    reg = SourceRegistry(persist_path=None)
    # hf-mirror.com + modelscope enabled; the 4 dead mirrors disabled.
    assert reg.get("hf-mirror-com").enabled is True
    assert reg.get("modelscope").enabled is True
    for dead in ("hf-mirror-us", "hf-cn-mirror-com",
                 "huggingface-dl-in-tel", "hf-cdn-sufy-com"):
        assert reg.get(dead).enabled is False
    # gitcode present but disabled by default
    assert reg.get("gitcode").enabled is False
    # github is the engine source
    assert reg.get("github").type == "github_engine"


def test_normalize_user_mirrors_skips_preset_hosts():
    metas = normalize_user_mirrors(
        ["https://hf-mirror.com", "https://my.hf.mirror", "not-a-url"],
        existing_ids={"hf-mirror-com"},
    )
    ids = {m.id for m in metas}
    assert "hf-mirror-com" not in ids        # covered by preset
    assert "my-hf-mirror" in ids             # new custom mirror kept
    assert len(metas) == 1                   # bad url dropped


def test_build_gitcode_candidates_empty_template_is_noop():
    assert build_gitcode_candidates("o/r", "f.bin", "main", "") == []


def test_build_gitcode_candidates_with_template():
    tpl = "https://example.invalid/{repo}/-/raw/{revision}/{path}"
    out = build_gitcode_candidates("owner/repo", "dir/f.bin", "main", tpl)
    assert out == ["https://example.invalid/owner/repo/-/raw/main/dir/f.bin"]


def test_registry_snapshot_shape():
    reg = SourceRegistry(persist_path=None)
    snap = reg.snapshot()
    assert isinstance(snap, list) and snap
    first = snap[0]
    for key in ("id", "name", "origin", "type", "enabled", "preset", "health"):
        assert key in first
    assert "breaker" in first["health"]


def test_registry_persist_roundtrip(tmp_path):
    path = tmp_path / "source_health.json"
    reg = SourceRegistry(persist_path=path)
    h = reg.health_of("hf-mirror-com")
    p = sources_mod.SourceProbe(
        url="https://hf-mirror.com/a", host="hf-mirror.com", ok=True,
        latency_ms=42, speed_mbps=11, status=200, size_bytes=100,
    )
    h.observe(p, alpha=0.4)
    reg.save()
    assert path.exists()
    reg2 = SourceRegistry(persist_path=path)
    reg2.load()
    assert reg2.health_of("hf-mirror-com").ewma_latency_ms == pytest.approx(42.0)


def test_source_meta_matches():
    m = SourceMeta(id="x", name="x", origin="https://hf-mirror.com",
                   type="hf_mirror", host_patterns=["hf-mirror.com"])
    assert m.matches("hf-mirror.com")
    assert m.matches("cdn.hf-mirror.com")
    assert not m.matches("modelscope.cn")


def test_zero_byte_probe_is_not_usable():
    """Regression (P1-1): a 2xx response with an empty body is not a source.

    SPA fallback pages and opaque error pages answer 200 with no payload.
    Before the fix such a probe scored by latency alone and could outrank a
    real source, poisoning the health cache for the whole TTL window.
    """
    from app.source_scheduler import _is_usable_probe

    assert _is_usable_probe({"ok": True, "status": 200, "size_bytes": 0}) is False
    assert _is_usable_probe({"ok": True, "status": 200, "size_bytes": 65536}) is True
    assert _is_usable_probe({"ok": False, "status": 200, "size_bytes": 65536}) is False
    assert _is_usable_probe({"ok": True, "status": 404, "size_bytes": 65536}) is False
    # Attribute-style probes must behave identically to dicts.
    from app.sources import SourceProbe
    empty = SourceProbe(url="u", host="h", ok=True, latency_ms=1.0,
                        speed_mbps=0.0, status=200, size_bytes=0)
    real = SourceProbe(url="u", host="h", ok=True, latency_ms=1.0,
                       speed_mbps=0.0, status=200, size_bytes=65536)
    assert _is_usable_probe(empty) is False
    assert _is_usable_probe(real) is True


def test_zero_byte_source_never_wins_ranking():
    """Regression (P1-1): a fast 0-byte source must lose to a slower real one."""
    from app.source_scheduler import SourceScheduler
    from app.sources import SourceProbe
    from app.sources_registry import SourceRegistry

    s = SourceScheduler(SourceRegistry())
    zero = SourceProbe(url="https://z.example/f.bin", host="z.example", ok=True,
                       latency_ms=1.0, speed_mbps=0.0, status=200, size_bytes=0)
    real = SourceProbe(url="https://g.example/f.bin", host="g.example", ok=True,
                       latency_ms=300.0, speed_mbps=50.0, status=200,
                       size_bytes=65536)
    ranked = s._score_ranking([zero.to_dict(), real.to_dict()], 0, "")
    assert ranked[0]["host"] == "g.example"
    assert ranked[0]["score"] is not None
    assert ranked[-1]["score"] is None


def test_profile_actually_changes_the_winner():
    """Regression (P1-3): measure_sources() used to hard-code file_size=0 and
    silently drop `profile`, so latency/speed profiles returned the same winner.
    """
    from app.source_scheduler import SourceScheduler
    from app.sources import SourceProbe
    from app.sources_registry import SourceRegistry

    s = SourceScheduler(SourceRegistry())
    # Same size for both, so only the profile weights decide.
    low_lat = SourceProbe(url="https://a.example/f", host="a.example", ok=True,
                          latency_ms=10.0, speed_mbps=1.0, status=200,
                          size_bytes=65536)
    hi_speed = SourceProbe(url="https://b.example/f", host="b.example", ok=True,
                           latency_ms=500.0, speed_mbps=100.0, status=200,
                           size_bytes=65536)
    probes = [low_lat.to_dict(), hi_speed.to_dict()]

    by_latency = s._score_ranking(list(probes), 0, "latency")
    by_speed = s._score_ranking(list(probes), 0, "speed")
    assert by_latency[0]["host"] == "a.example"
    assert by_speed[0]["host"] == "b.example"


def test_measure_sources_forwards_profile():
    """Regression (P1-3): the profile kwarg must reach the scheduler."""
    import inspect

    from app import sources

    src = inspect.getsource(sources.measure_sources)
    assert "profile=profile" in src, "measure_sources must forward profile"
