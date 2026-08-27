"""MNN LLM runtime — real inference via the `MNN` PyPI package.

The MNN wheel ships a full C++ LLM engine behind `MNN.llm`:
    from MNN.llm import create
    llm = create("<model-dir>/config.json")
    llm.load()
    out = llm.response(prompt, stream=False)

This module wraps that in a singleton with:
    * lazy import (engine missing → clear error, never a crash)
    * background load / unload
    * chat with history (apply_chat_template + response)
    * status introspection for the UI

Thread-safety: one Llm instance at a time; all calls serialize on a lock
(MNN's runtime is not reentrant per-instance).
"""
from __future__ import annotations

import asyncio
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any, Iterator

log = logging.getLogger("kevrai.mnn")

_LOCK = threading.Lock()
_STATE: dict[str, Any] = {
    "loaded": False,
    "model_dir": "",
    "model_name": "",
    "loading": False,
    "error": "",
    "loaded_at": 0.0,
    "chat_count": 0,
}
_LLM: Any = None  # MNN.llm.Llm instance (opaque)


class MnnEngineMissing(RuntimeError):
    """Raised when the MNN pip package is not installed."""


def _import_llm():
    try:
        from MNN.llm import create as _create
        return _create
    except Exception as e:  # noqa: BLE001
        raise MnnEngineMissing(
            f"MNN 引擎未安装或不可用（pip install MNN）：{e}"
        ) from e


def is_engine_available() -> bool:
    try:
        import MNN  # noqa: F401
        import MNN.llm  # noqa: F401
        return True
    except Exception:  # noqa: BLE001
        return False


def engine_version() -> str:
    try:
        import MNN
        return str(getattr(MNN, "__version__", "") or "unknown")
    except Exception:  # noqa: BLE001
        return ""


def load_model(model_dir: str | Path, model_name: str = "") -> dict[str, Any]:
    """Load an MNN model directory (contains config.json). Blocking."""
    global _LLM
    d = Path(model_dir)
    cfg = d / "config.json"
    if not cfg.is_file():
        raise FileNotFoundError(f"MNN 模型目录缺少 config.json：{d}")

    with _LOCK:
        unload_model_locked()
        _STATE["loading"] = True
        _STATE["error"] = ""
        t0 = time.monotonic()
        try:
            create = _import_llm()
            _LLM = create(str(cfg))
            _LLM.load()
            _STATE.update({
                "loaded": True,
                "model_dir": str(d),
                "model_name": model_name or d.name,
                "loaded_at": time.time(),
                "chat_count": 0,
            })
            log.info("mnn model loaded", extra={"dir": str(d), "sec": round(time.monotonic() - t0, 1)})
            return status_locked()
        except Exception as e:  # noqa: BLE001
            _LLM = None
            _STATE["loaded"] = False
            _STATE["error"] = str(e)
            log.exception("mnn load failed")
            raise
        finally:
            _STATE["loading"] = False


def unload_model_locked() -> None:
    global _LLM
    if _LLM is not None:
        try:
            _LLM.reset()
        except Exception:  # noqa: BLE001
            pass
    _LLM = None
    _STATE["loaded"] = False
    _STATE["model_dir"] = ""
    _STATE["model_name"] = ""


def unload_model() -> dict[str, Any]:
    with _LOCK:
        unload_model_locked()
    return {"ok": True}


def status_locked() -> dict[str, Any]:
    return {
        "engine_available": is_engine_available(),
        "engine_version": engine_version(),
        "loaded": _STATE["loaded"],
        "loading": _STATE["loading"],
        "model_dir": _STATE["model_dir"],
        "model_name": _STATE["model_name"],
        "error": _STATE["error"],
        "loaded_at": _STATE["loaded_at"],
        "chat_count": _STATE["chat_count"],
    }


def status() -> dict[str, Any]:
    with _LOCK:
        return status_locked()


