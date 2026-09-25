"""Neuron-Deep (K-Cortex slice 7) evidence-based audit + regression tests.

Scope: backend paths lightly covered by earlier slices. Each test either
locks a behaviour we audited as correct (guard) or reproduces a bug that was
confirmed and minimally fixed.

Covered paths
-------------
* ``GET/WS /ws/download/{task_id}`` — auth rejection, task-not-found close,
  already-terminal task must close promptly (regression), live event stream.
* ``PUT /api/settings`` — multi-field atomicity (one bad field => nothing
  persists).
* ``skill_hub`` / ``SkillManager`` — malformed SKILL.md tolerance, required
  skills cannot be disabled, duplicate ids rejected.
* ``catalog.load_catalog`` — corrupt ``engines.json`` tolerated; corrupt
  ``models.json`` propagates (documented; startup falls back to empty).
* ``Agent.run`` — memory write failure must not discard a computed answer
  (regression); max-iteration exhaustion yields a friendly answer.
"""
from __future__ import annotations

import json

import pytest
from starlette.websockets import WebSocketDisconnect

from app.agent import Agent, AgentMemory, ToolContext
from app.agent.tools import build_default_registry
from app.downloader import DownloadStatus, DownloadTask


# ===========================================================================
# WebSocket /ws/download/{task_id}
# ===========================================================================
def _make_task(tid: str, status: DownloadStatus) -> DownloadTask:
    t = DownloadTask(
        id=tid,
        url="https://example.com/model.gguf",
        dest_path="/tmp/kevrai-n7/" + tid + ".gguf",
        expected_sha256=None,
    )
    t.status = status
    t.total_bytes = 100
    t.downloaded_bytes = 100 if status == DownloadStatus.DONE else 40
    return t


def test_ws_download_rejects_bearerless_upgrade():
    """No/wrong bearer => handshake rejected with policy-violation (1008)."""
    from fastapi.testclient import TestClient

    from app import main as app_main

    # Explicitly override the conftest-injected bearer with a wrong one.
    with TestClient(app_main.app, headers={"authorization": "Bearer wrong-secret"}) as c:
        with pytest.raises(WebSocketDisconnect) as exc, c.websocket_connect("/ws/download/whatever"):
            pass
        assert exc.value.code == 1008


def test_ws_download_unknown_task_sends_error_then_closes(hub_client):
    dl = hub_client.app.state.downloader
    assert dl.get_task("nope-nope") is None
    with hub_client.websocket_connect("/ws/download/nope-nope") as ws:
        msg = ws.receive_json()
        assert msg["error"] == "task not found"
        with pytest.raises(WebSocketDisconnect):
            ws.receive_json()


def test_ws_download_closes_promptly_on_already_terminal_task(hub_client):
    """REGRESSION: a late subscriber to an already-done task used to hang
    forever on an empty queue (heartbeats only). It must close right after the
    terminal snapshot."""
    dl = hub_client.app.state.downloader
    task = _make_task("term-done", DownloadStatus.DONE)
    dl._tasks[task.id] = task
    with hub_client.websocket_connect(f"/ws/download/{task.id}") as ws:
        snap = ws.receive_json()
        assert snap["status"] == "done"
        # The server must close now (not block on queue.get()).
        with pytest.raises(WebSocketDisconnect):
            ws.receive_json()


def test_ws_download_closes_on_already_failed_task(hub_client):
    dl = hub_client.app.state.downloader
    task = _make_task("term-fail", DownloadStatus.FAILED)
    task.error = "boom"
    dl._tasks[task.id] = task
    with hub_client.websocket_connect(f"/ws/download/{task.id}") as ws:
        snap = ws.receive_json()
        assert snap["status"] == "failed"
        with pytest.raises(WebSocketDisconnect):
            ws.receive_json()


