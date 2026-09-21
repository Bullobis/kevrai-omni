// renderer/modules/agent.js — Kevrai Agent 通用 AI 助手面板
// 基于 ReAct 循环的本地 Agent：自然语言管理模型（搜索/推荐/硬件/下载规划）
// 借鉴 OpenClaw 的 Gateway+Runtime 架构，定制为 Kevrai Omni 本地模型管理场景
"use strict";
import { api } from "./api.js";
import { toast } from "./toast.js";
import { unwrap } from "./net.js";

const $ = (s, r) => (r || document).querySelector(s);

function esc(s) {
  return String(s == null ? "" : s).replace(/[&<>"']/g, (m) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[m]));
}

let _sessionId = "default";
let _busy = false;
let _status = { llm_ready: false, mode: "rule_based", tool_count: 0 };

// ---------------------------------------------------------------------------
// 初始化
// ---------------------------------------------------------------------------
export async function initAgent() {
  const root = document.getElementById("agent-root");
  if (!root) return;
  root.innerHTML = _renderShell();
  _wireEvents(root);
  await _refreshStatus();
  _loadSkills();
  _loadSessionList();
}

function _renderShell() {
  return `
    <div class="agent-container">
      <div class="agent-header">
        <div>
          <h2 class="section">🤖 Kevrai Agent</h2>
          <p class="hint">用自然语言管理本地 AI 模型：搜索、推荐、硬件检测、下载规划。
            短剧创作已并入本面板（内置技能「短剧创作工坊」）。
            <span id="agent-mode-badge" class="agent-badge">加载中…</span>
          </p>
        </div>
        <div class="agent-actions">
          <button id="agent-new-btn" class="btn btn-sm" title="新建会话">＋ 新会话</button>
          <select id="agent-session-select" class="agent-session-select" title="历史会话"></select>
        </div>
      </div>
      <details class="agent-skills-panel" id="agent-skills-panel">
        <summary>
          🧩 技能库
          <span class="agent-skills-summary" id="agent-skills-summary">加载中…</span>
        </summary>
        <div class="agent-skills-body">
          <div class="agent-skills-toolbar">
            <span class="hint">勾选即可为 Agent 添加技能（核心技能不可关闭），设置会自动保存。</span>
            <button id="agent-skills-reset" class="btn btn-sm" type="button">恢复默认</button>
          </div>
          <div id="agent-skills-list" class="agent-skills-list">加载中…</div>
          <div class="agent-skillhub">
            <div class="agent-skillhub-head">
              <span class="agent-skillhub-title">📥 导入外部技能（SKILL.md）</span>
              <button id="agent-skillhub-toggle" class="btn btn-sm" type="button">展开</button>
            </div>
            <div id="agent-skillhub-body" class="agent-skillhub-body" hidden>
              <p class="hint">支持三种来源：本地目录 / .zip 压缩包 / git 仓库（含
                <code>.claude-plugin/marketplace.json</code> 的插件市场）。导入的技能
                <strong>不会覆盖或删除内置技能</strong>。</p>
              <div class="agent-skillhub-inputs">
                <input id="agent-skillhub-path" class="agent-skillhub-input" type="text"
                  placeholder="本地目录路径，或 .zip 文件路径" maxlength="4096">
                <button id="agent-skillhub-import-dir" class="btn btn-sm" type="button">导入目录</button>
                <button id="agent-skillhub-import-zip" class="btn btn-sm" type="button">导入 zip</button>
              </div>
              <div class="agent-skillhub-inputs">
                <input id="agent-skillhub-git" class="agent-skillhub-input" type="text"
                  placeholder="git 仓库地址，例如 https://github.com/owner/skills" maxlength="2048">
                <button id="agent-skillhub-import-git" class="btn btn-sm" type="button">从仓库导入</button>
              </div>
              <div id="agent-skillhub-list" class="agent-skillhub-list">尚未加载</div>
            </div>
          </div>
        </div>
      </details>
      <div id="agent-messages" class="agent-messages"></div>
      <div class="agent-input-area">
        <textarea id="agent-input" class="agent-input" rows="2"
          placeholder="问我任何关于模型的问题，例如：&#10;• 我的硬件能跑什么模型？&#10;• 搜索音乐生成模型&#10;• 推荐适合8GB显存的图像模型&#10;• 帮我把「星际快递员」写成一部微电影短剧（短剧工坊技能）"
          maxlength="5000"></textarea>
        <button id="agent-send-btn" class="btn btn-primary agent-send-btn" disabled>发送</button>
      </div>
      <div id="agent-thinking" class="agent-thinking" style="display:none;">
        <span class="agent-spinner"></span>
        <span id="agent-thinking-text">思考中…</span>
      </div>
    </div>
  `;
}

// ---------------------------------------------------------------------------
// 事件绑定
// ---------------------------------------------------------------------------
function _wireEvents(root) {
  const input = $("#agent-input", root);
  const sendBtn = $("#agent-send-btn", root);
  const newBtn = $("#agent-new-btn", root);
  const sessionSelect = $("#agent-session-select", root);

  input.addEventListener("input", () => {
    sendBtn.disabled = !input.value.trim() || _busy;
  });
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      _sendMessage();
    }
  });
  sendBtn.addEventListener("click", _sendMessage);
  newBtn.addEventListener("click", () => {
    _sessionId = "sess_" + Date.now().toString(36);
    $("#agent-messages", root).innerHTML = "";
    input.value = "";
    sendBtn.disabled = true;
    _loadSessionList();
    toast("已新建会话", { kind: "info" });
  });
  sessionSelect.addEventListener("change", () => {
    if (sessionSelect.value) {
      _sessionId = sessionSelect.value;
      _loadSessionMessages();
    }
  });

  // 技能库：开关（事件委托）+ 恢复默认
  const skillsList = $("#agent-skills-list", root);
  if (skillsList) {
    skillsList.addEventListener("change", async (e) => {
      const cb = e.target.closest(".agent-skill-toggle");
      if (!cb) return;
      const id = cb.dataset.id;
      cb.disabled = true;
      try {
        await api.agentToggleSkill(id, cb.checked);
        toast(cb.checked ? `已添加技能：${id}` : `已关闭技能：${id}`, { kind: "ok" });
        await Promise.all([_loadSkills(), _refreshStatus()]);
      } catch (err) {
        cb.checked = !cb.checked; // 回滚 UI
        toast(`切换失败：${err.message || err}`, { kind: "err" });
      } finally {
        cb.disabled = false;
      }
    });
  }
  const resetBtn = $("#agent-skills-reset", root);
  if (resetBtn) {
    resetBtn.addEventListener("click", async () => {
      resetBtn.disabled = true;
      try {
        await api.agentResetSkills();
        toast("已恢复默认技能", { kind: "ok" });
        await Promise.all([_loadSkills(), _refreshStatus()]);
      } catch (err) {
        toast(`恢复失败：${err.message || err}`, { kind: "err" });
      } finally {
        resetBtn.disabled = false;
      }
    });
  }
  _wireSkillHub(root);
}

