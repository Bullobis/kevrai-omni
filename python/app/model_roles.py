"""Model capability tags + role slots (the Cherry Studio lesson).

Cherry Studio's single most transferable idea is **not** a feature — it is the
default baseline: models are grouped *by purpose* (default assistant / fast /
translation / drawing), so a new user never has to learn what "temperature" or
"context window" means before the app is useful.

This module brings that idea to Kevrai Omni:

* **Capabilities are explicit tags**, derived from the catalog's *structured*
  fields (``category`` / ``modality`` / ``tags`` / ``hardware``) — **not**
  regex-guessed from the model ID. Cherry Studio is explicit that guessing
  capability from an ID string is the thing to avoid; when we cannot tell, we
  say ``unknown`` instead of pretending.
* **Roles are slots** (``chat`` / ``fast`` / ``vision`` / ``image`` / …).
  Each slot picks the best *installed* model for its job, respecting a VRAM
  budget, and explains the choice in plain language.
* **User overrides are a separate layer.** The catalog is shipped data; what
  the user pins stays in the user layer and never rewrites the catalog.

The module is pure and dependency-free: no torch, no network, no I/O beyond
what the caller passes in. That keeps it testable and lets the health check
and the catalog UI use it safely.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Iterable

log = logging.getLogger("kevrai.model_roles")

# ---------------------------------------------------------------------------
# Capability vocabulary
# ---------------------------------------------------------------------------
# 与 Cherry Studio 的能力字段对齐：reasoning / function-calling / vision /
# text-to-image / embedding / rerank，再补上 Kevrai 的本地多模态能力。
CAPABILITIES: tuple[str, ...] = (
    "chat",            # 通用对话
    "reasoning",       # 深度推理 / 思维链
    "function_calling",  # 工具调用
    "vision",          # 看图理解
    "image_gen",       # 文生图
    "video_gen",       # 视频生成
    "three_d",         # 3D 生成
    "embedding",       # 向量化
    "rerank",          # 重排
    "asr",             # 语音识别
    "tts",             # 语音合成
    "music",           # 音乐生成
    "superres",        # 超分
    "diarization",     # 说话人分离（谁在什么时候说话）
)

#: 未知能力的显示名——不能确定的时候如实说不知道，不要猜。
UNKNOWN = "unknown"

# ---------------------------------------------------------------------------
# Role slots
# ---------------------------------------------------------------------------
# Cherry Studio 的四组（默认助手/快速/翻译/绘画）在这里扩展为九个槽位，
# 覆盖 Kevrai 的本地多模态能力。每个槽位只做一件事，选模型时互不干扰。
ROLES: dict[str, dict[str, Any]] = {
    "chat": {
        "label": "日常对话",
        "needs": ("chat",),
        "hint": "主对话模型，绝大多数问题都走它",
    },
    "fast": {
        "label": "后台小任务",
        "needs": ("chat",),
        "hint": "对话取名、搜索词提炼等轻量活；越小越快越好，不需要深度思考",
    },
    "reasoning": {
        "label": "复杂思考",
        "needs": ("reasoning",),
        "hint": "数学、代码、多步推理；慢一些但更准",
    },
    "vision": {
        "label": "看图",
        "needs": ("vision",),
        "hint": "需要模型能真正看懂图片，不是只看文字",
    },
    "image": {
        "label": "画图",
        "needs": ("image_gen",),
        "hint": "文生图专用模型",
    },
    "embedding": {
        "label": "知识库索引",
        "needs": ("embedding",),
        "hint": "把文档变成向量，知识库检索的基础",
    },
    "rerank": {
        "label": "结果重排",
        "needs": ("rerank",),
        "hint": "可选增强：对检索结果二次打分，提高命中率",
    },
    "planning": {
        "label": "Agent 规划",
        "needs": ("function_calling",),
        "hint": "Agent 拆解任务、调用工具时用，需要稳定的工具调用能力",
    },
    "tool": {
        "label": "工具调用",
        "needs": ("function_calling",),
        "hint": "与 planning 分开，便于给高风险操作配更稳的模型",
    },
}

# ---------------------------------------------------------------------------
# Capability derivation — structured fields only, no ID regex
# ---------------------------------------------------------------------------

#: category → 能力标签。这是**结构化**映射，不依赖任何字符串猜测。
#
# v4.11.0 修正：``vision`` 类目**不再笼统带 chat**。
# 这个类目里混着大量专用视觉模型（PP-OCR 文字检测/识别、YOLOv10 检测、
# InsightFace 人脸、SAM3 分割、CLIP），它们只能做视觉任务、根本不能对话。
# 笼统给 chat 会让「后台小任务」槽位挑中最小的那个（0.06GB 的 OCR 检测模型），
# 用户拿它跑后台任务完全不可用。chat 只给真正的多模态 LLM，见 _VLM_TAGS。
_CATEGORY_CAPS: dict[str, tuple[str, ...]] = {
    "llm": ("chat",),
    "vision": ("vision",),   # 仅视觉能力；多模态 LLM 另行判定
    "image": ("image_gen",),
    "video": ("video_gen",),
    "3d": ("three_d",),
    "embedding": ("embedding",),
    "tts": ("tts",),
    "audio": (),   # 音频大类需再按 tags 细分（asr / music / tts / diarization）
    "superres": ("superres",),
    "pending": (),  # 官方尚未发布权重，无任何可用能力
}

#: 哪些标记说明这个视觉模型是**能对话的多模态 LLM**（VLM），
#: 而不是只能做检测/识别/分割的专用模型。
_VLM_TAGS: frozenset[str] = frozenset({
    "vlm", "多模态", "multimodal", "vision-language", "视觉语言",
})

#: tags（小写）→ 额外能力。tags 是 catalog 里的结构化数组，不是 ID 的一部分。
_TAG_CAPS: dict[str, str] = {
    "asr": "asr",
    "语音识别": "asr",
    "stt": "asr",
    "music": "music",
    "音乐": "music",
    "tts": "tts",
    "语音合成": "tts",
    "rerank": "rerank",
    "重排": "rerank",
    "diarization": "diarization",
    "说话人分离": "diarization",
    "embedding": "embedding",
    "向量": "embedding",
    "reasoning": "reasoning",
    "推理": "reasoning",
    "thinking": "reasoning",
    "vision": "vision",
    "多模态": "vision",
    "multimodal": "vision",
    "tool": "function_calling",
    "function-calling": "function_calling",
    "工具调用": "function_calling",
    "agent": "function_calling",
    "superres": "superres",
    "超分": "superres",
}


def _norm(value: Any) -> str:
    return str(value or "").strip().lower()


def derive_capabilities(model: dict[str, Any]) -> list[str]:
    """Derive capability tags for one catalog entry.

    Reads **only** structured fields. Returns a sorted, de-duplicated list.
    Returns ``[]`` (not a guess) when nothing can be established — callers
    should surface ``unknown`` in that case.
    """
    caps: set[str] = set()

    category = _norm(model.get("category"))
    caps.update(_CATEGORY_CAPS.get(category, ()))

    # modality 是结构化布尔/数组字段，比任何字符串匹配都可靠
    modality = model.get("modality") or {}
    if isinstance(modality, dict):
        if modality.get("multimodal") is True:
            caps.add("vision")
        for g in (modality.get("generate") or []):
            g = _norm(g)
            if g in ("image", "图片"):
                caps.add("image_gen")
            elif g in ("video", "视频"):
                caps.add("video_gen")
            elif g in ("audio", "音频", "speech"):
                caps.add("tts")
            elif g in ("3d", "mesh", "网格"):
                caps.add("three_d")

    for t in (model.get("tags") or []):
        cap = _TAG_CAPS.get(_norm(t))
        if cap:
            caps.add(cap)

    # 视觉类目里只有货真价实的多模态 LLM 才能对话。
    # 专用模型（OCR / 检测 / 分割 / 人脸）保持纯视觉，避免被"后台小任务"
    # 这种挑最小的槽位选中去干它干不了的活。
    if category == "vision":
        tags = {_norm(t) for t in (model.get("tags") or [])}
        if tags & _VLM_TAGS:
            caps.add("chat")

    # pending 类目（官方未发布权重）明确没有任何能力，别被 tags 误加回来
    if category == "pending":
        return []

    # audio 大类若没细分出 asr/music/tts，就保持为空（不猜）
    return sorted(c for c in caps if c in CAPABILITIES)


def capabilities_or_unknown(model: dict[str, Any]) -> list[str]:
    """Like :func:`derive_capabilities` but never returns an empty list."""
    caps = derive_capabilities(model)
    return caps or [UNKNOWN]


# ---------------------------------------------------------------------------
# Role → model selection
# ---------------------------------------------------------------------------


def _coerce_vram(value: Any) -> float | None:
    """把目录里的显存字段收敛成 GB 数值；认不出来返回 None。

    与 Operit ``WorkflowExecutor.parseDoubleOrNullStrict`` 同一态度：
    **数值判断绝不"看着像"就算过**。目录是手工维护的 JSON，写成
    ``"min_vram_gb": "8"`` 是很自然的手误；若直接当成"不认识"丢掉，
    就会回落成 0 = "未知" = **fits anything**——把一个 40GB 的模型
    推荐给 8GB 显存的用户，等他下完 40GB 才发现跑不起来。

    所以：能解析的数字（含数字字符串）一律解析；解析不了的记 warning 后
    仍按"未知"处理，但**必须留下痕迹**，不能静默。
    """
    if isinstance(value, bool):  # bool 是 int 的子类，绝不能当显存用
        return None
    if isinstance(value, (int, float)):
        v = float(value)
        return v if v > 0 and v == v and v not in (float("inf"), float("-inf")) else None
    if isinstance(value, str):
        t = value.strip().replace("GB", "").replace("gb", "").strip()
        if not t:
            return None
        try:
            v = float(t)
        except ValueError:
            log.warning("model_roles: unreadable VRAM value %r (model %r)", value, None)
            return None
        return v if v > 0 and v == v else None
    return None


def _vram_need(model: dict[str, Any]) -> float:
    """Conservative VRAM requirement: prefer the *minimum* the vendor states.

    Falls back to the nominal value, then to 0 (unknown) — we never invent a
    number. A 0 means "unknown", and the selector treats unknown as "fits
    anything" rather than excluding the model (better to offer than to hide).

    Numeric strings are accepted (see :func:`_coerce_vram`) so a hand-edited
    catalog entry does not silently degrade to "fits anything".
    """
    hw = model.get("hardware") or {}
    for key in ("min_vram_gb", "vram_gb"):
        got = _coerce_vram(hw.get(key))
        if got is not None:
            return got
    return 0.0


def fits_budget(model: dict[str, Any], vram_budget_gb: float | None) -> bool:
    """Whether ``model`` can run within ``vram_budget_gb``.

    ``None`` budget = no constraint. Unknown requirement (0) always fits.
    """
    if vram_budget_gb is None:
        return True
    need = _vram_need(model)
    return need <= 0 or need <= float(vram_budget_gb)


@dataclass
class RoleChoice:
    """One slot's selection result, with a plain-language reason."""

    role: str
    label: str
    hint: str
    model_id: str = ""
    model_name: str = ""
    reason: str = ""
    vram_gb: float = 0.0
    candidates: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "role": self.role,
            "label": self.label,
            "hint": self.hint,
            "model_id": self.model_id,
            "model_name": self.model_name,
            "reason": self.reason,
            "vram_gb": round(self.vram_gb, 1),
            "candidates": self.candidates,
            "filled": bool(self.model_id),
        }


