"""Core agent loop — ReAct-style reasoning with tool use and reflection.

Inspired by OpenClaw's agent runtime (reason → act → observe loop, tool
guidance via system prompt, local memory) but customised for Kevrai Omni:
the agent's tools wrap the model catalog, hardware detector, engine manager,
and downloader. When no local LLM is loaded, the agent falls back to a
deterministic rule-based mode that can still answer simple queries and
execute tools.
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from dataclasses import dataclass, field
from typing import Any, Callable

from .memory import AgentMemory
from .model_router import ModelRouter
from .tool_registry import (
    ToolContext,
    ToolRegistry,
    extract_final_answer,
    parse_tool_call,
)
from .tools import build_default_registry

log = logging.getLogger("kevrai.agent")

# Maximum ReAct iterations per user message (prevents infinite loops).
MAX_ITERATIONS = 12

# Maximum characters of tool observation to feed back into the LLM context
# (prevents context window blowup from large catalog results).
MAX_OBSERVATION_CHARS = 2000


_SYSTEM_PROMPT_TEMPLATE = """你是 **Kevrai Agent**，Kevrai Omni 本地 AI 工作站的智能助手。
你的任务是帮助用户管理和使用本地 AI 模型：搜索模型、查看模型详情、根据硬件推荐模型、
检查已安装模型/引擎、规划下载、生成文本，以及记住用户偏好。

## 工作方式（ReAct 循环）
对每个用户请求，你按以下步骤思考：
1. **Thought**：分析用户需求，决定需要什么信息或操作。
2. **Action**：如果需要调用工具，输出 `Action: 工具名|{"参数": "值"}`；
   如果已经有足够信息回答，输出 `Final Answer: ...`。
3. **Observation**：工具执行结果会返回给你（你不需要自己写 Observation）。
4. 重复 1-3，直到你能给出最终答案。

## 可用工具
{tools_block}

## 工具使用准则
- 推荐模型前，**必须先调用 check_hardware** 了解用户显存/内存/磁盘。
- 不确定模型是否存在时，先用 search_models 搜索，再用 model_info 查看详情。
- 用户问"我能跑什么模型"时，先 check_hardware，再 recommend_models。
- 用户要下载模型时，用 download_model 获取下载计划（实际下载由 UI 队列执行）。
- 用户表达反复出现的偏好时（如"我喜欢用小模型"、"我只要开源模型"），
  用 set_preference 记住；回答前可用 get_preferences 查看已有偏好。
- 工具结果中的 error 字段要认真对待：如果工具失败，反思原因并尝试替代方案。
- 不要编造不存在的模型 ID 或工具名。
- 回答简洁、实用，用中文。涉及模型时给出名称、大小、适用硬件、许可类型。

## 用户偏好（已记住）
{preferences_block}

## 硬件概况
{hardware_block}

现在开始处理用户请求。记住：先 Thought，再 Action（工具）或 Final Answer。"""


@dataclass
class AgentStep:
    """A single step in the agent's ReAct loop."""
    iteration: int
    thought: str = ""
    action_tool: str = ""
    action_params: dict[str, Any] = field(default_factory=dict)
    observation: dict[str, Any] = field(default_factory=dict)
    raw_output: str = ""
    is_final: bool = False
    final_answer: str = ""
    error: str = ""
    timestamp: float = field(default_factory=time.time)


@dataclass
class AgentResult:
    """The result of processing one user message."""
    session_id: str
    answer: str
    steps: list[AgentStep] = field(default_factory=list)
    tools_used: list[str] = field(default_factory=list)
    success: bool = True
    error: str = ""
    llm_used: bool = True
    model_name: str = ""
    duration_ms: int = 0


@dataclass
class AgentSession:
    """A conversation session with the agent."""
    session_id: str
    memory: AgentMemory
    router: ModelRouter
    registry: ToolRegistry
    ctx: ToolContext


