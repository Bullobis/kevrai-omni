"""Tests for the v2.8.0 pluggable skill system and ported drama methodology.

Coverage:
- Skill dataclass validation (id, non-empty tools, intra-skill unique names)
- SkillManager: enable/disable, required-skill protection, reset, persistence,
  corrupt/unknown state, active-only registry, guidance block, cross-skill
  uniqueness, tool->skill map
- Built-in skill bundles (6 skills, default 16 active tools, 23 when all on,
  writing/media opt-in, v2.7.0 flat registry preserved at 11)
- media_prompt studio tools (positive/negative/theme_constraints, validation)
- writing studio tools (offline scaffold / extractive summary / stats)
- drama storycraft reference + normalized script new fields (mode, beat,
  scene registry auto-add, duration clamp) via a canned LLM
- Agent + SkillManager integration (system prompt guidance, rule routes,
  disabled-tool guards, reload)
- HTTP skills API (list/toggle/reset, 400/404 paths, tool-count linkage)
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.agent import Agent, AgentMemory, ModelRouter, Skill, SkillManager, ToolContext
from app.agent.tool_registry import Tool, ToolRegistry
from app.agent.tools import (
    ALL_TOOLS,
    BUILTIN_SKILLS,
    all_skill_tools,
    build_default_registry,
    build_skill_manager,
)


def _noop_tool(name: str) -> Tool:
    return Tool(name=name, description=name, parameters={}, handler=lambda p, c: {"ok": True})


# ===========================================================================
# Skill dataclass
# ===========================================================================
class TestSkillDataclass:
    def test_valid_skill(self):
        sk = Skill(id="demo_skill", name="Demo", description="d",
                   tools=[_noop_tool("demo_one")])
        assert sk.tool_names == ["demo_one"]
        spec = sk.to_spec(True)
        assert spec["enabled"] is True and spec["tool_count"] == 1
        assert spec["tool_names"] == ["demo_one"]

    @pytest.mark.parametrize("bad", ["", "UPPER", "1abc", "has-dash", "a b"])
    def test_bad_id_rejected(self, bad):
        with pytest.raises(ValueError):
            Skill(id=bad, name="x", description="x", tools=[_noop_tool("ok_tool")])

    def test_empty_tools_rejected(self):
        with pytest.raises(ValueError):
            Skill(id="empty_skill", name="x", description="x", tools=[])

    def test_duplicate_tool_within_skill_rejected(self):
        with pytest.raises(ValueError):
            Skill(id="dup_skill", name="x", description="x",
                  tools=[_noop_tool("same_tool"), _noop_tool("same_tool")])


# ===========================================================================
# SkillManager
# ===========================================================================
class TestSkillManager:
    def _skills(self):
        return [
            Skill(id="core", name="core", description="c", required=True,
                  tools=[_noop_tool("core_a"), _noop_tool("core_b")]),
            Skill(id="alpha", name="alpha", description="a", default_enabled=True,
                  tools=[_noop_tool("alpha_a")]),
            Skill(id="beta", name="beta", description="b", default_enabled=False,
                  tools=[_noop_tool("beta_a")]),
        ]

    def test_default_enablement(self):
        sm = SkillManager(self._skills())
        assert sm.is_enabled("core") and sm.is_enabled("alpha")
        assert sm.is_enabled("beta") is False
        assert sorted(sm.active_tool_names()) == ["alpha_a", "core_a", "core_b"]

    def test_required_cannot_disable(self):
        sm = SkillManager(self._skills())
        with pytest.raises(ValueError):
            sm.disable("core")
        # enabling a required skill is a harmless no-op
        sm.enable("core")
        assert sm.is_enabled("core")

    def test_enable_disable_toggle(self):
        sm = SkillManager(self._skills())
        sm.enable("beta")
        assert "beta_a" in sm.active_tool_names()
        sm.toggle("beta")
        assert sm.is_enabled("beta") is False
        sm.toggle("beta")
        assert sm.is_enabled("beta") is True

    def test_unknown_id_raises(self):
        sm = SkillManager(self._skills())
        with pytest.raises(KeyError):
            sm.is_enabled("ghost")
        with pytest.raises(KeyError):
            sm.enable("ghost")

    def test_cross_skill_duplicate_tool_rejected(self):
        skills = [
            Skill(id="s_one", name="1", description="", tools=[_noop_tool("shared_x")]),
            Skill(id="s_two", name="2", description="", tools=[_noop_tool("shared_x")]),
        ]
        with pytest.raises(ValueError):
            SkillManager(skills)

    def test_duplicate_skill_id_rejected(self):
        skills = self._skills()
        skills.append(Skill(id="alpha", name="dup", description="",
                            tools=[_noop_tool("alpha_b")]))
        with pytest.raises(ValueError):
            SkillManager(skills)

    def test_reset_restores_defaults(self):
        sm = SkillManager(self._skills())
        sm.enable("beta")
        sm.disable("alpha")
        sm.reset()
        assert sm.is_enabled("alpha") and not sm.is_enabled("beta")

    def test_registry_only_active_tools(self):
        sm = SkillManager(self._skills())
        reg = sm.build_registry()
        assert "alpha_a" in reg.list_names() and "beta_a" not in reg.list_names()
        sm.enable("beta")
        reg2 = sm.build_registry()
        assert "beta_a" in reg2.list_names()

    def test_guidance_block_only_enabled_with_text(self):
        skills = [
            Skill(id="core", name="核心", description="", required=True,
                  tools=[_noop_tool("core_a")], guidance="核心指引"),
            Skill(id="beta", name="测试", description="", default_enabled=False,
                  tools=[_noop_tool("beta_a")], guidance="测试指引"),
        ]
        sm = SkillManager(skills)
        block = sm.build_guidance_block()
        assert "核心指引" in block and "测试指引" not in block
        sm.enable("beta")
        assert "测试指引" in sm.build_guidance_block()

    def test_tool_to_skill_map(self):
        sm = SkillManager(self._skills())
        m = sm.tool_to_skill()
        assert m["alpha_a"] == "alpha" and m["core_a"] == "core"

    def test_persistence_roundtrip(self, tmp_path):
        state = tmp_path / "agent" / "skills.json"
        sm = SkillManager(self._skills(), state_path=state)
        sm.enable("beta")
        sm.disable("alpha")
        assert state.exists()
        # reload from disk
        sm2 = SkillManager(self._skills(), state_path=state)
        assert sm2.is_enabled("beta") and not sm2.is_enabled("alpha")
        assert sm2.is_enabled("core")  # required always on

    def test_corrupt_state_falls_back_to_defaults(self, tmp_path):
        state = tmp_path / "skills.json"
        state.write_text("{ not json", encoding="utf-8")
        sm = SkillManager(self._skills(), state_path=state)
        assert sm.is_enabled("alpha") and not sm.is_enabled("beta")

    def test_unknown_id_in_state_ignored(self, tmp_path):
        state = tmp_path / "skills.json"
        state.write_text(json.dumps({"disabled": ["ghost", "alpha"]}), encoding="utf-8")
        sm = SkillManager(self._skills(), state_path=state)
        assert not sm.is_enabled("alpha")
        assert "ghost" not in sm.disabled_ids()

    def test_required_in_state_forced_enabled(self, tmp_path):
        state = tmp_path / "skills.json"
        state.write_text(json.dumps({"disabled": ["core"]}), encoding="utf-8")
        sm = SkillManager(self._skills(), state_path=state)
        assert sm.is_enabled("core")


# ===========================================================================
# Built-in bundles
# ===========================================================================
class TestBuiltinSkills:
    def test_six_builtin_skills(self):
        ids = [s.id for s in BUILTIN_SKILLS]
        assert ids == ["core", "model_catalog", "local_system",
                       "drama_studio", "writing_studio", "media_prompt_studio"]

    def test_default_active_count(self):
        sm = build_skill_manager()
        # core(3)+catalog(5)+system(3)+drama(5) = 16; writing/media opt-in
        assert len(sm.active_tool_names()) == 16
        assert not sm.is_enabled("writing_studio")
        assert not sm.is_enabled("media_prompt_studio")

    def test_all_enabled_is_23(self):
        sm = build_skill_manager()
        for sid in ("writing_studio", "media_prompt_studio"):
            sm.enable(sid)
        assert len(sm.active_tool_names()) == 23
        assert len(all_skill_tools()) == 23

    def test_global_tool_name_uniqueness(self):
        names = [t.name for t in all_skill_tools()]
        assert len(names) == len(set(names))

    def test_legacy_flat_registry_preserved(self):
        reg = build_default_registry()
        assert len(reg.list_names()) == len(ALL_TOOLS) == 11
        # legacy registry has no creative-pack tools
        assert "drama_storycraft" not in reg.list_names()
        assert "build_image_prompt" not in reg.list_names()

    def test_every_skill_has_guidance(self):
        for sk in BUILTIN_SKILLS:
            assert sk.guidance.strip(), f"{sk.id} missing guidance"


# ===========================================================================
# media prompt studio
# ===========================================================================
class TestMediaPromptTools:
    def _reg(self):
        from app.agent.tools.media_prompt_tools import MEDIA_PROMPT_TOOLS
        reg = ToolRegistry()
        for t in MEDIA_PROMPT_TOOLS:
            reg.register(t)
        return reg

    def test_image_prompt_full_package(self):
        out = self._reg().execute("build_image_prompt", {
            "subject": "赛博侦探少女", "style": "赛博朋克", "ratio": "9:16",
            "negative_tags": ["塑料感"],
        }, ToolContext())
        for key in ("positive", "negative", "negative_tags", "theme_constraints", "parameters"):
            assert key in out
        assert "赛博侦探少女" in out["positive"]
        assert "塑料感" in out["negative"]
        assert out["parameters"]["ratio"] == "9:16"
        assert any("一致" in c for c in out["theme_constraints"])

    def test_image_requires_subject(self):
        out = self._reg().execute("build_image_prompt", {}, ToolContext())
        assert "error" in out

    def test_image_bad_ratio_rejected(self):
        out = self._reg().execute("build_image_prompt",
                                  {"subject": "x", "ratio": "99:1"}, ToolContext())
        assert "error" in out

    def test_video_requires_action_and_clamps_duration(self):
        reg = self._reg()
        assert "error" in reg.execute("build_video_prompt",
                                      {"subject": "x"}, ToolContext())
        out = reg.execute("build_video_prompt", {
            "subject": "机甲", "action": "转身举盾", "duration_s": 999,
        }, ToolContext())
        assert out["parameters"]["duration_s"] == 15
        assert any("变脸" in c for c in out["theme_constraints"])

    def test_music_prompt_clamps_tempo(self):
        out = self._reg().execute("build_music_prompt",
                                  {"mood": "紧张", "tempo_bpm": 5}, ToolContext())
        assert out["parameters"]["tempo_bpm"] == 40
        assert out["modality"] == "music" and out["negative"]


# ===========================================================================
# writing studio
# ===========================================================================
class TestWritingTools:
    def _reg(self):
        from app.agent.tools.writing_tools import WRITING_TOOLS
        reg = ToolRegistry()
        for t in WRITING_TOOLS:
            reg.register(t)
        return reg

    def test_outline_scaffold_offline(self):
        out = self._reg().execute("writing_outline",
                                  {"topic": "本地AI工作站", "doc_type": "方案"}, ToolContext())
        assert out["doc_type"] == "方案"
        assert len(out["outline"]) >= 3
        assert all("heading" in s for s in out["outline"])
        assert "llm_prompt" in out

    def test_outline_requires_topic(self):
        assert "error" in self._reg().execute("writing_outline", {}, ToolContext())

    def test_summary_extractive(self):
        text = ("本地模型可以离线运行。离线运行保护用户隐私。"
                "今天天气很好我们去散步。本地模型还能降低使用成本。")
        out = self._reg().execute("writing_summary",
                                  {"text": text, "max_sentences": 2}, ToolContext())
        assert out["extractive_summary"]
        assert len(out["key_sentences"]) == 2
        assert out["stats"]["sentence_count"] == 4

    def test_polish_stats_and_checklist(self):
        out = self._reg().execute("writing_polish",
                                  {"text": "这个东西非常的好。", "goal": "精简"}, ToolContext())
        assert out["goal"] == "精简" and out["checklist"] and out["stats"]["chars"] > 0

    def test_translate_without_llm_returns_prompt(self):
        out = self._reg().execute("writing_translate",
                                  {"text": "你好世界", "target_lang": "英文"}, ToolContext())
        assert out["target_lang"] == "英文" and "llm_prompt" in out and "note" in out


# ===========================================================================
# drama methodology port
# ===========================================================================
class TestDramaStorycraft:
    def test_reference_shape(self):
        from app import drama
        ref = drama.storycraft_reference()
        assert set(ref["modes"]) == {"micro_film", "hook_drama"}
        assert len(ref["beats"]) >= 8
        assert len(ref["director_styles"]) == 6
        assert isinstance(ref["animation_styles"], list) and ref["animation_styles"]
        assert len(ref["four_stage_deliverables"]) == 4

    def test_normalize_mode_unknown_falls_back(self):
        from app import drama
        assert drama._normalize_mode("garbage") == drama.DEFAULT_STORY_MODE
        assert drama._normalize_mode("HOOK_DRAMA") == "hook_drama"

    def test_normalize_beat_maps_synonyms(self):
        from app import drama
        assert drama._normalize_beat("climax") == "climax"
        assert drama._normalize_beat("高潮爆发") == "climax"
        assert drama._normalize_beat("自定义奇怪节拍") == "自定义奇怪节拍"

    def test_normalize_script_new_fields_and_clamps(self, monkeypatch):
        from app import drama
        canned = {
            "title": "测试剧", "logline": "一句话", "synopsis": "梗概正文",
            "genre": "科幻", "mode": "hook_drama", "style": "大卫·芬奇风格的电影",
            "characters": [{"name": "林岚", "role": "主角", "motivation": "查明真相",
                            "arc": "怯懦到果敢", "key_prop": "旧怀表"}],
            "scene_registry": [{"name": "天台", "kind": "主场景", "mood": "压抑"}],
            "scenes": [{
                "scene_id": 1, "location": "天台", "time": "夜", "beat": "开场钩子",
                "shots": [
                    {"shot_id": 1, "action": "回望", "dialogue": "你来了",
                     "beat": "hook", "duration_s": 99},
                    {"shot_id": 2, "action": "奔跑", "duration_s": 3},
                ],
            }, {
                # 未登记场景 -> 自动补登记
                "scene_id": 2, "location": "地下车库", "shots": [{"action": "对峙"}],
            }],
        }
        monkeypatch.setattr(drama, "_call_llm",
                            lambda *a, **k: "```json\n" + json.dumps(canned, ensure_ascii=False) + "\n```")
        script = drama.generate_script("测试主题", "", {}, mode="hook_drama",
                                       style_anchor="大卫·芬奇风格的电影")
        assert script["mode"] == "hook_drama"
        assert script["mode_label"]
        assert script["synopsis"] == "梗概正文"
        # duration clamped to _MAX_SHOT_SECONDS
        assert script["scenes"][0]["shots"][0]["duration_s"] == drama._MAX_SHOT_SECONDS
        # beat normalized to library key
        assert script["scenes"][0]["shots"][0]["beat"] == "hook"
        # character new fields preserved
        c0 = script["characters"][0]
        assert c0["motivation"] and c0["arc"] and c0["key_prop"]
        # unregistered scene auto-added
        names = [r["name"] for r in script["scene_registry"]]
        assert "地下车库" in names
        assert "地下车库" in script["registry_auto_added"]
        # every registry row coded
        assert all("scene_code" in r for r in script["scene_registry"])

    def test_normalize_script_requires_scenes(self):
        from app import drama
        with pytest.raises(drama.LlmOutputError):
            drama._normalize_script({"title": "空", "scenes": []})


# ===========================================================================
# Agent + SkillManager integration
# ===========================================================================
class TestAgentSkillIntegration:
    def _agent(self, tmp_path, state_path=None):
        catalog_dir = Path(__file__).resolve().parent.parent.parent / "catalog"
        from app.catalog import load_catalog
        catalog, engines = load_catalog(catalog_dir)
        mem = AgentMemory(tmp_path / "agent.sqlite3")
        sm = build_skill_manager(state_path)
        ctx = ToolContext(catalog=catalog, engines_catalog=engines, memory=mem,
                          models_dir=tmp_path, app_root=tmp_path)
        return Agent(memory=mem, router=ModelRouter(), ctx=ctx, skill_manager=sm), sm

    def test_system_prompt_contains_enabled_guidance(self, tmp_path):
        agent, sm = self._agent(tmp_path)
        prompt = agent._build_system_prompt()
        assert "短剧创作工坊" in prompt          # drama enabled by default
        assert "写作工坊" not in prompt           # writing opt-in, off
        sm.enable("writing_studio")
        agent.reload_skills()
        assert "写作工坊" in agent._build_system_prompt()

    @pytest.mark.asyncio
    async def test_rule_route_drama_storycraft(self, tmp_path):
        agent, _ = self._agent(tmp_path)
        res = await agent.run("帮我写个逆袭爽剧短剧", session_id="s1")
        assert res.success
        assert "drama_storycraft" in res.tools_used
        assert "钩子" in res.answer

    @pytest.mark.asyncio
    async def test_disabled_catalog_search_falls_through(self, tmp_path):
        agent, sm = self._agent(tmp_path)
        sm.disable("model_catalog")
        agent.reload_skills()
        assert "search_models" not in agent.registry.list_names()
        res = await agent.run("搜索音乐模型", session_id="s2")
        # must not claim to have run a missing tool
        assert "search_models" not in res.tools_used

    @pytest.mark.asyncio
    async def test_media_prompt_route_when_enabled(self, tmp_path):
        agent, sm = self._agent(tmp_path)
        sm.enable("media_prompt_studio")
        agent.reload_skills()
        res = await agent.run("帮我写一个赛博城市的图像提示词", session_id="s3")
        assert "build_image_prompt" in res.tools_used
        assert "负面" in res.answer

    def test_reload_skills_rebuilds_registry(self, tmp_path):
        agent, sm = self._agent(tmp_path)
        n0 = len(agent.registry.list_names())
        sm.enable("writing_studio")
        # before reload, registry unchanged
        assert len(agent.registry.list_names()) == n0
        agent.reload_skills()
        assert len(agent.registry.list_names()) == n0 + 4


# ===========================================================================
# HTTP skills API
# ===========================================================================
class TestSkillsAPI:
    @pytest.fixture()
    def client(self, tmp_path, monkeypatch):
        from app import main as M
        from fastapi.testclient import TestClient
        monkeypatch.setattr(M, "APP_ROOT", tmp_path)
        M._AGENT_SINGLETON.clear()
        with TestClient(M.app) as c:
            yield c
        M._AGENT_SINGLETON.clear()

    def test_list_skills(self, client):
        r = client.get("/api/agent/skills")
        assert r.status_code == 200
        d = r.json()
        assert d["count"] == 6 and d["active_count"] == 4
        assert d["active_tool_count"] == 16

    def test_toggle_changes_tool_count(self, client):
        r = client.post("/api/agent/skills/writing_studio", json={"enabled": True})
        assert r.status_code == 200 and r.json()["active_tool_count"] == 20
        tools = client.get("/api/agent/tools").json()["tools"]
        assert any(t["name"] == "writing_outline" for t in tools)

    def test_disable_core_returns_400(self, client):
        r = client.post("/api/agent/skills/core", json={"enabled": False})
        assert r.status_code == 400

    def test_unknown_skill_returns_404(self, client):
        r = client.post("/api/agent/skills/nope_skill", json={"enabled": True})
        assert r.status_code == 404

    def test_reset(self, client):
        client.post("/api/agent/skills/writing_studio", json={"enabled": True})
        r = client.post("/api/agent/skills/reset")
        assert r.status_code == 200 and r.json()["active_tool_count"] == 16

    def test_storycraft_endpoint(self, client):
        r = client.get("/api/drama/storycraft")
        assert r.status_code == 200
        assert set(r.json()["modes"]) == {"micro_film", "hook_drama"}