def test_ws_download_streams_live_event_then_closes(hub_client):
    """Happy path: pending task streams its snapshot, then a pushed terminal
    event is delivered and the socket closes."""
    dl = hub_client.app.state.downloader
    task = _make_task("live-1", DownloadStatus.PENDING)
    dl._tasks[task.id] = task
    with hub_client.websocket_connect(f"/ws/download/{task.id}") as ws:
        snap = ws.receive_json()
        assert snap["status"] == "pending"
        # Push a terminal event from inside the app loop (thread-safe).
        hub_client.portal.call(
            task.queue.put_nowait,
            {"status": "done", "event": "done", "downloaded_bytes": 100},
        )
        evt = ws.receive_json()
        assert evt["event"] == "done"
        with pytest.raises(WebSocketDisconnect):
            ws.receive_json()


# ===========================================================================
# PUT /api/settings — multi-field atomicity
# ===========================================================================
def test_settings_partial_invalid_is_atomic(hub_client):
    """One invalid field must reject the whole patch (400) and persist NOTHING
    — neither the bad field nor the otherwise-valid sibling."""
    before = hub_client.get("/api/settings").json()
    r = hub_client.put(
        "/api/settings",
        json={"theme": "rainbow", "telemetry_enabled": not before["telemetry_enabled"]},
    )
    assert r.status_code == 400, r.text
    after = hub_client.get("/api/settings").json()
    assert after["theme"] == before["theme"]
    assert after["telemetry_enabled"] == before["telemetry_enabled"]


