// renderer/__tests__/agent-slash.test.js — slash-context parsing, filtering and
// the composer popup DOM behavior.
import { test } from "node:test";
import assert from "node:assert/strict";
import { setupDom } from "./helpers/dom.js";

const html = `
  <div class="agent-input-area">
    <textarea id="agent-input"></textarea>
    <button id="agent-send-btn" type="button"></button>
  </div>`;
const { window, document } = setupDom(html);

const {
  slashContext, filterCommands, attachSlashMenu, SLASH_COMMANDS,
} = await import("../modules/agent-slash.js");

test("slashContext: slash at start", () => {
  const c = slashContext("/se", 3);
  assert.equal(c.begin, 0);
  assert.equal(c.q, "se");
});

test("slashContext: slash after whitespace", () => {
  const v = "hi /mu";
  const c = slashContext(v, v.length);
  assert.equal(c.begin, 3);
  assert.equal(c.q, "mu");
});

test("slashContext: no slash returns null", () => {
  assert.equal(slashContext("hello", 5), null);
  // slash not at word boundary (inside a word) -> null
  assert.equal(slashContext("abc/se", 6), null);
});

test("filterCommands matches id or description", () => {
  assert.equal(filterCommands("").length, SLASH_COMMANDS.length);
  const r = filterCommands("music");
  assert.ok(r.some(([id]) => id === "music"));
  assert.equal(filterCommands("zzz").length, 0);
});

test("typing slash opens the menu and lists rows", () => {
  const input = document.getElementById("agent-input");
  attachSlashMenu(input);
  input.value = "/";
  input.setSelectionRange(1, 1);
  input.dispatchEvent(new window.Event("input", { bubbles: true }));
  const menu = document.querySelector(".slash-menu");
  assert.ok(menu, "menu element exists");
  assert.equal(menu.hidden, false);
  assert.ok(menu.querySelectorAll(".slash-item").length > 5);
});

test("accept via Enter inserts the command and closes", () => {
  const input = document.getElementById("agent-input");
  input.value = "/se";
  input.setSelectionRange(3, 3);
  input.dispatchEvent(new window.Event("input", { bubbles: true }));
  input.dispatchEvent(new window.KeyboardEvent("keydown", {
    key: "Enter", bubbles: true, cancelable: true,
  }));
  const menu = document.querySelector(".slash-menu");
  assert.equal(menu.hidden, true);
  // first match for "se" is search -> inserts "/search "
  assert.ok(input.value.startsWith("/search "), `got ${input.value}`);
});
