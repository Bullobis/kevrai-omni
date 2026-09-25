// renderer/__tests__/resource-cleanup.test.js — R4 资源泄漏审计：
//   1. 视图可见性判断（isViewVisible / onViewState）
//   2. LTX 轮询在视图隐藏时 clearInterval、切回时恢复（mock setInterval）
//   3. MNN renderMnnPage 重复调用不叠加定时器 + 隐藏时暂停
//   4. initLtx 重复调用的 guard（不重复绑定监听器）
//   5. onViewChange unsubscribe 生效
"use strict";

import { test } from "node:test";
import assert from "node:assert/strict";
import { setupDom } from "./helpers/dom.js";

// ── 准备 DOM ────────────────────────────────────────────────────────────────
// pane-ltx 为静态壳（与 index.html 一致：pane-<name> + .active 标记可见）。
const bodyHtml = `
  <section id="pane-market" class="pane active"></section>
  <section id="pane-ltx" class="pane">
    <button id="ltx-generate"></button>
    <button id="ltx-cancel"></button>
    <button id="ltx-seed-random"></button>
    <input id="ltx-seed">
    <select id="ltx-preset"></select>
    <select id="ltx-mode"></select>
    <button id="ltx-refresh-outputs"></button>
    <button id="ltx-open-folder"></button>
    <div id="ltx-engine-badge"></div>
    <span id="ltx-install-hint"><code></code></span>
    <div id="ltx-active" hidden></div>
    <div id="ltx-progress" hidden></div>
    <div id="ltx-active-id"></div>
    <div id="ltx-active-state"></div>
    <div id="ltx-active-step"></div>
    <div id="ltx-active-elapsed"></div>
    <div id="ltx-task-list"></div>
    <div id="ltx-gallery"></div>
    <div id="ltx-status"></div>
  </section>
  <div id="mnn-root"></div>
`;

setupDom(bodyHtml);
const { window } = globalThis;
// mnn.js 模块顶层读 localStorage（kevrai_cvt_kind）。setupDom 用 opaque origin，
// jsdom 的 localStorage 不可用，这里给一个最小内存实现。
const _memStore = new Map();
globalThis.localStorage = {
  getItem: (k) => (_memStore.has(k) ? _memStore.get(k) : null),
  setItem: (k, v) => _memStore.set(k, String(v)),
  removeItem: (k) => _memStore.delete(k),
  clear: () => _memStore.clear(),
};

// ── mock 定时器：记账式 setInterval / clearInterval ─────────────────────────
const activeTimers = new Map();
let timerSeq = 0;
const realSetInterval = globalThis.setInterval;
const realClearInterval = globalThis.clearInterval;
globalThis.setInterval = (cb, ms) => {
  const id = ++timerSeq;
  activeTimers.set(id, cb);
  return id;
};
globalThis.clearInterval = (id) => { activeTimers.delete(id); };

// ── mock preload bridge（api.js 走 window.kevrai）───────────────────────────
window.kevrai = {
  ltxCapabilities: async () => ({ engine_ready: false, presets: [], install_hint: "", outputs_dir: "" }),
  ltxTasks: async () => ({ active: null, tasks: [] }),
  ltxOutputs: async () => ({ outputs: [] }),
  mnnStatus: async () => ({}),
  mnnLocal: async () => ({ models: [] }),
  mnnModels: async () => ({ models: [] }),
  mnnDownloadStatus: async () => ({ active: false, status: "idle" }),
  convertCapabilities: async () => ({}),
  convertTasks: async () => ({ tasks: [], active: null }),
  mnnDownloadCancel: async () => ({}),
};

// ── spy：统计 addEventListener 调用次数（验证 init guard）───────────────────
let addListenerCalls = 0;
const origAddEventListener = window.Element.prototype.addEventListener;
window.Element.prototype.addEventListener = function (type, fn, opts) {
  addListenerCalls++;
  return origAddEventListener.call(this, type, fn, opts);
};

// ── 动态 import（必须在全局 mock 装好之后）───────────────────────────────────
const { isViewVisible, onViewChange, onViewState } = await import("../modules/view-visibility.js");
await import("../modules/ltx.js");
const { renderMnnPage } = await import("../modules/mnn.js");
const { initLtx } = await import("../modules/ltx.js");

