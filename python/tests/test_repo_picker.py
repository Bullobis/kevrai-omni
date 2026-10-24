# python/tests/test_repo_picker.py — 仓库文件自动挑选。
#
# 背景：259 个模型里 165 个没有 gguf_repo，安装流程拿不到具体分片路径，
# 旧代码直接 400 "files required"，用户侧表现为"点了安装、权重从没下载"。
# 本测试守住补上的这一环。
"""Tests for app.repo_picker."""
from __future__ import annotations

from app.repo_picker import WEIGHT_EXTS, pick_repo_files, total_size_of


def _d(*pairs: tuple[str, int]) -> list[dict]:
    return [{"path": p, "size": s} for p, s in pairs]


# ---------------------------------------------------------------------------
# 基本挑选
# ---------------------------------------------------------------------------

def test_picks_weights_and_config():
    files = _d(
        ("config.json", 800),
        ("tokenizer.json", 1000),
        ("model.safetensors", 5_000_000),
        ("README.md", 3000),
    )
    out = pick_repo_files(files)
    assert "model.safetensors" in out
    assert "config.json" in out
    assert "tokenizer.json" in out
    # README 不是权重也不是必需配置
    assert "README.md" not in out


def test_shards_sorted_numerically():
    # 分片必须按索引排序，否则并发下载乱序落盘后拼接会错
    files = _d(
        ("model-00003-of-00003.safetensors", 100),
        ("model-00001-of-00003.safetensors", 100),
        ("model-00002-of-00003.safetensors", 100),
        ("model.safetensors.index.json", 50),
    )
    out = [p for p in pick_repo_files(files) if p.startswith("model-000")]
    assert out == [
        "model-00001-of-00003.safetensors",
        "model-00002-of-00003.safetensors",
        "model-00003-of-00003.safetensors",
    ]


def test_essential_before_weights():
    files = _d(("model.safetensors", 100), ("config.json", 10))
    out = pick_repo_files(files)
    assert out.index("config.json") < out.index("model.safetensors")


# ---------------------------------------------------------------------------
# 排除项
# ---------------------------------------------------------------------------

def test_skips_original_and_assets_dirs():
    files = _d(
        ("original/model.safetensors", 999),
        ("assets/logo.png", 999),
        ("model.safetensors", 100),
        ("sub/assets/thumb.png", 999),   # 嵌套也要能排除
    )
    out = pick_repo_files(files)
    assert out == ["model.safetensors"]


def test_skips_flax_tf_keras_variants():
    files = _d(
        ("flax_model.msgpack", 999),
        ("tf_model.h5", 999),
        ("rust_model.ot", 1),
        ("pytorch_model.bin", 999),
        ("model.safetensors", 100),
    )
    out = pick_repo_files(files)
    assert out == ["model.safetensors"]


def test_skips_training_artifacts():
    files = _d(
        ("optimizer.pt", 999),
        ("rng_state.pth", 999),
        ("trainer_state.json", 999),
        ("training_args.bin", 999),
        ("model.safetensors", 100),
    )
    assert pick_repo_files(files) == ["model.safetensors"]


# ---------------------------------------------------------------------------
# gguf 的特殊处理：不能一概排除
# ---------------------------------------------------------------------------

def test_gguf_dropped_when_other_format_present():
    """仓库本体是 safetensors 时，附带的 gguf 不下载（量化档由选择器另走）。"""
    files = _d(("model.safetensors", 100), ("model-Q4_K_M.gguf", 200))
    assert pick_repo_files(files) == ["model.safetensors"]


def test_gguf_kept_when_it_is_the_only_weight():
    """gguf 是唯一权重格式时必须保留 —— 排除掉就又变成"什么都不下载"。"""
    files = _d(("config.json", 10), ("model-Q4_K_M.gguf", 200))
    out = pick_repo_files(files)
    assert "model-Q4_K_M.gguf" in out


# ---------------------------------------------------------------------------
# 空输入 / 无效输入
# ---------------------------------------------------------------------------

def test_empty_listing():
    assert pick_repo_files([]) == []


def test_no_weights_returns_empty():
    """识别不出权重就返回空，绝不"下载整个仓库"（可能是几十 GB）。"""
    assert pick_repo_files(_d(("README.md", 10), (".gitattributes", 5))) == []


def test_accepts_bare_strings():
    assert pick_repo_files(["model.safetensors"]) == ["model.safetensors"]


def test_tolerates_junk_entries():
    out = pick_repo_files([None, 123, {}, {"path": ""}, "model.safetensors"])
    assert out == ["model.safetensors"]


def test_dedupes_preserving_order():
    files = _d(("model.safetensors", 100))
    assert pick_repo_files(files + files) == ["model.safetensors"]


def test_max_files_cap():
    files = _d(*[(f"w{i}.safetensors", 10) for i in range(300)])
    assert len(pick_repo_files(files, max_files=200)) == 200


# ---------------------------------------------------------------------------
# 体积统计
# ---------------------------------------------------------------------------

def test_total_size_of():
    files = _d(("a.safetensors", 100), ("b.safetensors", 200), ("c.md", 999))
    assert total_size_of(files, ["a.safetensors", "b.safetensors"]) == 300


def test_total_size_unknown_path_is_zero():
    assert total_size_of(_d(("a", 10)), ["nope"]) == 0


# ---------------------------------------------------------------------------
# 契约：扩展名集合
# ---------------------------------------------------------------------------

def test_weight_exts_cover_common_formats():
    for ext in (".safetensors", ".gguf", ".bin", ".onnx", ".mnn", ".pt"):
        assert ext in WEIGHT_EXTS
