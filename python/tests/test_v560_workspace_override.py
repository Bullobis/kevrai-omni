"""Tests for the explicit KEVRAI_DATA_ROOT workspace override."""
from __future__ import annotations

from pathlib import Path

from app.settings import default_data_root


def test_override_used_verbatim(monkeypatch, tmp_path):
    target = tmp_path / "myworkspace"
    monkeypatch.setenv("KEVRAI_DATA_ROOT", str(target))
    root = default_data_root()
    # Points directly at the workspace dir (no "KevraiOmni" suffix).
    assert root == target
    assert root.name == "myworkspace"


def test_override_expands_user(monkeypatch):
    monkeypatch.setenv("KEVRAI_DATA_ROOT", "~/ws-tilde")
    root = default_data_root()
    assert root == Path.home() / "ws-tilde"


def test_blank_override_falls_back(monkeypatch):
    monkeypatch.setenv("KEVRAI_DATA_ROOT", "   ")
    assert default_data_root().name == "KevraiOmni"


def test_override_precedence_over_xdg(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    forced = tmp_path / "forced"
    monkeypatch.setenv("KEVRAI_DATA_ROOT", str(forced))
    assert default_data_root() == forced