function setView(name) {
  // 模拟 switchView：只动 .active，再派发事件。
  document.querySelectorAll(".pane").forEach((p) => p.classList.toggle("active", p.id === "pane-" + name));
  window.dispatchEvent(new window.CustomEvent("kevrai:view-change", { detail: { view: name } }));
}

// ── 1. isViewVisible ───────────────────────────────────────────────────────
test("isViewVisible: false by default, true when pane has .active", () => {
  assert.equal(isViewVisible("ltx"), false);
  document.getElementById("pane-ltx").classList.add("active");
  assert.equal(isViewVisible("ltx"), true);
  document.getElementById("pane-ltx").classList.remove("active");
  assert.equal(isViewVisible("ltx"), false);
  assert.equal(isViewVisible("does-not-exist"), false);
});

// ── 2. onViewState 显隐回调 ─────────────────────────────────────────────────
test("onViewState: onShow fires when switching to the view, onHide otherwise", () => {
  let show = 0, hide = 0;
  const off = onViewState("ltx", { onShow: () => show++, onHide: () => hide++ });
  setView("ltx");   // -> onShow
  setView("market");// -> onHide
  setView("mnn");   // -> onHide (mnn 不是 ltx)
  assert.equal(show, 1);
  assert.equal(hide, 2);
  off();
  setView("ltx");   // unsubscribe 后不应再涨
  assert.equal(show, 1);
  assert.equal(hide, 2);
});

// ── 3. LTX 轮询：隐藏时 clearInterval、切回恢复 ─────────────────────────────
test("LTX polling: starts when view visible, cleared when hidden, resumed on return", async () => {
  // 初始市场页：LTX 不可见，不应有 LTX 轮询定时器。
  setView("market");
  const beforeLtx = activeTimers.size;

  setView("ltx");
  // 切到 LTX：onShow -> startPolling() 应恰好新增 1 个定时器。
  assert.equal(activeTimers.size, beforeLtx + 1, "switching to LTX starts exactly one poll interval");

  // 切走：onHide -> stopPolling() 应 clearInterval。
  setView("market");
  assert.equal(activeTimers.size, beforeLtx, "switching away from LTX clears the poll interval");

  // 切回：恢复。
  setView("ltx");
  assert.equal(activeTimers.size, beforeLtx + 1, "returning to LTX restarts one poll interval");

  setView("market");
});

// ── 4. MNN：renderMnnPage 重复调用不叠加定时器；隐藏时暂停 ──────────────────
test("MNN polling: repeated renderMnnPage does not leak intervals; hidden pauses", async () => {
  setView("mnn");
  const root = document.getElementById("mnn-root");

  await renderMnnPage(root);
  const afterFirst = activeTimers.size;
  assert.equal(afterFirst, 1, "first renderMnnPage starts exactly one download poll");

  // 模拟用户反复切回 MNN（每次都 renderMnnPage）。
  await renderMnnPage(root);
  await renderMnnPage(root);
  assert.equal(activeTimers.size, afterFirst, "repeated renderMnnPage must not accumulate intervals");

  // 切走：下载轮询应被暂停。
  setView("market");
  assert.equal(activeTimers.size, 0, "leaving MNN pauses its poll intervals");

  // 切回：恢复一个。
  setView("mnn");
  assert.equal(activeTimers.size, 1, "returning to MNN restarts exactly one poll interval");
  setView("market");
});

// ── 5. initLtx 重复调用 guard ────────────────────────────────────────────────
test("initLtx: second call is a no-op and does not rebind listeners", async () => {
  setView("ltx");
  addListenerCalls = 0;
  await initLtx();
  const afterFirstInit = addListenerCalls;
  assert.ok(afterFirstInit > 0, "first initLtx wires listeners");

  await initLtx();
  assert.equal(addListenerCalls, afterFirstInit, "second initLtx must not rebind listeners (guard)");
  setView("market");
});

// ── 清理：恢复真实定时器，避免污染其它测试文件 ──────────────────────────────
process.on("exit", () => {
  globalThis.setInterval = realSetInterval;
  globalThis.clearInterval = realClearInterval;
});
