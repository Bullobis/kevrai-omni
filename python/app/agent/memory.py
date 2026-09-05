"""Persistent agent memory — SQLite-backed conversation history + preferences.

Inspired by OpenClaw's local markdown/SQLite memory, but tailored for Kevrai
Omni: stores conversation sessions, messages, extracted user preferences,
and task history. All data stays on the local machine (no cloud sync).
"""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any


_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    title TEXT DEFAULT '',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    message_count INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    role TEXT NOT NULL,              -- 'user' | 'assistant' | 'system' | 'tool'
    content TEXT NOT NULL,
    tool_name TEXT DEFAULT '',
    tool_params TEXT DEFAULT '',
    created_at REAL NOT NULL,
    FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS preferences (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS task_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT,
    task_summary TEXT DEFAULT '',
    tools_used TEXT DEFAULT '',
    success INTEGER DEFAULT 1,
    created_at REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id, created_at);
CREATE INDEX IF NOT EXISTS idx_messages_role ON messages(role);
"""


class AgentMemory:
    """SQLite-backed persistent memory for the agent.

    Thread-safe enough for the sidecar's async context (each operation opens
    a short-lived connection; SQLite handles serialisation).
    """

    def __init__(self, db_path: Path | str) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _init_schema(self) -> None:
        with self._conn() as conn:
            conn.executescript(_SCHEMA)
            conn.commit()

    # ------------------------------------------------------------------
    # Sessions
    # ------------------------------------------------------------------
    def create_session(self, session_id: str, title: str = "") -> dict[str, Any]:
        now = time.time()
        with self._conn() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO sessions (id, title, created_at, updated_at) VALUES (?, ?, ?, ?)",
                (session_id, title, now, now),
            )
            conn.commit()
        return self.get_session(session_id)

    def get_session(self, session_id: str) -> dict[str, Any] | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM sessions WHERE id = ?", (session_id,)
            ).fetchone()
        return dict(row) if row else None

    def list_sessions(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM sessions ORDER BY updated_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(r) for r in rows]

    def delete_session(self, session_id: str) -> bool:
        with self._conn() as conn:
            cur = conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
            conn.commit()
        return cur.rowcount > 0

    def touch_session(self, session_id: str) -> None:
        with self._conn() as conn:
            conn.execute(
                "UPDATE sessions SET updated_at = ?, message_count = (SELECT COUNT(*) FROM messages WHERE session_id = ?) WHERE id = ?",
                (time.time(), session_id, session_id),
            )
            conn.commit()

    # ------------------------------------------------------------------
    # Messages
    # ------------------------------------------------------------------
    def add_message(
        self,
        session_id: str,
        role: str,
        content: str,
        tool_name: str = "",
        tool_params: str = "",
    ) -> int:
        self.create_session(session_id)
        now = time.time()
        with self._conn() as conn:
            cur = conn.execute(
                "INSERT INTO messages (session_id, role, content, tool_name, tool_params, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (session_id, role, content, tool_name, tool_params, now),
            )
            conn.commit()
            msg_id = cur.lastrowid
        self.touch_session(session_id)
        return int(msg_id)

    def get_messages(
        self, session_id: str, limit: int = 100, offset: int = 0
    ) -> list[dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM messages WHERE session_id = ? ORDER BY created_at ASC LIMIT ? OFFSET ?",
                (session_id, limit, offset),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_recent_messages(self, session_id: str, n: int = 20) -> list[dict[str, Any]]:
        """Return the last N messages (most recent first in time, but returned
        in chronological order for prompt building)."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM messages WHERE session_id = ? ORDER BY created_at DESC LIMIT ?",
                (session_id, n),
            ).fetchall()
        return [dict(r) for r in reversed(rows)]

    # ------------------------------------------------------------------
    # Preferences
    # ------------------------------------------------------------------
    def set_preference(self, key: str, value: Any) -> None:
        now = time.time()
        val = json.dumps(value, ensure_ascii=False)
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO preferences (key, value, updated_at) VALUES (?, ?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
                (key, val, now),
            )
            conn.commit()

    def get_preference(self, key: str, default: Any = None) -> Any:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT value FROM preferences WHERE key = ?", (key,)
            ).fetchone()
        if row is None:
            return default
        try:
            return json.loads(row["value"])
        except (json.JSONDecodeError, TypeError):
            return default

    def get_all_preferences(self) -> dict[str, Any]:
        with self._conn() as conn:
            rows = conn.execute("SELECT key, value FROM preferences").fetchall()
        out: dict[str, Any] = {}
        for r in rows:
            try:
                out[r["key"]] = json.loads(r["value"])
            except (json.JSONDecodeError, TypeError):
                out[r["key"]] = r["value"]
        return out

    # ------------------------------------------------------------------
    # Task history
    # ------------------------------------------------------------------
    def record_task(
        self,
        session_id: str | None,
        task_summary: str,
        tools_used: list[str],
        success: bool = True,
    ) -> None:
        now = time.time()
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO task_history (session_id, task_summary, tools_used, success, created_at) VALUES (?, ?, ?, ?, ?)",
                (session_id, task_summary, json.dumps(tools_used), 1 if success else 0, now),
            )
            conn.commit()

    def get_task_history(self, limit: int = 20) -> list[dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM task_history ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(r) for r in rows]