def chat(prompt: str, history: list[dict[str, str]] | None = None,
         max_new_tokens: int = 512) -> dict[str, Any]:
    """One-shot chat. `history` is [{role, content}] from the UI.

    MNN keeps its own context between response() calls, so we simply feed
    the latest user turn through the chat template and generate.
    """
    global _LLM
    if _LLM is None:
        raise RuntimeError("MNN 模型尚未加载（先调用 load）")
    prompt = str(prompt or "").strip()
    if not prompt:
        raise ValueError("prompt 不能为空")
    if len(prompt) > 32_000:
        raise ValueError("prompt 过长（>32000 字符）")
    history = history or []
    if len(history) > 40:
        history = history[-40:]

    with _LOCK:
        if _LLM is None:
            raise RuntimeError("MNN 模型已卸载")
        t0 = time.monotonic()
        try:
            try:
                templated = _LLM.apply_chat_template({"role": "user", "content": prompt})
            except Exception:  # noqa: BLE001 — some models lack template support
                templated = prompt
            try:
                _LLM.set_config({"max_new_tokens": int(max(16, min(int(max_new_tokens), 4096)))})
            except Exception:  # noqa: BLE001
                pass
            out = _LLM.response(templated, False)
        except Exception as e:  # noqa: BLE001
            _STATE["error"] = str(e)
            raise
        elapsed = time.monotonic() - t0
        _STATE["chat_count"] += 1
        text = str(out or "")
        return {
            "text": text,
            "elapsed_s": round(elapsed, 2),
            "chars": len(text),
            "speed_cps": round(len(text) / elapsed, 1) if elapsed > 0 else 0.0,
        }


# ---------------------------------------------------------------------------
# 多模态 + 流式（OpenAI /v1/chat/completions 用）
#
# pymnn 的 llm.response(prompt, stream) 中 prompt 支持两种形态：
#   * str —— 纯文本；
#   * dict —— MultimodalPrompt：
#       {"text": str,
#        "images": [{"data": numpy uint8 HxWx3 | "file_path": str, "width": W, "height": H}],
#        "audios": [{"file_path": str}]}
#   图片 data 优先传 numpy 数组（pymnn parse_multimodal_input 支持 data/width/height）；
#   若模型/绑定不支持 dict，自动回退纯文本。
#
# 流式：优先 generate_init + generate(后台线程) + get_context()["generate_str"] 轮询增量；
#       模型不支持时回退一次性 response（整段作为唯一 delta）。
# ---------------------------------------------------------------------------

def _require_llm() -> Any:
    global _LLM
    if _LLM is None:
        raise RuntimeError("MNN 模型尚未加载（先调用 load）")
    return _LLM


def _build_multimodal_prompt(text: str, images: list[str] | None = None,
                             audios: list[str] | None = None) -> str | dict[str, Any]:
    """构建 pymnn 多模态 prompt；无媒体时返回纯文本 str。"""
    images = images or []
    audios = audios or []
    if not images and not audios:
        return text
    prompt: dict[str, Any] = {"text": str(text or "")}
    if images:
        loaded: list[dict[str, Any]] = []
        for p in images:
            p = str(p or "").strip()
            if not p or not os.path.exists(p):
                raise ValueError(f"图片不存在: {p}")
            try:
                import numpy as np
                from PIL import Image
                im = Image.open(p).convert("RGB")
                arr = np.asarray(im, dtype=np.uint8)  # HxWx3
                loaded.append({"data": arr, "width": arr.shape[1], "height": arr.shape[0]})
            except ImportError:
                # 无 numpy/PIL 时交给 pymnn 按路径加载
                loaded.append({"file_path": p})
            except Exception as e:  # noqa: BLE001
                raise ValueError(f"读取图片失败 {p}: {e}") from e
        prompt["images"] = loaded
    if audios:
        paths = []
        for a in audios:
            a = str(a or "").strip()
            if not a or not os.path.exists(a):
                raise ValueError(f"音频不存在: {a}")
            paths.append({"file_path": a})
        prompt["audios"] = paths
    return prompt


