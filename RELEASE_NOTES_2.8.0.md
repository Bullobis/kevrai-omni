# Kevrai Omni v2.8.0 — 可插拔技能库 + 短剧编剧方法论移植

**发布日期**: 2026-09-12
**版本**: 2.8.0
**代号**: Skills

---

## 概述

v2.8.0 把 v2.7.0 扁平的 11 工具 Agent 升级为**可插拔技能（Skill）系统**：每个技能是一组工具 + 一段领域方法论（注入系统提示词），用户可在 Agent 面板的「🧩 技能库」里按需勾选添加 / 关闭，状态本地持久化；核心技能不可关闭。同时把专业短剧编剧方法论（双基调结构、情绪节拍库、导演/动画风格锚点、场景先登记后使用、四段产物）移植进短剧 Agent，并新增「写作工坊」「多模态提示词工坊」两个可选技能。工具总数由 11 扩展到 23（默认启用 16）。

---

## 新增功能

### 1. 可插拔技能系统（`python/app/agent/skill.py`）

- **Skill**：元数据（id/name/description/version/category/icon/required/default_enabled）+ 工具列表 + guidance 方法论。
- **SkillManager**：启用/禁用/切换/重置、active 工具物化、guidance 编译、工具→技能反查。
- **持久化**：启停状态写入 `agent/skills.json`（原子写 .tmp 再 replace），与 SQLite 会话记忆解耦；损坏 / 含未知 id / 含必备技能时安全回退默认。
- **保护规则**：`core` 必备技能禁关；技能 id 与工具名构造期校验，**跨技能工具名重复直接报错**，一个工具只属于一个技能。
- **向后兼容**：`ALL_TOOLS`（11）与 `build_default_registry()` 原样保留，旧测试与外部调用不受影响。

### 2. 六个内置技能（默认 16 工具，全开 23）

| 技能 id | 名称 | 默认 | 工具数 | 工具 |
|---|---|---|---|---|
| `core` | 核心助手 | 必备·常开 | 3 | get_preferences / set_preference / generate_text |
| `model_catalog` | 模型市场检索 | 开 | 5 | search_models / model_info / recommend_models / list_installed / list_categories |
| `local_system` | 本机环境与下载 | 开 | 3 | check_hardware / list_engines / download_model |
| `drama_studio` | 短剧创作工坊 | 开 | 5 | drama_storycraft / drama_brainstorm / drama_compose_script / drama_storyboard / drama_render_plan |
| `writing_studio` | 写作工坊 | **关（按需添加）** | 4 | writing_outline / writing_polish / writing_summary / writing_translate |
| `media_prompt_studio` | 多模态提示词工坊 | **关（按需添加）** | 3 | build_image_prompt / build_video_prompt / build_music_prompt |

### 3. 短剧编剧方法论移植（`python/app/drama.py`）

方法论移植自专业短剧创作流程，并改造为「LLM 提示词注入 + Python 规则钳制」，不引入对话式分批确认等宿主交互：

- **双剧作基调** `STORY_MODES`：`micro_film` 微电影三幕式 / `hook_drama` 短视频钩子驱动，各含章节结构比例与创作原则。
- **情绪节拍库** `BEAT_LIBRARY`（9 种：hook/conflict/escalate/break_ice/turn/payoff/climax/closure/transition），中英文自由文本归一到标准键。
- **风格锚点库**：6 大真人导演流派组 + 5 种动画流派，要求风格字段具体到导演/流派，杜绝「电影感/高级质感」这类模糊词。
- **四段产物**：剧情梗概 synopsis、人物小传（role/motivation/arc/key_prop）、场景登记清单 scene_registry、分场分镜（含 beat）。
- **场景先登记后使用**：本地小模型未遵守时自动补登记并记录 `registry_auto_added`，不中断流水线。
- 数量/时长钳制沿用并扩展：单镜最长 8s、场景/角色/每场景镜头数上限。

### 4. 两个新工具包

- **写作工坊**：大纲骨架、润色清单+文本统计、抽取式摘要（词频+位置加权）、翻译规范；离线确定性可用，`use_llm=true` 且已加载对话模型时附带本地模型成品。
- **多模态提示词工坊**：为图像/视频/音乐编译提示词包，**每个结果都含 positive 正向、negative 负面（内置负面词库）、theme_constraints 主题一致性约束**，并做画幅白名单、时长/BPM 钳制——直接服务后续「提示词必须含负面词与主题约束」的视频生产要求。

### 5. Agent 集成与规则回退增强（`python/app/agent/agent.py`）

- 构造函数接入 SkillManager，`reload_skills()` 在技能切换后重建活动工具表。
- 系统提示词新增「已启用技能（领域方法论）」区块，只注入启用技能的 guidance。
- 规则回退（无 LLM）新增短剧 / 提示词 / 写作大纲三条确定性路由，并给原有硬件、搜索路由加**工具启用守卫**：技能关闭后不会尝试调用缺失工具。

### 6. HTTP / IPC / 前端

- 新增 `GET /api/agent/skills`、`POST /api/agent/skills/{id}`、`POST /api/agent/skills/reset`、`GET /api/drama/storycraft`；drama brainstorm/script 透传 `mode`、`style_anchor`。
- preload / main 三层 IPC 全打通；Agent 面板新增可折叠「🧩 技能库」（开关、工具标签、必备/默认标记、恢复默认、切换后实时刷新工具数）。
- 短剧页新增基调单选（微电影/钩子驱动）、导演/动画风格锚点下拉；剧本展示剧情梗概、场景登记清单、人物动机/成长弧/关键道具与节拍标签；修复 `_topic` 隐式全局。

---

## 测试与验证

- 新增 `python/tests/test_v280_skills.py`（**54 个用例**）：Skill/SkillManager 校验与持久化、内置技能计数、三个工具包、drama 方法论（罐装 LLM，不依赖真模型）、Agent+技能集成、HTTP API（含 400/404）。
- 全量 `pytest`：**475 passed**（v2.7.0 基线 421 + 新增 54），零回归。
- `scripts/smoke.sh` 全绿：catalog JSON/schema、密钥扫描、pip、全量 pytest、所有 JS `node --check`、electron-builder 校验。
- 极端测试：空/空白/超长输入、非法画幅/模式、负时长与越界 BPM 钳制、工具 handler 抛异常不炸注册表、损坏 skills.json、只留 core 技能的回退路径。
- 实机验证：真实 `uvicorn` 起服走 HTTP 验证 skills/toggle/reset/storycraft/规则路由；三层 IPC 命名逐一交叉核对一致。

---

## 版本号

| 文件 | 2.7.0 → 2.8.0 |
|---|---|
| `python/app/__init__.py` | ✅ |
| `package.json` / `package-lock.json` | ✅ |
| `catalog/models.json` / `catalog/engines.json`（顶层 version） | ✅ |
| `python/tests/test_v260_catalog.py`（版本断言） | ✅ |

## 兼容性

- 完全向后兼容：旧的扁平注册表 API 保留；默认启用技能与 v2.7.0 行为一致（额外默认开启短剧工坊）。
- 新增本地状态文件 `agent/skills.json`，删除即恢复默认，不影响会话记忆。
