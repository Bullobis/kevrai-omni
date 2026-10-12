// renderer/__tests__/agent-stream.test.js — the live "typing" answer bubble.
import { test } from "node:test";
import assert from "node:assert/strict";
import { setupDom } from "./helpers/dom.js";

const { window, document } = setupDom('<div id="agent-messages"></div>');

const { appendDelta, StreamBubble } = await import("../modules/agent-stream.js");

// Minimal esc + a stub Markdown renderer that wraps the answer in a marker so we
// can tell the deferred render from the raw streaming text.
function esc(s) {
  return String(s).replace(/[&<>"]/g, (m) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;",
  }[m]));
}
function renderMarkdown(md) {
  return `<md>${esc(md)}</md>`;
}

test("appendDelta concatenates deltas", () => {
  assert.equal(appendDelta("", "你好"), "你好");
  assert.equal(appendDelta("你好", "，世界"), "你好，世界");
  assert.equal(appendDelta("x", null), "x");
});

test("streaming shows escaped plain text, then finalize renders Markdown", () => {
  const container = document.getElementById("agent-messages");
  const bubble = new StreamBubble(container, { renderMarkdown, esc });

  // While streaming: raw escaped text + caret, NOT markdown.
  bubble.appendDelta("Hello ");
  bubble.appendDelta("<b>raw</b>");
  const live = bubble.div.querySelector(".agent-msg-stream-text");
  assert.ok(live.textContent.includes("Hello <b>raw</b>"));
  // The angle brackets must be escaped (no real <b> injected).
  assert.ok(live.innerHTML.includes("&lt;b&gt;"));
  assert.ok(bubble.div.querySelector(".agent-stream-caret"));

  // On finalize: the complete answer is rendered through renderMarkdown.
  let wired = null;
  bubble.finalize("Hello <b>raw</b>\n\n```py\nprint(1)\n```", {
    tools: ["rec_tool"],
    onWire: (div, text) => { wired = { div, text }; },
  });
  const md = bubble.div.querySelector(".agent-msg-md");
  assert.ok(md, "finalized bubble has markdown content");
  assert.ok(md.innerHTML.startsWith("<md>"));
  assert.ok(bubble.div.querySelector('[data-msg-action="copy"]'));
  assert.ok(bubble.div.querySelector(".agent-tool-tag"));
  assert.equal(wired.text, "Hello <b>raw</b>\n\n```py\nprint(1)\n```");
});

test("appendDelta after finalize is ignored", () => {
  const container = document.getElementById("agent-messages");
  const bubble = new StreamBubble(container, { renderMarkdown, esc });
  bubble.appendDelta("partial");
  bubble.finalize("final answer");
  bubble.appendDelta("should-not-appear");
  assert.ok(!bubble.div.textContent.includes("should-not-appear"));
});

test("abort keeps streamed text and marks stopped", () => {
  const container = document.getElementById("agent-messages");
  const bubble = new StreamBubble(container, { renderMarkdown, esc });
  bubble.appendDelta("partial answer");
  bubble.abort("\n\n（已取消）");
  assert.ok(bubble.div.textContent.includes("partial answer"));
  assert.ok(bubble.div.textContent.includes("已取消"));
});
