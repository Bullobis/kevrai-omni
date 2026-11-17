"""v4.18.2 regression: piper voice downloads must send the product UA.

hf-mirror.com returns 403 for the default Python-urllib UA (verified live
2026-11-17). On CN networks the HF main site is unreachable, so the bare
urlopen made EVERY voice download fail with "主站与镜像均不可达".
"""
from __future__ import annotations

import io
from pathlib import Path


def test_piper_download_sends_product_ua(tmp_path: Path, monkeypatch):
    from app import USER_AGENT, piper_runtime

    captured = {}

    class _Resp(io.BytesIO):
        def __enter__(self):
            return self
        def __exit__(self, *a):
            self.close()
            return False

    def _fake_urlopen(req, timeout=None):
        captured["ua"] = req.get_header("User-agent")
        captured["url"] = req.full_url
        return _Resp(b"{}")

    monkeypatch.setattr(piper_runtime.urllib.request, "urlopen", _fake_urlopen)
    dest = tmp_path / "v.onnx.json"
    piper_runtime._download("https://hf-mirror.com/rhasspy/piper-voices/resolve/main/x.json", dest)
    assert captured["ua"] == USER_AGENT
    assert dest.read_bytes() == b"{}"
