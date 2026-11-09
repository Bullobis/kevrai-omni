"""TTS 引擎目录 bootstrap 回归测试。

「引擎页装完 → 运行时 import 不到」是本地推理应用最常见、也最伤用户的
一类缺陷：安装成功、状态变绿，真正合成时却报「未安装」。这里用假包把
Kokoro / Chatterbox / Ming 三条路径钉死。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import ming_tts_runtime as ming  # noqa: E402
from app import piper_runtime as prt  # noqa: E402


def _cleanup(names: list[str], paths: list[str]) -> None:
    for name in names:
        sys.modules.pop(name, None)
    for p in paths:
        while p in sys.path:
            sys.path.remove(p)


def test_module_available_sees_engine_dir(tmp_path):
    target = tmp_path / "engines" / "pip-kokoro-engine"
    target.mkdir(parents=True)
    (target / "fakekokoro.py").write_text("VALUE = 1\n")
    try:
        assert prt._module_available("fakekokoro", tmp_path) is True
    finally:
        _cleanup(["fakekokoro"], [str(target)])


def test_kokoro_synthesize_uses_engine_dir(tmp_path):
    target = tmp_path / "engines" / "pip-kokoro-engine"
    target.mkdir(parents=True)
    (target / "kokoro.py").write_text(
        "import numpy as np\n"
        "CALLS = {}\n"
        "class KPipeline:\n"
        "    def __init__(self, lang_code):\n"
        "        CALLS['lang'] = lang_code\n"
        "    def __call__(self, text, voice=None, **kw):\n"
        "        CALLS['voice'] = voice\n"
        "        yield '', '', np.zeros(2400, dtype='float32')\n"
    )
    try:
        mgr = prt.PiperManager(tmp_path)
        res = mgr.synthesize_kokoro("zf_xiaobei", "你好，本地语音")
        assert res["sample_rate"] == prt.KOKORO_SAMPLE_RATE
        assert res["engine"] == "kokoro"
        assert Path(res["path"]).exists()
        assert Path(res["path"]).stat().st_size > 44
    finally:
        _cleanup(["kokoro"], [str(target)])


def test_capabilities_reports_engine_dir_kokoro(tmp_path):
    for name, module in (
        ("pip-kokoro-engine", "kokoro"),
        ("pip-chatterbox", "chatterbox"),
    ):
        d = tmp_path / "engines" / name
        d.mkdir(parents=True)
        if module == "chatterbox":
            (d / "chatterbox").mkdir()
            (d / "chatterbox" / "__init__.py").write_text("")
            (d / "chatterbox" / "tts.py").write_text("class ChatterboxTTS: pass\n")
        else:
            (d / "kokoro.py").write_text("class KPipeline: pass\n")
    try:
        caps = prt.capabilities(tmp_path)
        assert caps["kokoro"]["installed"] is True
        assert caps["chatterbox"]["installed"] is True
    finally:
        _cleanup(["kokoro", "chatterbox", "chatterbox.tts"],
                 [str(tmp_path / "engines" / "pip-kokoro-engine"),
                  str(tmp_path / "engines" / "pip-chatterbox")])


def test_ming_uses_engine_dir(tmp_path, monkeypatch):
    target = tmp_path / "engines" / "pip-ming-tts"
    target.mkdir(parents=True)
    (target / "ming_tts_mnn.py").write_text(
        "CALLS = {}\n"
        "class MingTTSMNN:\n"
        "    @classmethod\n"
        "    def from_pretrained(cls, precision, source=None, cache_dir=None, quiet=True):\n"
        "        CALLS['precision'] = precision\n"
        "        CALLS['cache_dir'] = cache_dir\n"
        "        return cls()\n"
        "    def synthesize(self, text, instruction=None, seed=0):\n"
        "        return [0.0, 0.25, -0.25, 0.0]*100\n"
    )
    monkeypatch.setattr(ming._MingSingleton, "model", None, raising=False)
    monkeypatch.setattr(ming._MingSingleton, "precision", None, raising=False)
    try:
        data = ming.synthesize("测试", data_root=tmp_path, precision="int8")
        assert data[:4] == b"RIFF"
        mod = sys.modules["ming_tts_mnn"]
        assert mod.CALLS["precision"] == "int8"
            # cache_dir 必须落在 data_root/tts/ming 下
        assert str(tmp_path / "tts" / "ming") == mod.CALLS["cache_dir"]
    finally:
        ming._MingSingleton.model = None
        ming._MingSingleton.precision = None
        _cleanup(["ming_tts_mnn"], [str(target)])
