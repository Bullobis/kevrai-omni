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

import contextlib
import logging
import multiprocessing
import os
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

log = logging.getLogger("kevrai.mnn")

# Force the spawn start method for the MNN worker process. The sidecar parent
# is multi-threaded; fork()-ing a multi-threaded process can deadlock inside the
# child (the C++ engine holds locks fork never re-acquires) and emits
# DeprecationWarnings. spawn starts a fresh interpreter on every platform and is
# the Windows default, so behavior is consistent across OSes.
_MP_CTX = multiprocessing.get_context("spawn")

_LOCK = threading.Lock()

# 流式生成轮询的墙钟上限。C++ 引擎一旦死锁/挂起，后台线程永远不会 set done 事件，
# 而 chat_stream 全程持有 _LOCK——不设上限会让整个 MNN 子系统永久不可用。
_STREAM_GENERATION_TIMEOUT_S = 600.0

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


# Test-double injection channel (production off).
#
# Spawn children re-import this module in a fresh interpreter, so a fork-time
# monkeypatch of ``_import_llm`` in the parent never reaches them. The process
# environment IS inherited across the OS spawn boundary, so tests set this env
# var to a behavior string ("normal" / "error" / "value_error" / "hang") and
# the child resolves the fake below. No effect when the var is unset.
_TEST_FAKE_ENV_VAR = "KEVRAI_MNN_TEST_FAKE"


class _FakeLlmForTests:
    """In-process stand-in for ``MNN.llm.Llm``; only active under
    ``_TEST_FAKE_ENV_VAR``. Mirrors the surface the runtime drives."""

    def __init__(self, cfg_path: str, behavior: str = "normal"):
        self._behavior = behavior
        self._cfg = cfg_path
        self._prompt = ""

    def load(self) -> None:
        pass

    def reset(self) -> None:
        pass

    def apply_chat_template(self, msg):
        if isinstance(msg, dict):
            return msg.get("content", "")
        return str(msg)

    def set_config(self, cfg) -> None:
        pass

    def response(self, prompt, stream):
        if self._behavior == "error":
            raise RuntimeError("fake boom")
        if self._behavior == "value_error":
            raise ValueError("fake bad input")
        return "fake response: " + str(prompt)

    def generate_init(self, prompt) -> None:
        self._prompt = prompt

    def get_context(self):
        return {"generate_str": "partial"}

    def generate(self) -> None:
        if self._behavior == "hang":
            threading.Event().wait()  # simulate C++ deadlock forever


def _import_llm():
    behavior = os.environ.get(_TEST_FAKE_ENV_VAR, "")
    if behavior:
        def _fake_create(cfg_path: str):
            return _FakeLlmForTests(cfg_path, behavior=behavior)
        return _fake_create
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
    if _subprocess_mode():
        return _get_client().load_model(model_dir, model_name)
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
        with contextlib.suppress(Exception):  # best-effort reset; ignore failures
            _LLM.reset()
    _LLM = None
    _STATE["loaded"] = False
    _STATE["model_dir"] = ""
    _STATE["model_name"] = ""


def unload_model() -> dict[str, Any]:
    if _subprocess_mode():
        return _get_client().unload_model()
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
    if _subprocess_mode():
        return _get_client().status()
    with _LOCK:
        return status_locked()