class Agent:
    """The Kevrai Omni general-purpose agent.

    Usage:
        agent = Agent(memory, router, registry, ctx)
        result = await agent.run("帮我找一个8GB能跑的音乐模型", session_id)
    """

    def __init__(
        self,
        memory: AgentMemory,
        router: ModelRouter | None = None,
        registry: ToolRegistry | None = None,
        ctx: ToolContext | None = None,
    ) -> None:
        self.memory = memory
        self.router = router or ModelRouter()
        self.registry = registry or build_default_registry()
        self.ctx = ctx or ToolContext()
        self.ctx.memory = memory
        self._step_callback: Callable[[AgentStep], None] | None = None

    def set_step_callback(self, cb: Callable[[AgentStep], None] | None) -> None:
        """Set a callback invoked after each ReAct step (for streaming UI)."""
        self._step_callback = cb

    # ------------------------------------------------------------------
    # Context building
    # ------------------------------------------------------------------
    def _build_system_prompt(self) -> str:
        tools_block = self.registry.build_tool_prompt_block()
        prefs = self.memory.get_all_preferences() if self.memory else {}
        if prefs:
            preferences_block = "\n".join(f"- {k}: {v}" for k, v in prefs.items())
        else:
            preferences_block = "（暂无）"

        hw = self.ctx.hardware_info or {}
        if hw:
            vram = hw.get("gpu_best_vram_gb", 0)
            ram = hw.get("ram_total_gb", 0)
            disk = (hw.get("disk") or {}).get("free_gb", 0)
            vendor = hw.get("gpu_vendor", "unknown")
            hardware_block = f"GPU: {vendor} (VRAM {vram}GB), RAM: {ram}GB, 磁盘剩余: {disk}GB"
        else:
            hardware_block = "（尚未检测，调用 check_hardware 后更新）"

        return (_SYSTEM_PROMPT_TEMPLATE
            .replace("{tools_block}", tools_block)
            .replace("{preferences_block}", preferences_block)
            .replace("{hardware_block}", hardware_block))

    def _build_history_block(self, session_id: str, n: int = 10) -> str:
        msgs = self.memory.get_recent_messages(session_id, n=n) if self.memory else []
        if not msgs:
            return ""
        lines = []
        for m in msgs:
            role = m.get("role", "")
            content = str(m.get("content", ""))[:500]
            if role == "user":
                lines.append(f"用户: {content}")
            elif role == "assistant":
                lines.append(f"助手: {content}")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Rule-based fallback (no LLM loaded)
    # ------------------------------------------------------------------
    def _rule_based_response(self, message: str, session_id: str) -> AgentResult:
        """Deterministic response when no LLM is loaded.

        Handles common simple queries by directly calling tools, so the agent
        is still useful without a reasoning model.
        """
        result = AgentResult(
            session_id=session_id,
            answer="",
            llm_used=False,
            model_name="",
        )
        msg_lower = message.lower()
        steps: list[AgentStep] = []

        # Hardware check trigger
        if any(w in msg_lower for w in ["硬件", "显存", "vram", "配置", "我能跑", "我的电脑"]):
            step = AgentStep(iteration=1, action_tool="check_hardware")
            obs = self.registry.execute("check_hardware", {}, self.ctx)
            step.observation = obs
            steps.append(step)
            if obs.get("ok") is not False and "summary" in obs:
                result.answer = (
                    f"你的硬件：{obs['summary']}\n\n"
                    "如需推荐模型，请告诉我你想做什么（文本/图像/音频/视频/3D），"
                    "我会根据你的硬件推荐合适的模型。"
                )
                result.tools_used.append("check_hardware")
                result.steps = steps
                return result

        # Search trigger
        if any(w in msg_lower for w in ["搜索", "找", "有什么", "推荐", "search"]):
            # Extract category hint
            category = None
            for cat, keywords in _CATEGORY_KEYWORDS.items():
                if any(k in msg_lower for k in keywords):
                    category = cat
                    break
            step = AgentStep(iteration=1, action_tool="search_models",
                             action_params={"query": message, "category": category, "limit": 8})
            obs = self.registry.execute("search_models", {"query": message, "category": category, "limit": 8}, self.ctx)
            step.observation = obs
            steps.append(step)
            results = obs.get("results", [])
            if results:
                lines = [f"找到 {len(results)} 个相关模型：\n"]
                for r in results[:8]:
                    lines.append(
                        f"- **{r.get('name')}** ({r.get('id')}) — {r.get('category')}, "
                        f"{r.get('size_gb')}GB, 引擎: {', '.join(r.get('engine') or [])}"
                    )
                    desc = r.get("description", "")
                    if desc:
                        lines.append(f"  {desc[:120]}")
                lines.append("\n告诉我你对哪个模型感兴趣，我可以查看详情或规划下载。")
                result.answer = "\n".join(lines)
            else:
                result.answer = "没有找到匹配的模型。请尝试更具体的关键词，或告诉我你需要什么类型的模型。"
            result.tools_used.append("search_models")
            result.steps = steps
            return result

        # Default: explain that LLM is not loaded
        result.answer = (
            "Kevrai Agent 已就绪，但当前未加载对话 AI 模型，因此我只能执行基础工具操作"
            "（硬件检测、模型搜索、推荐）。\n\n"
            "如需完整的智能对话能力，请：\n"
            "1. 在「AI 引擎」页安装 MNN 引擎\n"
            "2. 在「模型市场」下载一个 LLM 对话模型（如 Qwen、Granite、DeepSeek 等）\n"
            "3. 在「MNN 引擎」页加载模型\n\n"
            "加载后我就能进行复杂推理、多步规划和文本生成。\n\n"
            "你现在可以问我：\n"
            "- \"我的硬件能跑什么模型？\"\n"
            "- \"搜索音乐生成模型\"\n"
            "- \"推荐适合8GB显存的图像模型\""
        )
        result.steps = steps
        return result

    # ------------------------------------------------------------------
    # Main run loop
    # ------------------------------------------------------------------
    async def run(self, message: str, session_id: str = "default") -> AgentResult:
        """Process one user message through the ReAct loop."""
        t0 = time.time()
        message = (message or "").strip()[:5000]
        if not message:
            return AgentResult(
                session_id=session_id,
                answer="请输入你的问题。",
                success=False,
                error="empty message",
                duration_ms=0,
            )

        # Ensure hardware info is populated (lazy, cached in ctx)
        if not self.ctx.hardware_info or hasattr(self.ctx.hardware_info, "send"):
            try:
                import asyncio as _aio
                import concurrent.futures
                from ..hardware import detect_hardware
                path = self.ctx.models_dir or self.ctx.app_root or Path(".")
                def _detect_sync():
                    return _aio.run(detect_hardware(Path(path)))
                try:
                    _loop = _aio.get_event_loop()
                    if _loop.is_running():
                        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                            self.ctx.hardware_info = pool.submit(_detect_sync).result(timeout=30)
                    else:
                        self.ctx.hardware_info = _detect_sync()
                except RuntimeError:
                    self.ctx.hardware_info = _detect_sync()
            except Exception:
                self.ctx.hardware_info = {}

        # Record user message
        if self.memory:
            self.memory.add_message(session_id, "user", message)

        # Check if LLM is ready
        llm_ready, model_name = self.router.is_ready()
        if not llm_ready:
            log.info("agent running in rule-based mode (no LLM loaded)")
            result = self._rule_based_response(message, session_id)
            result.duration_ms = int((time.time() - t0) * 1000)
            if self.memory:
                self.memory.add_message(session_id, "assistant", result.answer)
                self.memory.record_task(session_id, message[:200], result.tools_used, success=True)
            return result

        # --- LLM-driven ReAct loop ---
        system_prompt = self._build_system_prompt()
        history_block = self._build_history_block(session_id, n=10)

        steps: list[AgentStep] = []
        tools_used: list[str] = []
        scratchpad = ""  # accumulates Thought/Action/Observation for the LLM
        final_answer = ""
        error_msg = ""

        for iteration in range(1, MAX_ITERATIONS + 1):
            # Build prompt for this iteration
            prompt_parts = [system_prompt]
            if history_block:
                prompt_parts.append(f"\n## 对话历史\n{history_block}")
            prompt_parts.append(f"\n## 当前用户请求\n{message}")
            if scratchpad:
                prompt_parts.append(f"\n## 推理过程\n{scratchpad}")
            prompt_parts.append("\nThought: ")
            prompt = "\n".join(prompt_parts)

            # Call LLM
            llm_res = self.router.chat(prompt, system="", max_new_tokens=1500)
            if not llm_res.get("ok"):
                error_msg = f"LLM 调用失败：{llm_res.get('error', 'unknown')}"
                log.warning("agent LLM call failed at iteration %d: %s", iteration, error_msg)
                break

            raw = llm_res.get("text", "")
            # Prepend "Thought: " because the prompt ends with it
            full_output = "Thought: " + raw

            step = AgentStep(iteration=iteration, raw_output=full_output)

            # Check for final answer
            if "final answer" in raw.lower() or "最终答案" in raw or "最终回答" in raw:
                final_answer = extract_final_answer(full_output)
                step.is_final = True
                step.final_answer = final_answer
                steps.append(step)
                if self._step_callback:
                    self._step_callback(step)
                break

            # Parse tool call
            tool_call = parse_tool_call(full_output)
            if tool_call is None:
                # No tool call and no final answer — treat the output as the answer
                final_answer = raw.strip()
                step.is_final = True
                step.final_answer = final_answer
                steps.append(step)
                if self._step_callback:
                    self._step_callback(step)
                break

            tool_name, tool_params = tool_call
            step.action_tool = tool_name
            step.action_params = tool_params

            # Extract thought (text before Action:)
            thought_match = __import__("re").search(
                r"Thought\s*:\s*(.*?)(?=\n\s*Action\s*:|\Z)", full_output, __import__("re").S
            )
            if thought_match:
                step.thought = thought_match.group(1).strip()[:500]

            # Execute tool
            log.info("agent tool call: %s(%s)", tool_name, json.dumps(tool_params, ensure_ascii=False)[:200])
            obs = self.registry.execute(tool_name, tool_params, self.ctx)
            step.observation = obs

            # Truncate observation for context
            obs_str = json.dumps(obs, ensure_ascii=False, default=str)
            if len(obs_str) > MAX_OBSERVATION_CHARS:
                obs_str = obs_str[:MAX_OBSERVATION_CHARS] + "...(truncated)"

            # Append to scratchpad
            scratchpad += f"\nThought: {step.thought}\n"
            scratchpad += f"Action: {tool_name}|{json.dumps(tool_params, ensure_ascii=False)}\n"
            scratchpad += f"Observation: {obs_str}\n"

            tools_used.append(tool_name)
            steps.append(step)
            if self._step_callback:
                self._step_callback(step)

            # Reflection: if tool failed, note it and let the LLM decide next
            if obs.get("ok") is False:
                log.info("agent tool %s failed: %s", tool_name, obs.get("error", ""))
                # The LLM will see the error in Observation and can adapt

        # If loop exhausted without final answer
        if not final_answer and not error_msg:
            error_msg = f"达到最大迭代次数 ({MAX_ITERATIONS})，未能生成最终答案。"
            final_answer = "抱歉，处理过程中达到了最大步骤限制。请尝试简化你的问题，或分步骤提问。"

        if error_msg and not final_answer:
            final_answer = f"处理过程中出现问题：{error_msg}"

        result = AgentResult(
            session_id=session_id,
            answer=final_answer,
            steps=steps,
            tools_used=tools_used,
            success=not error_msg,
            error=error_msg,
            llm_used=True,
            model_name=model_name,
            duration_ms=int((time.time() - t0) * 1000),
        )

        # Persist
        if self.memory:
            self.memory.add_message(session_id, "assistant", final_answer)
            self.memory.record_task(session_id, message[:200], tools_used, success=not error_msg)

        return result


# Category keyword mapping for rule-based fallback
_CATEGORY_KEYWORDS: dict[str, list[str]] = {
    "llm": ["大语言", "对话", "聊天", "llm", "文本生成", "写作", "翻译"],
    "image": ["图像", "图片", "文生图", "image", "画图", "绘画"],
    "audio": ["音频", "音乐", "music", "audio", "声音", "配乐", "bgm"],
    "video": ["视频", "video", "文生视频", "短片"],
    "tts": ["语音", "tts", "配音", "朗读", "语音合成"],
    "3d": ["3d", "三维", "模型", "3d生成"],
    "superres": ["超分", "superres", "放大", "清晰度"],
}
