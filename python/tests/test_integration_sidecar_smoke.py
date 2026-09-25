"""Neuron4-Smoke：真实子进程 sidecar 启动集成冒烟测试。

与 ``test_smoke.py``（进程内 ``TestClient``）不同，本测试以**真实子进程**方式
拉起 ``python -m uvicorn app.main:app``，走完整 ASGI / HTTP 栈，验证：

  * 空闲端口选择（绑定端口 0）+ 健康轮询等待启动（30s 上限，0.5s 间隔）
  * ``GET /api/health`` 无需鉴权，且数据根被 ``XDG_DATA_HOME`` 隔离到 tmp
  * ``GET /api/models`` / ``/api/engines`` / ``/api/search`` / ``/api/agent/status``
    携带 Bearer 后返回稳定 JSON 结构（不加载模型也不崩）
  * 校验错误（``page=not-an-int``）返回结构化 4xx JSON，而非 500 崩溃
  * 未知路由返回 404 JSON
  * 无 Bearer → 401（证明真实子进程里的鉴权中间件生效，而非进程内桩）
  * ``finally`` 中 ``terminate()`` → 最多等 10s → 否则 ``kill()``，断言子进程被回收

设计约束：
  * 零新增依赖（uvicorn / fastapi / httpx 均已在 requirements 中）。
  * 零 token 入库：secret 是测试内固定假值，不读真实环境里的任何 token。
  * 默认在本环境运行；设 ``KEVRAI_RUN_INTEGRATION=0`` 可跳过（不碰网络、
    不装引擎、不下载模型——全部走本地 catalog 与临时数据根）。
  * Windows 兼容：``terminate()/kill()/wait(timeout=)`` 在 Python 跨平台；
    另设 ``APPDATA/LOCALAPPDATA`` 让 win32 数据根也落到 tmp（本环境为 Linux，
    仅做路径兼容，不实跑 Windows）。
"""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

# <repo>/python/ —— uvicorn 子进程的 cwd，保证 ``app`` 包可被 ``-m`` 导入。
_PYTHON_DIR = Path(__file__).resolve().parent.parent

# 默认开启；显式设为 0/false/no/off 时跳过（例如离线 CI 只想跑纯单测）。
_RUN_INTEGRATION = os.environ.get("KEVRAI_RUN_INTEGRATION", "1").strip().lower() not in (
    "0", "false", "no", "off",
)

pytestmark = pytest.mark.skipif(
    not _RUN_INTEGRATION,
    reason="set KEVRAI_RUN_INTEGRATION=1 to run the real-subprocess sidecar smoke test",
)


