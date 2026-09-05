// renderer/modules/agent.js — Kevrai Agent 通用 AI 助手面板
// 基于 ReAct 循环的本地 Agent：自然语言管理模型（搜索/推荐/硬件/下载规划）
// 借鉴 OpenClaw 的 Gateway+Runtime 架构，定制为 Kevrai Omni 本地模型管理场景
"use strict";
import { api } from "./api.js";
import { toast } from "./toast.js";

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
  _loadSessionList();
}

function _renderShell() {
  return `
    <div class="agent-container">
      <div class="agent-header">
        <div>
          <h2 class="section">🤖 Kevrai Agent</h2>
          <p class="hint">用自然语言管理本地 AI 模型：搜索、推荐、硬件检测、下载规划。
            <span id="agent-mode-badge" class="agent-badge">加载中…</span>
          </p>
        </div>
        <div class="agent-actions">
          <button id="agent-new-btn" class="btn btn-sm" title="新建会话">＋ 新会话</button>
          <select id="agent-session-select" class="agent-session-select" title="历史会话"></select>
        </div>
      </div>
      <div id="agent-messages" class="agent-messages"></div>
      <div class="agent-input-area">
        <textarea id="agent-input" class="agent-input" rows="2"
          placeholder="问我任何关于模型的问题，例如：&#10;• 我的硬件能跑什么模型？&#10;• 搜索音乐生成模型&#10;• 推荐适合8GB显存的图像模型"
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
}

// ---------------------------------------------------------------------------
// 状态与会话
// ---------------------------------------------------------------------------
async function _refreshStatus() {
  try {
    const res = await api.agentStatus();
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

async function _loadSessionList() {
  const select = document.getElementById("agent-session-select");
  if (!select) return;
  try {
    const res = await api.agentSessions(20);
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
    const res = await api.agentSessionMessages(_sessionId, 100);
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
    const res = await api.agentChat({
      message,
      session_id: _sessionId,
    });

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
