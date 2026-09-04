"""v2.4.1 regression tests: catalog fact fixes, gated downloads (HF token),
engine update detection, LTX preset notes."""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import httpx
import pytest

PYDIR = Path(__file__).resolve().parents[1]
REPO = PYDIR.parent
sys.path.insert(0, str(PYDIR))
sys.path.insert(0, str(REPO))

from app.downloader import Downloader  # noqa: E402
from app.engines import (  # noqa: E402
    EngineManager,
    EngineRecord,
    EngineState,
    _write_manifest,
    apply_engine_update,
    check_engine_updates,
    list_engines_status,
)
from app.settings import Settings, load_settings, save_settings  # noqa: E402


# ---------------------------------------------------------------------------
# Catalog fact fixes
# ---------------------------------------------------------------------------

def _catalog():
    return json.loads((REPO / "catalog" / "models.json").read_text(encoding="utf-8"))


def test_catalog_no_ghost_hailuo_repo():
    data = _catalog()
    for m in data["models"]:
        blob = json.dumps(m, ensure_ascii=False)
        assert "Hailuo-H3" not in blob, f"ghost repo still present in {m['id']}"


def test_catalog_minimax_entries_corrected():
    models = {m["id"]: m for m in _catalog()["models"]}
    assert models["minimax-h3"]["repo"] == "Comfy-Org/MiniMax-H3"
    assert "768p" in models["minimax-h3-omni"]["description"]
    assert "Regenerate-2K" in models["minimax-h3-omni"]["description"]
    pend = models["minimax-2k-pending"]
    assert "Regenerate-2K" in pend["name"]
    assert "AMA" in pend["description"]
    assert any("minimax.io" in u for u in pend["sources"])


def test_catalog_ltx25_gated_and_16gb():
    ltx = {m["id"]: m for m in _catalog()["models"]}["ltx-2.5"]
    assert ltx.get("gated") is True
    assert ltx["hardware"]["min_vram_gb"] == 16
    assert ltx["license"].startswith("LTX-2.x")
    assert "12GB 显存本地运行" not in ltx["description"]


def test_catalog_qwen38_official_plus_community():
    """v2.6.0: official Qwen/Qwen3.8-27B weights opened 2026-08-14, so it is now
    the primary `repo`; the JonathanColetti uncensored quant is retained as the
    optional `gguf_repo`."""
    q = {m["id"]: m for m in _catalog()["models"]}["qwen3.8-27b"]
    assert q["repo"] == "Qwen/Qwen3.8-27B"
    assert q["license"] == "Apache-2.0"
    assert q["modality"]["multimodal"] is True
    assert q["gguf_repo"] == "JonathanColetti/Qwen3.8-27B-Uncensored-GGUF"
    # primary sources must point at the official repo, not the community quant
    assert q["primary_url"].rstrip("/").endswith("Qwen/Qwen3.8-27B")


def test_catalog_schema_still_valid():
    import catalog.schema as cs
    data = _catalog()
    assert cs.validate_models(data) == []
    engines = json.loads((REPO / "catalog" / "engines.json").read_text(encoding="utf-8"))
    assert cs.validate_engines(engines) == []


# ---------------------------------------------------------------------------
# Settings: HF token roundtrip
# ---------------------------------------------------------------------------

def test_settings_hf_token_roundtrip(tmp_path):
    p = tmp_path / "settings.json"
    s = Settings()
    s.hf_token = "hf_abc123"
    save_settings(s, p)
    assert load_settings(p).hf_token == "hf_abc123"


# ---------------------------------------------------------------------------
# Downloader: extra headers (gated repos)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_downloader_sends_extra_headers(tmp_path):
    seen = {}

    def handler(request):
        seen["auth"] = request.headers.get("authorization")
        return httpx.Response(200, content=b"hello kevrai")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    dl = Downloader(client=client)
    tid = await dl.start(
        "https://huggingface.co/x/resolve/main/f.bin",
        tmp_path / "f.bin",
        extra_headers={"Authorization": "Bearer hf_test"},
    )
    snap = None
    for _ in range(400):
        snap = await dl.progress(tid)
        if snap["status"] in ("done", "failed"):
            break
        await asyncio.sleep(0.01)
    assert snap is not None and snap["status"] == "done"
    assert seen["auth"] == "Bearer hf_test"
    # Token must never leak into progress snapshots.
    assert "hf_test" not in json.dumps(snap)
    await dl.aclose()