def chat(prompt: str, history: list[dict[str, str]] | None = None,
         max_new_tokens: int = 512) -> dict[str, Any]:
    """One-shot chat. `history` is [{role, content}] from the UI.

    MNN keeps its own context between response() calls, so we simply feed
    the latest user turn through the chat template and generate.
    """
    if _subprocess_mode():
        return _get_client().chat(prompt, history, max_new_tokens)
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
            with contextlib.suppress(Exception):  # best-effort config; ignore failures
                _LLM.set_config({"max_new_tokens": int(max(16, min(int(max_new_tokens), 4096)))})
            out = _LLM.response(templated, False)
        except Exception as e:  # noqa: BLE001
            _STATE["error"] = str(e)
            raise
        elapsed = time.monotonic() - t0
        _STATE["chat_count"] += 1
        _STATE["error"] = ""  # 成功一次后清掉上一轮的失败残留
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
    if _subprocess_mode():
        return _get_client().chat_multimodal(prompt, history, max_new_tokens, images, audios)
    _require_llm()
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
            with contextlib.suppress(Exception):  # best-effort template; ignore failures
                if isinstance(m_prompt, str):
                    templated = _LLM.apply_chat_template({"role": "user", "content": m_prompt})
                else:
                    # 多模态 dict 走模板可能失败，先试，失败用原 dict
                    templated = _LLM.apply_chat_template({"role": "user", "content": m_prompt})
            with contextlib.suppress(Exception):  # best-effort config; ignore failures
                _LLM.set_config({"max_new_tokens": int(max(16, min(int(max_new_tokens), 4096)))})
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
        _STATE["error"] = ""  # 成功一次后清掉上一轮的失败残留
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
    if _subprocess_mode():
        yield from _get_client().chat_stream(prompt, history, max_new_tokens, images, audios)
        return
    _require_llm()
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
            with contextlib.suppress(Exception):  # best-effort template; ignore failures
                templated = _LLM.apply_chat_template({"role": "user", "content": m_prompt})
            with contextlib.suppress(Exception):  # best-effort config; ignore failures
                _LLM.set_config({"max_new_tokens": int(max(16, min(int(max_new_tokens), 4096)))})
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
            deadline = time.monotonic() + _STREAM_GENERATION_TIMEOUT_S
            while not done.is_set():
                if time.monotonic() >= deadline:
                    # 引擎挂死：记录错误并抛超时。锁随生成器退出而释放，
                    # 后台 daemon 线程最终会自行结束（见模块 docstring 的提案说明）。
                    _STATE["error"] = (
                        f"MNN 流式生成超时（{_STREAM_GENERATION_TIMEOUT_S:.0f}s 无完成信号）"
                    )
                    raise TimeoutError(_STATE["error"])
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
                _STATE["error"] = str(err[0])
                raise err[0]
            _STATE["chat_count"] += 1
            _STATE["error"] = ""  # 成功后清掉上一轮的失败残留
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


# ===========================================================================
# Subprocess isolation (opt-in via KEVRAI_MNN_SUBPROCESS=1)
# ---------------------------------------------------------------------------
# The C++ MNN engine can hard-deadlock during generate(). In-process that
# leaves a parked daemon thread holding the engine's internal state and no way
# to unwind it — the wall-clock timeout only releases the Python lock. With
# this mode the engine lives in a forked child process; the parent talks to it
# over a multiprocessing.Pipe (pickle frames). If the child hangs or dies, the
# parent SIGKILLs the child — reclaiming every C++ resource at the OS level —
# and raises; the next load spawns a fresh child.
#
# Default (env unset) keeps the in-process path byte-identical. Zero new deps:
# multiprocessing/Pipe are stdlib. The child runs the SAME in-process functions
# above (no logic duplication); it just dispatches JSON-ish tuples from the pipe.
# ===========================================================================

_SUBPROCESS_ENV_VAR = "KEVRAI_MNN_SUBPROCESS"
_CHILD_INDICATOR_VAR = "KEVRAI_MNN_IN_CHILD"  # set inside the child → never recurse
_CHILD_READY_TIMEOUT_S = 30.0
_LOAD_TIMEOUT_S = 600.0
_CHAT_TIMEOUT_S = 600.0
# Parent-side ceiling on waiting for the next stream message from the child.
# Reuses the same default as the in-process timeout; tests monkeypatch it short.
_SUBPROCESS_STREAM_TIMEOUT_S = _STREAM_GENERATION_TIMEOUT_S

