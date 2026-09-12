// renderer/modules/drama.js — AI 短剧生成 Agent（updream 式四步流水线）
// 头脑风暴 → 剧本 → 分镜表 → 渲染计划（可选 3D/图片/TTS/音乐/视频 模型）
"use strict";
import { api } from "./api.js";
import { toast } from "./toast.js";

const $ = (s, r) => (r || document).querySelector(s);

function esc(s) {
  return String(s == null ? "" : s).replace(/[&<>"']/g, (m) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[m]));
}

// 当前会话状态
let _opts = null;        // dramaOptions 返回
let _topic = "";         // 创意主题（修复原隐式全局）
let _mode = "micro_film"; // 剧作基调：micro_film 微电影三幕式 / hook_drama 短视频钩子驱动
let _styleAnchor = "";   // 风格锚点（导演/动画流派）
let _storycraft = null;  // 剧作方法论库 storycraft_reference()
let _angle = "";         // 头脑风暴方向
let _questions = [];     // 引导问题
let _answers = {};       // 用户回答 {q1: ...}
let _script = null;      // 剧本
let _storyboard = null;  // 分镜表
let _plan = null;        // 渲染计划
let _busy = false;

const MODALITY_LABEL = {
  dialogue: "对话 AI",
  image: "文生图",
  scene3d: "3D 资产",
  tts: "语音合成",
  music: "音乐 / 音频",
  video: "文生视频",
};

export async function renderDramaPage(root) {
  root.innerHTML = `
    <div class="hw-toolbar">
      <div>
        <h2 class="hw-title">🎬 短剧 Agent</h2>
        <p class="hint">四步生成一集竖屏短剧：头脑风暴 → 剧本 → 分镜表 → 多模态渲染计划。
          对话 AI 工作流：① 下载 MNN 引擎 → ② 模型市场下载对话模型 → ③ 选 MNN 引擎运行；
          图像 / 3D / 音频 / 视频走模型市场中的本地引擎。</p>
      </div>
      <div class="hw-toolbar-actions">
        <button class="secondary" id="drama-refresh">刷新</button>
      </div>
    </div>
    <div id="drama-status"></div>
    <div id="drama-step1" class="drama-card"></div>
    <div id="drama-step2" class="drama-card" hidden></div>
    <div id="drama-step3" class="drama-card" hidden></div>
    <div id="drama-step4" class="drama-card" hidden></div>
  `;

  $("#drama-refresh", root).addEventListener("click", () => refresh(root));
  await refresh(root);
}

async function refresh(root) {
  try {
    const r = await api.dramaOptions();
    _opts = r?.body || r || {};
  } catch (e) {
    _opts = null;
  }
  if (!_storycraft) {
    try {
      const sc = await api.dramaStorycraft();
      _storycraft = sc?.body || sc || null;
    } catch (e) {
      _storycraft = null;
    }
  }
  renderStatus(root);
  renderStep1(root);
}

function renderStatus(root) {
  const el = $("#drama-status", root);
  if (!_opts) { el.innerHTML = `<p class="hint">无法获取模型选项（sidecar 未返回）。</p>`; return; }
  const ds = _opts.dialogue_status || {};
  const engineOk = !!ds.engine_available;
  const loaded = ds.loaded
    ? `<span class="pill ok">已加载 ${esc(ds.model_name || "")}</span>`
    : `<span class="pill">对话 AI 未就绪 —— ${engineOk ? "① 引擎已装 → ② 到「模型市场」下载对话模型 → ③ 在「MNN 引擎」页选 MNN 加载运行" : "① 先到「AI 引擎」页下载 MNN 引擎 → ② 模型市场下载对话模型 → ③ 选 MNN 引擎运行"}</span>`;
  const counts = [
    ["image", "图"], ["scene3d", "3D"], ["tts", "语音"],
    ["audio", "音频"], ["video", "视频"], ["llm", "LLM"],
  ].map(([k, n]) => `${n} ${((_opts[k] || []).length)}`).join(" · ");
  el.innerHTML = `
    <div class="mnn-state-card ${ds.loaded ? "ok" : ""}">
      <div class="grow">
        <div class="name">短剧流水线状态</div>
        <div class="sub">${loaded}</div>
        <div class="sub">对话 AI 可选模型：${ds.dialogue_count || 0} 个（模型市场 MNN 版 + 官方市场）</div>
        <div class="sub">模型市场可用：${esc(counts)}</div>
      </div>
    </div>`;
}

// ---------------------------------------------------------------------------
// Step 1 — 头脑风暴
// ---------------------------------------------------------------------------

const DIRECTOR_GROUP_LABEL = {
  oriental_life: "东方生活流/家庭治愈",
  neon_retro: "港风霓虹/怀旧胶片",
  symmetric_author: "高饱和作者美学",
  cold_precise: "极简冷冽/精密构图",
  poetic_natural: "诗性自然/影展作者",
  social_realist: "社会现实主义/纪实",
};

function _styleAnchorOptions() {
  if (!_storycraft) return "";
  const groups = [];
  const dirs = _storycraft.director_styles || {};
  for (const [g, items] of Object.entries(dirs)) {
    const opts = (items || []).map((d) =>
      `<option value="${esc(d.anchor || "")}">${esc(d.anchor || "")}（${esc(d.fit || "")}）</option>`
    ).join("");
    if (opts) groups.push(`<optgroup label="真人·${esc(DIRECTOR_GROUP_LABEL[g] || g)}">${opts}</optgroup>`);
  }
  const anim = Array.isArray(_storycraft.animation_styles) ? _storycraft.animation_styles : [];
  const aopts = anim.map((v) =>
    `<option value="${esc(v.anchor || "")}">${esc(v.anchor || "")}（${esc(v.fit || "")}）</option>`
  ).join("");
  if (aopts) groups.push(`<optgroup label="动画流派">${aopts}</optgroup>`);
  return groups.join("");
}

function renderStep1(root) {
  const el = $("#drama-step1", root);
  const modes = (_storycraft?.modes) || {};
  const mf = modes.micro_film || {};
  const hd = modes.hook_drama || {};
  el.innerHTML = `
    <h3 class="section">① 头脑风暴</h3>
    <p class="hint">输入一个模糊创意，AI 导演会引导你锚定故事方向。</p>
    <label class="field">
      <span>剧作基调</span>
      <div class="drama-mode-row" id="drama-mode-row">
        <label class="drama-mode-opt">
          <input type="radio" name="drama-mode" value="micro_film" ${_mode === "micro_film" ? "checked" : ""}>
          <span><b>微电影三幕式</b><em>${esc(mf.label || "铺陈→冲突→高潮落点，人物弧完整")}</em></span>
        </label>
        <label class="drama-mode-opt">
          <input type="radio" name="drama-mode" value="hook_drama" ${_mode === "hook_drama" ? "checked" : ""}>
          <span><b>短视频钩子驱动</b><em>${esc(hd.label || "3秒强钩子→冲突堆叠→爽点释放")}</em></span>
        </label>
      </div>
    </label>
    <label class="field">
      <span>风格锚点（具体到导演/动画流派，防止风格漂移；可留空由 AI 自选）</span>
      <select id="drama-style-anchor">
        <option value="">— 不指定 / AI 自选 —</option>
        ${_styleAnchorOptions()}
      </select>
    </label>
    <label class="field">
      <span>创意主题</span>
      <textarea id="drama-topic" rows="2" maxlength="1000"
        placeholder="例：一个古装女法医穿越到赛博都市查连环失踪案">${esc(_topic || "")}</textarea>
    </label>
    <div class="row"><button id="drama-bt-bs" class="primary">开始头脑风暴</button></div>
    <div id="drama-bs-out"></div>
  `;
  const topicEl = $("#drama-topic", root);
  const styleEl = $("#drama-style-anchor", root);
  if (_styleAnchor && styleEl) styleEl.value = _styleAnchor;
  $("#drama-mode-row", root).querySelectorAll("input[name='drama-mode']").forEach((rb) => {
    rb.addEventListener("change", () => { if (rb.checked) _mode = rb.value; });
  });
  if (styleEl) styleEl.addEventListener("change", () => { _styleAnchor = styleEl.value; });
  $("#drama-bt-bs", root).addEventListener("click", async () => {
    const topic = topicEl.value.trim();
    if (!topic) { toast("请先输入创意主题", { kind: "err" }); return; }
    _topic = topic;
    _styleAnchor = styleEl ? styleEl.value : "";
    await brainstorm(root, topic);
  });
}

async function brainstorm(root, topic) {
  if (_busy) return;
  _busy = true;
  const out = $("#drama-bs-out", root);
  out.innerHTML = `<p class="hint">AI 导演思考中（调用本地对话模型，基调：${_mode === "hook_drama" ? "钩子驱动" : "微电影三幕式"}）…</p>`;
  try {
    const r = await api.dramaBrainstorm({ topic, mode: _mode });
    const body = r?.body || r || {};
    _angle = body.angle || "";
    _questions = Array.isArray(body.questions) ? body.questions : [];
    _answers = {};
    if (_questions.length) {
      out.innerHTML = `
        <div class="drama-block">
          <div class="sub"><b>方向锚定：</b>${esc(_angle || "（无）")}</div>
          ${_questions.map((q, i) => `
            <label class="field">
              <span>Q${i + 1}. ${esc(q)}</span>
              <textarea data-qi="${i}" rows="2" maxlength="2000" placeholder="你的回答（可选，不填则 AI 自行发挥）"></textarea>
            </label>`).join("")}
          <div class="row">
            <button id="drama-bt-script" class="primary">② 生成剧本</button>
            <button id="drama-bt-again" class="secondary">换个角度再来一轮</button>
          </div>
        </div>`;
      out.querySelectorAll("[data-qi]").forEach((ta) => {
        ta.addEventListener("input", () => { _answers["q" + (+ta.dataset.qi + 1)] = ta.value.trim(); });
      });
      $("#drama-bt-script", out).addEventListener("click", () => genScript(root));
      $("#drama-bt-again", out).addEventListener("click", () => brainstorm(root, topic));
    } else {
      out.innerHTML = `<p class="hint">对话 AI 未返回引导问题：${esc(body.detail || "")}</p>`;
    }
  } catch (e) {
    out.innerHTML = `<p class="hint err">头脑风暴失败：${esc(e?.message || e)}</p>`;
  } finally {
    _busy = false;
  }
}

// ---------------------------------------------------------------------------
// Step 2 — 剧本
// ---------------------------------------------------------------------------

async function genScript(root) {
  if (_busy) return;
  _busy = true;
  const step2 = $("#drama-step2", root);
  step2.hidden = false;
  step2.innerHTML = `<h3 class="section">② 剧本生成中…</h3><p class="hint">正在调用对话 AI 创作结构化剧本（可能需要几十秒）</p>`;
  try {
    const r = await api.dramaScript({
      topic: _topic, angle: _angle, answers: _answers,
      mode: _mode, style_anchor: _styleAnchor,
    });
    _script = r?.body?.script || r?.script || null;
    if (!_script) throw new Error("未返回剧本");
    renderScript(root);
  } catch (e) {
    step2.innerHTML = `<h3 class="section">② 剧本</h3><p class="hint err">剧本生成失败：${esc(e?.message || e)}</p>`;
  } finally {
    _busy = false;
  }
}

function renderScript(root) {
  const step2 = $("#drama-step2", root);
  const s = _script;
  const chars = (s.characters || []).map((c) => `
    <div class="drama-char">
      <b>${esc(c.name)}</b> ${esc(c.age || "")}
      ${c.role ? `<span class="pill">${esc(c.role)}</span>` : ""}<br/>
      <span class="sub">外形：${esc(c.appearance || "—")}</span><br/>
      <span class="sub">音色：${esc(c.voice || "—")}</span>
      ${c.motivation ? `<br/><span class="sub">动机：${esc(c.motivation)}</span>` : ""}
      ${c.arc ? `<br/><span class="sub">成长弧：${esc(c.arc)}</span>` : ""}
      ${c.key_prop ? `<br/><span class="sub">关键道具：${esc(c.key_prop)}</span>` : ""}
    </div>`).join("");
  // 场景登记清单（先登记后使用）
  const registry = Array.isArray(s.scene_registry) ? s.scene_registry : [];
  const registryHtml = registry.length ? `
    <details class="drama-details" open>
      <summary>主要故事场景登记（${registry.length}）</summary>
      ${registry.map((r) => `
        <div class="sub">· <b>${esc(r.name || r.scene_code || "")}</b>
          ${r.kind ? `［${esc(r.kind)}］` : ""} ${esc(r.mood || "")}
          ${r.props ? `｜关键陈设：${esc(r.props)}` : ""}</div>`).join("")}
    </details>` : "";
  const autoAdded = s.registry_auto_added || [];
  const warnHtml = autoAdded.length
    ? `<p class="hint err">提示：以下场景模型未预先登记，已自动补登记：${esc(autoAdded.join("、"))}</p>` : "";
  const scenes = (s.scenes || []).map((sc) => `
    <div class="drama-scene">
      <div class="sub"><b>场景 ${sc.scene_id}</b> · ${esc(sc.location || "")} · ${esc(sc.time || "")}
        ${sc.beat ? `<span class="pill">节拍·${esc(sc.beat)}</span>` : ""}</div>
      <div class="sub">${esc(sc.summary || "")}</div>
      ${(sc.shots || []).map((sh) => `
        <div class="drama-shot">
          <span class="pill">镜 ${sh.shot_id}</span>
          ${sh.beat ? `<span class="pill">${esc(sh.beat)}</span>` : ""}
          <span class="sub">${esc(sh.shot_type || "中景")} / ${esc(sh.camera || "固定")} / ${sh.duration_s}s</span>
          <div class="sub">${esc(sh.action || "")}</div>
          ${sh.dialogue ? `<div class="sub">🎙 ${esc(sh.dialogue)}</div>` : ""}
        </div>`).join("")}
    </div>`).join("");
  step2.innerHTML = `
    <h3 class="section">② 剧本</h3>
    <div class="drama-block">
      <div class="name">${esc(s.title || "未命名短剧")}</div>
      <div class="sub">${esc(s.logline || "")}</div>
      ${s.synopsis ? `<details class="drama-details" open><summary>剧情梗概</summary><div class="sub">${esc(s.synopsis).replace(/\n/g, "<br>")}</div></details>` : ""}
      <div class="sub">
        <span class="pill">${esc(s.genre || "未知")}</span>
        <span class="pill">${esc(s.style || "")}</span>
        ${s.mode_label ? `<span class="pill">${esc(s.mode_label)}</span>` : ""}
        <span class="pill">${esc(s.music_mood || "")}</span>
        <span class="pill">${s.shot_count || 0} 镜</span>
        <span class="pill">≈ ${s.est_duration_s || 0}s</span>
      </div>
      ${warnHtml}
      ${registryHtml}
      <h4>角色</h4>${chars || `<p class="hint">无</p>`}
      <h4>分场</h4>${scenes}
      <div class="row"><button id="drama-bt-sb" class="primary">③ 生成分镜表</button></div>
    </div>`;
  $("#drama-bt-sb", step2).addEventListener("click", () => genStoryboard(root));
}

// ---------------------------------------------------------------------------
// Step 3 — 分镜表
// ---------------------------------------------------------------------------

async function genStoryboard(root) {
  if (_busy) return;
  _busy = true;
  const step3 = $("#drama-step3", root);
  step3.hidden = false;
  step3.innerHTML = `<h3 class="section">③ 分镜表生成中…</h3><p class="hint">规则化补齐 3D / TTS / 音乐渲染字段</p>`;
  try {
    const r = await api.dramaStoryboard({ script: _script });
    _storyboard = r?.body || r || {};
    if (!_storyboard.shots) throw new Error("未返回分镜");
    renderStoryboard(root);
  } catch (e) {
    step3.innerHTML = `<h3 class="section">③ 分镜表</h3><p class="hint err">分镜生成失败：${esc(e?.message || e)}</p>`;
  } finally {
    _busy = false;
  }
}

function renderStoryboard(root) {
  const step3 = $("#drama-step3", root);
  const sb = _storyboard;
  const rows = (sb.shots || []).map((sh) => `
    <div class="drama-shot">
      <div class="sub">
        <span class="pill">镜 ${sh.shot_id}</span>
        ${sh.beat ? `<span class="pill">${esc(sh.beat)}</span>` : ""}
        <span class="sub">${esc(sh.shot_type || "")} / ${esc(sh.camera || "")} / ${sh.duration_s}s</span>
      </div>
      <div class="sub">🎙 ${esc(sh.dialogue || "（无台词）")}</div>
      <div class="sub">🗣 音色：${esc(sh.tts_voice || "—")}</div>
      <details class="drama-details">
        <summary>画面提示词 / 3D 资产 / 风格</summary>
        <div class="sub">画面：${esc(sh.visual_prompt || "—")}</div>
        <div class="sub">3D：${esc(sh.scene3d_prompt || "—")}</div>
        <div class="sub">风格：${esc(sh.style_tag || "—")} · 音乐：${esc(sh.music_hint || "—")}</div>
        <div class="sub">TTS 文本：${esc(sh.tts_text || "—")}</div>
      </details>
    </div>`).join("");
  step3.innerHTML = `
    <h3 class="section">③ 分镜表（${sb.shot_count || 0} 镜）</h3>
    <div class="drama-block">${rows}</div>
    <div class="row"><button id="drama-bt-plan" class="primary">④ 配置渲染计划</button></div>`;
  $("#drama-bt-plan", step3).addEventListener("click", () => renderStep4(root));
}

// ---------------------------------------------------------------------------
// Step 4 — 渲染计划（选择各模态模型）
// ---------------------------------------------------------------------------

function renderStep4(root) {
  const step4 = $("#drama-step4", root);
  step4.hidden = false;
  const opts = _opts || {};
  const selected = _plan?.choices || {};
  const modal = (key, catKey, need) => {
    const list = opts[catKey] || [];
    const options = list.map((m) => {
      const eng = (m.engine || []).map((e) => `引擎 ${esc(e)}`).join(", ");
      return `<option value="${esc(m.id)}">${esc(m.name)} (${m.size_gb || "?"}GB${eng ? " · " + eng : ""})</option>`;
    }).join("");
    if (!list.length) return "";
    return `
      <label class="field">
        <span>${MODALITY_LABEL[key]}（可选）</span>
        <select data-modal="${key}">
          <option value="">不使用</option>
          ${options}
        </select>
      </label>`;
  };
  step4.innerHTML = `
    <h3 class="section">④ 渲染计划</h3>
    <p class="hint">为每个镜头选择下游多模态模型（可多选；不选则跳过该模态）。</p>
    <div class="drama-block">
      ${modal("image", "image")}
      ${modal("scene3d", "scene3d")}
      ${modal("tts", "tts")}
      ${modal("music", "audio")}
      ${modal("video", "video")}
      <div class="row"><button id="drama-bt-render" class="primary">生成渲染计划</button></div>
    </div>
    <div id="drama-plan-out"></div>`;
  // 回填上次选择
  Object.entries(selected).forEach(([k, v]) => {
    const sel = $(`[data-modal="${k}"]`, step4);
    if (sel) sel.value = v;
  });
  $("#drama-bt-render", step4).addEventListener("click", async () => {
    const model_choices = {};
    step4.querySelectorAll("[data-modal]").forEach((sel) => {
      if (sel.value) model_choices[sel.dataset.modal] = sel.value;
    });
    await genPlan(root, model_choices);
  });
}

async function genPlan(root, model_choices) {
  if (_busy) return;
  _busy = true;
  const out = $("#drama-plan-out", root);
  out.innerHTML = `<p class="hint">生成逐镜头渲染指令卡…</p>`;
  try {
    const r = await api.dramaRenderPlan({ storyboard: _storyboard, model_choices });
    _plan = r?.body || r || {};
    if (!_plan.shots) throw new Error("未返回渲染计划");
    renderPlan(root);
  } catch (e) {
    out.innerHTML = `<p class="hint err">渲染计划失败：${esc(e?.message || e)}</p>`;
  } finally {
    _busy = false;
  }
}

function renderPlan(root) {
  const out = $("#drama-plan-out", root);
  const plan = _plan;
  const choices = Object.entries(plan.choices || {})
    .map(([k, v]) => `<span class="pill">${MODALITY_LABEL[k] || k}: ${esc(v)}</span>`).join(" ");
  const rows = (plan.shots || []).map((sh) => {
    const mods = Object.entries(sh.modalities || {}).map(([k, m]) => `
      <details class="drama-details">
        <summary>${MODALITY_LABEL[k] || k} · ${esc(m.model_name || m.model_id || "")}（${m.size_gb || "?"}GB）</summary>
        <div class="sub">${esc(m.prompt || m.text || "")}</div>
        ${m.voice ? `<div class="sub">音色：${esc(m.voice)}</div>` : ""}
        ${m.duration_s ? `<div class="sub">时长：${m.duration_s}s</div>` : ""}
      </details>`).join("");
    return `
      <div class="drama-shot">
        <div class="sub"><span class="pill">镜 ${sh.shot_id}</span> ${esc(sh.shot_type || "")} / ${esc(sh.camera || "")}</div>
        ${sh.dialogue ? `<div class="sub">🎙 ${esc(sh.dialogue)}</div>` : ""}
        ${mods || `<span class="sub">（本镜无模态指令）</span>`}
      </div>`;
  }).join("");
  out.innerHTML = `
    <div class="drama-block">
      <div class="sub">已选模型：${choices || "（未选择任何模态模型）"}</div>
      ${rows}
    </div>`;
}
