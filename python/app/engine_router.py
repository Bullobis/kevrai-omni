"""引擎路由：把"用哪个引擎"变成单一真相源。

背景（真实断链）
----------------
``settings.default_engine_id`` 能改、能持久化、也有测试覆盖往返，
但**全仓库没有任何一处读取它**。原因不是忘了写，而是引擎从来没有被
"解析"过——``llm_runtime`` 与 ``mnn_runtime`` 是两套独立端点，选引擎等于
调哪个端点，中间缺一层按设置路由的决策。

于是用户在设置里选了默认引擎，实际行为一动不动。这是"不是语法错误但
很严重"的典型：功能看起来存在，其实没有任何效果。

设计原则（沿用 Cherry Studio 的两条）
--------------------------------------
1. **只从结构化字段推导，不猜。** Cherry 的 ``endpointImpliedCapability()``
   由接口类型推能力，绝不用模型 ID / 名字正则去猜。这里同理：引擎候选
   来自 catalog 的 ``engine`` 字段，识别不了的返回 ``unknown`` 并说明原因，
   绝不硬塞一个看起来合理的默认值。

2. **显式选择不在候选里就报错，不静默回退。** 用户选了 A 却静默跑 B，
   比直接失败更糟——他以为是 A 的行为，排查方向全错。
"""
from __future__ import annotations

from typing import Any

# 引擎 id → 应用内运行时 + 端点。
#
# 这里登记的是**确实存在加载端点**的引擎。外置服务（vLLM / SGLang /
# ComfyUI / Ollama / MLX）没有应用内端点，但可以路由到用户启动的服务；
# 返回 runtime="external" 并给出服务名，而不是谎称 "other"。
RUNTIME_BY_ENGINE: dict[str, str] = {
    "mnn": "mnn",
    "llama.cpp": "llamacpp",
    "llamacpp": "llamacpp",
    "llama_cpp": "llamacpp",
    "transformers": "transformers",
    "diffusers": "diffusers",
    "sentence-transformers": "embeddings",
    "faster-whisper": "asr",
    "demucs": "separation",
    "audio-separator": "separation",
    "piper": "piper",
    "ltx-video": "ltx",
    "music-runtime": "music",
    "trellis": "three-d",
    "triposr": "three-d",
    "hunyuan3d": "three-d",
    "direct3d-s2": "three-d",
    "partcrafter": "three-d",
    "triposg": "three-d",
    "hunyuanworld": "three-d",
    "sam3d": "three-d",
    "pixal3d": "three-d",
    "4danyone": "three-d",
    "onnxruntime": "onnx",
}

ROUTE_BY_RUNTIME: dict[str, str] = {
    "mnn": "/api/mnn/load",
    "llamacpp": "/api/engines",
    "transformers": "/api/local/transformers/chat",
    "diffusers": "/api/multimodal/generate-image",
    "embeddings": "/v1/embeddings",
    "asr": "/api/asr/transcribe",
    "separation": "/api/separation/separate",
    "piper": "/api/tts-piper/synthesize",
    "ltx": "/api/ltx/generate",
    "music": "/api/music/generate",
    "three-d": "/api/3d/generate",
    "onnx": "/api/ocr/recognize",
}

#: Services the user must start separately; a route cannot launch them.
EXTERNAL_ENGINE_SERVICES: dict[str, str] = {
    "vllm": "vLLM",
    "sglang": "SGLang",
    "sglang-omni": "SGLang-Omni",
    "comfyui": "ComfyUI",
    "ollama": "Ollama",
    "mlx": "MLX",
    "kokoro-engine": "Kokoro",
    "fish-speech": "Fish Speech",
    "f5-tts": "F5-TTS",
    "cosyvoice": "CosyVoice",
    "spark-tts": "Spark-TTS",
    "chatterbox": "Chatterbox",
    "indextts": "IndexTTS",
    "gpt-sovits": "GPT-SoVITS",
}


