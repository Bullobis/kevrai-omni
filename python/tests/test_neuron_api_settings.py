"""Regression: ``PUT /api/settings`` rejects out-of-Literal values with 400.

The patch loop used ``setattr``, which does not run pydantic validators by
default, so ``theme="rainbow"`` was silently accepted and persisted. Enabling
``validate_assignment`` on ``Settings`` + translating the resulting
``ValidationError`` into HTTP 400 closes the gap.
"""
from __future__ import annotations

import pytest

from app.settings import Settings


def test_settings_put_invalid_theme_returns_400(hub_client):
    r = hub_client.put("/api/settings", json={"theme": "rainbow"})
    assert r.status_code == 400, r.text
    assert "detail" in r.json()


def test_settings_put_invalid_hardware_accel_returns_400(hub_client):
    r = hub_client.put("/api/settings", json={"hardware_acceleration": "quantum"})
    assert r.status_code == 400, r.text


def test_settings_put_valid_theme_succeeds(hub_client):
    r = hub_client.put("/api/settings", json={"theme": "dark"})
    assert r.status_code == 200, r.text
    assert r.json()["theme"] == "dark"


def test_settings_setattr_rejects_invalid_literal():
    s = Settings()
    with pytest.raises(Exception):
        s.theme = "rainbow"  # must raise, not silently persist
