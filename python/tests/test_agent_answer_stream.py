"""Tests for the agent's token-by-token final-answer streaming.

The brain streams its polished final answer over the WebSocket as ``answer_delta``
events; the ReAct loop forwards each delta to an answer callback while the loop's
own blocking ``chat()`` (thought/action) is unchanged.

Coverage:
  * the ``answer_stream`` reasoning-strip generator holds back a leading
    ``<think>…</think>`` block and yields only the answer suffix (and reconciles
    an unclosed think block, matching ``strip_reasoning``);
  * the agent loop forwards streamed deltas in order to the answer callback;
  * only the *complete* answer is persisted — a partial stream is never stored;
  * the WebSocket emits ``answer_delta`` frames before the ``final`` frame;
  * cancelling mid-stream honours cooperatively and does not persist a partial.
"""
from __future__ import annotations

import asyncio
import sys
import threading
import time

import app.llm_runtime as brain_mod
from app.agent import Agent, AgentMemory, ToolContext
from app.agent.tool_registry import ToolRegistry


# ---------------------------------------------------------------------------
# Part 1: the brain's answer_stream reasoning-strip generator (unit)
# ---------------------------------------------------------------------------
class _FakeStreamer:
    """Stand-in for transformers.TextIteratorStreamer: yields fixed chunks."""

    chunks: list[str] = []

    def __init__(self, tokenizer, skip_prompt=True, skip_special_tokens=True):
        self._it = iter(self.chunks)

    def __iter__(self):
        return self

    def __next__(self):
        return next(self._it)


class _FakeTok:
    model_max_length = 4096

    def apply_chat_template(self, messages, tokenize=False, add_generation_prompt=True):
        return "PROMPT"

    def __call__(self, text, add_special_tokens=True):
        return {"input_ids": [1, 2, 3]}

    def decode(self, ids, skip_special_tokens=True):
        return "x"


class _FakeModel:
    config = type("C", (), {"max_position_embeddings": 512})()

    def eval(self):
        return self

    def generate(self, input_ids, **kwargs):
        # The streamer already carries the chunks; nothing to do here.
        return [[1, 2, 3]]


def _patch_brain(tmp_path, monkeypatch, chunks):
    _FakeStreamer.chunks = list(chunks)

    class AutoTok:
        @staticmethod
        def from_pretrained(repo):
            return _FakeTok()

    class AutoModel:
        @staticmethod
        def from_pretrained(repo, torch_dtype=None):
            return _FakeModel()

    class TF:
        AutoTokenizer = AutoTok
        AutoModelForCausalLM = AutoModel
        TextIteratorStreamer = _FakeStreamer

        @staticmethod
        def StoppingCriteriaList(items):
            return items

    monkeypatch.setattr(brain_mod, "import_transformers", lambda data_root=None: TF())

    class FakeTorch:
        long = int

        class no_grad:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        @staticmethod
        def tensor(rows, dtype=None):
            return rows[0]

    monkeypatch.setitem(sys.modules, "torch", FakeTorch())
    return brain_mod.TextBrainManager(data_root=tmp_path)


def test_answer_stream_hides_think_block(tmp_path, monkeypatch):
    mgr = _patch_brain(tmp_path, monkeypatch, [
        "<think>\nhidden reasoning\n", "</think>\n", "Hello ", "world",
    ])
    gen = mgr.answer_stream("o/r", "hi")
    deltas = list(gen)
    # Everything up to & including </think> is held back; only the suffix streams.
    assert "think" not in "".join(deltas).lower()
    assert "Hello" in "".join(deltas)
    # The generator's return value is the complete stripped answer.
    assert gen.gi_frame is None  # exhausted


def test_answer_stream_plain_answer_streams_all(tmp_path, monkeypatch):
    mgr = _patch_brain(tmp_path, monkeypatch, ["Hi ", "there", "!"])
    deltas = list(mgr.answer_stream("o/r", "hi"))
    assert deltas == ["Hi ", "there", "!"]


def test_answer_stream_unclosed_think_reconciles(tmp_path, monkeypatch):
    # No closing </think>: strip_reasoning would return the whole buffer. The
    # generator must emit what it held back at the end.
    mgr = _patch_brain(tmp_path, monkeypatch, ["<think>never closed reasoning"])
    deltas = list(mgr.answer_stream("o/r", "hi"))
    assert "never closed" in "".join(deltas)


# ---------------------------------------------------------------------------
# Part 2: agent loop forwards streamed deltas; persists only the complete answer
# ---------------------------------------------------------------------------
class _StreamRouter:
    """Scripted router: chat() ends the ReAct loop; answer_stream() yields chunks."""

    def __init__(self, chunks, chat_text="最终答案：占位"):
        self._chunks = list(chunks)
        self._chat_text = chat_text
        self.deltas: list[str] = []

    def is_ready(self):
        return True, "stream-mock"

    def chat(self, prompt, system="", max_new_tokens=2048, should_stop=None):
        return {"ok": True, "text": self._chat_text, "model_name": "stream-mock"}

    def answer_stream(self, question, system="", max_new_tokens=512, should_stop=None):
        for c in self._chunks:
            if should_stop is not None and should_stop():
                break
            self.deltas.append(c)
            yield c
        return "".join(self._chunks)


