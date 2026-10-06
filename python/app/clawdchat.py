"""ClawdChat (虾聊) sidecar heartbeat backend.

The front-end unified scheduler (``renderer/modules/scheduler.js``) calls the
sidecar once every ~2 h. This module performs the *mechanical* heartbeat:

  * loads the agent's API credential from a local file (the renderer never
    touches it);
  * GETs ``/agents/status`` (required) and, best-effort, ``/home``;
  * returns a *minimal* dict that contains **no** API key.

Security invariants
-------------------
* The key is sent ONLY to ``https://clawdchat.cn`` over TLS.
* The key never appears in return values, log records, or anywhere on disk
  beyond the user's own credential file.
* Upstream/network failures degrade to ``reachable=False`` (HTTP 200 to the
  caller) — they are never raised as a 500.
"""
from __future__ import annotations

import json
import logging
import os
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

log = logging.getLogger("kevrai.clawdchat")

#: Upstream API root. The bearer key is attached ONLY to requests built from
#: this constant (i.e. only ever sent to clawdchat.cn over https).
API_BASE = "https://clawdchat.cn/api/v1"
DEFAULT_TIMEOUT = 15.0
DEFAULT_AGENT_NAME = "kevrai-kcortex"


# ---------------------------------------------------------------------------
# Credential loading
# ---------------------------------------------------------------------------

def _credential_paths() -> list[Path]:
    """Where to look for ``credentials.json``, in priority order."""
    out: list[Path] = []
    home_env = (os.environ.get("CLAWDCHAT_HOME") or "").strip()
    if home_env:
        out.append(Path(home_env) / "credentials.json")
    out.append(Path.home() / ".clawdchat" / "credentials.json")
    return out


def _valid_account(entry: Any) -> dict[str, str] | None:
    """Normalize one raw credential entry; ``None`` when unusable."""
    if not isinstance(entry, dict):
        return None
    key = str(entry.get("api_key") or "").strip()
    if not key:
        return None
    return {
        "api_key": key,
        "agent_name": str(entry.get("agent_name") or "").strip(),
    }


def load_credentials(preferred_name: str = DEFAULT_AGENT_NAME) -> dict[str, str] | None:
    """Load the ClawdChat API credential for ``preferred_name``.

    Search order: ``$CLAWDCHAT_HOME/credentials.json`` then
    ``~/.clawdchat/credentials.json``. The file is normally a JSON array of
    accounts ``[{"api_key": ..., "agent_name": ...}]``; a legacy single-object
    file is also accepted. The account whose ``agent_name`` matches
    ``preferred_name`` wins; otherwise the first valid account is used.

    Returns an internal ``{"api_key", "agent_name"}`` dict (holding the key in
    memory only) or ``None`` when nothing valid can be found/parsed. Never
    raises, and never logs the key.
    """
    for path in _credential_paths():
        try:
            if not path.is_file():
                continue
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue  # unreadable / malformed file: try the next location
        if isinstance(raw, list):
            entries: list[Any] = raw
        elif isinstance(raw, dict):
            entries = [raw]  # legacy single-account file
        else:
            continue
        accounts = [a for a in (_valid_account(e) for e in entries) if a]
        if not accounts:
            continue
        preferred = (preferred_name or "").strip()
        if preferred:
            for acc in accounts:
                if acc["agent_name"] == preferred:
                    return acc
        return accounts[0]
    return None


# ---------------------------------------------------------------------------
# Heartbeat
# ---------------------------------------------------------------------------

def _base_result(now: str, *, configured: bool, reachable: bool) -> dict[str, Any]:
    """All fields the front-end scheduler understands; no key, ever."""
    return {
        "checked_at": now,
        "configured": configured,
        "reachable": reachable,
        "status": None,
        "claimed": False,
        "claim_url": None,
        "name": None,
        "display_name": None,
        "active": False,
        "post_count": 0,
        "comment_count": 0,
        "karma": 0,
        "last_active_at": None,
        "unread_messages": 0,
        "unread_notifications": 0,
    }


def _default_client() -> Any:
    """Lazily build the real synchronous httpx client (stdlib-free at import)."""
    import httpx

    return httpx.Client(timeout=DEFAULT_TIMEOUT, follow_redirects=True)


def heartbeat(
    preferred_name: str = DEFAULT_AGENT_NAME,
    include_home: bool = True,
    transport: Callable[[], Any] | None = None,
) -> dict[str, Any]:
    """Perform one mechanical heartbeat against ClawdChat.

    Parameters
    ----------
    preferred_name:
        Which account in the credential file to use (``agent_name`` match).
    include_home:
        Also GET ``/home`` for unread counters; failure there is non-fatal.
    transport:
        Test hook — a zero-arg factory returning a context-manager client with
        a ``.get(url, headers=...)`` method. ``None`` uses the real httpx
        client.

    Never raises: upstream/network problems are reported as ``reachable=False``.
    The returned dict never contains the API key.
    """
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    creds = load_credentials(preferred_name)
    if not creds:
        # No credential configured — the scheduler should just skip silently.
        return _base_result(now, configured=False, reachable=False)

    # The bearer header is built here and only handed to requests whose URL is
    # derived from the hardcoded https://clawdchat.cn API_BASE.
    headers = {
        "Authorization": f"Bearer {creds['api_key']}",
        "Accept": "application/json",
    }
    result = _base_result(now, configured=True, reachable=False)

    client_cm = transport() if transport is not None else _default_client()
    try:
        with client_cm as client:
            # ---- required: /agents/status (any failure -> reachable=False) --
            resp = client.get(f"{API_BASE}/agents/status", headers=headers)
            resp.raise_for_status()
            data = resp.json()
            if not isinstance(data, dict):
                raise ValueError("status payload is not a JSON object")
            agent = data.get("agent")
            agent = agent if isinstance(agent, dict) else {}
            result.update({
                "reachable": True,
                "status": data.get("status"),
                "claimed": str(data.get("status") or "") == "claimed",
                "claim_url": data.get("claim_url"),
                "name": agent.get("name"),
                "display_name": agent.get("display_name") or agent.get("name"),
                "active": bool(agent.get("is_active")),
                "post_count": int(agent.get("post_count") or 0),
                "comment_count": int(agent.get("comment_count") or 0),
                "karma": int(agent.get("karma") or 0),
                "last_active_at": agent.get("last_active_at"),
            })

            # ---- optional: /home (non-fatal; counters default to 0) --------
            if include_home:
                try:
                    home_resp = client.get(f"{API_BASE}/home", headers=headers)
                    home_resp.raise_for_status()
                    home = home_resp.json()
                    if isinstance(home, dict):
                        unread = home.get("unread_messages")
                        if isinstance(unread, dict):
                            result["unread_messages"] = int(unread.get("count") or 0)
                        notifications = home.get("notifications")
                        if isinstance(notifications, dict):
                            result["unread_notifications"] = int(
                                notifications.get("unread_total") or 0
                            )
                except Exception:  # noqa: BLE001 - home is best-effort
                    # Deliberately does NOT log the URL/auth details.
                    log.info("clawdchat /home heartbeat failed (non-fatal)")
    except Exception:  # noqa: BLE001 - network errors must not become a 500
        # Keep configured=True: the credential file exists, the scheduler
        # should simply retry next cycle.
        log.info("clawdchat heartbeat unreachable")
    return result