_CLIENT: _MnnSubprocessClient | None = None
# Guards the lazy first-time construction of the subprocess client. Without it,
# two concurrent threadpool callers (e.g. two simultaneous /api/mnn/load +
# /api/mnn/chat) both see _CLIENT is None, each build a client, and each
# _ensure_started() spawns its own MNN child process — the orphaned client's
# child leaks until process exit.
_CLIENT_INIT_LOCK = threading.Lock()


def _subprocess_mode() -> bool:
    """True when the parent should delegate MNN work to a child process.

    The child itself sets ``_CHILD_INDICATOR_VAR`` so it always runs the
    in-process path (no recursive fork).
    """
    if os.environ.get(_CHILD_INDICATOR_VAR, "") == "1":
        return False
    return os.environ.get(_SUBPROCESS_ENV_VAR, "") == "1"


def _get_client() -> _MnnSubprocessClient:
    global _CLIENT
    if _CLIENT is None:
        with _CLIENT_INIT_LOCK:
            # Double-checked: another thread may have built it while we waited.
            if _CLIENT is None:
                _CLIENT = _MnnSubprocessClient()
    return _CLIENT


def _reset_client_for_tests() -> None:
    """Drop the singleton client (test teardown); never called in production."""
    global _CLIENT
    if _CLIENT is not None:
        with contextlib.suppress(Exception):
            _CLIENT.close()
    _CLIENT = None


# -- exception tagging across the pipe ---------------------------------------

def _exc_tag(e: BaseException) -> str:
    if isinstance(e, MnnEngineMissing):
        return "MnnEngineMissing"
    if isinstance(e, FileNotFoundError):
        return "FileNotFoundError"
    if isinstance(e, ValueError):
        return "ValueError"
    if isinstance(e, TimeoutError):
        return "TimeoutError"
    if isinstance(e, RuntimeError):
        return "RuntimeError"
    return "Exception"


def _raise_tagged(tag: str, msg: str) -> None:
    if tag == "MnnEngineMissing":
        raise MnnEngineMissing(msg)
    if tag == "FileNotFoundError":
        raise FileNotFoundError(msg)
    if tag == "ValueError":
        raise ValueError(msg)
    if tag == "TimeoutError":
        raise TimeoutError(msg)
    if tag == "RuntimeError":
        raise RuntimeError(msg)
    raise RuntimeError(msg)


# -- child entry point (runs in the forked child) ----------------------------

def _mnn_child_main(conn: multiprocessing.connection.Connection) -> None:
    """Dispatch loop inside the child: run in-process MNN calls, pipe back results.

    The child never uses subprocess mode (sets the indicator env var first), so
    every call below hits the normal in-process path with its own _LLM/_STATE.
    """
    os.environ[_CHILD_INDICATOR_VAR] = "1"
    with contextlib.suppress(Exception):
        os.setsid()  # own session group; parent may killpg if grandchildren appear
    try:
        conn.send(("ready",))
    except Exception:
        return
    while True:
        try:
            cmd = conn.recv()
        except EOFError:
            return
        if not isinstance(cmd, tuple) or not cmd:
            continue
        kind = cmd[0]
        try:
            if kind == "shutdown":
                with contextlib.suppress(Exception):
                    unload_model()
                with contextlib.suppress(Exception):
                    conn.send(("bye",))
                return
            if kind == "load":
                _, model_dir, model_name = cmd
                conn.send(("ok", load_model(model_dir, model_name)))
            elif kind == "unload":
                unload_model()
                conn.send(("ok", status_locked()))
            elif kind == "status":
                conn.send(("ok", status_locked()))
            elif kind == "chat":
                _, prompt, history, mx = cmd
                conn.send(("ok", chat(prompt, history, mx)))
            elif kind == "chat_multimodal":
                _, prompt, history, mx, images, audios = cmd
                conn.send(("ok", chat_multimodal(prompt, history, mx, images, audios)))
            elif kind == "chat_stream":
                _, prompt, history, mx, images, audios = cmd
                for delta, _fin in chat_stream(prompt, history, mx, images, audios):
                    conn.send(("delta", delta))
                conn.send(("done", status_locked()))
            else:
                conn.send(("err", "RuntimeError", f"unknown command: {kind!r}"))
        except Exception as e:  # noqa: BLE001 — reflect any failure back
            with contextlib.suppress(Exception):
                conn.send(("err", _exc_tag(e), str(e)))


