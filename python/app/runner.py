"""Inference orchestrator — thin proxy to installed engines."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any


def find_engine_binary(engine_id: str, engines_status: dict[str, Any]) -> str | None:
    """Return absolute path to engine binary if installed, else None."""
    st = engines_status.get(engine_id) or {}
    p = st.get("path")
    if not p:
        return None
    base = Path(p)
    if engine_id == "llama.cpp":
        exe = "llama-server.exe" if sys.platform.startswith("win") else "llama-server"
        cand = base / exe
        if cand.exists():
            return str(cand)
        # zip layout: llama.cpp-<ver>-bin-.../llama-server
        for f in base.rglob(exe):
            return str(f)
    return str(base) if base.exists() else None


def spawn_llama_server(model_path: Path, port: int = 8080) -> subprocess.Popen:
    """Spawn llama-server for a given GGUF model. Caller manages the Popen handle."""
    raise NotImplementedError(
        "llama.cpp binary is launched by the Electron main process; the Python sidecar "
        "only serves an HTTP control plane. See electron/main.js → startLLMServer()."
    )
