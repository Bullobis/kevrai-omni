"""vLLM / OpenAI-compatible inference connector.

vLLM itself needs a CUDA GPU and a multi-GB wheel; this app cannot bundle it.
Instead this module connects to any OpenAI-compatible server (a local or remote
vLLM, or another compatible runtime) and exposes a small, testable surface:

    GET  /api/vllm/status
    POST /api/vllm/config
    GET  /api/vllm/models
    POST /api/vllm/chat

The default base URL is http://127.0.0.1:8000 (vLLM's default).  Config is
persisted in <data_root>/config/vllm.json so the desktop app remembers it.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

import httpx

_DEFAULT_BASE = "http://127.0.0.1:8000"


def _data_root() -> Path:
    root = os.environ.get("KEVRAI_DATA_ROOT")
    if root:
        return Path(root)
    return Path.home() / ".kevrai"


def _config_path() -> Path:
    p = _data_root() / "config" / "vllm.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def load_config() -> dict[str, Any]:
    cfg: dict[str, Any] = {
        "base_url": os.environ.get("KEVRAI_VLLM_BASE_URL", _DEFAULT_BASE),
        "model": os.environ.get("KEVRAI_VLLM_MODEL", ""),
        "api_key": os.environ.get("KEVRAI_VLLM_API_KEY", ""),
    }
    try:
        saved = json.loads(_config_path().read_text(encoding="utf-8"))
        if isinstance(saved, dict):
            cfg.update({k: v for k, v in saved.items() if v is not None})
    except (OSError, ValueError):
        pass
    cfg["base_url"] = str(cfg.get("base_url") or _DEFAULT_BASE).rstrip("/")
    return cfg


def save_config(base_url: str | None = None, model: str | None = None,
                api_key: str | None = None) -> dict[str, Any]:
    cfg = load_config()
    if base_url is not None:
        cfg["base_url"] = base_url.rstrip("/") or _DEFAULT_BASE
    if model is not None:
        cfg["model"] = model
    if api_key is not None:
        cfg["api_key"] = api_key
    _config_path().write_text(json.dumps(cfg, ensure_ascii=False, indent=2),
                              encoding="utf-8")
    return cfg


def _headers(cfg: dict[str, Any]) -> dict[str, str]:
    h = {"Content-Type": "application/json"}
    if cfg.get("api_key"):
        h["Authorization"] = f"Bearer {cfg['api_key']}"
    return h


def _client(cfg: dict[str, Any], timeout: float = 30.0) -> httpx.Client:
    return httpx.Client(base_url=cfg["base_url"], headers=_headers(cfg),
                        timeout=httpx.Timeout(timeout, connect=5.0, read=timeout,
                                             write=timeout, pool=5.0))


def list_models(base_url: str | None = None, api_key: str | None = None,
                timeout: float = 10.0) -> dict[str, Any]:
    cfg = load_config()
    if base_url:
        cfg["base_url"] = base_url.rstrip("/")
    if api_key is not None:
        cfg["api_key"] = api_key
    try:
        with _client(cfg, timeout) as c:
            r = c.get("/v1/models")
            r.raise_for_status()
            data = r.json()
        items = data.get("data") if isinstance(data, dict) else data
        models: list[str] = []
        for m in items or []:
            if isinstance(m, dict):
                mid = m.get("id") or m.get("name")
                if mid:
                    models.append(str(mid))
            elif isinstance(m, str):
                models.append(m)
        return {"ok": True, "base_url": cfg["base_url"], "models": models}
    except Exception as e:  # noqa: BLE001 - a probe failure means "not reachable"
        return {"ok": False, "base_url": cfg.get("base_url", ""),
                "models": [], "error": f"{type(e).__name__}: {e}"}


def status() -> dict[str, Any]:
    cfg = load_config()
    probe = list_models(timeout=8.0)
    installed = False
    version = ""
    try:
        import vllm  # type: ignore
        installed = True
        version = str(getattr(vllm, "__version__", ""))
    except Exception:  # noqa: BLE001, S110 - optional dependency probe
        pass
    return {
        "engine": "vllm",
        "installed": installed,
        "version": version,
        "base_url": cfg["base_url"],
        "model": cfg.get("model", ""),
        "reachable": probe["ok"],
        "models": probe["models"],
        "error": probe.get("error", ""),
        "note": ("vLLM 服务端需要 CUDA GPU；本机未安装 vllm 时可用任意 "
                 "OpenAI 兼容服务（远程 vLLM / llama.cpp server 等）。"),
    }


def chat(messages: list[dict[str, Any]], model: str | None = None,
         temperature: float = 0.7, max_tokens: int = 512,
         base_url: str | None = None, api_key: str | None = None,
         timeout: float = 120.0) -> dict[str, Any]:
    cfg = load_config()
    if base_url:
        cfg["base_url"] = base_url.rstrip("/")
    if api_key is not None:
        cfg["api_key"] = api_key
    chosen = model or cfg.get("model") or ""
    if not chosen:
        probe = list_models(base_url=cfg["base_url"], timeout=8.0)
        if probe["models"]:
            chosen = probe["models"][0]
    if not chosen:
        raise RuntimeError("没有可用模型：请先 POST /api/vllm/config 指定 model")
    payload: dict[str, Any] = {
        "model": chosen,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": False,
    }
    t0 = time.time()
    with _client(cfg, timeout) as c:
        r = c.post("/v1/chat/completions", json=payload)
        r.raise_for_status()
        data = r.json()
    choice = (data.get("choices") or [{}])[0]
    msg = choice.get("message") or {}
    return {
        "text": msg.get("content") or choice.get("text") or "",
        "model": data.get("model") or chosen,
        "usage": data.get("usage") or {},
        "duration_ms": int((time.time() - t0) * 1000),
        "base_url": cfg["base_url"],
    }