def chat_multimodal(prompt: str, history: list[dict[str, str]] | None = None,
                    max_new_tokens: int = 512,
                    images: list[str] | None = None,
                    audios: list[str] | None = None) -> dict[str, Any]:
    """多模态对话：images/audios 为本地文件路径列表，可为空（等价 chat()）。"""
    llm = _require_llm()
    prompt = str(prompt or "").strip()
    if not prompt and not images and not audios:
        raise ValueError("prompt 不能为空")
    history = history or []
    if len(history) > 40:
        history = history[-40:]
    m_prompt = _build_multimodal_prompt(prompt, images, audios)

    with _LOCK:
        if _LLM is None:
            raise RuntimeError("MNN 模型已卸载")
        t0 = time.monotonic()
        try:
            templated = m_prompt
            try:
                if isinstance(m_prompt, str):
                    templated = _LLM.apply_chat_template({"role": "user", "content": m_prompt})
                else:
                    # 多模态 dict 走模板可能失败，先试，失败用原 dict
                    templated = _LLM.apply_chat_template({"role": "user", "content": m_prompt})
            except Exception:  # noqa: BLE001
                pass
            try:
                _LLM.set_config({"max_new_tokens": int(max(16, min(int(max_new_tokens), 4096)))})
            except Exception:  # noqa: BLE001
                pass
            try:
                out = _LLM.response(templated, False)
            except TypeError:
                # 模型不支持 dict 多模态 → 回退纯文本
                if isinstance(templated, dict):
                    out = _LLM.response(templated.get("text", "") or "", False)
                else:
                    raise
            except Exception as e:  # noqa: BLE001
                if isinstance(templated, dict):
                    out = _LLM.response(templated.get("text", "") or "", False)
                else:
                    raise e
        except Exception as e:  # noqa: BLE001
            _STATE["error"] = str(e)
            raise
        elapsed = time.monotonic() - t0
        _STATE["chat_count"] += 1
        text = str(out or "")
        return {
            "text": text,
            "elapsed_s": round(elapsed, 2),
            "chars": len(text),
            "speed_cps": round(len(text) / elapsed, 1) if elapsed > 0 else 0.0,
            "multimodal": isinstance(m_prompt, dict),
        }


def chat_stream(prompt: str, history: list[dict[str, str]] | None = None,
                max_new_tokens: int = 512,
                images: list[str] | None = None,
                audios: list[str] | None = None) -> Iterator[tuple[str, bool]]:
    """流式对话生成器：yield (delta_text, finished)。

    finished=True 的最后一段 delta 为空字符串，仅作结束信号；
    调用方负责把 delta 拼成完整回复。全程持有 _LOCK（MNN 非重入）。
    """
    llm = _require_llm()
    prompt = str(prompt or "").strip()
    if not prompt and not images and not audios:
        raise ValueError("prompt 不能为空")
    history = history or []
    if len(history) > 40:
        history = history[-40:]
    m_prompt = _build_multimodal_prompt(prompt, images, audios)

    with _LOCK:
        if _LLM is None:
            raise RuntimeError("MNN 模型已卸载")
        try:
            templated = m_prompt
            try:
                templated = _LLM.apply_chat_template({"role": "user", "content": m_prompt})
            except Exception:  # noqa: BLE001
                pass
            try:
                _LLM.set_config({"max_new_tokens": int(max(16, min(int(max_new_tokens), 4096)))})
            except Exception:  # noqa: BLE001
                pass
        except Exception as e:  # noqa: BLE001
            _STATE["error"] = str(e)
            raise

        # 方案 A：generate_init + 后台 generate + get_context 轮询
        if hasattr(_LLM, "generate_init") and hasattr(_LLM, "get_context"):
            try:
                _LLM.generate_init(templated)
            except Exception as e:  # noqa: BLE001
                _STATE["error"] = str(e)
                # 不支持 → 回退一次性
                yield from _fallback_once(_LLM, templated, m_prompt)
                return
            done = threading.Event()
            err: list[Exception] = []

            def _run() -> None:
                try:
                    _LLM.generate()
                except Exception as e:  # noqa: BLE001
                    err.append(e)
                finally:
                    done.set()

            t = threading.Thread(target=_run, daemon=True)
            t.start()
            prev = ""
            while not done.is_set():
                try:
                    ctx = _LLM.get_context() or {}
                except Exception:  # noqa: BLE001
                    ctx = {}
                cur = str(ctx.get("generate_str", "") or "")
                if cur != prev:
                    yield cur[len(prev):], False
                    prev = cur
                time.sleep(0.03)
            try:
                ctx = _LLM.get_context() or {}
            except Exception:  # noqa: BLE001
                ctx = {}
            cur = str(ctx.get("generate_str", "") or "")
            if cur != prev:
                yield cur[len(prev):], False
            if err:
                raise err[0]
            _STATE["chat_count"] += 1
            yield "", True
            return

        # 方案 B：模型无流式接口 → 一次性返回
        yield from _fallback_once(_LLM, templated, m_prompt)


def _fallback_once(llm: Any, templated: Any, m_prompt: Any) -> Iterator[tuple[str, bool]]:
    try:
        out = llm.response(templated, False)
    except TypeError:
        if isinstance(templated, dict):
            out = llm.response(templated.get("text", "") or "", False)
        else:
            raise
    except Exception:  # noqa: BLE001
        if isinstance(templated, dict):
            out = llm.response(templated.get("text", "") or "", False)
        else:
            raise
    text = str(out or "")
    _STATE["chat_count"] += 1
    if text:
        yield text, False
    yield "", True
