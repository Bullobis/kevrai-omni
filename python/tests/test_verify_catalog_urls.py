"""Tests for scripts/verify_catalog_urls.py (offline — no network).

The single most important behaviour under test: **403 must never be reported
as a broken address**. Sandboxes and CI boxes routinely 403 the *entire*
huggingface.co / modelscope.cn domain, and a naive checker would flag hundreds
of perfectly good URLs as dead.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

# tests/ 在 python/ 下，仓库根还要再上一级：parents = [tests, python, <root>]
_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "verify_catalog_urls.py"
_spec = importlib.util.spec_from_file_location("verify_catalog_urls", _SCRIPT)
assert _spec and _spec.loader
v = importlib.util.module_from_spec(_spec)
sys.modules["verify_catalog_urls"] = v
_spec.loader.exec_module(v)


# ---------------------------------------------------------------------------
# status classification
# ---------------------------------------------------------------------------

def test_200_is_ok():
    assert v.classify(200, "") == v.OK


def test_404_is_missing():
    assert v.classify(404, "") == v.MISSING


@pytest.mark.parametrize("code", [401, 403])
def test_auth_codes_are_gated_not_missing(code):
    """核心回归：401/403 是授权或反爬，绝不是地址错误。"""
    assert v.classify(code, "") == v.GATED
    assert v.classify(code, "") != v.MISSING


def test_none_code_is_unreachable():
    assert v.classify(None, "timeout") == v.UNREACHABLE


@pytest.mark.parametrize("code", [500, 502, 503, 418])
def test_other_codes_are_unknown(code):
    assert v.classify(code, "") == v.UNKNOWN


def test_only_404_counts_as_broken():
    assert v.BAD == (v.MISSING,)
    assert v.GATED not in v.BAD
    assert v.UNREACHABLE not in v.BAD


# ---------------------------------------------------------------------------
# source detection
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("url,expected", [
    ("https://modelscope.cn/models/a/b/", "modelscope"),
    ("https://www.modelscope.cn/models/a/b", "modelscope"),
    ("https://huggingface.co/a/b", "huggingface"),
    ("https://hf-mirror.com/a/b", "hf-mirror"),
    ("https://example.com/a/b", "other"),
])
def test_source_of(url, expected):
    assert v.source_of(url) == expected


def test_source_of_empty_url():
    assert v.source_of("") == "other"


# ---------------------------------------------------------------------------
# collection
# ---------------------------------------------------------------------------

def _model(mid, sources, primary=""):
    return {"id": mid, "sources": list(sources), "primary_url": primary}


def test_collect_includes_sources():
    out = v.collect([_model("a", ["https://huggingface.co/a"])], False)
    assert ("a", "https://huggingface.co/a", "source") in out


def test_collect_adds_primary_when_absent():
    out = v.collect([_model("a", ["https://huggingface.co/a"],
                            "https://huggingface.co/a/primary")], False)
    urls = {u for _, u, _ in out}
    assert "https://huggingface.co/a/primary" in urls


def test_collect_does_not_duplicate_primary():
    u = "https://huggingface.co/a"
    out = v.collect([_model("a", [u], u)], False)
    assert len([1 for _, x, _ in out if x == u]) == 1


def test_collect_marks_primary_role():
    u = "https://huggingface.co/a"
    out = v.collect([_model("a", [u], u)], False)
    assert out[0][2] == "primary"


def test_only_missing_skips_models_with_modelscope():
    with_ms = _model("a", ["https://modelscope.cn/models/a/b"])
    without = _model("b", ["https://huggingface.co/b"])
    out = v.collect([with_ms, without], True)
    mids = {m for m, _, _ in out}
    assert mids == {"b"}


def test_only_missing_false_keeps_everything():
    with_ms = _model("a", ["https://modelscope.cn/models/a/b"])
    out = v.collect([with_ms], False)
    assert len(out) == 1


def test_collect_handles_missing_fields():
    assert v.collect([{"id": "x"}], False) == []


# ---------------------------------------------------------------------------
# probe: never raises, always classifies
# ---------------------------------------------------------------------------

def test_probe_network_failure_is_unreachable_not_missing(monkeypatch):
    """连不上 = 网络问题，绝不能报成地址不存在。"""
    def _boom(*a, **kw):
        raise OSError("DNS failure")

    monkeypatch.setattr(v.urllib.request, "urlopen", _boom)
    status, code, err, ms = v.probe("https://example.invalid/x", 1.0)
    assert status == v.UNREACHABLE
    assert code is None
    assert err == "OSError"
    assert ms >= 0


def test_probe_http_error_classifies(monkeypatch):
    import urllib.error

    def _raise(*a, **kw):
        raise urllib.error.HTTPError("u", 404, "Not Found", {}, None)

    monkeypatch.setattr(v.urllib.request, "urlopen", _raise)
    status, code, _, _ = v.probe("https://example.com/x", 1.0)
    assert status == v.MISSING
    assert code == 404


def test_probe_falls_back_to_get_on_405(monkeypatch):
    """HEAD 被禁（405）时应退回 GET，而不是直接判死。"""
    import urllib.error

    calls: list[str] = []

    class _Resp:
        status = 200

    def _fake(req, *a, **kw):
        calls.append(req.get_method())
        if req.get_method() == "HEAD":
            raise urllib.error.HTTPError("u", 405, "Method Not Allowed", {}, None)
        return _Resp()

    monkeypatch.setattr(v.urllib.request, "urlopen", _fake)
    status, code, _, _ = v.probe("https://example.com/x", 1.0)
    assert calls == ["HEAD", "GET"]
    assert status == v.OK


def test_probe_returns_unknown_when_both_methods_blocked(monkeypatch):
    import urllib.error

    def _raise(*a, **kw):
        raise urllib.error.HTTPError("u", 405, "nope", {}, None)

    monkeypatch.setattr(v.urllib.request, "urlopen", _raise)
    status, _, _, _ = v.probe("https://example.com/x", 1.0)
    assert status == v.UNKNOWN


# ---------------------------------------------------------------------------
# end-to-end main() on a synthetic catalog
# ---------------------------------------------------------------------------

def _write_catalog(tmp_path: Path, models: list[dict]) -> Path:
    p = tmp_path / "catalog" / "models.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"version": "test", "models": models}, ensure_ascii=False),
                 encoding="utf-8")
    return p


def test_main_all_gated_exits_zero(tmp_path, monkeypatch, capsys):
    """整站 403（沙箱常见）→ 退出码 0，不能报'有坏地址'。"""
    cat = _write_catalog(tmp_path, [
        _model("a", ["https://modelscope.cn/models/a/b"]),
        _model("b", ["https://modelscope.cn/models/c/d"]),
    ])
    monkeypatch.setattr(v, "probe", lambda url, t: (v.GATED, 403, "", 1.0))
    rc = v.main(["--catalog", str(cat), "--quiet"])
    assert rc == 0


def test_main_detects_404_and_exits_one(tmp_path, monkeypatch):
    cat = _write_catalog(tmp_path, [
        _model("dead", ["https://modelscope.cn/models/x/y"]),
    ])
    monkeypatch.setattr(v, "probe", lambda url, t: (v.MISSING, 404, "", 1.0))
    rc = v.main(["--catalog", str(cat), "--quiet"])
    assert rc == 1


def test_main_one_good_url_saves_the_model(tmp_path, monkeypatch):
    """只要有一个地址可用，就不算坏模型。"""
    cat = _write_catalog(tmp_path, [
        _model("a", ["https://huggingface.co/a", "https://modelscope.cn/models/a/b"]),
    ])

    def _fake(url, t):
        return (v.OK, 200, "", 1.0) if "huggingface" in url else (v.MISSING, 404, "", 1.0)

    monkeypatch.setattr(v, "probe", _fake)
    rc = v.main(["--catalog", str(cat), "--quiet"])
    assert rc == 0


def test_main_source_filter(tmp_path, monkeypatch):
    cat = _write_catalog(tmp_path, [
        _model("a", ["https://huggingface.co/a", "https://modelscope.cn/models/a/b"]),
    ])
    seen: list[str] = []

    def _fake(url, t):
        seen.append(url)
        return (v.OK, 200, "", 1.0)

    monkeypatch.setattr(v, "probe", _fake)
    v.main(["--catalog", str(cat), "--source", "modelscope", "--quiet"])
    assert all("modelscope" in u for u in seen)
    assert len(seen) == 1


def test_main_writes_json_and_md(tmp_path, monkeypatch):
    cat = _write_catalog(tmp_path, [_model("a", ["https://modelscope.cn/models/a/b"])])
    monkeypatch.setattr(v, "probe", lambda url, t: (v.OK, 200, "", 1.0))
    j, m = tmp_path / "r.json", tmp_path / "r.md"
    rc = v.main(["--catalog", str(cat), "--quiet", "--json", str(j), "--md", str(m)])
    assert rc == 0
    assert j.is_file()
    assert m.is_file()
    body = json.loads(j.read_text(encoding="utf-8"))
    assert body["counts"]["ok"] == 1
    assert body["broken_models"] == []
    assert "ok" in m.read_text(encoding="utf-8")


def test_main_empty_selection_is_noop(tmp_path, monkeypatch, capsys):
    cat = _write_catalog(tmp_path, [
        _model("a", ["https://modelscope.cn/models/a/b"]),
    ])
    rc = v.main(["--catalog", str(cat), "--source", "hf-mirror"])
    assert rc == 0
    assert "没有需要核验" in capsys.readouterr().out


def test_main_reports_exit_code_and_broken_list(tmp_path, monkeypatch, capsys):
    cat = _write_catalog(tmp_path, [_model("dead", ["https://modelscope.cn/models/x/y"])])
    monkeypatch.setattr(v, "probe", lambda url, t: (v.MISSING, 404, "", 1.0))
    rc = v.main(["--catalog", str(cat)])
    out = capsys.readouterr().out
    assert rc == 1
    assert "dead" in out
    assert "404" in out