class EngineResolveError(ValueError):
    """显式指定的引擎不可用（不在该模型候选内）。

    刻意抛错而不是回退：用户明选了 A，静默跑 B 会让他以为看到的是 A 的行为。
    """


def resolve_engine(
    candidates: list[str] | str | None,
    *,
    explicit: str | None = None,
    default_engine_id: str | None = None,
) -> dict[str, Any]:
    """决定用哪个引擎，并说清这个决定是怎么来的。

    优先级：**显式 > 设置里的 default_engine_id > catalog 顺序第一个**。

    返回 dict：
      - ``engine``：选中的引擎 id（无候选时为 ``None``）
      - ``source``：``explicit`` / ``default`` / ``catalog`` / ``none``
      - ``candidates``：归一化后的候选列表
      - ``runtime``：``mnn`` / ``llamacpp`` / ``transformers`` / ``diffusers`` /
        ``external`` / ``other`` / ``unknown``
      - ``route``：建议调用的应用内端点（外置服务为 ``None``）
      - ``note``：需要告知用户的说明
    """
    cand = _normalize(candidates)

    if not cand:
        # 用户显式指定了引擎，但模型根本没声明任何引擎 —— 这是矛盾的，
        # 必须报错。返回"没有引擎"会让调用方以为请求成功了。
        if explicit:
            raise EngineResolveError(
                f"指定引擎 {explicit!r}，但该模型没有声明任何引擎候选"
            )
        return _result(None, "none", cand, note="该模型没有声明任何引擎")

    chosen: str | None = None
    source = "catalog"

    if explicit:
        if explicit not in cand:
            # 不回退：明确告诉他选的这个不在候选里。
            raise EngineResolveError(
                f"指定引擎 {explicit!r} 不在该模型的候选 {cand} 内"
            )
        chosen, source = explicit, "explicit"
    elif default_engine_id and default_engine_id in cand:
        # 这一行就是让 default_engine_id 真正生效的地方（原先是死设置）。
        chosen, source = default_engine_id, "default"
    else:
        chosen = cand[0]

    key = str(chosen).lower()
    service = EXTERNAL_ENGINE_SERVICES.get(key)
    if service:
        runtime = "external"
        route = None
        note = f"引擎 {chosen!r} 需要先启动 {service} 服务，应用内不负责拉起该进程"
    else:
        runtime = RUNTIME_BY_ENGINE.get(key, "other")
        route = ROUTE_BY_RUNTIME.get(runtime)
        note = ""
        if runtime == "other":
            note = (
                f"引擎 {chosen!r} 当前没有对应的应用内加载端点"
                f"（可下载 / 可被工作流调用，但不在本路由表内）"
            )
        elif key == "onnxruntime":
            note = "ONNX 路由当前覆盖 OCR；超分/检测模型需各自的预处理与后处理"
        elif runtime == "diffusers":
            note = (
                "Diffusers 通用管线：图像 / 视频 / 音频分别调用 "
                "/api/multimodal/generate-image、/api/local/video、/api/local/audio"
            )
        elif runtime == "transformers":
            note = "Transformers 本地对话调用 /api/local/transformers/chat"

    return _result(chosen, source, cand, runtime=runtime, route=route, note=note)


def _normalize(candidates: list[str] | str | None) -> list[str]:
    """归一化并去重（保序）。空串与重复项去掉。"""
    if candidates is None:
        return []
    raw = [candidates] if isinstance(candidates, str) else list(candidates)
    out: list[str] = []
    for c in raw:
        if not isinstance(c, str):
            continue
        c = c.strip()
        if c and c not in out:
            out.append(c)
    return out


def _result(
    engine: str | None,
    source: str,
    candidates: list[str],
    *,
    runtime: str = "unknown",
    route: str | None = None,
    note: str = "",
) -> dict[str, Any]:
    return {
        "engine": engine,
        "source": source,
        "candidates": candidates,
        "runtime": runtime,
        "route": route,
        "note": note,
    }