# -- parent-side client ------------------------------------------------------

class _MnnSubprocessClient:
    """Parent-side proxy to the forked MNN child.

    Every call serializes on ``_call_lock`` (MNN is non-reentrant anyway).
    On a child hang (pipe read timeout) or crash (EOFError / process exit) the
    child is SIGKILLed — the whole point of isolation — and the caller sees an
    error. The next ``load_model`` respawns a fresh child.
    """

    def __init__(self) -> None:
        self._conn: Any = None
        self._proc: multiprocessing.Process | None = None
        self._call_lock = threading.Lock()
        self._state: dict[str, Any] = {
            "loaded": False, "model_dir": "", "model_name": "",
            "loading": False, "error": "", "loaded_at": 0.0, "chat_count": 0,
        }
        self._watchdog_stop = threading.Event()
        self._killed_by_us = False

    # -- lifecycle -----------------------------------------------------------
    def _ensure_started(self) -> None:
        if self._proc is not None and self._proc.is_alive() and self._conn is not None:
            return
        parent_conn, child_conn = _MP_CTX.Pipe(duplex=True)
        p = _MP_CTX.Process(
            target=_mnn_child_main, args=(child_conn,), daemon=True,
        )
        p.start()
        child_conn.close()
        self._conn = parent_conn
        self._proc = p
        self._killed_by_us = False
        self._state["error"] = ""
        if not parent_conn.poll(_CHILD_READY_TIMEOUT_S):
            self._kill()
            raise RuntimeError(f"MNN 子进程 {_CHILD_READY_TIMEOUT_S:.0f}s 内未就绪")
        try:
            msg = parent_conn.recv()
        except EOFError:
            self._kill()
            raise RuntimeError("MNN 子进程启动后立即退出") from None
        if not (isinstance(msg, tuple) and msg and msg[0] == "ready"):
            self._kill()
            raise RuntimeError(f"MNN 子进程未正常就绪: {msg!r}")
        threading.Thread(target=self._watchdog_loop, daemon=True).start()

    def _kill(self) -> None:
        self._killed_by_us = True
        if self._proc is not None:
            with contextlib.suppress(Exception):
                if self._proc.is_alive():
                    self._proc.kill()
            with contextlib.suppress(Exception):
                self._proc.join(timeout=5)
        self._proc = None
        self._conn = None
        self._state["loaded"] = False
        self._state["loading"] = False

    def close(self) -> None:
        """Clean shutdown (app exit / test teardown). Best-effort."""
        self._watchdog_stop.set()
        with contextlib.suppress(Exception):
            if self._proc is not None and self._proc.is_alive() and self._conn is not None:
                self._conn.send(("shutdown",))
                if self._conn.poll(2.0):
                    self._conn.recv()
        self._kill()

    def _watchdog_loop(self) -> None:
        """Detect idle crashes (segfault / OOM between calls) without touching
        the pipe (no contention with in-flight calls)."""
        while not self._watchdog_stop.wait(3.0):
            proc = self._proc
            if proc is None:
                return
            if not proc.is_alive() and not self._killed_by_us:
                self._state["loaded"] = False
                self._state["loading"] = False
                self._state["error"] = "MNN 子进程意外退出"
                self._conn = None
                return

    # -- RPC primitives ------------------------------------------------------
    def _request(self, cmd: tuple, timeout: float) -> Any:
        self._ensure_started()
        self._conn.send(cmd)
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                self._kill()
                raise TimeoutError(f"MNN 子进程调用超时（{timeout:.0f}s 无响应）")
            if self._conn.poll(remaining):
                try:
                    return self._conn.recv()
                except EOFError:
                    self._kill()
                    raise RuntimeError("MNN 子进程在响应期间意外退出") from None

    @staticmethod
    def _unwrap_ok(msg: tuple) -> Any:
        if msg[0] == "ok":
            return msg[1]
        if msg[0] == "err":
            _raise_tagged(msg[1], msg[2])
        raise RuntimeError(f"子进程返回未知消息: {msg!r}")

    # -- public operations (mirror the module API) --------------------------
    def load_model(self, model_dir: str | Path, model_name: str = "") -> dict[str, Any]:
        with self._call_lock:
            st = self._unwrap_ok(self._request(
                ("load", str(model_dir), model_name), _LOAD_TIMEOUT_S))
            self._state.update(st)
            return st

    def unload_model(self) -> dict[str, Any]:
        with self._call_lock:
            with contextlib.suppress(RuntimeError):
                self._unwrap_ok(self._request(("unload",), 30.0))
            self._state["loaded"] = False
            self._state["model_dir"] = ""
            self._state["model_name"] = ""
            return {"ok": True}

    def status(self) -> dict[str, Any]:
        with self._call_lock:
            if self._proc is None or not self._proc.is_alive():
                st = dict(self._state)
                st["loaded"] = False
                st["loading"] = False
                st["engine_available"] = is_engine_available()
                st["engine_version"] = engine_version()
                return st
            try:
                st = self._unwrap_ok(self._request(("status",), 10.0))
            except Exception:  # noqa: BLE001
                st = dict(self._state)
            self._state.update(st)
            return st

    def chat(self, prompt: str, history: list | None = None,
             max_new_tokens: int = 512) -> dict[str, Any]:
        with self._call_lock:
            return self._unwrap_ok(self._request(
                ("chat", prompt, history or [], int(max_new_tokens)), _CHAT_TIMEOUT_S))

    def chat_multimodal(self, prompt: str, history: list | None = None,
                        max_new_tokens: int = 512,
                        images: list | None = None,
                        audios: list | None = None) -> dict[str, Any]:
        with self._call_lock:
            return self._unwrap_ok(self._request(
                ("chat_multimodal", prompt, history or [], int(max_new_tokens),
                 images or [], audios or []), _CHAT_TIMEOUT_S))

    def chat_stream(self, prompt: str, history: list | None = None,
                    max_new_tokens: int = 512,
                    images: list | None = None,
                    audios: list | None = None) -> Iterator[tuple[str, bool]]:
        with self._call_lock:
            self._ensure_started()
            self._conn.send(("chat_stream", prompt, history or [],
                             int(max_new_tokens), images or [], audios or []))
            deadline = time.monotonic() + _SUBPROCESS_STREAM_TIMEOUT_S
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    self._kill()
                    raise TimeoutError(
                        f"MNN 流式生成超时（{_SUBPROCESS_STREAM_TIMEOUT_S:.0f}s 无完成信号）")
                if not self._conn.poll(remaining):
                    self._kill()
                    raise TimeoutError(
                        f"MNN 流式生成超时（{_SUBPROCESS_STREAM_TIMEOUT_S:.0f}s 无完成信号）")
                try:
                    msg = self._conn.recv()
                except EOFError:
                    self._kill()
                    raise RuntimeError("MNN 子进程在流式生成中崩溃") from None
                kind = msg[0]
                if kind == "delta":
                    yield msg[1], False
                elif kind == "done":
                    self._state.update(msg[1])
                    yield "", True
                    return
                elif kind == "err":
                    self._state["error"] = msg[2]
                    _raise_tagged(msg[1], msg[2])

