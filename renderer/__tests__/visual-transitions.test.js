// renderer/__tests__/visual-transitions.test.js — Kova R4 视觉精修单测。
// 覆盖：模态打开/关闭时序、reduced-motion 立即关闭、关闭过渡不立即 hidden、
// body 滚动锁引用计数。纯 DOM 逻辑，jsdom 下单测。
import { test } from "node:test";
import assert from "node:assert/strict";
import { setupDom } from "./helpers/dom.js";

const bodyHtml = `
<div id="settings-overlay" class="overlay" role="dialog" aria-modal="true" hidden>
  <div class="overlay-card" role="document"><h2>设置</h2></div>
</div>
<div id="download-overlay" class="overlay" role="dialog" aria-modal="true" hidden>
  <div class="overlay-card" role="document"><h2>下载</h2></div>
</div>`;

setupDom(bodyHtml);

// jsdom 默认不实现 matchMedia —— 默认「不减少动画」。
const normalMatch = (q) => ({
  matches: false, media: q, addEventListener() {}, removeEventListener() {},
});
window.matchMedia = window.matchMedia || normalMatch;

const { overlayOpen, overlayClose } = await import("../modules/overlay-fx.js");
const $ = (s) => document.querySelector(s);
// jsdom 不派发 transitionend，关闭一律走 setTimeout(180ms)。
const settle = (ms = 240) => new Promise((r) => setTimeout(r, ms));

test("overlayOpen: 移除 hidden、标记动画态、置 aria-hidden=false、加滚动锁", async () => {
  const el = $("#settings-overlay");
  overlayOpen(el);
  assert.equal(el.hasAttribute("hidden"), false, "打开后不再 hidden");
  assert.ok(el.classList.contains("overlay-anim"), "标记 overlay-anim 起始态");
  assert.ok(el.classList.contains("is-open"), "reflow 后标记 is-open");
  assert.equal(el.getAttribute("aria-hidden"), "false");
  assert.ok(document.body.classList.contains("modal-open"), "body 滚动锁已加");
  overlayClose(el);
  await settle();
});

test("overlayClose: 关闭瞬间不立即 hidden，过渡结束后才 hidden", async () => {
  const el = $("#download-overlay");
  overlayOpen(el);
  overlayClose(el);

  // 关闭瞬间：不立即 hidden，而是进入 is-closing，aria-hidden 立即 true。
  assert.ok(el.classList.contains("is-closing"), "进入关闭过渡态");
  assert.equal(el.getAttribute("aria-hidden"), "true");
  assert.equal(el.hasAttribute("hidden"), false, "过渡期间保持可渲染，不立即 hidden");

  await settle();
  assert.equal(el.hasAttribute("hidden"), true, "过渡结束后才 set hidden");
  assert.ok(!el.classList.contains("is-closing"), "清理关闭态");
  assert.ok(!el.classList.contains("overlay-anim"), "清理动画态");
});

test("reduced-motion: overlayClose 同步立即 hidden，不等待过渡", () => {
  const orig = window.matchMedia;
  window.matchMedia = (q) => ({
    matches: true, media: q, addEventListener() {}, removeEventListener() {},
  });
  try {
    const el = $("#settings-overlay");
    overlayOpen(el);
    overlayClose(el);
    assert.equal(el.hasAttribute("hidden"), true, "减少动画时立即 hidden");
  } finally {
    window.matchMedia = orig;
  }
});

test("滚动锁引用计数：开两个浮层关一个仍上锁，全关才解锁", async () => {
  const a = $("#settings-overlay");
  const b = $("#download-overlay");
  overlayOpen(a);
  overlayOpen(b);
  assert.ok(document.body.classList.contains("modal-open"));

  overlayClose(a);
  // a 的 finish 尚未到（180ms），b 仍开 → 锁应保留。
  assert.ok(document.body.classList.contains("modal-open"), "仍有浮层打开时保持滚动锁");

  overlayClose(b);
  await settle();
  assert.ok(!document.body.classList.contains("modal-open"), "全部关闭后释放滚动锁");
});

test("overlayClose 幂等：对正在关闭的浮层重复调用不抛错", async () => {
  const el = $("#download-overlay");
  overlayOpen(el);
  assert.doesNotThrow(() => {
    overlayClose(el);
    overlayClose(el); // 第二次：已在 is-closing，应安全短路
  });
  await settle();
});

test("关闭途中重开：取消挂起的 finish，浮层保持可见不被误隐藏", async () => {
  const el = $("#settings-overlay");
  overlayOpen(el);
  overlayClose(el);                 // 开始 180ms 关闭过渡
  assert.ok(el.classList.contains("is-closing"));
  overlayOpen(el);                  // 立即重开 → 应取消挂起的 finish
  assert.ok(!el.classList.contains("is-closing"), "重开后退出关闭态");
  assert.equal(el.hasAttribute("hidden"), false, "重开后保持可见");

  await settle();                   // 等待超过原 180ms
  assert.equal(el.hasAttribute("hidden"), false, "挂起 finish 已取消，不会误隐藏");

  overlayClose(el);
  await settle();
});