def _build_agent(tmp_path, router):
    mem = AgentMemory(tmp_path / "stream.sqlite3")
    ctx = ToolContext(memory=mem, models_dir=tmp_path, app_root=tmp_path)
    ctx.hardware_info = {
        "gpu_vendor": "mock", "gpu_best_vram_gb": 0,
        "ram_total_gb": 0, "disk": {"free_gb": 0},
    }
    reg = ToolRegistry()
    return Agent(memory=mem, router=router, registry=reg, ctx=ctx)


def _assistant_messages(mem, sid):
    return [m["content"] for m in mem.get_messages(sid) if m["role"] == "assistant"]


def test_agent_forwards_stream_deltas_and_persists_complete(tmp_path):
    router = _StreamRouter(["你好，", "这是", "流式答案"])
    agent = _build_agent(tmp_path, router)
    seen: list[str] = []
    agent.set_answer_callback(lambda d: seen.append(d))

    result = asyncio.run(agent.run("写一句问候", session_id="s1"))

    # Deltas forwarded in order, unchanged.
    assert seen == ["你好，", "这是", "流式答案"]
    # The final answer equals the fully-streamed text (extracted placeholder replaced).
    assert result.answer == "你好，这是流式答案"
    # Only the COMPLETE answer persisted — no half-streamed copy.
    persisted = _assistant_messages(agent.memory, "s1")
    assert persisted == ["你好，这是流式答案"]


def test_agent_no_callback_uses_extracted_answer(tmp_path):
    # Without an answer callback wired, behaviour is unchanged (extracted text).
    router = _StreamRouter(["不会被用", "因为没回调"], chat_text="最终答案：原始答案")
    agent = _build_agent(tmp_path, router)
    result = asyncio.run(agent.run("写一句问候", session_id="s2"))
    assert "原始答案" in result.answer


# ---------------------------------------------------------------------------
# Part 3: cancel mid-stream
# ---------------------------------------------------------------------------
class _GateStreamRouter:
    """answer_stream yields one chunk, then waits for the test to cancel."""

    def __init__(self):
        self.got_first = threading.Event()
        self.release = threading.Event()

    def is_ready(self):
        return True, "gate-stream"

    def chat(self, prompt, system="", max_new_tokens=2048, should_stop=None):
        return {"ok": True, "text": "最终答案：占位", "model_name": "gate-stream"}

    def answer_stream(self, question, system="", max_new_tokens=512, should_stop=None):
        yield "first"
        self.got_first.set()
        self.release.wait(timeout=5)
        if should_stop is not None and should_stop():
            return
        yield "second"


def test_cancel_mid_stream_does_not_persist_partial(tmp_path):
    router = _GateStreamRouter()
    agent = _build_agent(tmp_path, router)
    seen: list[str] = []
    agent.set_answer_callback(lambda d: seen.append(d))
    sid = "s-cancel"

    holder: dict = {}

    def _run():
        holder["result"] = asyncio.run(agent.run("写一句问候", session_id=sid))

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    assert router.got_first.wait(timeout=5)
    # Only the first delta has streamed so far.
    assert seen == ["first"]
    assert agent.cancel(sid) is True
    router.release.set()
    t.join(timeout=5)

    result = holder["result"]
    assert result.cancelled is True
    assert result.error == "cancelled"
    # The partial streamed text ("first") must NOT be persisted as the answer.
    persisted = _assistant_messages(agent.memory, sid)
    assert "first" not in " ".join(persisted)


# ---------------------------------------------------------------------------
# Part 4: WebSocket ordering — answer_delta frames precede final
# ---------------------------------------------------------------------------
def test_ws_emits_answer_delta_before_final(hub_client):
    hub_client.get("/api/agent/tools")  # build the singleton
    from app import main as app_main

    agent = app_main._AGENT_SINGLETON["agent"]
    agent.ctx.hardware_info = {
        "gpu_vendor": "mock", "gpu_best_vram_gb": 0,
        "ram_total_gb": 0, "disk": {"free_gb": 0},
    }
    router = _StreamRouter(["A", "B", "C"])
    orig = agent.router
    agent.router = router
    try:
        events = []
        with hub_client.websocket_connect("/ws/agent/ws-stream") as ws:
            ws.send_json({"message": "写一句问候"})
            deadline = time.time() + 5
            while time.time() < deadline:
                ev = ws.receive_json()
                events.append(ev.get("event"))
                if ev.get("event") == "final":
                    break
        deltas = [e for e in events if e == "answer_delta"]
        assert deltas == ["answer_delta", "answer_delta", "answer_delta"]
        # final is strictly after the deltas.
        assert events[-1] == "final"
        assert events.index("final") > max(i for i, e in enumerate(events) if e == "answer_delta")
    finally:
        agent.router = orig