// ---------------------------------------------------------------------------
// Skill hub（导入外部 SKILL.md 技能，v2.9.0）
// CSP 为 script-src 'self'，内联事件处理器会被拦截，全部走 addEventListener。
// ---------------------------------------------------------------------------
function _wireSkillHub(root) {
  const box = $("#agent-skillhub-body", root);
  const toggle = $("#agent-skillhub-toggle", root);
  if (!box || !toggle) return;

  toggle.addEventListener("click", () => {
    box.hidden = !box.hidden;
    toggle.textContent = box.hidden ? "展开" : "收起";
    if (!box.hidden) _loadSkillHub();
  });

  const pathInput = $("#agent-skillhub-path", root);
  const gitInput = $("#agent-skillhub-git", root);

  const runImport = async (btn, fn, label) => {
    const raw = (pathInput && pathInput.value || "").trim();
    btn.disabled = true;
    try {
      await fn(raw);
      toast(`${label}成功`, { kind: "ok" });
      if (pathInput) pathInput.value = "";
      await Promise.all([_loadSkillHub(), _loadSkills(), _refreshStatus()]);
    } catch (err) {
      toast(`${label}失败：${err.message || err}`, { kind: "err" });
    } finally {
      btn.disabled = false;
    }
  };

  const dirBtn = $("#agent-skillhub-import-dir", root);
  if (dirBtn) {
    dirBtn.addEventListener("click", () => {
      const raw = (pathInput && pathInput.value || "").trim();
      if (!raw) { toast("请先填写本地目录路径", { kind: "err" }); return; }
      runImport(dirBtn, (p) => api.skillHubImportDir(p), "导入目录");
    });
  }
  const zipBtn = $("#agent-skillhub-import-zip", root);
  if (zipBtn) {
    zipBtn.addEventListener("click", () => {
      const raw = (pathInput && pathInput.value || "").trim();
      if (!raw) { toast("请先填写 .zip 文件路径", { kind: "err" }); return; }
      runImport(zipBtn, (p) => api.skillHubImportZip(p), "导入 zip");
    });
  }
  const gitBtn = $("#agent-skillhub-import-git", root);
  if (gitBtn) {
    gitBtn.addEventListener("click", async () => {
      const url = (gitInput && gitInput.value || "").trim();
      if (!url) { toast("请先填写 git 仓库地址", { kind: "err" }); return; }
      gitBtn.disabled = true;
      try {
        await api.skillHubImportGit(url);
        toast("从仓库导入成功", { kind: "ok" });
        if (gitInput) gitInput.value = "";
        await Promise.all([_loadSkillHub(), _loadSkills(), _refreshStatus()]);
      } catch (err) {
        toast(`从仓库导入失败：${err.message || err}`, { kind: "err" });
      } finally {
        gitBtn.disabled = false;
      }
    });
  }

  // 删除已导入技能（事件委托）。内置技能后端返回 403，这里也不会渲染删除按钮。
  const list = $("#agent-skillhub-list", root);
  if (list) {
    list.addEventListener("click", async (e) => {
      const btn = e.target.closest(".agent-skillhub-remove");
      if (!btn) return;
      const id = btn.dataset.id;
      btn.disabled = true;
      try {
        await api.skillHubRemove(id);
        toast(`已删除导入技能：${id}`, { kind: "ok" });
        await Promise.all([_loadSkillHub(), _loadSkills(), _refreshStatus()]);
      } catch (err) {
        toast(`删除失败：${err.message || err}`, { kind: "err" });
        btn.disabled = false;
      }
    });
  }
}

