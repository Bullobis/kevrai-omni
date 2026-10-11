// renderer/modules/agent-approval.js — pure, DOM-free helpers for the agent
// tool-call approval gate: build a pending-request card's HTML and parse/validate
// the JSON parameters the user may have edited before approving.
//
// Kept separate from agent.js so these rules (which JSON counts as valid edited
// params, how the card is structured) can be unit-tested without booting the
// whole agent panel or a window.kevrai bridge.
"use strict";

function esc(s) {
  return String(s == null ? "" : s).replace(/[&<>"']/g, (m) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[m]));
}

// Parse the edited-params textarea into the object to send on approve.
// Returns ``null`` when the field is blank (=> approve with the original
// params). Throws an Error with a human-readable message for anything that is
// not a JSON object (arrays / scalars are rejected — tool params are objects).
export function parseEditedParams(text) {
  const raw = String(text == null ? "" : text).trim();
  if (!raw) return null;
  let obj;
  try {
    obj = JSON.parse(raw);
  } catch (e) {
    throw new Error(`参数不是合法 JSON：${e.message || e}`);
  }
  if (obj === null || typeof obj !== "object" || Array.isArray(obj)) {
    throw new Error("参数必须是 JSON 对象");
  }
  return obj;
}

// Build the HTML for one pending approval card. The params are rendered into an
// editable <textarea>; the approve button reads it back and routes through
// parseEditedParams(). Caller wires the data-* buttons via event delegation.
export function approvalCardHtml(req) {
  const paramsText = esc(JSON.stringify(req.tool_params || {}, null, 2));
  return `
      <div class="agent-approval-card" data-id="${esc(req.id)}">
        <div class="agent-approval-head">
          ⏸️ 等待确认工具调用
          <code class="agent-approval-tool">${esc(req.tool_name)}</code>
        </div>
        <textarea class="agent-approval-params" spellcheck="false"
          title="可编辑参数后再批准">${paramsText}</textarea>
        <div class="agent-approval-actions">
          <button type="button" class="btn btn-primary btn-sm" data-approval-approve>✅ 批准运行</button>
          <button type="button" class="btn btn-sm agent-msg-btn-danger" data-approval-reject>✋ 拒绝</button>
        </div>
      </div>`;
}
