"""Full backup: AgentMemory dump/restore, BackupManager aggregation, routes."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.agent.memory import AgentMemory  # noqa: E402
from app.backup import BackupManager  # noqa: E402

# ── AgentMemory export / import ──────────────────────────────────────────────


def test_memory_export_roundtrip(tmp_path: Path) -> None:
    m = AgentMemory(tmp_path / "a.sqlite3")
    m.add_message("s1", "user", "hello")
    m.add_message("s1", "assistant", "hi")
    m.set_preference("tool_approval_policy::s1", "confirm")

    dump = m.export_all()
    assert dump["version"] == 1
    assert len(dump["sessions"]) == 1
    sess = dump["sessions"][0]
    assert sess["id"] == "s1"
    assert [x["role"] for x in sess["messages"]] == ["user", "assistant"]
    assert dump["preferences"]["tool_approval_policy::s1"] == "confirm"

    m2 = AgentMemory(tmp_path / "b.sqlite3")
    counts = m2.import_all(dump, replace=True)
    assert counts == {"sessions": 1, "messages": 2}
    assert len(m2.get_messages("s1")) == 2
    assert m2.get_preference("tool_approval_policy::s1") == "confirm"


def test_memory_merge_skips_existing_session(tmp_path: Path) -> None:
    m = AgentMemory(tmp_path / "a.sqlite3")
    m.add_message("s1", "user", "hello")
    dump = m.export_all()

    m2 = AgentMemory(tmp_path / "b.sqlite3")
    m2.import_all(dump)
    # Re-importing in merge mode must not duplicate the existing session.
    again = m2.import_all(dump)
    assert again == {"sessions": 0, "messages": 0}
    assert len(m2.get_messages("s1")) == 1


def test_memory_import_missing_sessions_raises(tmp_path: Path) -> None:
    m = AgentMemory(tmp_path / "a.sqlite3")
    with pytest.raises(ValueError):
        m.import_all({"version": 1})


def test_memory_import_new_session_in_merge(tmp_path: Path) -> None:
    m = AgentMemory(tmp_path / "a.sqlite3")
    m.add_message("old", "user", "x")
    dump = {
        "version": 1,
        "sessions": [
            {"id": "new", "messages": [
                {"role": "user", "content": "q"},
                {"role": "assistant", "content": "a"},
            ]},
        ],
        "preferences": {},
    }
    counts = m.import_all(dump)
    assert counts == {"sessions": 1, "messages": 2}
    assert len(m.get_messages("new")) == 2


# ── BackupManager aggregation with a fake knowledge layer ────────────────────


class _FakeKnowledge:
    def __init__(self) -> None:
        self.imported: list[dict] = []

    def list_kbs(self) -> list[dict]:
        return [{"id": "k1"}]

    def export_kb(self, kb_id: str) -> dict:
        return {"format": "kevrai-kb", "version": 1,
                "meta": {"name": "Lib"}, "documents": []}

    def import_kb(self, bundle: dict) -> dict:
        self.imported.append(bundle)
        return {"kb_id": "k2", "name": "Lib", "doc_count": 0}


def test_backup_manager_export_import(tmp_path: Path) -> None:
    bm = BackupManager(tmp_path, _FakeKnowledge())
    disk = bm.export_disk()
    assert disk["version"] == 1
    assert len(disk["knowledge"]) == 1
    assert disk["brain"] == {"type": "auto"}
    assert "agent_memory" in disk

    result = bm.import_disk(disk)
    assert result["knowledge_bases"] == 1
    assert result["sessions"] == 0


def test_backup_manager_preserves_brain(tmp_path: Path) -> None:
    # Configure a brain, then confirm export captures it.
    bm = BackupManager(tmp_path, _FakeKnowledge())
    bm.router.configure("transformers:Qwen/Qwen3-0.6B")
    disk = bm.export_disk()
    assert disk["brain"] == {"type": "transformers", "repo": "Qwen/Qwen3-0.6B"}


# ── Routes ───────────────────────────────────────────────────────────────────


def test_route_backup_disk_get(hub_client) -> None:
    r = hub_client.get("/api/backup/disk")
    assert r.status_code == 200
    body = r.json()
    for key in ("version", "knowledge", "agent_memory", "brain"):
        assert key in body


def test_route_backup_disk_roundtrip(hub_client) -> None:
    # APP_ROOT's memory is shared across the whole test session (it is a fixed
    # module constant, not tmp-isolated), so use a unique session id to make the
    # merge insert deterministic regardless of earlier runs.
    import uuid

    sid = "restored-" + uuid.uuid4().hex[:8]
    disk = {
        "version": 1,
        "knowledge": [],
        "brain": {"type": "auto"},
        "agent_memory": {
            "version": 1,
            "sessions": [
                {"id": sid, "messages": [
                    {"role": "user", "content": "question"},
                    {"role": "assistant", "content": "answer"},
                ]},
            ],
            "preferences": {},
        },
    }
    r = hub_client.post("/api/backup/disk", json={"disk": disk, "mode": "merge"})
    assert r.status_code == 200
    out = r.json()
    assert out["sessions"] == 1
    assert out["messages"] == 2

    again = hub_client.get("/api/backup/disk").json()
    ids = [s["id"] for s in again["agent_memory"]["sessions"]]
    assert sid in ids


def test_route_backup_disk_bad_mode(hub_client) -> None:
    r = hub_client.post(
        "/api/backup/disk",
        json={"disk": {"version": 1}, "mode": "sideways"},
    )
    assert r.status_code == 422
