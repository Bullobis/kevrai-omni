"""Model format converter for Kevrai Omni.

Some engines only accept a specific format.  The converter turns models
downloaded from HuggingFace (safetensors / ONNX / TorchScript) into a
format the target engine can load, e.g.:

    HF safetensors  ──llm-export──▶  MNN-LLM directory (config.json + bins)
    ONNX            ──MNNConvert──▶  *.mnn
    TorchScript     ──MNNConvert──▶  *.mnn

Toolchain sources (mirror-friendly, user principle: more sources = better):
  * llm-export : MNN official script at
                 alibaba/MNN → transformers/llm/export/llmexport.py
                 cloned lazily from the verified gitcode.com mirror.
  * MNNConvert : pymnn built-in converter (pip MNN) or a local binary
                 found on PATH.

Design:
  * Single-flight task registry (one convert at a time, like MNN download).
  * Cooperative cancellation via subprocess terminate + flag.
  * Every task keeps a structured log the UI can poll.
  * No fake results: if a tool is missing the task fails with a clear
    install hint instead of pretending to convert.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import shlex
import shutil
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable

log = logging.getLogger("kevrai.converter")

# ---------------------------------------------------------------------------
# Task state
# ---------------------------------------------------------------------------


class ConvertStatus(str, Enum):
    PENDING = "pending"
    PREPARING = "preparing"      # ensuring toolchain (clone llm-export, deps)
    RUNNING = "running"          # conversion subprocess active
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"


# MNN official repo mirrors — llm-export script source (verified reachable).
MNN_REPO_MIRRORS = (
    "https://gitcode.com/GitHub_Trending/mn/MNN.git",
    "https://gitcode.com/alibaba/MNN.git",
    "https://github.com/alibaba/MNN.git",
)

# llama.cpp official repo mirrors — convert_hf_to_gguf.py source.
LLAMACPP_REPO_MIRRORS = (
    "https://gitcode.com/GitHub_Trending/ggml-org/llama.cpp.git",
    "https://gitcode.com/ggml-org/llama.cpp.git",
    "https://github.com/ggml-org/llama.cpp.git",
)

# llm-export script lives here inside the MNN repo.
_LLM_EXPORT_REL = "transformers/llm/export/llmexport.py"

# Supported conversion kinds.
KIND_HF_TO_MNN = "hf-to-mnn-llm"       # safetensors/原始权重 → MNN-LLM
KIND_HF_TO_GGUF = "hf-to-gguf"         # safetensors → GGUF（llama.cpp / ollama）
KIND_HF_TO_ONNX = "hf-to-onnx"         # safetensors → ONNX（onnxruntime）
KIND_HF_TO_MLX = "hf-to-mlx"           # safetensors → MLX（Apple Silicon）
KIND_ONNX_TO_MNN = "onnx-to-mnn"       # ONNX → *.mnn
KIND_TORCH_TO_MNN = "torch-to-mnn"     # TorchScript → *.mnn
KIND_MNN_TO_JSON = "mnn-to-json"       # 调试：MNN 模型转 JSON 结构


@dataclass
class ConvertTask:
    id: str
    kind: str
    src: str
    dst: str
    status: ConvertStatus = ConvertStatus.PENDING
    progress: float = 0.0              # 0..1
    log_lines: list[str] = field(default_factory=list)
    error: str = ""
    created_at: float = field(default_factory=time.time)
    started_at: float = 0.0
    finished_at: float = 0.0
    proc: subprocess.Popen | None = None
    cancel_flag: bool = False
    options: dict[str, Any] = field(default_factory=dict)
    result: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Task registry (single-flight)
# ---------------------------------------------------------------------------

_LOCK = threading.Lock()
_TASKS: dict[str, ConvertTask] = {}
_ACTIVE: ConvertTask | None = None
_TOOLS_DIR: Path | None = None
_LLM_EXPORT_PY: Path | None = None


def configure_tools_dir(tools_dir: str | os.PathLike[str]) -> None:
    """Point the converter at a persistent tools cache directory."""
    global _TOOLS_DIR, _LLM_EXPORT_PY
    _TOOLS_DIR = Path(tools_dir)
    _TOOLS_DIR.mkdir(parents=True, exist_ok=True)
    _LLM_EXPORT_PY = _TOOLS_DIR / "MNN" / _LLM_EXPORT_REL


def _task_state(t: ConvertTask) -> dict[str, Any]:
    return {
        "id": t.id,
        "kind": t.kind,
        "src": t.src,
        "dst": t.dst,
        "status": t.status.value,
        "progress": round(t.progress, 3),
        "error": t.error,
        "log": list(t.log_lines[-200:]),
        "created_at": t.created_at,
        "started_at": t.started_at,
        "finished_at": t.finished_at,
        "result": t.result,
    }


def list_tasks() -> list[dict[str, Any]]:
    with _LOCK:
        return [_task_state(t) for t in _TASKS.values()]


def get_task(task_id: str) -> dict[str, Any] | None:
    with _LOCK:
        t = _TASKS.get(task_id)
        return _task_state(t) if t else None


def active_task() -> dict[str, Any] | None:
    with _LOCK:
        return _task_state(_ACTIVE) if _ACTIVE else None


def _append_log(t: ConvertTask, line: str) -> None:
    ts = time.strftime("%H:%M:%S")
    t.log_lines.append(f"[{ts}] {line}")
    log.info("convert[%s]: %s", t.id, line)


def cancel_task(task_id: str) -> bool:
    """Request cancellation. Returns True if a task matched."""
    with _LOCK:
        t = _TASKS.get(task_id)
        if t is None:
            return False
        t.cancel_flag = True
        if t.proc is not None and t.proc.poll() is None:
            try:
                t.proc.terminate()
            except Exception:  # noqa: BLE001
                pass
        _append_log(t, "取消请求已发送")
        return True


# ---------------------------------------------------------------------------
# Toolchain provisioning
# ---------------------------------------------------------------------------

def _run_subprocess(
    t: ConvertTask,
    cmd: list[str],
    *,
    cwd: str | os.PathLike[str] | None = None,
    env: dict[str, str] | None = None,
    tail: int = 60,
) -> tuple[int, str]:
    """Run a subprocess, streaming its output into the task log."""
    _append_log(t, "$ " + " ".join(cmd))
    proc = subprocess.Popen(
        cmd,
        cwd=cwd,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )
    t.proc = proc
    buffer: list[str] = []
    assert proc.stdout is not None
    for raw in proc.stdout:
        line = raw.rstrip("\n")
        if line.strip():
            _append_log(t, line.strip())
            buffer.append(line.strip())
            if len(buffer) > tail:
                buffer.pop(0)
        if t.cancel_flag and proc.poll() is None:
            proc.terminate()
    code = proc.wait()
    t.proc = None
    return code, "\n".join(buffer)


def _find_mnnconvert() -> str | None:
    """Locate MNNConvert: PATH binary or pymnn python converter module."""
    # 1) PATH binary (MNN source build)
    which = shutil.which("MNNConvert")
    if which:
        return which
    # 2) pymnn wheel ships a python converter (MNN.tools.converter)
    try:
        import MNN  # noqa: F401
        for mod in ("MNN.tools.converter", "MNN.converter"):
            try:
                __import__(mod, fromlist=["main"])
                return f"{sys_executable()} -m {mod}"
            except Exception:  # noqa: BLE001
                continue
    except Exception:  # noqa: BLE001
        pass
    return None


def _is_llm_export_ready() -> bool:
    return bool(_LLM_EXPORT_PY and _LLM_EXPORT_PY.is_file())


def _llm_export_cmd(t: ConvertTask) -> list[str]:
    """优先使用官方独立 pip 包 llmexport（wangzhaode/llm-export，参数与仓库脚本一致），
    fallback 到 MNN 仓库内 llmexport.py。官方 master 文档命令：
      python llmexport.py --path <dir> --export mnn [--quant_bit 4 --quant_block 128]"""
    if shutil.which("llmexport"):
        return ["llmexport"]
    try:
        import importlib.util
        if importlib.util.find_spec("llmexport") is not None:
            return [sys_executable(), "-m", "llmexport"]
    except Exception:  # noqa: BLE001
        pass
    script = _ensure_llm_export(t)
    return [sys_executable(), str(script)]


def _ensure_llm_export(t: ConvertTask) -> Path:
    """Clone (sparse) MNN repo so llmexport.py is available. Mirrors in order."""
    if _is_llm_export_ready():
        return _LLM_EXPORT_PY  # type: ignore[return-value]
    if _TOOLS_DIR is None:
        raise RuntimeError("converter 未初始化（configure_tools_dir 未调用）")

    repo_dir = _TOOLS_DIR / "MNN"
    _append_log(t, "拉取 MNN 官方转换脚本（llm-export）…")
    if not (repo_dir / ".git").is_dir():
        repo_dir.mkdir(parents=True, exist_ok=True)
        last_err = ""
        for mirror in MNN_REPO_MIRRORS:
            if t.cancel_flag:
                raise RuntimeError("已取消")
            _append_log(t, f"尝试镜像: {mirror}")
            try:
                _run_subprocess(
                    t,
                    ["git", "clone", "--depth", "1", "--filter", "blob:none",
                     "--sparse", mirror, str(repo_dir)],
                    cwd=_TOOLS_DIR,
                )
                if (repo_dir / ".git").is_dir():
                    break
            except Exception as e:  # noqa: BLE001
                last_err = str(e)
                shutil.rmtree(repo_dir, ignore_errors=True)
                repo_dir.mkdir(parents=True, exist_ok=True)
        else:
            raise RuntimeError(f"MNN 仓库克隆失败：{last_err or '所有镜像不可达'}")

    # sparse-checkout the export dir only
    rel_dir = str(Path(_LLM_EXPORT_REL).parent)
    _run_subprocess(t, ["git", "-C", str(repo_dir), "sparse-checkout", "set", rel_dir])
    if not _is_llm_export_ready():
        raise RuntimeError(
            f"llmexport.py 未找到（预期 {_LLM_EXPORT_REL}）。"
            "请检查网络后重试，或手动将 alibaba/MNN 的 transformers/llm/export 放入 "
            f"{_TOOLS_DIR / 'MNN'}"
        )
    _append_log(t, "llm-export 脚本就绪")
    return _LLM_EXPORT_PY  # type: ignore[return-value]


def _ensure_python_deps(t: ConvertTask, deps: list[str]) -> None:
    """pip install missing python deps (torch/transformers/safetensors)."""
    import importlib.util

    missing = [d for d in deps if importlib.util.find_spec(d.replace("-", "_").split(">=")[0]) is None]
    if not missing:
        return
    _append_log(t, f"安装转换依赖: {', '.join(missing)}")
    code, _ = _run_subprocess(
        t,
        [sys_executable(), "-m", "pip", "install", "--quiet", *missing],
    )
    if code != 0:
        raise RuntimeError(f"依赖安装失败: {', '.join(missing)}")


def sys_executable() -> str:
    return os.environ.get("KEVRAI_PYTHON") or sys.executable


# ---------------------------------------------------------------------------
# Conversion workers
# ---------------------------------------------------------------------------

def _detect_hf_arch(src: Path) -> str | None:
    """Best-effort architecture detection for llm-export --type."""
    cfg = src / "config.json"
    if not cfg.is_file():
        return None
    try:
        import json
        data = json.loads(cfg.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None
    mt = str(data.get("model_type", "")).lower()
    # llm-export accepts: qwen, llama, qwen2, qwen2_5, phi, gemma, chatglm, ...
    known = {
        "qwen": "qwen", "qwen2": "qwen", "qwen2.5": "qwen2_5",
        "qwen3": "qwen3", "llama": "llama", "llama2": "llama",
        "llama3": "llama3", "phi": "phi", "phi3": "phi3",
        "gemma": "gemma", "chatglm": "chatglm", "glm": "chatglm",
        "internlm": "internlm", "baichuan": "baichuan", "deepseek": "deepseek",
    }
    if mt in known:
        return known[mt]
    if mt.startswith("qwen"):
        return "qwen3" if "3" in mt else "qwen"
    if mt.startswith("llama"):
        return "llama3" if "3" in mt else "llama"
    return None


def _worker_hf_to_mnn(t: ConvertTask) -> dict[str, Any]:
    src = Path(t.src)
    dst = Path(t.dst)
    if not src.is_dir():
        raise RuntimeError(f"源模型目录不存在：{src}")
    if not (src / "config.json").is_file():
        raise RuntimeError(f"源目录缺少 config.json（不是 HF 模型目录）：{src}")

    t.status = ConvertStatus.PREPARING
    cmd_prefix = _llm_export_cmd(t)
    if cmd_prefix[0] == "llmexport" or (cmd_prefix and cmd_prefix[-1] == "llmexport"):
        # 官方独立 pip 包优先：pip 安装 llmexport（PyPI 清华镜像可达）
        _ensure_python_deps(t, ["llmexport"])
        cmd_prefix = _llm_export_cmd(t)
    else:
        # 仓库脚本模式：需要 torch/transformers 等
        _ensure_python_deps(t, ["torch", "transformers", "safetensors", "sentencepiece", "numpy"])

    arch = (t.options.get("arch") or "").strip()
    quant_bit = int(t.options.get("quant_bit", 4))
    quant_block = int(t.options.get("quant_block", 0))
    # 官方文档：--lm_quant_bit 默认跟随 quant_bit（不传则按 quant_bit）
    lm_quant_bit = t.options.get("lm_quant_bit")
    dst.mkdir(parents=True, exist_ok=True)

    t.status = ConvertStatus.RUNNING
    t.progress = 0.15
    cmd = [
        *cmd_prefix,
        "--path", str(src),
        "--export", "mnn",
        "--dst_path", str(dst),
        "--quant_bit", str(quant_bit),
        "--quant_block", str(quant_block),
    ]
    if arch:
        # 官方新版支持自动检测；仅当用户显式指定架构时传 --type
        cmd += ["--type", arch]
    if lm_quant_bit:
        cmd += ["--lm_quant_bit", str(lm_quant_bit)]
    # 视觉量化仅官方独立 pip 包 llmexport 支持（--visual_quant_bit）
    if t.options.get("visual_quant_bit") and cmd_prefix[0] == "llmexport":
        cmd += ["--visual_quant_bit", str(t.options["visual_quant_bit"])]
    # 直接转 MNN 需 pymnn 或 MNNConvert：能找到 MNNConvert 就显式传入
    mnnc = _find_mnnconvert()
    if mnnc:
        cmd += ["--mnnconvert", str(mnnc)]
    code, _ = _run_subprocess(t, cmd, cwd=str(Path(cmd_prefix[-1]).parent) if Path(cmd_prefix[-1]).exists() else None)
    if t.cancel_flag:
        raise RuntimeError("已取消")
    if code != 0:
        raise RuntimeError(f"llm-export 转换失败（exit {code}），请查看日志")

    cfg = dst / "config.json"
    if not cfg.is_file():
        raise RuntimeError("转换完成但未生成 config.json，产物可能不完整")
    t.progress = 1.0
    size_gb = round(sum(f.stat().st_size for f in dst.rglob("*") if f.is_file()) / 1e9, 2)
    return {"config_json": str(cfg), "size_gb": size_gb, "format": "mnn-llm"}


def _worker_hf_to_gguf(t: ConvertTask) -> dict[str, Any]:
    """HF safetensors → GGUF（llama.cpp / ollama）。官方文档：
    python convert_hf_to_gguf.py /path/to/model --outfile xxx.gguf --outtype f16
    脚本位于 llama.cpp 仓库根目录，依赖 torch/transformers/gguf。"""
    src = Path(t.src)
    dst = Path(t.dst)
    if not src.is_dir():
        raise RuntimeError(f"源模型目录不存在：{src}")
    if not (src / "config.json").is_file():
        raise RuntimeError(f"源目录缺少 config.json（不是 HF 模型目录）：{src}")

    t.status = ConvertStatus.PREPARING
    _ensure_python_deps(t, ["torch", "transformers", "safetensors", "numpy", "gguf"])

    script = _ensure_llamacpp_convert(t)
    dst.parent.mkdir(parents=True, exist_ok=True)

    outtype = str(t.options.get("outtype", "f16"))
    t.status = ConvertStatus.RUNNING
    t.progress = 0.2
    cmd = [
        sys_executable(), str(script), str(src),
        "--outfile", str(dst),
        "--outtype", outtype,
    ]
    code, _ = _run_subprocess(t, cmd, cwd=str(script.parent))
    if t.cancel_flag:
        raise RuntimeError("已取消")
    if code != 0:
        raise RuntimeError(f"GGUF 转换失败（exit {code}），请查看日志")
    if not dst.is_file():
        raise RuntimeError("转换完成但未生成 .gguf 文件")
    t.progress = 1.0
    size_mb = round(dst.stat().st_size / 1e6, 2)
    return {"gguf_file": str(dst), "size_mb": size_mb, "format": "gguf", "outtype": outtype}


def _worker_hf_to_onnx(t: ConvertTask) -> dict[str, Any]:
    """HF safetensors → ONNX（onnxruntime）。官方文档（HuggingFace Optimum）：
    pip install optimum[onnx]
    optimum-cli export onnx --model <dir> <outdir>"""
    src = Path(t.src)
    dst = Path(t.dst)
    if not src.is_dir():
        raise RuntimeError(f"源模型目录不存在：{src}")
    if not (src / "config.json").is_file():
        raise RuntimeError(f"源目录缺少 config.json（不是 HF 模型目录）：{src}")

    t.status = ConvertStatus.PREPARING
    _ensure_python_deps(t, ["optimum[onnx]"])

    task = str(t.options.get("task", "")) or None
    t.status = ConvertStatus.RUNNING
    t.progress = 0.2
    dst.mkdir(parents=True, exist_ok=True)
    cmd = ["optimum-cli", "export", "onnx", "--model", str(src)]
    if task:
        cmd += ["--task", task]
    cmd.append(str(dst))
    code, _ = _run_subprocess(t, cmd)
    if t.cancel_flag:
        raise RuntimeError("已取消")
    if code != 0:
        raise RuntimeError(f"ONNX 导出失败（exit {code}），请查看日志")
    onnx_files = list(dst.glob("*.onnx"))
    if not onnx_files:
        raise RuntimeError("转换完成但未生成 .onnx 文件")
    t.progress = 1.0
    size_mb = round(sum(f.stat().st_size for f in dst.rglob("*") if f.is_file()) / 1e6, 2)
    return {"onnx_files": [str(p) for p in onnx_files], "size_mb": size_mb, "format": "onnx"}


def _worker_hf_to_mlx(t: ConvertTask) -> dict[str, Any]:
    """HF safetensors → MLX（Apple Silicon，mlx-lm）。官方文档：
    pip install mlx-lm
    python -m mlx_lm.convert --hf-path <dir> -q（4bit 量化）"""
    src = Path(t.src)
    dst = Path(t.dst)
    if not src.is_dir():
        raise RuntimeError(f"源模型目录不存在：{src}")
    if not (src / "config.json").is_file():
        raise RuntimeError(f"源目录缺少 config.json（不是 HF 模型目录）：{src}")

    t.status = ConvertStatus.PREPARING
    _ensure_python_deps(t, ["mlx-lm"])

    dst.mkdir(parents=True, exist_ok=True)
    quantize = bool(t.options.get("quantize", True))
    t.status = ConvertStatus.RUNNING
    t.progress = 0.2
    cmd = [
        sys_executable(), "-m", "mlx_lm.convert",
        "--hf-path", str(src),
        "--out-path", str(dst),
    ]
    if quantize:
        cmd += ["-q"]
    code, _ = _run_subprocess(t, cmd)
    if t.cancel_flag:
        raise RuntimeError("已取消")
    if code != 0:
        raise RuntimeError(f"MLX 转换失败（exit {code}），请查看日志")
    if not (dst / "config.json").is_file():
        raise RuntimeError("转换完成但未生成 config.json，产物可能不完整")
    t.progress = 1.0
    size_mb = round(sum(f.stat().st_size for f in dst.rglob("*") if f.is_file()) / 1e6, 2)
    return {"config_json": str(dst / "config.json"), "size_mb": size_mb, "format": "mlx"}


def _ensure_llamacpp_convert(t: ConvertTask) -> Path:
    """Clone llama.cpp 仓库获取官方 convert_hf_to_gguf.py（根目录）。"""
    if _TOOLS_DIR is None:
        raise RuntimeError("converter 未初始化（configure_tools_dir 未调用）")
    script = _TOOLS_DIR / "llama.cpp" / "convert_hf_to_gguf.py"
    if script.is_file():
        return script

    repo_dir = _TOOLS_DIR / "llama.cpp"
    _append_log(t, "拉取 llama.cpp 官方转换脚本（convert_hf_to_gguf.py）…")
    if not (repo_dir / ".git").is_dir():
        repo_dir.mkdir(parents=True, exist_ok=True)
        last_err = ""
        for mirror in LLAMACPP_REPO_MIRRORS:
            if t.cancel_flag:
                raise RuntimeError("已取消")
            _append_log(t, f"尝试镜像: {mirror}")
            try:
                _run_subprocess(
                    t,
                    ["git", "clone", "--depth", "1", mirror, str(repo_dir)],
                    cwd=_TOOLS_DIR,
                )
                if script.is_file():
                    break
            except Exception as e:  # noqa: BLE001
                last_err = str(e)
                shutil.rmtree(repo_dir, ignore_errors=True)
                repo_dir.mkdir(parents=True, exist_ok=True)
        else:
            raise RuntimeError(f"llama.cpp 克隆失败：{last_err or '所有镜像不可达'}")
    if not script.is_file():
        raise RuntimeError(f"convert_hf_to_gguf.py 未找到（预期 {script}）")
    _append_log(t, "convert_hf_to_gguf.py 就绪")
    return script


def _worker_mnnconvert(t: ConvertTask) -> dict[str, Any]:
    src = Path(t.src)
    dst = Path(t.dst)
    if not src.is_file():
        raise RuntimeError(f"源模型文件不存在：{src}")

    converter = t.options.get("converter")
    if converter:
        mnnc = converter
    else:
        mnnc = _find_mnnconvert()
    if not mnnc:
        raise RuntimeError(
            "MNNConvert 不可用。请安装 pymnn（pip install MNN）或编译 MNN 后将其加入 PATH；"
            "本环境无 MNN 包时需在真实运行环境执行转换。"
        )

    framework = {"onnx-to-mnn": "ONNX", "torch-to-mnn": "TORCH"}.get(t.kind, "ONNX")
    t.status = ConvertStatus.RUNNING
    t.progress = 0.2
    cmd = [
        *shlex.split(mnnc),
        "-f", framework, "--modelFile", str(src),
        "--MNNModel", str(dst), "--bizCode", str(t.options.get("biz_code", "kevrai")),
    ]
    code, _ = _run_subprocess(t, cmd)
    if t.cancel_flag:
        raise RuntimeError("已取消")
    if code != 0:
        raise RuntimeError(f"MNNConvert 转换失败（exit {code}），请查看日志")
    if not dst.is_file():
        raise RuntimeError("转换完成但未生成 .mnn 文件")
    t.progress = 1.0
    size_mb = round(dst.stat().st_size / 1e6, 2)
    return {"mnn_file": str(dst), "size_mb": size_mb, "format": "mnn"}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

_WORKERS: dict[str, Callable[[ConvertTask], dict[str, Any]]] = {
    KIND_HF_TO_MNN: _worker_hf_to_mnn,
    KIND_HF_TO_GGUF: _worker_hf_to_gguf,
    KIND_HF_TO_ONNX: _worker_hf_to_onnx,
    KIND_HF_TO_MLX: _worker_hf_to_mlx,
    KIND_ONNX_TO_MNN: _worker_mnnconvert,
    KIND_TORCH_TO_MNN: _worker_mnnconvert,
}


def start_convert(
    kind: str,
    src: str,
    dst: str,
    *,
    options: dict[str, Any] | None = None,
    loop: asyncio.AbstractEventLoop | None = None,
) -> ConvertTask:
    """Create + start a conversion task (single-flight)."""
    global _ACTIVE
    if kind not in _WORKERS:
        raise ValueError(f"不支持的转换类型：{kind}")
    with _LOCK:
        if _ACTIVE is not None and _ACTIVE.status in (
            ConvertStatus.PENDING, ConvertStatus.PREPARING, ConvertStatus.RUNNING
        ):
            raise RuntimeError(f"已有转换任务进行中（{_ACTIVE.id}）")
        task = ConvertTask(
            id=uuid.uuid4().hex[:12],
            kind=kind,
            src=str(src),
            dst=str(dst),
            options=dict(options or {}),
        )
        _TASKS[task.id] = task
        _ACTIVE = task
        _append_log(task, f"转换任务已创建: {kind} -> {dst}")

    def _run() -> None:
        try:
            task.status = ConvertStatus.PREPARING
            task.started_at = time.time()
            _append_log(task, f"开始转换（源: {task.src}）")
            result = _WORKERS[kind](task)
            task.status = ConvertStatus.DONE
            task.result = result
            _append_log(task, "转换完成")
        except Exception as e:  # noqa: BLE001
            task.status = ConvertStatus.CANCELLED if task.cancel_flag else ConvertStatus.FAILED
            task.error = str(e)
            _append_log(task, f"失败: {e}")
        finally:
            task.finished_at = time.time()
            global _ACTIVE
            with _LOCK:
                if _ACTIVE is task:
                    _ACTIVE = None

    if loop is not None and loop.is_running():
        loop.run_in_executor(None, _run)
    else:
        threading.Thread(target=_run, daemon=True).start()
    return task
