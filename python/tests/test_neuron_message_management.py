"""Message management (delete single / edit-and-resend) backend tests.

Benchmarks the two new memory operations and their HTTP endpoints, mirroring
Cherry Studio's per-message controls:

* ``AgentMemory.delete_message_from`` — suffix-delete a message and everything
  after it (keeps the session on a clean turn boundary).
* ``AgentMemory.edit_user_message`` — edit a user message in place and trim
  everything after it; refuses assistant/system/tool turns.
* ``DELETE /api/agent/sessions/{sid}/messages/{mid}`` and
  ``POST /api/agent/sessions/{sid}/messages/{mid}/edit``.

Fully offline: the agent runs in rule-based mode (no LLM), so no GPU/network.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.agent import AgentMemory  # noqa: E402


# ---------------------------------------------------------------------------
# Memory layer
# ---------------------------------------------------------------------------
@pytest.fixture()
def mem(tmp_path):
    return AgentMemory(tmp_path / "msg-mgmt.sqlite3")


def _seed(mem, sid="s1"):
    """Four messages: user/assistant/user/assistant."""
    mem.add_message(sid, "user", "q1")
    mem.add_message(sid, "assistant", "a1")
    mem.add_message(sid, "user", "q2")
    mem.add_message(sid, "assistant", "a2")
    return mem.get_messages(sid)


class TestDeleteMessageFrom:
    def test_suffix_delete_counts_and_boundary(self, mem):
        msgs = _seed(mem)
        mid = msgs[2]["id"]  # second user (q2)
        deleted = mem.delete_message_from("s1", mid)
        # q2 (id) + its trailing assistant (id+1) are removed.
        assert deleted == 2
        remaining = mem.get_messages("s1")
        assert [m["content"] for m in remaining] == ["q1", "a1"]

    def test_delete_first_message_removes_everything_after(self, mem):
        msgs = _seed(mem)
        deleted = mem.delete_message_from("s1", msgs[0]["id"])
        assert deleted == 4
        assert mem.get_messages("s1") == []

    def test_delete_last_message_removes_only_it(self, mem):
        msgs = _seed(mem)
        deleted = mem.delete_message_from("s1", msgs[-1]["id"])
        assert deleted == 1
        assert [m["content"] for m in mem.get_messages("s1")] == ["q1", "a1", "q2"]

    def test_delete_user_drops_its_trailing_assistant(self, mem):
        msgs = _seed(mem)
        # Deleting the first user (q1) also drops a1 (the assistant that
        # follows it) — no orphaned half-turn survives.
        deleted = mem.delete_message_from("s1", msgs[0]["id"])
        assert deleted == 4
        assert mem.get_messages("s1") == []

    def test_nonexistent_id_returns_zero(self, mem):
        _seed(mem)
        assert mem.delete_message_from("s1", 99999) == 0
        assert len(mem.get_messages("s1")) == 4

    def test_session_isolation(self, mem):
        _seed(mem, sid="s1")
        mem.add_message("s2", "user", "other")
        mem.add_message("s2", "assistant", "other-a")
        # Deleting the last message in s1 must not touch s2.
        s1_msgs = mem.get_messages("s1")
        mem.delete_message_from("s1", s1_msgs[-1]["id"])
        assert [m["content"] for m in mem.get_messages("s2")] == ["other", "other-a"]

    def test_session_count_updated(self, mem):
        _seed(mem)
        before = mem.get_session("s1")["message_count"]
        assert before == 4
        s1_msgs = mem.get_messages("s1")
        mem.delete_message_from("s1", s1_msgs[2]["id"])
        after = mem.get_session("s1")["message_count"]
        assert after == 2


class TestEditUserMessage:
    def test_edits_user_and_trims_after(self, mem):
        msgs = _seed(mem)
        mid = msgs[2]["id"]  # q2 (user)
        assert mem.edit_user_message("s1", mid, "edited q2") is True
        remaining = mem.get_messages("s1")
        # q2's content is replaced; a2 (after it) is removed.
        assert [m["content"] for m in remaining] == ["q1", "a1", "edited q2"]
        assert remaining[-1]["role"] == "user"

    def test_nonexistent_message_returns_false(self, mem):
        _seed(mem)
        assert mem.edit_user_message("s1", 99999, "x") is False
        assert len(mem.get_messages("s1")) == 4

    def test_assistant_message_returns_none(self, mem):
        msgs = _seed(mem)
        aid = msgs[1]["id"]  # a1 (assistant)
        assert mem.edit_user_message("s1", aid, "x") is None
        # Nothing changed.
        assert [m["content"] for m in mem.get_messages("s1")] == ["q1", "a1", "q2", "a2"]

    def test_editing_first_user_keeps_only_that_user(self, mem):
        msgs = _seed(mem)
        mid = msgs[0]["id"]
        assert mem.edit_user_message("s1", mid, "brand new") is True
        remaining = mem.get_messages("s1")
        assert [m["content"] for m in remaining] == ["brand new"]

    def test_session_isolation(self, mem):
        _seed(mem, sid="s1")
        mem.add_message("s2", "user", "orig")
        s1 = mem.get_messages("s1")
        mem.edit_user_message("s1", s1[0]["id"], "s1 edited")
        assert mem.get_messages("s2")[0]["content"] == "orig"


# ---------------------------------------------------------------------------
# HTTP endpoints
# ---------------------------------------------------------------------------
def _seed_via_memory(agent, sid):
    # The agent singleton is process-global and its SQLite DB persists across
    # tests in the same process, so clear any prior rows for this session id
    # before seeding a known, deterministic 4-message layout.
    mem = agent.memory
    mem.delete_session(sid)
    mem.add_message(sid, "user", "q1")
    mem.add_message(sid, "assistant", "a1")
    mem.add_message(sid, "user", "q2")
    mem.add_message(sid, "assistant", "a2")
    return mem.get_messages(sid)


class TestEndpoints:
    def test_delete_message_suffix(self, hub_client):
        from app import main as app_main
        hub_client.get("/api/agent/tools")
        agent = app_main._AGENT_SINGLETON["agent"]
        msgs = _seed_via_memory(agent, "del-sid")
        mid = msgs[2]["id"]

        resp = hub_client.delete(f"/api/agent/sessions/del-sid/messages/{mid}")
        assert resp.status_code == 200
        body = resp.json()
        assert body["deleted"] == 2
        left = agent.memory.get_messages("del-sid")
        assert [m["content"] for m in left] == ["q1", "a1"]

    def test_delete_message_404_when_missing(self, hub_client):
        resp = hub_client.delete("/api/agent/sessions/del-sid/messages/999999")
        assert resp.status_code == 404

    def test_edit_message_ok(self, hub_client):
        from app import main as app_main
        hub_client.get("/api/agent/tools")
        agent = app_main._AGENT_SINGLETON["agent"]
        msgs = _seed_via_memory(agent, "edit-sid")
        mid = msgs[2]["id"]

        resp = hub_client.post(
            f"/api/agent/sessions/edit-sid/messages/{mid}/edit",
            json={"content": "  edited q2  "},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["edited"] is True
        assert body["content"] == "edited q2"  # whitespace trimmed
        left = agent.memory.get_messages("edit-sid")
        assert [m["content"] for m in left] == ["q1", "a1", "edited q2"]

    def test_edit_message_404_when_missing(self, hub_client):
        resp = hub_client.post(
            "/api/agent/sessions/edit-sid/messages/999999/edit",
            json={"content": "x"},
        )
        assert resp.status_code == 404

    def test_edit_message_rejects_assistant(self, hub_client):
        from app import main as app_main
        hub_client.get("/api/agent/tools")
        agent = app_main._AGENT_SINGLETON["agent"]
        msgs = _seed_via_memory(agent, "edit-asst")
        aid = msgs[1]["id"]  # assistant
        resp = hub_client.post(
            f"/api/agent/sessions/edit-asst/messages/{aid}/edit",
            json={"content": "nope"},
        )
        assert resp.status_code == 400

    def test_edit_message_rejects_empty_content(self, hub_client):
        from app import main as app_main
        hub_client.get("/api/agent/tools")
        agent = app_main._AGENT_SINGLETON["agent"]
        msgs = _seed_via_memory(agent, "edit-empty")
        mid = msgs[2]["id"]
        # whitespace-only body is rejected after strip
        resp = hub_client.post(
            f"/api/agent/sessions/edit-empty/messages/{mid}/edit",
            json={"content": "   "},
        )
        assert resp.status_code == 400
        # nothing changed
        assert len(agent.memory.get_messages("edit-empty")) == 4

    def test_delete_and_edit_coexist_with_regenerate(self, hub_client):
        """After edit+resend trims, regenerate still works on the fresh pair."""
        from app import main as app_main
        hub_client.get("/api/agent/tools")
        agent = app_main._AGENT_SINGLETON["agent"]
        agent.ctx.hardware_info = {
            "gpu_vendor": "mock", "gpu_best_vram_gb": 0,
            "ram_total_gb": 0, "disk": {"free_gb": 0},
        }
        sid = "regen-after-edit"
        msgs = _seed_via_memory(agent, sid)
        mid = msgs[2]["id"]

        # Edit the second user turn (trims its trailing assistant).
        resp = hub_client.post(
            f"/api/agent/sessions/{sid}/messages/{mid}/edit",
            json={"content": "edited q2"},
        )
        assert resp.status_code == 200
        # Simulate the frontend resend: drop the pending turn, then chat adds a
        # fresh user+assistant pair (rule-based, no LLM).
        hub_client.delete(f"/api/agent/sessions/{sid}/messages/{mid}")
        chat = hub_client.post(
            "/api/agent/chat", json={"message": "edited q2", "session_id": sid}
        )
        assert chat.status_code == 200

        # Now the session ends with a user+assistant pair — regenerate works.
        regen = hub_client.post(f"/api/agent/sessions/{sid}/regenerate")
        assert regen.status_code == 200
        body = regen.json()
        assert body["regenerated"] is True
        # The fresh pair was trimmed and re-added: still ends with assistant.
        msgs_after = agent.memory.get_messages(sid)
        assert msgs_after[-1]["role"] == "assistant"