@pytest.mark.asyncio
async def test_downloader_no_headers_by_default(tmp_path):
    seen = {}

    def handler(request):
        seen["auth"] = request.headers.get("authorization")
        return httpx.Response(200, content=b"x")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    dl = Downloader(client=client)
    tid = await dl.start("https://example.com/a.bin", tmp_path / "a.bin")
    for _ in range(400):
        snap = await dl.progress(tid)
        if snap["status"] in ("done", "failed"):
            break
        await asyncio.sleep(0.01)
    assert snap["status"] == "done"
    assert seen["auth"] is None
    await dl.aclose()


# ---------------------------------------------------------------------------
# Engine update detection
# ---------------------------------------------------------------------------

CAT = {"llama.cpp": {"id": "llama.cpp", "name": "llama.cpp",
                     "category": "llm", "github": "ggerganov/llama.cpp"}}


class _FakeResp:
    def __init__(self, status=200, payload=None):
        self.status_code = status
        self._p = payload or {}
        self.headers = {"content-type": "application/json"}

    def json(self):
        return self._p


class _FakeClient:
    def __init__(self, tag="b5000"):
        self.tag = tag
        self.calls = 0

    async def get(self, url):
        self.calls += 1
        return _FakeResp(200, {"tag_name": self.tag, "assets": [
            {"name": "llama-bin-win-cuda-x64.zip",
             "browser_download_url": "https://example.invalid/win.zip"},
            {"name": "llama-bin-linux-x64.zip",
             "browser_download_url": "https://example.invalid/linux.zip"},
        ]})

    async def aclose(self):
        pass


def _install_record(root, eid, version=""):
    rec = EngineRecord(id=eid, version=version, state=EngineState.INSTALLED,
                       install_path=str(Path(root) / "engines" / eid))
    _write_manifest(Path(root), [rec])


@pytest.mark.asyncio
async def test_check_updates_flags_newer_tag(tmp_path):
    _install_record(tmp_path, "llama.cpp", version="b1")
    res = await check_engine_updates(tmp_path, CAT, client=_FakeClient("b5000"))
    assert len(res) == 1
    assert res[0]["latest_tag"] == "b5000"
    assert res[0]["update_available"] is True


@pytest.mark.asyncio
async def test_check_updates_stamps_baseline_for_fresh_install(tmp_path):
    _install_record(tmp_path, "llama.cpp", version="")
    res = await check_engine_updates(tmp_path, CAT, client=_FakeClient("b5000"))
    assert res[0]["update_available"] is False
    assert EngineManager(tmp_path).get("llama.cpp").version == "b5000"


@pytest.mark.asyncio
async def test_check_updates_cache_hit(tmp_path):
    _install_record(tmp_path, "llama.cpp", version="b1")
    fc = _FakeClient("b5000")
    await check_engine_updates(tmp_path, CAT, client=fc)
    res = await check_engine_updates(tmp_path, CAT, client=fc)
    assert res[0]["from_cache"] is True
    assert fc.calls == 1
    assert res[0]["update_available"] is True


@pytest.mark.asyncio
async def test_check_updates_network_error_is_per_engine(tmp_path):
    _install_record(tmp_path, "llama.cpp", version="b1")

    class _Boom(_FakeClient):
        async def get(self, url):
            raise httpx.ConnectError("boom")

    res = await check_engine_updates(tmp_path, CAT, client=_Boom())
    assert len(res) == 1
    assert "error" in res[0]


def test_apply_update_unknown_engine(tmp_path):
    res = apply_engine_update("ghost", {}, tmp_path)
    assert res.ok is False


def test_apply_update_no_url(tmp_path):
    res = apply_engine_update("llama.cpp", {"llama.cpp": {"platforms": {}}}, tmp_path)
    assert res.ok is False
    assert "no download url" in res.message


def test_list_engines_status_exposes_version(tmp_path):
    _install_record(tmp_path, "llama.cpp", version="b123")
    rows = list_engines_status(CAT, tmp_path)
    row = [e for e in rows if e["id"] == "llama.cpp"][0]
    assert row["version"] == "b123"
    assert row["installed"] is True


# ---------------------------------------------------------------------------
# LTX presets: official 16GB minimum must be visible
# ---------------------------------------------------------------------------

def test_ltx_presets_note_sub_minimums():
    from app.ltx_runtime import PRESETS
    for pid, p in PRESETS.items():
        assert "note" in p, f"preset {pid} missing note"
        if p["vram_gb"] < 16:
            assert "实验" in p["label"]
            assert "16GB" in p["note"]
        if pid == "quality":
            assert "官方最低" in p["note"]