def _has_all(model: dict[str, Any], needs: Iterable[str]) -> bool:
    caps = set(derive_capabilities(model))
    return all(n in caps for n in needs)


def pick_for_role(
    role: str,
    models: list[dict[str, Any]],
    vram_budget_gb: float | None = None,
    installed_ids: Iterable[str] | None = None,
) -> RoleChoice:
    """Pick the best installed model for ``role``.

    Selection rules, in order of intent:

    * ``fast`` — smallest model that can chat (Cherry Studio: pick a cheap,
      fast model without deep thinking for background tasks).
    * everything else — largest/most capable model that still fits the VRAM
      budget, breaking ties by ``trending`` then by name.

    Only *installed* models are considered (a catalog entry you have not
    downloaded cannot serve a slot).
    """
    spec = ROLES.get(role)
    if spec is None:
        raise KeyError(f"unknown role: {role!r}")

    installed = set(installed_ids or ())
    pool = [
        m for m in models
        if m.get("id") in installed
        and fits_budget(m, vram_budget_gb)
        and _has_all(m, spec["needs"])
    ]
    # pending 条目永远不能上岗（官方没放权重）
    pool = [m for m in pool if _norm(m.get("category")) != "pending"]

    choice = RoleChoice(
        role=role, label=spec["label"], hint=spec["hint"], candidates=len(pool)
    )
    if not pool:
        choice.reason = (
            "没有已安装且具备该能力的模型"
            if installed else "当前没有已安装模型"
        )
        return choice

    if role == "fast":
        best = min(pool, key=lambda m: (_size(m), m.get("id", "")))
        choice.reason = f"后台小任务挑最小的：{_size(best):.1f}GB，快且省显存"
    else:
        best = max(
            pool,
            key=lambda m: (
                _size(m),
                bool(m.get("trending")),
                m.get("id", ""),
            ),
        )
        need = _vram_need(best)
        choice.reason = (
            f"在显存预算内挑能力最强的：{_size(best):.1f}GB"
            + (f"，需 {need:.0f}GB 显存" if need > 0 else "，显存需求未标注")
        )

    choice.model_id = best.get("id", "")
    choice.model_name = best.get("name", choice.model_id)
    choice.vram_gb = _vram_need(best)
    return choice