def test_settings_multi_valid_fields_all_apply(hub_client):
    r = hub_client.put(
        "/api/settings",
        json={"theme": "dark", "telemetry_enabled": True, "max_model_size_gb": 77},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["theme"] == "dark"
    assert body["telemetry_enabled"] is True
    assert body["max_model_size_gb"] == 77


# ===========================================================================
# skill_hub runtime / SkillManager
# ===========================================================================
def test_scan_library_tolerates_malformed_skill(tmp_path):
    from app.agent import skill_hub

    good = tmp_path / "good_one"
    good.mkdir()
    (good / "SKILL.md").write_text(
        "---\nname: Good One\ndescription: fine\n---\nbody\n", encoding="utf-8"
    )
    bad = tmp_path / "bad_one"
    bad.mkdir()
    (bad / "SKILL.md").write_text("no frontmatter, just prose\n", encoding="utf-8")

    items = {i["id"]: i for i in skill_hub.scan_library(tmp_path)}
    assert items["good_one"]["ok"] is True
    assert items["bad_one"]["ok"] is False
    assert items["bad_one"]["error"]


def test_load_imported_skips_malformed_and_keeps_good(tmp_path):
    from app.agent import skill_hub

    good = tmp_path / "nice_skill"
    good.mkdir()
    (good / "SKILL.md").write_text(
        "---\nname: Nice\ndescription: d\n---\nbody guidance\n", encoding="utf-8"
    )
    bad = tmp_path / "broken_skill"
    bad.mkdir()
    (bad / "SKILL.md").write_text("x", encoding="utf-8")

    skills = skill_hub.load_imported(tmp_path)
    ids = {s.id for s in skills}
    assert "nice_skill" in ids
    assert "broken_skill" not in ids


def test_skill_manager_required_skill_cannot_be_disabled():
    from app.agent.skill import Skill, SkillManager

    s = Skill(
        id="core_skill", name="Core", description="d", tools=[],
        guidance="g", required=True,
    )
    mgr = SkillManager([s])
    assert mgr.is_enabled("core_skill") is True
    with pytest.raises(ValueError):
        mgr.disable("core_skill")
    # Still enabled, and persisted state must not contain it.
    assert mgr.is_enabled("core_skill") is True
    assert "core_skill" not in mgr.disabled_ids()


def test_skill_manager_rejects_duplicate_id():
    from app.agent.skill import Skill, SkillManager

    s1 = Skill(id="dup_skill", name="A", description="x", tools=[], guidance="g")
    s2 = Skill(id="dup_skill", name="B", description="y", tools=[], guidance="h")
    with pytest.raises(ValueError):
        SkillManager([s1, s2])


def test_derive_skill_id_normalizes():
    from app.agent.skill_hub import derive_skill_id

    assert derive_skill_id("My Cool Skill!") == "my_cool_skill"
    assert derive_skill_id("123abc").startswith("sk_")
    assert derive_skill_id("") == "imported_skill"


# ===========================================================================
# catalog.load_catalog — malformed-file tolerance
# ===========================================================================
def test_load_catalog_tolerates_corrupt_engines_json(tmp_path):
    from app.catalog import load_catalog

    (tmp_path / "models.json").write_text(
        json.dumps({"version": "9", "models": []}), encoding="utf-8"
    )
    (tmp_path / "engines.json").write_text("{ not valid json", encoding="utf-8")
    catalog, engines = load_catalog(tmp_path, dev_mode=True)
    assert catalog.version == "9"
    assert engines == {}


def test_load_catalog_corrupt_models_json_propagates(tmp_path):
    """models.json is the primary data: corrupt JSON propagates (the app
    startup wraps load_catalog and falls back to an empty catalog)."""
    from app.catalog import load_catalog

    (tmp_path / "models.json").write_text("{ definitely broken", encoding="utf-8")
    with pytest.raises(json.JSONDecodeError):
        load_catalog(tmp_path, dev_mode=True)


# ===========================================================================
# Agent ReAct loop — memory failure isolation + max iterations
# ===========================================================================
class _ExplodingMemory(AgentMemory):
    """A memory layer whose writes always fail (disk full / locked DB)."""

    def add_message(self, *args, **kwargs):  # noqa: ANN002, ANN003
        raise OSError("disk on fire")

    def record_task(self, *args, **kwargs):  # noqa: ANN002, ANN003
        raise OSError("disk on fire")


class _FinalRouter:
    def is_ready(self):
        return True, "mock"

    def chat(self, prompt, system="", max_new_tokens=2048):
        return {"ok": True, "text": "最终答案：这是一个算好的答案", "model_name": "mock"}


class _NotReadyRouter:
    def is_ready(self):
        return False, ""

    def chat(self, prompt, system="", max_new_tokens=2048):  # pragma: no cover
        return {"ok": False, "error": "not ready"}


class _LoopingRouter:
    """Always emits an unknown tool call — forces MAX_ITERATIONS exhaustion."""

    def is_ready(self):
        return True, "mock"

    def chat(self, prompt, system="", max_new_tokens=2048):
        return {"ok": True, "text": 'Action: no_such_tool|{"q": "x"}', "model_name": "mock"}


@pytest.mark.asyncio
async def test_run_survives_memory_write_failure(tmp_path):
    """REGRESSION: a failing memory must not crash run() or discard the answer."""
    mem = _ExplodingMemory(tmp_path / "boom.sqlite3")
    ctx = ToolContext(memory=mem, models_dir=tmp_path, app_root=tmp_path)
    agent = Agent(memory=mem, router=_FinalRouter(),
                  registry=build_default_registry(), ctx=ctx)
    result = await agent.run("你好", session_id="s-mem")
    assert "算好的答案" in result.answer
    assert result.success is True


@pytest.mark.asyncio
async def test_run_rule_based_survives_memory_failure(tmp_path):
    """The rule-based (no-LLM) path must also survive memory write failures."""
    mem = _ExplodingMemory(tmp_path / "boom2.sqlite3")
    ctx = ToolContext(memory=mem, models_dir=tmp_path, app_root=tmp_path)
    agent = Agent(memory=mem, router=_NotReadyRouter(),
                  registry=build_default_registry(), ctx=ctx)
    result = await agent.run("帮我搜索音乐模型", session_id="s-rule")
    assert isinstance(result.answer, str) and result.answer


@pytest.mark.asyncio
async def test_run_max_iterations_yields_friendly_answer(tmp_path):
    from app.agent.agent import MAX_ITERATIONS

    mem = AgentMemory(tmp_path / "loop.sqlite3")
    ctx = ToolContext(memory=mem, models_dir=tmp_path, app_root=tmp_path)
    agent = Agent(memory=mem, router=_LoopingRouter(),
                  registry=build_default_registry(), ctx=ctx)
    result = await agent.run("一直循环", session_id="s-loop")
    assert result.success is False
    assert "最大" in result.answer
    assert len(result.steps) == MAX_ITERATIONS
