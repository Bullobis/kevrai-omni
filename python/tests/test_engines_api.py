"""Regression tests for engine uninstall route + MNN cancel final state."""

from __future__ import annotations

from pathlib import Path


def test_engines_uninstall_route_removes_and_404s(hub_client, monkeypatch):
    em = hub_client.app.state.engine_manager
    calls: list[str] = []

    def fake_uninstall(engine_id: str) -> bool:
        calls.append(engine_id)
        return engine_id == "mnn"

    monkeypatch.setattr(em, "uninstall", fake_uninstall)

    r = hub_client.post("/api/engines/uninstall", json={"engine_id": "mnn"})
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True

    r = hub_client.post("/api/engines/uninstall", json={"engine_id": "missing"})
    assert r.status_code == 404

    r = hub_client.post("/api/engines/uninstall", json={"engine_id": "../bad"})
    assert r.status_code == 400
    assert calls == ["mnn", "missing"]


def test_mnn_cancel_during_final_file_is_not_overwritten_as_done(tmp_path, monkeypatch):
    """Cancelling in the last chunk must leave status=cancelled, not done."""
    from app import main as app_main

    app_main._MNN_DL.update({
        "active": False, "status": "idle", "cancel": False,
        "files_done": 0, "files_total": 0, "bytes_done": 0,
        "current_file": "", "error": "",
    })
    monkeypatch.setattr(
        app_main.mnn_market, "list_mnn_files",
        lambda repo: [{"path": "config.json", "size": 2, "url": "http://x/a"},
                      {"path": "model.safetensors", "size": 2, "url": "http://x/b"}],
    )

    def fake_one_file(f, dest: Path) -> None:
        # Simulate the user clicking cancel while the final file is streaming.
        with app_main._MNN_DL_LOCK:
            app_main._MNN_DL["cancel"] = True

    monkeypatch.setattr(app_main, "_mnn_download_one_file", fake_one_file)

    app_main._mnn_download_worker({"id": "demo", "repo": "o/r", "name": "Demo"}, tmp_path)
    st = app_main._mnn_dl_state()
    assert st["status"] == "cancelled"
    assert st["active"] is False