async function _loadSkillHub() {
  const list = document.getElementById("agent-skillhub-list");
  if (!list) return;
  try {
    const res = unwrap(await api.skillHubList());
    const items = res.skills || [];
    if (!items.length) {
      list.innerHTML = '<span class="hint">尚未导入任何外部技能。</span>';
      return;
    }
    list.innerHTML = items.map(_renderHubRow).join("");
  } catch (e) {
    list.innerHTML = `<span class="hint">技能库加载失败：${esc(e.message || e)}</span>`;
  }
}

function _renderHubRow(it) {
  const ok = !!it.ok;
  const tags = [];
  if (it.has_scripts) tags.push('<span class="agent-tool-tag">scripts/</span>');
  if (it.has_references) tags.push('<span class="agent-tool-tag">references/</span>');
  if (it.has_assets) tags.push('<span class="agent-tool-tag">assets/</span>');
  return `
    <div class="agent-skillhub-row ${ok ? "" : "is-broken"}">
      <div class="agent-skillhub-rowhead">
        <span class="agent-skillhub-name">${esc(it.name || it.id)}</span>
        <span class="agent-skillhub-id">${esc(it.id)}</span>
        <button class="btn btn-sm agent-skillhub-remove" data-id="${esc(it.id)}"
          type="button" title="删除该导入技能">删除</button>
      </div>
      <p class="agent-skill-desc">${esc(ok ? (it.description || "") : ("解析失败：" + (it.error || "")))}</p>
      ${tags.length ? `<div class="agent-skill-tools">${tags.join("")}</div>` : ""}
    </div>
  `;
}

// ---------------------------------------------------------------------------
// 状态与会话
// ---------------------------------------------------------------------------
async function _refreshStatus() {
  try {
    const res = unwrap(await api.agentStatus());
    _status = res;
    const badge = document.getElementById("agent-mode-badge");
    if (badge) {
      if (res.llm_ready) {
        badge.textContent = `LLM 已就绪 (${esc(res.model_name || "")})`;
        badge.className = "agent-badge agent-badge-ready";
      } else {
        badge.textContent = "规则模式（未加载 LLM，基础工具可用）";
        badge.className = "agent-badge agent-badge-fallback";
      }
    }
  } catch (e) {
    console.warn("agent status failed:", e);
  }
}

// ---------------------------------------------------------------------------
// 技能库（可插拔技能，v2.8.0）
// ---------------------------------------------------------------------------
async function _loadSkills() {
  const list = document.getElementById("agent-skills-list");
  const summary = document.getElementById("agent-skills-summary");
  if (!list) return;
  try {
    const res = unwrap(await api.agentSkills());
    const skills = res.skills || [];
    const active = res.active_count ?? skills.filter((s) => s.enabled).length;
    if (summary) {
      summary.textContent = `${active}/${res.count ?? skills.length} 启用 · ${res.active_tool_count ?? ""} 个工具`;
    }
    list.innerHTML = skills.map(_renderSkillRow).join("") || '<span class="hint">暂无技能</span>';
  } catch (e) {
    list.innerHTML = `<span class="hint">技能库加载失败：${esc(e.message || e)}</span>`;
    if (summary) summary.textContent = "加载失败";
  }
}

