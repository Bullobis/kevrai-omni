// renderer/modules/agent-stream.js — live "typing" bubble for the agent's
// streamed final answer.
//
// The brain now streams its final answer token-by-token over /ws/agent as
// `answer_delta` events. While streaming we show the raw, still-incomplete text
// as escaped plain text (a half-written fenced code block must never flash broken
// Markdown). When the `final` event arrives we re-render the same bubble with
// the complete answer through the safe Markdown renderer (PR#192), so code
// blocks / copy buttons light up exactly like a normal assistant message.
"use strict";

import { t } from "./i18n.js";

// Pure accumulator: append a delta to the running raw answer. Split out so it
// can be unit-tested without a DOM.
export function appendDelta(current, delta) {
  return current + (delta == null ? "" : String(delta));
}

export class StreamBubble {
  // container: the #agent-messages element.
  // helpers: { renderMarkdown, esc } — injected so the class is testable.
  constructor(container, { renderMarkdown, esc }) {
    this.container = container;
    this.renderMarkdown = renderMarkdown;
    this.esc = esc;
    this.raw = "";
    this.done = false;

    this.div = document.createElement("div");
    this.div.className = "agent-msg agent-msg-assistant agent-msg-streaming";
    this.div.innerHTML = `
      <div class="agent-msg-label">Kevrai Agent</div>
      <div class="agent-msg-content agent-msg-stream-text"></div>
    `;
    container.appendChild(this.div);
    this.contentEl = this.div.querySelector(".agent-msg-stream-text");
    this._scroll();
  }

  _scroll() {
    this.container.scrollTop = this.container.scrollHeight;
  }

  // Append one streamed delta. Content is plain escaped text + a caret; the
  // heavy Markdown render is deferred to finalize().
  appendDelta(delta) {
    if (this.done) return;
    this.raw = appendDelta(this.raw, delta);
    this.contentEl.innerHTML =
      this.esc(this.raw) + '<span class="agent-stream-caret"></span>';
    this._scroll();
  }

  // Replace the live bubble with the fully-rendered assistant message.
  // answer: the complete final answer (from the `final` event).
  // opts: { traceHtml, tools, msgId, onWire(div, text) }
  finalize(answer, { traceHtml = "", tools = [], msgId = null, onWire = null } = {}) {
    this.done = true;
    const text = answer != null ? String(answer) : this.raw;
    this.raw = text;
    if (msgId != null) this.div.dataset.msgId = String(msgId);
    const toolsHtml = tools && tools.length
      ? `<div class="agent-tools-used">${t("agent.toolsUsedLabel")} ${
        tools.map((x) => `<span class="agent-tool-tag">${this.esc(x)}</span>`).join("")
      }</div>`
      : "";
    const regenBtn = `<button type="button" class="agent-msg-btn" data-msg-action="regen" title="${t("agent.regenTitle")}">↻ ${t("agent.regen")}</button>`;
    const deleteBtn = msgId != null
      ? `<button type="button" class="agent-msg-btn agent-msg-btn-danger" data-msg-action="delete-msg" title="${t("agent.deleteMsgTitle")}">🗑 ${t("agent.deleteMsg")}</button>`
      : "";
    this.div.classList.remove("agent-msg-streaming");
    this.div.classList.add("agent-msg-md");
    this.div.innerHTML = `
      <div class="agent-msg-label">Kevrai Agent</div>
      <div class="agent-msg-content agent-msg-md">${this.renderMarkdown(text)}</div>
      ${traceHtml || ""}
      ${toolsHtml}
      <div class="agent-msg-actions">
        <button type="button" class="agent-msg-btn" data-msg-action="copy" title="${t("agent.copyTitle")}">⧉ ${t("agent.copy")}</button>
        ${regenBtn}
        ${deleteBtn}
      </div>
    `;
    this._scroll();
    if (typeof onWire === "function") onWire(this.div, text);
  }

  // Mark the bubble as stopped (cancel / error) without discarding what we
  // already streamed.
  abort(note = "") {
    if (this.done) return;
    this.done = true;
    this.div.classList.remove("agent-msg-streaming");
    this.contentEl.innerHTML = this.esc(this.raw) + (note ? this.esc(note) : "");
    this._scroll();
  }
}
