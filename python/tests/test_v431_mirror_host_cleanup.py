"""Regression: non-resolving phantom mirror hosts removed from active paths.

The phantom hosts ``hf-mirror.us`` / ``hf-cn-mirror.com`` /
``huggingface.dl.in.tel`` fail DNS (verified 2026-10). They used to appear in:
  * ``mnn_catalog._MIRRORS`` — an *active* fallback list, where each dead host
    added a connection-timeout delay on every MNN operation;
  * ``catalog.DEFAULT_MODEL_HOSTS`` — the positive allowlist that validates
    catalog sources, which wrongly blessed non-resolving hosts.

They remain registered as *disabled* presets in ``sources_registry`` (for
recognition / migration), which is intentional and not asserted here.
"""
from __future__ import annotations

from app.catalog import DEFAULT_MODEL_HOSTS
from app.mnn_catalog import _MIRRORS

PHANTOM_HOSTS = (
    "hf-mirror.us",
    "hf-cn-mirror.com",
    "huggingface.dl.in.tel",
)


def test_mnn_active_mirrors_have_no_phantom_host():
    for prefix in _MIRRORS:
        for host in PHANTOM_HOSTS:
            assert host not in prefix, f"phantom host {host} still tried in _MIRRORS"


def test_mnn_active_mirrors_keep_known_good():
    joined = "\n".join(_MIRRORS)
    assert "https://hf-mirror.com" in joined
    assert "https://huggingface.co" in joined


def test_model_host_allowlist_has_no_phantom_host():
    for host in PHANTOM_HOSTS:
        assert host not in DEFAULT_MODEL_HOSTS, f"phantom host {host} still allowlisted"


def test_model_host_allowlist_keeps_known_good():
    assert "hf-mirror.com" in DEFAULT_MODEL_HOSTS
    assert "huggingface.co" in DEFAULT_MODEL_HOSTS
    assert "modelscope.cn" in DEFAULT_MODEL_HOSTS
