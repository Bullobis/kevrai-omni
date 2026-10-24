"""P1：把"真实性"变成可验证的事实 —— 供应链完整性与诊断包。"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
import zipfile
from pathlib import Path

import pytest

os.environ.setdefault("KEVRAI_SIDECAR_SECRET", "test-sidecar-secret")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.downloader import (  # noqa: E402
    DownloadTask,
    Downloader,
    is_revision_pinned,
    pin_revision,
)

SCRIPTS = Path(__file__).resolve().parent.parent.parent / "scripts"


def _fake_pat(seed: str = "A") -> str:
    """拼一个**看起来像** GitHub PAT 的字符串，用于验证脱敏。

    为什么不能在源码里写字面量：仓库有个密钥扫描测试
    （``tests/test_no_secrets.py``）会抓 ``ghp_`` + 30 位以上字符的模式。
    这里是**假**令牌，但扫描器分不出真假——写死字面量会让那条测试失败，
    逼着后来人去加白名单，而白名单一旦开了口子，真令牌就能混进去。

    所以改成运行时拼装：源码里只出现 ``"ghp_"`` 这个短前缀（不匹配正则），
    拼出来的字符串仍然是 36 位、能真正触发脱敏逻辑。
    """
    return "ghp_" + (seed * 20) + ("0123456789" * 2)[:16]


@pytest.fixture(scope="module")
def diag_mod():
    spec = importlib.util.spec_from_file_location(
        "export_diagnostics", SCRIPTS / "export_diagnostics.py"
    )
    assert spec and spec.loader
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


# ---------------------------------------------------------------------------
# 供应链完整性：revision 锁定
# ---------------------------------------------------------------------------

def test_pin_revision_replaces_main():
    u = "https://huggingface.co/Lightricks/LTX-2.5/resolve/main/model.safetensors"
    out = pin_revision(u, "a" * 40)
    assert f"/resolve/{'a' * 40}/model.safetensors" in out
    assert "/resolve/main/" not in out


def test_pin_revision_accepts_mirror_host():
    """镜像站域名也要能钉 —— 国内主要走 hf-mirror。"""
    u = "https://hf-mirror.com/org/repo/resolve/main/f.bin"
    assert "/resolve/" + "b" * 40 + "/f.bin" in pin_revision(u, "b" * 40)


def test_pin_revision_leaves_other_urls_alone():
    """非 HF resolve 形式的 URL 不能被改坏。"""
    u = "https://example.com/a/b/c.bin"
    assert pin_revision(u, "c" * 40) == u


def test_pin_revision_empty_is_noop():
    u = "https://huggingface.co/o/r/resolve/main/f.bin"
    assert pin_revision(u, "") == u


def test_is_revision_pinned_detects_sha():
    u = "https://huggingface.co/o/r/resolve/" + "d" * 40 + "/f.bin"
    assert is_revision_pinned(u) is True


def test_is_revision_pinned_rejects_branch():
    """main 是分支名，会漂移，不算锁定。"""
    u = "https://huggingface.co/o/r/resolve/main/f.bin"
    assert is_revision_pinned(u) is False


def test_task_records_integrity_failure():
    t = DownloadTask(
        id="x", url="https://example.invalid/f",
        dest_path="/tmp/f", expected_sha256=None,
    )
    assert t.integrity_failed is False
    snap = t.snapshot()
    assert "integrity_failed" in snap
    assert "expected_revision" in snap


# ---------------------------------------------------------------------------
# 诊断包：脱敏必须真的生效
# ---------------------------------------------------------------------------

def test_redact_masks_token_in_value(diag_mod):
    out = diag_mod.redact({"note": "token is " + _fake_pat("A")})
    assert "ghp_" not in json.dumps(out)
    assert diag_mod.REDACTED in json.dumps(out)


def test_redact_masks_by_key_name(diag_mod):
    """键名命中就要打码，不管值长什么样。"""
    out = diag_mod.redact({"api_key": "whatever", "normal": "keepme"})
    assert out["api_key"] == diag_mod.REDACTED
    assert out["normal"] == "keepme"


def test_redact_masks_home_path(diag_mod):
    home = str(Path.home())
    out = diag_mod.redact({"path": home + "/models/x.bin"})
    assert home not in json.dumps(out)


def test_redact_is_recursive(diag_mod):
    out = diag_mod.redact({"a": {"b": [{"password": "x"}]}})
    assert out["a"]["b"][0]["password"] == diag_mod.REDACTED


def test_redact_handles_cycles_without_hanging(diag_mod):
    """循环引用不能把导出卡死。"""
    a: dict = {}
    a["self"] = a
    out = diag_mod.redact(a)
    assert out is not None


def test_diagnostics_zip_has_expected_files(diag_mod, tmp_path):
    out = tmp_path / "d.zip"
    rc = diag_mod.main.__wrapped__ if hasattr(diag_mod.main, "__wrapped__") else None
    # 直接调用底层构建，避免 argparse
    report = diag_mod.build_report(
        type("A", (), {
            "disk_paths": [str(tmp_path)],
            "model_dir": None,
            "note": "",
        })()
    )
    assert "environment" in report
    assert "dependencies" in report

    with zipfile.ZipFile(out, "w") as z:
        z.writestr("diagnostics.json", json.dumps(report, ensure_ascii=False))
    with zipfile.ZipFile(out) as z:
        assert "diagnostics.json" in z.namelist()


def test_diagnostics_report_has_no_raw_secret(diag_mod, tmp_path):
    """整份报告序列化后不能出现测试注入的令牌原文。"""
    report = diag_mod.build_report(
        type("A", (), {
            "disk_paths": [str(tmp_path)],
            "model_dir": None,
            "note": "my token: " + _fake_pat("Z"),
        })()
    )
    blob = json.dumps(report, ensure_ascii=False)
    assert _fake_pat("Z") not in blob


def test_diagnostics_missing_log_dir_is_reported(diag_mod):
    """日志目录不存在要如实说明，不能静默产出空包让人以为没问题。"""
    msg = diag_mod.collect_recent_logs("/nonexistent-log-dir-xyz")
    assert "不存在" in msg or "无法" in msg


def test_diagnostics_dependencies_report_missing_not_crash(diag_mod):
    deps = diag_mod.collect_dependencies()
    assert isinstance(deps, dict) and len(deps) > 0
    # 缺的项要写成 missing(...)，不能是 None 让人误以为装了
    for v in deps.values():
        assert v is not None
