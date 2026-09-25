// renderer/__tests__/a11y.test.js — focus-return.js 焦点管理 / Esc 优先级栈单测。
import { test } from "node:test";
import assert from "node:assert/strict";
import { setupDom } from "./helpers/dom.js";

setupDom(`
  <button id="trigger-before">打开</button>
  <div id="modal-top" hidden>
    <button id="top-first">顶部按钮</button>
    <input id="top-input" type="text" />
    <button id="top-last">底部按钮</button>
  </div>
  <div id="modal-low" hidden>
    <button id="low-first">下层按钮</button>
  </div>
`);

const { recordFocus, restoreFocus, trapFocus, registerEsc } =
  await import("../modules/focus-return.js");

test("recordFocus/restoreFocus: 焦点归还给打开前的触发元素", () => {
  const trigger = document.getElementById("trigger-before");
  trigger.focus();
  assert.equal(document.activeElement, trigger);

  const saved = recordFocus();
  assert.equal(saved, trigger);

  // 模拟模态打开期间焦点跑到模态里
  const top = document.getElementById("modal-top");
  top.removeAttribute("hidden");
  trapFocus(top);
  assert.notEqual(document.activeElement, trigger);

  restoreFocus(saved);
  assert.equal(document.activeElement, trigger);
});

test("restoreFocus: 触发元素已被移除时安全放弃，不抛错", () => {
  const ghost = document.createElement("button");
  document.body.appendChild(ghost);
  ghost.focus();
  const saved = recordFocus();
  ghost.remove();
  assert.equal(saved.isConnected, false);
  assert.equal(restoreFocus(saved), false);
  // null 也安全
  assert.equal(restoreFocus(null), false);
});

test("trapFocus: 打开时聚焦首个可聚焦元素，Tab 在首尾循环", () => {
  const top = document.getElementById("modal-top");
  top.removeAttribute("hidden");
  trapFocus(top);
  assert.equal(document.activeElement, document.getElementById("top-first"));

  // 在最后一个元素上按 Tab → 绕回首元素
  document.getElementById("top-last").focus();
  top.dispatchEvent(new KeyboardEvent("keydown", { key: "Tab", bubbles: true }));
  assert.equal(document.activeElement, document.getElementById("top-first"));

  // 在首元素上按 Shift+Tab → 绕回尾元素
  top.dispatchEvent(new KeyboardEvent("keydown", { key: "Tab", shiftKey: true, bubbles: true }));
  assert.equal(document.activeElement, document.getElementById("top-last"));
});

test("registerEsc: 只关闭最上层浮层，下层保持打开", () => {
  const top = document.getElementById("modal-top");
  const low = document.getElementById("modal-low");
  top.removeAttribute("hidden");
  low.removeAttribute("hidden");

  let topClosed = false;
  let lowClosed = false;
  registerEsc({
    order: 100, root: top,
    isOpen: () => !top.hasAttribute("hidden"),
    close: () => { topClosed = true; },
  });
  registerEsc({
    order: 50, root: low,
    isOpen: () => !low.hasAttribute("hidden"),
    close: () => { lowClosed = true; },
  });

  document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true, cancelable: true }));
  assert.equal(topClosed, true, "最上层（高 order）应被关闭");
  assert.equal(lowClosed, false, "下层浮层不应被关闭");
});

test("registerEsc editableFirst: 输入框内第一次 Esc 仅失焦，第二次才关闭", () => {
  const top = document.getElementById("modal-top");
  top.removeAttribute("hidden");
  let closed = false;
  registerEsc({
    order: 200, root: top, editableFirst: true,
    isOpen: () => !top.hasAttribute("hidden"),
    close: () => { closed = true; },
  });

  const input = document.getElementById("top-input");
  input.focus();
  assert.equal(document.activeElement, input);

  document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true, cancelable: true }));
  assert.equal(closed, false, "第一次 Esc 只 blur，不关闭");
  assert.notEqual(document.activeElement, input);

  document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true, cancelable: true }));
  assert.equal(closed, true, "第二次 Esc 关闭浮层");
});
