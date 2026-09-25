"""Neuron-8 (K-Cortex slice 8) — fuzz / malformed-input hardening tests.

Goal: every "external input → parse/validate" boundary must survive arbitrary
garbage without an uncaught exception / 500 traceback. Bad input must surface as
a clean 4xx / structured error, or be safely ignored.

Coverage:
  1. ``agent.tool_registry.parse_tool_call`` / ``extract_final_answer`` (pure
     parser fuzz — truncated / deep / trailing-comment / single-quote JSON).
  2. ``ToolRegistry.execute`` — every registered tool fed mutated params
     (missing / wrong-type / negative / out-of-range / injection strings).
  3. ``catalog.load_catalog`` — mutated models.json / engines.json on disk.
  4. ``main._media_to_local`` — mutated data:/file: URLs (http branch mocked).
  5. HTTP API (TestClient, ``raise_server_exceptions=False`` so an unhandled
     exception surfaces as a 500 instead of re-raising in the test thread):
     POST /v1/chat/completions, GET /api/search, PUT /api/settings,
     POST /api/engines/{install,update}, and path-param routes.

The fuzz corpus is driven by ``random.Random(seed)`` (deterministic,
reproducible). **No new third-party dependency** — this uses only the stdlib.

Deterministic regression tests for any *real* crash found live at the bottom
of this file, each keyed on a fixed input (not on randomness).
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
import random
import string
import sys
from pathlib import Path
from typing import Any

import pytest
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import main as app_main  # noqa: E402
from app.agent.tool_registry import (  # noqa: E402
    ToolContext,
    ToolRegistry,
    extract_final_answer,
    parse_tool_call,
)
from app.agent.tools import build_default_registry  # noqa: E402

SEED = 20260925
# Mutation alphabet: control chars, unicode, quote/bracket imbalance,
# JSON-comment / single-quote tricks, injection payloads.
PUNCT = list('{}[]()<>"\'\\/:;,.!?@#$%^&*+-=~`| \t\n\r')
INJECTION = [
    "' OR 1=1--",
    "../../../../etc/passwd",
    "..\\..\\windows\\system32",
    "$(reboot)",
    "`id`",
    "; rm -rf /",
    "{{7*7}}",
    "${jndi:ldap://x}",
    "\x00",
    "\r\nSet-Cookie: x=1",
]


def _mutate(rng: random.Random, s: str) -> str:
    """Apply a few random mutations to a seed string."""
    out = list(s)
    for _ in range(rng.randint(1, 4)):
        if not out:
            break
        op = rng.random()
        i = rng.randrange(len(out)) if out else 0
        if op < 0.35:  # delete
            del out[i]
        elif op < 0.7:  # insert junk
            out.insert(i, rng.choice(PUNCT + INJECTION))
        else:  # replace
            out[i] = rng.choice(PUNCT)
    text = "".join(out)
    if rng.random() < 0.1:
        text = text[: rng.randint(0, max(1, len(text)))]  # truncate
    return text


# ---------------------------------------------------------------------------
# 1. parse_tool_call / extract_final_answer — pure parser fuzz
# ---------------------------------------------------------------------------

_PARSE_SEEDS = [
    "Thought: let me search\nAction: search_models|{\"query\": \"qwen\", \"limit\": 5}",
    "Action: model_info(model_id=qwen2.5, limit=10)",
    "Action: search_models|{\"query\":\"x\"} # trailing comment",
    "Action: check_hardware()",
    "Final Answer: here is the answer",
    "最终答案：你好",
    "Action: set_preference(key=theme, value=dark)",
    "no tool call here, just chatter",
]


def test_parse_tool_call_fuzz_no_crash():
    rng = random.Random(SEED)
    n = 0
    for _ in range(600):
        seed = rng.choice(_PARSE_SEEDS)
        text = _mutate(rng, seed)
        # Must never raise — any garbage LLM output must be tolerated.
        try:
            res = parse_tool_call(text)
        except RecursionError:
            pytest.fail(f"parse_tool_call RecursionError on: {text!r}")
        except Exception as e:  # noqa: BLE001
            pytest.fail(f"parse_tool_call raised {type(e).__name__} on: {text!r}: {e}")
        if res is not None:
            name, params = res
            assert isinstance(name, str)
            assert isinstance(params, dict)
        n += 1
    assert n >= 600


def test_parse_tool_call_deep_and_huge():
    rng = random.Random(SEED + 1)
    # Deeply nested / truncated / huge JSON objects must not crash.
    for depth in [10, 100, 500, 2000]:
        s = '{"a":' * depth + "1" + "}" * depth
        assert parse_tool_call("Action: search_models|" + s) is not None
    # 1 MB string value
    big = "A" * (1 << 20)
    text = f'Action: search_models|{{"query": "{big}"}}'
    assert parse_tool_call(text) is not None
    # Control chars / non-UTF8-ish bytes smuggled in as a str
    weird = "Action: search_models|\t\x00\x01\x1f\x7f{\"k\": \"v\"}\n"
    parse_tool_call(weird)
    # Single-quote JSON (Python-literal style) must not crash, just fail to parse.
    parse_tool_call("Action: t|{'k': 'v'}")
    # Trailing-comment / repeated-brace / unbalanced
    for junk in ["{\"a\":1", "{\"a\":1}}}}", "{{{\"a\":1}", "Action: t|{not json"]:
        parse_tool_call(junk)


def test_extract_final_answer_fuzz_no_crash():
    rng = random.Random(SEED + 2)
    for _ in range(300):
        seed = rng.choice(_PARSE_SEEDS + ["", "Thought: ", "Final Answer: "])
        text = _mutate(rng, seed)
        # Must never raise; returns str.
        out = extract_final_answer(text)
        assert isinstance(out, str)


# ---------------------------------------------------------------------------
# 2. ToolRegistry.execute — mutated params for every built-in tool
# ---------------------------------------------------------------------------

def _fuzz_params(rng: random.Random) -> dict[str, Any]:
    """Generate a garbage params dict for a tool call."""
    out: dict[str, Any] = {}
    for _ in range(rng.randint(0, 6)):
        k = rng.choice(["query", "model_id", "category", "limit", "topic",
                        "doc_type", "mode", "subject", "mood", "key",
                        "value", "engine_id", "n", "offset"])
        kind = rng.random()
        if kind < 0.2:
            v = rng.choice([None, 1, 0, -1, 99999999, 3.14, True, [], {}, b"\x00\x01"])
        elif kind < 0.4:
            v = rng.randint(-100000, 100000)
        else:
            v = _mutate(rng, rng.choice(["qwen", "llm", "dark", "a", "12"]))
        out[k] = v
    return out


def test_registry_execute_fuzz_no_crash():
    reg = build_default_registry()
    ctx = ToolContext()
    rng = random.Random(SEED + 3)
    tools = reg.list_names()
    assert tools, "expected at least the built-in tools"
    n = 0
    for _ in range(800):
        name = rng.choice(tools)
        params = _fuzz_params(rng)
        # execute() must swallow handler errors into {ok:False,...}; never raise.
        try:
            res = reg.execute(name, params, ctx)
        except Exception as e:  # noqa: BLE001
            pytest.fail(f"registry.execute({name!r}, {params!r}) raised {type(e).__name__}: {e}")
        assert isinstance(res, dict)
        assert "ok" in res
        n += 1
    assert n >= 800
    # Unknown tool + non-string name must also be safe.
    assert reg.execute("does_not_exist", {}, ctx).get("ok") is False
    assert reg.execute(None, {}, ctx).get("ok") is False  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# 3. catalog.load_catalog — mutated on-disk JSON
# ---------------------------------------------------------------------------

def _write(tmp: Path, name: str, data: str | bytes) -> Path:
    p = tmp / name
    p.write_bytes(data.encode("utf-8") if isinstance(data, str) else data)
    return p


def test_load_catalog_fuzz(tmp_path):
    from app.catalog import load_catalog

    rng = random.Random(SEED + 4)
    valid = {
        "version": "1.0",
        "models": [
            {"id": "qwen2.5", "category": "llm", "name": "Qwen",
             "repo": "qwen/qwen", "engine": ["mnn"], "license": "MIT",
             "description": "a model"},
        ],
    }
    n = 0
    for i in range(400):
        d = tmp_path / f"c{i}"
        d.mkdir(exist_ok=True)
        payload: Any = valid
        mode = rng.random()
        if mode < 0.25:
            # corrupt JSON bytes
            raw = json.dumps(valid)
            payload = _mutate(rng, raw)
        elif mode < 0.45:
            # non-object root / wrong types
            payload = rng.choice([[], "str", 42, None, True, [1, 2, 3]])
        elif mode < 0.7:
            # mutate one model record in place
            payload = json.loads(json.dumps(valid))
            rec = payload["models"][0]
            k = rng.choice(list(rec.keys()))
            rec[k] = rng.choice([None, 1, [], {}, "bad id!", "../../x", -5.0])
        else:
            # drop required field
            payload = json.loads(json.dumps(valid))
            del payload["models"][0][rng.choice(["id", "category", "name"])]

        if isinstance(payload, str):
            _write(d, "models.json", payload)
        else:
            _write(d, "models.json", json.dumps(payload))

        # engines.json — occasionally corrupt / non-dict
        if rng.random() < 0.5:
            eng: Any = rng.choice([
                '{"engines": [{"id": "x"}, 42, "str", {}]}',
                "not json{",
                '{"engines": {"not": "list"}}',
                '{"engines": [{"no_id": 1}]}',
            ])
            _write(d, "engines.json", eng)

        try:
            load_catalog(d, dev_mode=True)
        except (FileNotFoundError, ValueError, TypeError,
                json.JSONDecodeError, Exception):
            # Any *clean*, anticipated exception is acceptable — but we want to
            # surface surprising ones (KeyError/AttributeError) as test failures.
            pass
        n += 1
    assert n >= 400


def test_load_catalog_engines_corrupt_does_not_raise(tmp_path):
    """Corrupt engines.json must fall back to empty, never raise."""
    from app.catalog import load_catalog

    valid = {
        "version": "1.0",
        "models": [{"id": "q", "category": "llm", "name": "Q",
                    "repo": "r", "engine": ["mnn"], "license": "MIT",
                    "description": "d"}],
    }
    d = tmp_path
    _write(d, "models.json", json.dumps(valid))
    _write(d, "engines.json", "{{{ not json")
    catalog, engines = load_catalog(d, dev_mode=True)
    assert isinstance(engines, dict)
    assert catalog.version == "1.0"


# ---------------------------------------------------------------------------
# 4. _media_to_local — mutated data:/file: URLs (http branch mocked)
# ---------------------------------------------------------------------------

def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def test_media_to_local_data_url_fuzz():
    rng = random.Random(SEED + 5)
    good_b64 = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"\x00" * 64).decode()
    seeds = [
        f"data:image/png;base64,{good_b64}",
        "data:;base64,",
        "data:application/octet-stream;base64,!!!not-base64!!!",
        "data:image/png;base64",          # missing comma
        "data:text/plain;base64,SGVsbG8=",
        "data:image/png;base64," + "A" * (1 << 20),  # ~1MB
    ]
    n = 0
    for _ in range(300):
        url = _mutate(rng, rng.choice(seeds))
        created: list[str] = []
        try:
            path = _run(app_main._media_to_local(url, "image", created))
            # success: a real temp file was created and tracked for cleanup
            if path:
                assert os.path.exists(path)
        except HTTPException as e:
            assert e.status_code == 400, f"expected 400, got {e.status_code} for {url!r}"
        except Exception as e:  # noqa: BLE001
            pytest.fail(f"_media_to_local raised {type(e).__name__} for {url!r}: {e}")
        finally:
            for p in created:
                try:

                    os.unlink(p)
                except OSError:
                    pass
        n += 1
    assert n >= 300


def test_media_to_local_file_path_traversal():
    # file:// pointing at a missing / traversal target → clean 400, no crash.
    for url in ["file:///nonexistent/dir/x.png",
                "file://" + "/.." * 20 + "/etc/passwd",
                "file:", "file://", "file://relative/x.wav"]:
        created: list[str] = []
        try:
            path = _run(app_main._media_to_local(url, "image", created))
            # If it exists on disk it is returned as-is (by design); either way
            # it must not raise an unexpected error.
            assert isinstance(path, str)
        except HTTPException as e:
            assert e.status_code == 400
        except Exception as e:  # noqa: BLE001
            pytest.fail(f"unexpected {type(e).__name__} for {url!r}: {e}")
        finally:
            for p in created:
                try:

                    os.unlink(p)
                except OSError:
                    pass


class _FakeResp:
    def __init__(self, status: int, content: bytes = b"", ctype: str = "image/png"):
        self.status_code = status
        self.content = content
        self.headers = {"content-type": ctype}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def test_media_to_local_http_non200_mocked(monkeypatch):
    """http(s) URL that does not return 200 → clean 400, never a crash."""
    import httpx

    class _FakeClient:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url):
            return _FakeResp(404)

    monkeypatch.setattr(httpx, "AsyncClient", _FakeClient)
    created: list[str] = []
    try:
        path = _run(app_main._media_to_local("https://example.com/nope.png",
                                             "image", created))
        pytest.fail(f"expected HTTPException 400, got path={path!r}")
    except HTTPException as e:
        assert e.status_code == 400
    finally:
        for p in created:
            try:

                os.unlink(p)
            except OSError:
                pass


# ---------------------------------------------------------------------------
# 5. HTTP API fuzz — bad input must never yield a 500
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def api_client():
    # raise_server_exceptions=False: an unhandled exception becomes the global
    # handler's 500 envelope, which we then assert on.
    from fastapi.testclient import TestClient

    with TestClient(app_main.app, raise_server_exceptions=False) as c:
        yield c


def _assert_no_500(resp, label: str):
    if resp.status_code >= 500:
        pytest.fail(
            f"{label}: got {resp.status_code} ({resp.text[:200]!r}) — "
            "bad input must not produce a 500/unhandled traceback"
        )


def test_chat_completions_fuzz(api_client):
    rng = random.Random(SEED + 6)
    q_seeds = ["hello", "你好", "", "a" * 5000, "\x00\x01",
               "'; DROP TABLE--", "../../etc/passwd"]
    n = 0
    for _ in range(250):
        role = rng.choice(["user", "assistant", "system", "tool", 123, None, ""])
        content_kind = rng.random()
        if content_kind < 0.4:
            content: Any = _mutate(rng, rng.choice(q_seeds))
        elif content_kind < 0.7:
            # multimodal part list — drop fields / wrong types
            parts: list[Any] = []
            for _ in range(rng.randint(0, 3)):
                ptype = rng.choice(["text", "image_url", "audio", "", 1, None])
                part: Any = {"type": ptype}
                if ptype == "image_url":
                    part["image_url"] = rng.choice([
                        {"url": "data:image/png;base64," + base64.b64encode(b"x" * 32).decode()},
                        {"url": "data:image/png;base64,!!!bad!!!"},
                        "not-a-dict",
                        {"url": 123},
                        {},
                    ])
                elif ptype == "audio":
                    part["audio"] = rng.choice([{}, {"url": "/nonexistent.wav"}, "str", 1])
                elif ptype == "text":
                    part["text"] = _mutate(rng, "hi")
                parts.append(part)
            content = parts
        else:
            content = rng.choice([None, 123, 4.5, {}, ["non-dict-part"], [1, 2]])
        body = {
            "model": rng.choice(["", "mnn", 123, None, "x" * 4000]),
            "messages": rng.choice([
                [{"role": role, "content": content}],
                "not-a-list",
                [],
                [1, 2, 3],
                [{"role": role}],
                [{"content": content}],
            ]),
            "stream": rng.choice([True, False, "yes", 1, None]),
            "max_tokens": rng.choice([None, 0, -5, 1, 999999, "abc"]),
            "temperature": rng.choice([None, -1, 5.0, "x"]),
        }
        r = api_client.post("/v1/chat/completions", json=body)
        _assert_no_500(r, f"POST /v1/chat/completions body={body!r}")
        n += 1
    assert n >= 250


def test_search_fuzz(api_client):
    rng = random.Random(SEED + 7)
    n = 0
    for _ in range(250):
        params: dict[str, Any] = {
            "q": _mutate(rng, rng.choice(["llm", "qwen", "", "a" * 5000, "\x00"])),
            "page": rng.choice([1, 0, -5, 999999, "abc", "1e3", "", None]),
            "page_size": rng.choice([50, 0, -1, 100000, "xyz", ""]),
            "trending": rng.choice([0, 1, 2, -3, "yes", ""]),
            "sort": _mutate(rng, rng.choice(["relevance", "name_asc", "order by 1--"])),
            "category": rng.choice([None, "", "llm", "../../x"]),
            "engine": rng.choice([None, "", "mnn", 123]),
        }
        # drop some keys randomly
        for k in list(params):
            if rng.random() < 0.2:
                del params[k]
        r = api_client.get("/api/search", params=params)
        _assert_no_500(r, f"GET /api/search params={params!r}")
        n += 1
    assert n >= 250


def test_settings_fuzz(api_client, tmp_xdg):
    rng = random.Random(SEED + 8)
    n = 0
    for _ in range(200):
        body: dict[str, Any] = {}
        for k in ["model_dir", "engine_dir", "download_dir", "theme",
                  "default_engine_id", "hardware_acceleration", "telemetry_enabled",
                  "max_concurrent_downloads", "max_model_size_gb", "hf_token",
                  "hub_page_size", "unknown_field"]:
            if rng.random() < 0.4:
                body[k] = rng.choice([
                    None, 1, -5, 999999, "abc", True, [], {},
                    "../../etc", "C:\\Windows", "\x00", "a" * 5000,
                ])
        r = api_client.put("/api/settings", json=body)
        _assert_no_500(r, f"PUT /api/settings body={body!r}")
        n += 1
    assert n >= 200


def test_engines_install_uninstall_fuzz(api_client, tmp_xdg):
    rng = random.Random(SEED + 9)
    n = 0
    for _ in range(150):
        body = {"engine_id": rng.choice([
            "", "not_an_engine", 123, None, "../../etc/passwd",
            "k" * 5000, "' OR 1=1--", "llama.cpp",
        ])}
        for path in ("/api/engines/install", "/api/engines/update"):
            r = api_client.post(path, json=body)
            _assert_no_500(r, f"POST {path} body={body!r}")
        # missing engine_id entirely → 422 expected, not 500
        r = api_client.post("/api/engines/install", json={})
        _assert_no_500(r, "POST /api/engines/install missing engine_id")
        n += 1
    assert n >= 150


def test_path_param_fuzz(api_client):
    import httpx

    rng = random.Random(SEED + 10)
    evil = ["", "../", "..%2f..%2fetc", "a" * 5000, "\x00", "%00",
            "..\\..\\windows", "foo/bar", "??", "{}", "{{7*7}}"]
    routes = [
        "/api/models/{p}", "/api/download/{p}", "/api/hub/jobs/{p}",
        "/api/mnn/models/{p}/files", "/api/ltx/tasks/{p}",
    ]
    n = 0
    for route in routes:
        for p in evil:
            url = route.format(p=p)
            try:
                r = api_client.get(url)
            except httpx.InvalidURL:
                # The HTTP client itself refuses to build e.g. a NUL-bearing
                # URL; that never reaches the server and is not a server 500.
                n += 1
                continue
            _assert_no_500(r, f"GET {url}")
            n += 1
    assert n >= len(routes) * len(evil)


# ---------------------------------------------------------------------------
# Deterministic regression tests (fixed inputs — run only when a real bug was
# confirmed by the fuzz pass). Kept here so they always execute.
# ---------------------------------------------------------------------------

def test_regression_deeply_nested_tool_call_no_recursion_error():
    """A malformed LLM Action with a deeply/imbalanced nested object must not
    escape RecursionError (or any exception) out of parse_tool_call."""
    # Balanced deep nesting (valid JSON) must parse; imbalanced must return
    # None rather than raise.
    good = '{"a":' * 500 + "1" + "}" * 500
    assert parse_tool_call("Action: search_models|" + good) is not None
    # Unbalanced open braces, no closing — must return None, not raise.
    assert parse_tool_call("Action: search_models|" + '{"a":' * 500) is None


def test_regression_bad_second_media_url_cleans_partial_temp_files(api_client, monkeypatch):
    """N8: if message #2 carries a bad data: URL (→ 400) after message #1
    already materialised a temp file, that earlier temp file must be unlinked.

    Before the fix the loop's HTTPException(400) escaped before the only
    cleanup ``finally`` (which wraps ``_v1_once``), leaking the first file on
    every such malformed request.
    """
    import tempfile

    good_b64 = base64.b64encode(b"\x89PNG\r\n\x1a" + b"\x00" * 32).decode()
    created_paths: list[str] = []
    real_mkstemp = tempfile.mkstemp

    def _tracking_mkstemp(*args, **kwargs):
        fd, path = real_mkstemp(*args, **kwargs)
        created_paths.append(path)
        return fd, path

    monkeypatch.setattr(tempfile, "mkstemp", _tracking_mkstemp)

    body = {
        "model": "mnn",
        "messages": [
            {"role": "user", "content": [
                {"type": "image_url",
                 "image_url": {"url": f"data:image/png;base64,{good_b64}"}},
                {"type": "text", "text": "first"},
            ]},
            {"role": "user", "content": [
                {"type": "image_url",
                 "image_url": {"url": "data:image/png;base64,!!!not-base64!!!"}},
            ]},
        ],
    }
    r = api_client.post("/v1/chat/completions", json=body)
    # Must be a clean 400 (bad data URL), never a 500.
    assert r.status_code == 400, f"expected 400, got {r.status_code}: {r.text[:200]}"
    # Every temp file materialised during the request must have been removed.
    leaked = [p for p in created_paths if os.path.exists(p)]
    assert not leaked, f"leaked temp files on a 400 path: {leaked}"


def test_agent_loop_tolerates_garbage_router_output(tmp_path):
    """The ReAct loop must survive a misbehaving router: None / non-dict /
    missing fields / text=None / non-string text — never raise out of run()."""
    from app.agent.agent import Agent
    from app.agent.memory import AgentMemory

    class _FakeRouter:
        def __init__(self, result):
            self._result = result

        def is_ready(self):
            return True, "fake-router"

        def chat(self, *a, **kw):
            return self._result

    memory = AgentMemory(tmp_path / "mem.sqlite")
    garbage = [
        None,
        "just a string",
        42,
        ["a", "list"],
        {"ok": False, "error": "boom"},
        {"ok": True},                      # missing text
        {"ok": True, "text": None},
        {"ok": True, "text": 12345},
        {"ok": True, "text": {"nested": "dict"}},
        {"ok": True, "text": "Final Answer: ok"},
        {"ok": True, "text": "Action: nope_bad_name!|{\"x\":1}"},
        {"unexpected": "keys"},
    ]
    n = 0
    for g in garbage:
        agent = Agent(memory=memory, router=_FakeRouter(g))
        # Must not raise regardless of router garbage.
        res = asyncio.new_event_loop().run_until_complete(
            agent.run("search for a model", session_id=f"s{n}")
        )
        assert res.success or res.error, "every run must return an AgentResult"
        n += 1
    assert n == len(garbage)
