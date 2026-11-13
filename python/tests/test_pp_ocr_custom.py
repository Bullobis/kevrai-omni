"""PP-OCRv6 det/rec pair wiring tests (no ONNX runtime needed)."""

from __future__ import annotations

from pathlib import Path

from app.ocr_runtime import OCRManager, _keys_file


def test_keys_file_extracts_character_dict(tmp_path):
    rec = tmp_path / "rec"
    rec.mkdir()
    (rec / "inference.yml").write_text("PostProcess:\n  character_dict:\n  - a\n  - b\n", encoding="utf-8")
    keys = _keys_file(rec)
    assert keys.read_text(encoding="utf-8") == "a\nb"


def test_custom_engine_passes_det_and_rec_paths(tmp_path, monkeypatch):
    det = tmp_path / "det"
    rec = tmp_path / "rec"
    det.mkdir()
    rec.mkdir()
    (rec / "inference.yml").write_text("PostProcess:\n  character_dict:\n  - a\n", encoding="utf-8")

    class FakeRapidOCR:
        def __init__(self, params):
            self.params = params

    class FakeModule:
        RapidOCR = FakeRapidOCR

    monkeypatch.setattr("app.ocr_runtime.import_rapidocr", lambda _root=None: FakeModule)
    mgr = OCRManager(tmp_path)
    engine = mgr._get_custom_engine(str(det), str(rec))
    assert engine.params["Det.model_path"].endswith("inference.onnx")
    assert engine.params["Rec.model_path"].endswith("inference.onnx")
    assert engine.params["Rec.rec_keys_path"].endswith(".txt")
    assert Path(engine.params["Rec.rec_keys_path"]).exists()