def _size(model: dict[str, Any]) -> float:
    v = model.get("size_gb")
    return float(v) if isinstance(v, (int, float)) else 0.0


def assign_roles(
    models: list[dict[str, Any]],
    vram_budget_gb: float | None = None,
    installed_ids: Iterable[str] | None = None,
) -> dict[str, dict[str, Any]]:
    """Fill every role slot. Unfillable slots come back with ``filled: False``."""
    return {
        role: pick_for_role(role, models, vram_budget_gb, installed_ids).to_dict()
        for role in ROLES
    }


# ---------------------------------------------------------------------------
# User override layer
# ---------------------------------------------------------------------------


@dataclass
class OverrideStore:
    """User pins, kept separate from the shipped catalog.

    Cherry Studio merges *factory definition + user DB row* at runtime; the
    factory catalog is never mutated. Same here: pins live in this store, and
    :meth:`resolve` applies them on top of :func:`assign_roles`.
    """

    pins: dict[str, str] = field(default_factory=dict)      # role -> model_id
    cap_overrides: dict[str, list[str]] = field(default_factory=dict)  # model_id -> caps

    def pin(self, role: str, model_id: str) -> None:
        if role not in ROLES:
            raise KeyError(f"unknown role: {role!r}")
        self.pins[role] = model_id

    def unpin(self, role: str) -> None:
        self.pins.pop(role, None)

    def set_capabilities(self, model_id: str, caps: list[str]) -> None:
        bad = [c for c in caps if c not in CAPABILITIES]
        if bad:
            raise ValueError(f"unknown capabilities: {bad}")
        self.cap_overrides[model_id] = sorted(set(caps))

    def capabilities_for(self, model: dict[str, Any]) -> list[str]:
        """Override wins over derived; derived wins over nothing."""
        mid = model.get("id", "")
        if mid in self.cap_overrides:
            return list(self.cap_overrides[mid])
        return capabilities_or_unknown(model)

    def resolve(
        self,
        models: list[dict[str, Any]],
        vram_budget_gb: float | None = None,
        installed_ids: Iterable[str] | None = None,
    ) -> dict[str, dict[str, Any]]:
        """Assign roles, then apply user pins on top."""
        out = assign_roles(models, vram_budget_gb, installed_ids)
        by_id = {m.get("id"): m for m in models}
        for role, mid in self.pins.items():
            m = by_id.get(mid)
            if m is None:
                # 用户钉住的模型已不在目录里：保留 id 但标记为失效，
                # 不要静默回落到自动选择（用户会以为自己的设置生效了）
                out[role]["model_id"] = mid
                out[role]["model_name"] = mid
                out[role]["reason"] = "你手动指定的模型已不在模型目录中"
                out[role]["filled"] = False
                out[role]["pinned"] = True
                continue
            out[role].update({
                "model_id": mid,
                "model_name": m.get("name", mid),
                "reason": "你手动指定的模型",
                "vram_gb": round(_vram_need(m), 1),
                "filled": True,
                "pinned": True,
            })
        return out


# ---------------------------------------------------------------------------
# Cherry Studio 的默认值基线（研究报告里最值钱的一条）
# ---------------------------------------------------------------------------
# 知识库：先用 BM25，向量检索叠加，Rerank 只作可选二次评分。
# Top K 默认 6、相似度阈值默认 0.0 —— 新用户用默认值就够，不必调。
RAG_DEFAULTS: dict[str, Any] = {
    "mode": "bm25",          # 关键词能命中就不必上向量
    "top_k": 6,
    "score_threshold": 0.0,  # 从 0 开始，觉得噪音多再上调
    "rerank_enabled": False, # 只在召回不稳时才开
    "chunk_size_tokens": 1024,
    "chunk_overlap_tokens": 200,
    "note": (
        "这套默认值沿用 Cherry Studio 的基线：BM25 起步、Top K=6、阈值 0.0、"
        "Rerank 默认关闭。关键词能命中就不开向量，向量召回不稳再加 Rerank。"
    ),
}