function _renderSkillRow(s) {
  const locked = !!s.required;
  const tools = (s.tool_names || []).map((t) =>
    `<span class="agent-tool-tag">${esc(t)}</span>`
  ).join("");
  return `
    <div class="agent-skill-row ${s.enabled ? "is-on" : "is-off"}">
      <label class="agent-skill-head">
        <input type="checkbox" class="agent-skill-toggle" data-id="${esc(s.id)}"
          ${s.enabled ? "checked" : ""} ${locked ? "disabled" : ""}>
        <span class="agent-skill-icon">${esc(s.icon || "🧩")}</span>
        <span class="agent-skill-name">${esc(s.name)}
          ${locked ? '<span class="agent-skill-lock" title="必备技能，不可关闭">必备</span>' : ""}
          ${s.default_enabled && !locked ? '<span class="agent-skill-default">默认</span>' : ""}
          ${s.source === "imported" ? '<span class="agent-skill-imported" title="通过技能库导入">外部</span>' : ""}
        </span>
      </label>
      <p class="agent-skill-desc">${esc(s.description || "")}</p>
      <div class="agent-skill-tools">${tools}</div>
    </div>
  `;
}

async function _loadSessionList() {
  const select = document.getElementById("agent-session-select");
  if (!select) return;
  try {
    const res = unwrap(await api.agentSessions(20));
    select.innerHTML = '<option value="">— 历史会话 —</option>';
    for (const s of res.sessions || []) {
      const opt = document.createElement("option");
      opt.value = s.id;
      opt.textContent = `${s.id} (${s.message_count || 0} 条)`;
      if (s.id === _sessionId) opt.selected = true;
      select.appendChild(opt);
    }
  } catch (e) {
    console.warn("session list failed:", e);
  }
}

async function _loadSessionMessages() {
  const container = document.getElementById("agent-messages");
  if (!container) return;
  container.innerHTML = "";
  try {
    const res = unwrap(await api.agentSessionMessages(_sessionId, 100));
    for (const msg of res.messages || []) {
      _appendMessage(msg.role, msg.content, false);
    }
    container.scrollTop = container.scrollHeight;
  } catch (e) {
    console.warn("load messages failed:", e);
  }
}

// ---------------------------------------------------------------------------
// 发送消息
// ---------------------------------------------------------------------------
async function _sendMessage() {
  if (_busy) return;
  const input = document.getElementById("agent-input");
  const sendBtn = document.getElementById("agent-send-btn");
  const thinking = document.getElementById("agent-thinking");
  const thinkingText = document.getElementById("agent-thinking-text");
  const message = input.value.trim();
  if (!message) return;

  _busy = true;
  sendBtn.disabled = true;
  input.disabled = true;
  thinking.style.display = "flex";
  thinkingText.textContent = "思考中…";

  _appendMessage("user", message, false);
  input.value = "";

  try {
    const res = unwrap(await api.agentChat({
      message,
      session_id: _sessionId,
    }));

    // 显示工具调用步骤
    if (res.steps && res.steps.length > 0) {
      for (const step of res.steps) {
        if (step.action_tool) {
          thinkingText.textContent = `调用工具: ${step.action_tool}`;
          await new Promise((r) => setTimeout(r, 200));
        }
      }
    }

    _appendMessage("assistant", res.answer, false, res.tools_used || []);
    _loadSessionList();
  } catch (e) {
    _appendMessage("assistant", `请求失败：${esc(e.message || e)}`, false);
    toast("Agent 请求失败", { kind: "err" });
  } finally {
    _busy = false;
    thinking.style.display = "none";
    input.disabled = false;
    sendBtn.disabled = !input.value.trim();
    input.focus();
  }
}

// ---------------------------------------------------------------------------
// 消息渲染
// ---------------------------------------------------------------------------
function _appendMessage(role, content, animate = false, tools = []) {
  const container = document.getElementById("agent-messages");
  if (!container) return;
  const div = document.createElement("div");
  div.className = `agent-msg agent-msg-${role}`;
  const label = role === "user" ? "你" : "Kevrai Agent";
  const toolsHtml = tools && tools.length
    ? `<div class="agent-tools-used">工具: ${tools.map((t) => `<span class="agent-tool-tag">${esc(t)}</span>`).join("")}</div>`
    : "";
  div.innerHTML = `
    <div class="agent-msg-label">${esc(label)}</div>
    <div class="agent-msg-content">${esc(content).replace(/\n/g, "<br>")}</div>
    ${toolsHtml}
  `;
  container.appendChild(div);
  container.scrollTop = container.scrollHeight;
}
