// renderer/__tests__/shortcuts-compose.test.js — Ctrl+N (new Agent session) and
// Ctrl+Enter (send focused composer) keyboard behavior, plus cheat-sheet docs.
import { test } from "node:test";
import assert from "node:assert/strict";
import { setupDom } from "./helpers/dom.js";

const html = `
  <nav class="sidebar">
    <button class="pane-tab" data-tab="agent" type="button"></button>
  </nav>
  <button id="agent-new-btn" type="button"></button>
  <textarea id="agent-input"></textarea>
  <button id="agent-send-btn" type="button"></button>
  <textarea id="translate-source"></textarea>
  <button id="translate-run" type="button"></button>
  <input id="kb-question" />
  <button id="kb-ask-btn" type="button"></button>
  <input id="mm-question" />
  <button id="mm-run" type="button"></button>`;
const { window, document } = setupDom(html);

const { initCommandPalette } = await import("../modules/command-palette.js");
initCommandPalette();

function fire(target, key, extra = {}) {
  target.dispatchEvent(new window.KeyboardEvent("keydown", {
    key, bubbles: true, cancelable: true, ctrlKey: true, ...extra,
  }));
}
function clickFlag(id) {
  let n = 0;
  document.getElementById(id).addEventListener("click", () => { n += 1; });
  return () => n;
}

test("Ctrl+Enter clicks the send button of the focused composer", () => {
  const agentSent = clickFlag("agent-send-btn");
  fire(document.getElementById("agent-input"), "Enter");
  assert.equal(agentSent(), 1);

  const trSent = clickFlag("translate-run");
  fire(document.getElementById("translate-source"), "Enter");
  assert.equal(trSent(), 1);

  const kbSent = clickFlag("kb-ask-btn");
  fire(document.getElementById("kb-question"), "Enter");
  assert.equal(kbSent(), 1);

  const mmSent = clickFlag("mm-run");
  fire(document.getElementById("mm-question"), "Enter");
  assert.equal(mmSent(), 1);
});

test("Ctrl+Enter does not click a disabled send button", () => {
  const btn = document.getElementById("agent-send-btn");
  btn.disabled = true;
  let n = 0;
  btn.addEventListener("click", () => { n += 1; });
  fire(document.getElementById("agent-input"), "Enter");
  assert.equal(n, 0);
  btn.disabled = false;
});

test("Ctrl+Enter on an unrelated target does nothing", () => {
  const other = document.createElement("input");
  document.body.appendChild(other);
  let n = 0;
  document.getElementById("agent-send-btn").addEventListener("click", () => { n += 1; });
  fire(other, "Enter");
  assert.equal(n, 0);
});

test("Ctrl+N opens the Agent tab and starts a new session", () => {
  let tabClicks = 0;
  document.querySelector("[data-tab='agent']").addEventListener("click", () => { tabClicks += 1; });
  const newClicks = clickFlag("agent-new-btn");
  fire(document.body, "n");
  assert.equal(tabClicks, 1);
  assert.equal(newClicks(), 1);
});

test("cheat-sheet documents the new shortcuts", () => {
  // Ctrl+/ opens the help overlay.
  fire(document.body, "/");
  const kbds = [...document.querySelectorAll(".cmdpal-help kbd")].map((k) => k.textContent);
  assert.ok(kbds.includes("Ctrl N"), "Ctrl N documented");
  assert.ok(kbds.includes("Ctrl Enter"), "Ctrl Enter documented");
});