def _free_port() -> int:
    """Bind port 0 and let the kernel hand back a free loopback port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_for_health(
    base_url: str,
    proc: subprocess.Popen,
    log_path: Path,
    timeout_s: float = 30.0,
    interval_s: float = 0.5,
) -> dict:
    """Poll /api/health until 200; fail fast if the child exits early."""
    deadline = time.monotonic() + timeout_s
    last_err: Exception | None = None
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            tail = log_path.read_text(errors="replace")[-2000:] if log_path.exists() else ""
            raise RuntimeError(
                f"sidecar exited early (rc={proc.returncode}); log tail:\n{tail}"
            )
        try:
            r = httpx.get(base_url + "/api/health", timeout=2.0)
            if r.status_code == 200:
                return r.json()
        except Exception as exc:  # server not accepting connections yet
            last_err = exc
        time.sleep(interval_s)
    raise TimeoutError(
        f"sidecar did not become healthy within {timeout_s}s (last error={last_err!r})"
    )


def test_sidecar_real_subprocess_smoke(tmp_path: Path) -> None:
    # 固定假 secret：仅本子进程使用，绝不读/写真实 token。
    secret = "smoke-test-secret-not-a-real-token"
    port = _free_port()
    base_url = f"http://127.0.0.1:{port}"

    # 数据根隔离：Linux/macOS 走 XDG_DATA_HOME，Windows 走 APPDATA/LOCALAPPDATA。
    env = {
        **os.environ,
        "XDG_DATA_HOME": str(tmp_path),
        "XDG_CACHE_HOME": str(tmp_path / "cache"),
        "APPDATA": str(tmp_path),
        "LOCALAPPDATA": str(tmp_path),
        "KEVRAI_SIDECAR_SECRET": secret,
        "PYTHONUNBUFFERED": "1",
    }

    # 子进程日志落到文件（不用 PIPE，避免管道缓冲写满把 server 卡死）；
    # 失败时可 tail 这个文件定位。
    log_path = tmp_path / "sidecar.log"
    log_fh = open(log_path, "wb")
    cmd = [
        sys.executable, "-m", "uvicorn", "app.main:app",
        "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning",
    ]

    t0 = time.monotonic()
    proc = subprocess.Popen(
        cmd,
        cwd=str(_PYTHON_DIR),
        env=env,
        stdout=log_fh,
        stderr=subprocess.STDOUT,
    )
    startup_s: float | None = None
    shutdown_s: float | None = None
    try:
        health = _wait_for_health(base_url, proc, log_path)
        startup_s = time.monotonic() - t0

        auth = {"Authorization": f"Bearer {secret}"}
        with httpx.Client(base_url=base_url, timeout=10.0) as client:
            # 1) 健康端点：无需鉴权；数据根必须落在 tmp（证明隔离生效）。
            assert health["ok"] is True
            assert isinstance(health.get("version"), str) and health["version"]
            assert health.get("models_dir") and health.get("app_root")
            Path(health["models_dir"]).resolve().relative_to(tmp_path.resolve())

            # 2) 模型列表：带 Bearer。
            r = client.get("/api/models", headers=auth)
            assert r.status_code == 200, r.text
            body = r.json()
            assert isinstance(body.get("models"), list)
            assert isinstance(body.get("count"), int)
            assert body["count"] == len(body["models"])

            # 3) 引擎列表：带 Bearer，至少含 llama.cpp / mnn。
            r = client.get("/api/engines", headers=auth)
            assert r.status_code == 200, r.text
            engines = r.json()["engines"]
            assert isinstance(engines, list) and len(engines) >= 3
            engine_ids = {e["id"] for e in engines}
            assert {"llama.cpp", "mnn"} <= engine_ids

            # 4) 搜索：带 Bearer；结果可空但结构必须稳定。
            r = client.get("/api/search", params={"q": "llama"}, headers=auth)
            assert r.status_code == 200, r.text
            sbody = r.json()
            for key in ("query", "count", "items"):
                assert key in sbody, sbody
            assert isinstance(sbody["items"], list)

            # 5) Agent 状态：不加载模型也应返回稳定结构（rule_based）。
            r = client.get("/api/agent/status", headers=auth)
            assert r.status_code == 200, r.text
            ast = r.json()
            for key in ("llm_ready", "model_name", "tool_count", "mode"):
                assert key in ast, ast

            # 6) 校验错误：page 非整数 → 结构化 4xx JSON，绝不能 500。
            r = client.get("/api/search", params={"page": "not-an-int"}, headers=auth)
            assert 400 <= r.status_code < 500, r.text
            assert "application/json" in r.headers.get("content-type", "")
            assert "detail" in r.json()

            # 7) 未知路由 → 404 JSON。
            r = client.get("/api/__nope__/missing-route", headers=auth)
            assert r.status_code == 404, r.text
            assert "application/json" in r.headers.get("content-type", "")
            assert "detail" in r.json()

            # 8) 无 Bearer → 401（真实子进程里的鉴权中间件生效）。
            r = client.get("/api/models")
            assert r.status_code == 401, r.text
    finally:
        # 干净关闭：terminate → 最多等 10s → 仍存活则 kill。
        tshut = time.monotonic()
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
        shutdown_s = time.monotonic() - tshut
        log_fh.close()
        assert proc.poll() is not None, "sidecar subprocess was not reaped"

    # 运行耗时仅记录，不作为通过/失败阈值（供议会提案归档）。
    print(f"\n[sidecar-smoke] startup={startup_s:.2f}s shutdown={shutdown_s:.2f}s")
