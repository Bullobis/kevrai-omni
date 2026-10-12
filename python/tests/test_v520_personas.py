"""Tests for Agent persona presets (v4.8.x)."""
from __future__ import annotations

import pytest

from app.agent import Agent
from app.agent.memory import AgentMemory
from app.agent.personas import (
    DEFAULT_PERSONA,
    PERSONAS,
    PersonaStore,
    build_persona_block,
    list_personas,
)
from app.agent.tool_registry import ToolContext, ToolRegistry


# ---------------------------------------------------------------------------
# PersonaStore / presets
# ---------------------------------------------------------------------------
def test_persona_defaults(tmp_path):
    s = PersonaStore(tmp_path / "persona.json")
    assert s.persona == DEFAULT_PERSONA == "general"


def test_persona_set_and_persist(tmp_path):
    path = tmp_path / "persona.json"
    s = PersonaStore(path)
    assert s.set("coder") == "coder"
    assert PersonaStore(path).persona == "coder"


def test_persona_unknown_rejected(tmp_path):
    s = PersonaStore(tmp_path / "persona.json")
    with pytest.raises(ValueError):
        s.set("does-not-exist")


def test_persona_corrupt_file_falls_back(tmp_path):
    path = tmp_path / "persona.json"
    path.write_text("not json", encoding="utf-8")
    assert PersonaStore(path).persona == "general"


def test_list_personas_shape():
    items = list_personas()
    ids = [p["id"] for p in items]
    assert len(items) >= 6
    assert set(ids) == set(PERSONAS.keys())
    for p in items:
        assert p["label"] and p["guidance"]


def test_build_persona_block_contains_label():
    block = build_persona_block("coder")
    assert PERSONAS["coder"]["label"] in block
    assert PERSONAS["coder"]["guidance"] in block


# ---------------------------------------------------------------------------
# Agent integration
# ---------------------------------------------------------------------------
def _agent(tmp_path):
    mem = AgentMemory(tmp_path / "p.sqlite3")
    ctx = ToolContext(memory=mem, models_dir=tmp_path, app_root=tmp_path)
    ctx.hardware_info = {
        "gpu_vendor": "mock", "gpu_best_vram_gb": 0,
        "ram_total_gb": 0, "disk": {"free_gb": 0},
    }
    return Agent(memory=mem, registry=ToolRegistry(), ctx=ctx)


def test_agent_get_persona(tmp_path):
    agent = _agent(tmp_path)
    res = agent.get_persona()
    assert res["persona"] == "general"
    assert len(res["items"]) >= 6


def test_agent_set_persona_persists(tmp_path):
    agent = _agent(tmp_path)
    agent.set_persona("writer")
    # A fresh agent over the same memory dir reloads the persisted persona.
    mem2 = AgentMemory(tmp_path / "p.sqlite3")
    ctx2 = ToolContext(memory=mem2, models_dir=tmp_path, app_root=tmp_path)
    agent2 = Agent(memory=mem2, registry=ToolRegistry(), ctx=ctx2)
    assert agent2.get_persona()["persona"] == "writer"


def test_persona_guidance_in_system_prompt(tmp_path):
    agent = _agent(tmp_path)
    agent.set_persona("coder")
    prompt = agent._build_system_prompt()
    assert build_persona_block("coder") in prompt


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
def test_route_persona_get(hub_client):
    r = hub_client.get("/api/agent/persona")
    assert r.status_code == 200
    body = r.json()
    assert "persona" in body and len(body["items"]) >= 6


def test_route_persona_set(hub_client):
    r = hub_client.post("/api/agent/persona", json={"persona": "creative"})
    assert r.status_code == 200
    assert r.json()["persona"] == "creative"
    again = hub_client.get("/api/agent/persona").json()
    assert again["persona"] == "creative"
    # restore default to keep global state neutral
    hub_client.post("/api/agent/persona", json={"persona": "general"})


def test_route_persona_unknown_400(hub_client):
    r = hub_client.post("/api/agent/persona", json={"persona": "nope"})
    assert r.status_code == 400
