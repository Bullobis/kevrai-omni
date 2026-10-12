"""Unified full backup — on-disk state aggregation / restore.

Bundles the sidecar's on-disk state into one portable object:

* knowledge bases (RAG),
* agent sessions / ordered messages / preferences (incl. tool-approval policy),
* the selected reasoning brain.

Frontend-only state (settings, favorites, recent) is bundled by the renderer
around this disk bundle. Knowledge restore always merges (duplicate names are
suffixed) to avoid deleting the user's existing libraries; ``replace`` applies
to agent conversation history.
"""
from __future__ import annotations

import logging
import time
from pathlib import Path  # noqa: F401  (kept for type clarity)
from typing import Any

from .agent.memory import AgentMemory
from .agent.model_router import ModelRouter

log = logging.getLogger("kevrai.backup")

BACKUP_DISK_VERSION = 1


class BackupError(Exception):
    """Raised when a backup bundle is malformed."""


class BackupManager:
    def __init__(self, app_root: Path, knowledge: Any) -> None:
        self.app_root = Path(app_root)
        self.knowledge = knowledge
        # Second connection/reader to the same files as the live agent; SQLite
        # serialises access and brain.json is a tiny state file.
        self.memory = AgentMemory(self.app_root / "agent" / "memory.sqlite3")
        self.router = ModelRouter(self.app_root / "agent" / "brain.json")

    # ── export ────────────────────────────────────────────────────────────────
    def export_disk(self) -> dict[str, Any]:
        knowledge: list[dict[str, Any]] = []
        for meta in self.knowledge.list_kbs():
            kid = meta.get("id")
            try:
                knowledge.append(self.knowledge.export_kb(kid))
            except Exception as e:  # noqa: BLE001 - never let one KB abort backup
                log.warning("skip knowledge base %s during backup: %s", kid, e)

        kind, repo = self.router.backend
        brain: dict[str, Any] = {"type": kind or "auto"}
        if kind == "transformers":
            brain["repo"] = repo

        return {
            "version": BACKUP_DISK_VERSION,
            "exported_at": time.time(),
            "knowledge": knowledge,
            "agent_memory": self.memory.export_all(),
            "brain": brain,
        }

    # ── import ────────────────────────────────────────────────────────────────
    def import_disk(
        self, data: dict[str, Any], *, replace: bool = False
    ) -> dict[str, int]:
        if not isinstance(data, dict):
            raise BackupError("磁盘备份数据格式错误")

        n_kb = 0
        for bundle in data.get("knowledge") or []:
            try:
                self.knowledge.import_kb(bundle)
                n_kb += 1
            except Exception as e:  # noqa: BLE001 - skip a bad bundle, keep going
                log.warning("skip knowledge bundle during restore: %s", e)

        mem_counts = self.memory.import_all(
            data.get("agent_memory") or {}, replace=replace
        )

        brain = data.get("brain") or {}
        btype = brain.get("type")
        if btype == "transformers" and brain.get("repo"):
            self.router.configure(f"transformers:{brain['repo']}")
        elif btype == "mnn":
            self.router.configure("mnn")
        else:  # auto / unknown -> safe default
            self.router.configure("auto")

        return {"knowledge_bases": n_kb, **mem_counts}
