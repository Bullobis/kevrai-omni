"""Regression: HF mirror host set only contains verified, resolving hosts."""
from __future__ import annotations

from app.sources import HF_MIRROR_HOSTS, expand_mirror_candidates

# Hosts that were listed but do not resolve (verified via DNS/HTTP).
PHANTOM_HOSTS = {
    "hf-mirror.us",
    "huggingface.dl.in.tel",
    "hf-cn-mirror.com",
}


def test_phantom_mirror_hosts_removed():
    assert not (HF_MIRROR_HOSTS & PHANTOM_HOSTS)


def test_known_good_mirrors_present():
    assert "huggingface.co" in HF_MIRROR_HOSTS
    assert "hf-mirror.com" in HF_MIRROR_HOSTS


def test_mirror_expansion_still_works_after_cleanup():
    primary = "https://huggingface.co/owner/model/resolve/main/file.gguf"
    out = expand_mirror_candidates(primary, ["https://hf-mirror.com"])
    assert out[0] == primary
    assert any(u.startswith("https://hf-mirror.com/") for u in out)
    # no candidate references a phantom host
    assert not any(ph in u for u in out for ph in PHANTOM_HOSTS)
