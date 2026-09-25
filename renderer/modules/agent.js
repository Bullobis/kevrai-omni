// renderer/modules/agent.js — Kevrai Agent 通用 AI 助手面板
// 基于 ReAct 循环的本地 Agent：自然语言管理模型（搜索/推荐/硬件/下载规划）
// 借鉴 OpenClaw 的 Gateway+Runtime 架构，定制为 Kevrai Omni 本地模型管理场景
"use strict";
import { api } from "./api.js";
import { toast } from "./toast.js";
import { unwrap } from "./net.js";
import { t } from "./i18n.js";

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
          <p class="hint">${t("agent.hint")}
            <span id="agent-mode-badge" class="agent-badge">${t("agent.loading")}</span>
          </p>
        </div>
        <div class="agent-actions">
          <button id="agent-new-btn" class="btn btn-sm" title="${t("agent.newSessionTitle")}">${t("agent.newSession")}</button>
          <select id="agent-session-select" class="agent-session-select" title="${t("agent.historyTitle")}"></select>
        </div>
      </div>
      <details class="agent-skills-panel" id="agent-skills-panel">
        <summary>
          🧩 ${t("agent.skillsLibrary")}
          <span class="agent-skills-summary" id="agent-skills-summary">${t("agent.loading")}</span>
        </summary>
        <div class="agent-skills-body">
          <div class="agent-skills-toolbar">
            <span class="hint">${t("agent.skillsHint")}</span>
            <button id="agent-skills-reset" class="btn btn-sm" type="button">${t("agent.resetDefault")}</button>
          </div>
          <div id="agent-skills-list" class="agent-skills-list">${t("agent.loading")}</div>
          <div class="agent-skillhub">
            <div class="agent-skillhub-head">
              <span class="agent-skillhub-title">${t("agent.importSkillTitle")}</span>
              <button id="agent-skillhub-toggle" class="btn btn-sm" type="button">${t("agent.expand")}</button>
            </div>
            <div id="agent-skillhub-body" class="agent-skillhub-body" hidden>
              <p class="hint">${t("agent.skillhubHint")}</p>
              <div class="agent-skillhub-inputs">
                <input id="agent-skillhub-path" class="agent-skillhub-input" type="text"
                  placeholder="${t("agent.dirPlaceholder")}" maxlength="4096">
                <button id="agent-skillhub-import-dir" class="btn btn-sm" type="button">${t("agent.importDir")}</button>
                <button id="agent-skillhub-import-zip" class="btn btn-sm" type="button">${t("agent.importZip")}</button>
              </div>
              <div class="agent-skillhub-inputs">
                <input id="agent-skillhub-git" class="agent-skillhub-input" type="text"
                  placeholder="${t("agent.gitPlaceholder")}" maxlength="2048">
                <button id="agent-skillhub-import-git" class="btn btn-sm" type="button">${t("agent.importFromRepo")}</button>
              </div>
              <div id="agent-skillhub-list" class="agent-skillhub-list">${t("agent.notLoaded")}</div>
            </div>
          </div>
        </div>
      </details>
      <div id="agent-messages" class="agent-messages"></div>
      <div class="agent-input-area">
        <textarea id="agent-input" class="agent-input" rows="2"
          placeholder="${t("agent.inputPlaceholder")}"
          maxlength="5000"></textarea>
        <button id="agent-send-btn" class="btn btn-primary agent-send-btn" disabled>${t("agent.send")}</button>
      </div>
      <div id="agent-thinking" class="agent-thinking" style="display:none;">
        <span class="agent-spinner"></span>
        <span id="agent-thinking-text">${t("agent.thinking")}</span>
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
    toast(t("agent.sessionCreated"), { kind: "info" });
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
        toast(cb.checked ? t("agent.skillAdded", { id }) : t("agent.skillDisabled", { id }), { kind: "ok" });
        await Promise.all([_loadSkills(), _refreshStatus()]);
      } catch (err) {
        cb.checked = !cb.checked; // 回滚 UI
        toast(t("agent.toggleFailed", { err: err.message || err }), { kind: "err" });
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
        toast(t("agent.skillsReset"), { kind: "ok" });
        await Promise.all([_loadSkills(), _refreshStatus()]);
      } catch (err) {
        toast(t("agent.resetFailed", { err: err.message || err }), { kind: "err" });
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
    toggle.textContent = box.hidden ? t("agent.expand") : t("agent.collapse");
    if (!box.hidden) _loadSkillHub();
  });

  const pathInput = $("#agent-skillhub-path", root);
  const gitInput = $("#agent-skillhub-git", root);

  const runImport = async (btn, fn, label) => {
    const raw = (pathInput && pathInput.value || "").trim();
    btn.disabled = true;
    try {
      await fn(raw);
      toast(t("agent.importOk", { label }), { kind: "ok" });
      if (pathInput) pathInput.value = "";
      await Promise.all([_loadSkillHub(), _loadSkills(), _refreshStatus()]);
    } catch (err) {
      toast(t("agent.importFailed", { label, err: err.message || err }), { kind: "err" });
    } finally {
      btn.disabled = false;
    }
  };

  const dirBtn = $("#agent-skillhub-import-dir", root);
  if (dirBtn) {
    dirBtn.addEventListener("click", () => {
      const raw = (pathInput && pathInput.value || "").trim();
      if (!raw) { toast(t("agent.needDirPath"), { kind: "err" }); return; }
      runImport(dirBtn, (p) => api.skillHubImportDir(p), "导入目录");
    });
  }
  const zipBtn = $("#agent-skillhub-import-zip", root);
  if (zipBtn) {
    zipBtn.addEventListener("click", () => {
      const raw = (pathInput && pathInput.value || "").trim();
      if (!raw) { toast(t("agent.needZipPath"), { kind: "err" }); return; }
      runImport(zipBtn, (p) => api.skillHubImportZip(p), "导入 zip");
    });
  }
  const gitBtn = $("#agent-skillhub-import-git", root);
  if (gitBtn) {
    gitBtn.addEventListener("click", async () => {
      const url = (gitInput && gitInput.value || "").trim();
      if (!url) { toast(t("agent.needGitUrl"), { kind: "err" }); return; }
      gitBtn.disabled = true;
      try {
        await api.skillHubImportGit(url);
        toast(t("agent.gitImportOk"), { kind: "ok" });
        if (gitInput) gitInput.value = "";
        await Promise.all([_loadSkillHub(), _loadSkills(), _refreshStatus()]);
      } catch (err) {
        toast(t("agent.gitImportFailed", { err: err.message || err }), { kind: "err" });
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
        toast(t("agent.skillRemoved", { id }), { kind: "ok" });
        await Promise.all([_loadSkillHub(), _loadSkills(), _refreshStatus()]);
      } catch (err) {
        toast(t("agent.removeFailed", { err: err.message || err }), { kind: "err" });
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
      list.innerHTML = `<span class="hint">${t("agent.noImportedSkills")}</span>`;
      return;
    }
    list.innerHTML = items.map(_renderHubRow).join("");
  } catch (e) {
    list.innerHTML = `<span class="hint">${t("agent.skillLoadFailed", { err: esc(e.message || e) })}</span>`;
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
        badge.textContent = t("agent.llmReady", { model: esc(res.model_name || "") });
        badge.className = "agent-badge agent-badge-ready";
      } else {
        badge.textContent = t("agent.ruleMode");
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
      summary.textContent = t("agent.skillsSummary", { active, total: res.count ?? skills.length, tools: res.active_tool_count ?? "" });
    }
    list.innerHTML = skills.map(_renderSkillRow).join("") || `<span class="hint">${t("agent.noSkills")}</span>`;
  } catch (e) {
    list.innerHTML = `<span class="hint">${t("agent.skillLoadFailed", { err: esc(e.message || e) })}</span>`;
    if (summary) summary.textContent = t("agent.loadFailed");
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
    select.innerHTML = `<option value="">${t("agent.historyPlaceholder")}</option>`;
    for (const s of res.sessions || []) {
      const opt = document.createElement("option");
      opt.value = s.id;
      opt.textContent = t("agent.sessionLabel", { id: s.id, n: s.message_count || 0 });
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
  const regenBtn = role === "assistant"
    ? `<button type="button" class="agent-msg-btn" data-msg-action="regen" title="重新生成">↻ 重新生成</button>`
    : "";
  div.innerHTML = `
    <div class="agent-msg-label">${esc(label)}</div>
    <div class="agent-msg-content">${esc(content).replace(/\n/g, "<br>")}</div>
    ${toolsHtml}
    <div class="agent-msg-actions">
      <button type="button" class="agent-msg-btn" data-msg-action="copy" title="复制">⧉ 复制</button>
      ${regenBtn}
    </div>
  `;
  container.appendChild(div);
  div.querySelector('[data-msg-action="copy"]').addEventListener("click", () => _copyText(content));
  div.querySelector('[data-msg-action="regen"]')?.addEventListener("click", () => _regenerate());
  container.scrollTop = container.scrollHeight;
}

async function _copyText(text) {
  try {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      await navigator.clipboard.writeText(text);
    } else {
      const ta = document.createElement("textarea");
      ta.value = text;
      document.body.appendChild(ta);
      ta.select();
      document.execCommand("copy");
      ta.remove();
    }
    toast("已复制", { kind: "ok" });
  } catch (_) {
    toast("复制失败", { kind: "err" });
  }
}

// Regenerate the last assistant answer (backend trims the final pair and
// re-runs); then redraw the whole session so the UI matches the store.
async function _regenerate() {
  if (_busy) return;
  try {
    _busy = true;
    await api.agentRegenerate(_sessionId);
    await _loadSessionMessages();
  } catch (e) {
    console.warn("regenerate failed:", e);
  } finally {
    _busy = false;
  }
}
