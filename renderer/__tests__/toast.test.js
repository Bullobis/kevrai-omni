// renderer/__tests__/toast.test.js — toast rendering, kinds/icons, title+msg,
// sticky mode, close() handle and the MAX=4 stack cap.
import { test, beforeEach, afterEach } from "node:test";
import assert from "node:assert/strict";
import { setupDom } from "./helpers/dom.js";

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// toast.js keeps module-level STACK + hostEl state, and setupDom swaps the
// global document per test. Cache-busting the dynamic import gives each test a
// fresh module (so hostEl rebinds to the new jsdom document and STACK resets).
let toast;
let cleanup = [];

beforeEach(async () => {
  setupDom('<div id="host"></div>');
  ({ toast } = await import(`../modules/toast.js?x=${Math.random()}`));
  cleanup = [];
});

afterEach(async () => {
  cleanup.forEach((c) => { try { c(); } catch (_) {} });
  await sleep(260); // let any slide-out animations / removals settle
});

test("basic render: mounts a stack + item with the message and role", () => {
  const close = toast("hello world", { ttl: 0 });
  cleanup.push(close);
  const stack = document.querySelector(".toast-stack");
  assert.ok(stack, "toast-stack exists");
  assert.equal(stack.getAttribute("aria-live"), "polite");
  const item = stack.querySelector(".toast-item");
  assert.ok(item, "toast-item exists");
  assert.equal(item.textContent, "hello world");
  assert.equal(item.getAttribute("role"), "status");
});

test("each kind renders an inline SVG icon (and err uses role=alert)", () => {
  for (const kind of ["ok", "warn", "err", "info"]) {
    const close = toast("m", { kind, ttl: 0 });
    cleanup.push(close);
    const item = document.querySelector(`.toast-item.${kind}`);
    assert.ok(item, `item for kind=${kind}`);
    const svg = item.querySelector("svg.toast-icon");
    assert.ok(svg, `icon svg for kind=${kind}`);
    assert.equal(svg.getAttribute("viewBox"), "0 0 24 24");
  }
  // err escalates role to alert.
  assert.equal(document.querySelector(".toast-item.err").getAttribute("role"), "alert");
});

test("title + msg structure: bold title above body; options.msg fallback", () => {
  const close = toast("body here", { title: "Headline", kind: "ok", ttl: 0 });
  cleanup.push(close);
  const item = document.querySelector(".toast-item");
  assert.equal(item.querySelector(".toast-title").textContent, "Headline");
  assert.equal(item.querySelector(".toast-msg").textContent, "body here");
});

test("toast(null, {title, msg}) variant works", () => {
  const close = toast(null, { title: "T", msg: "M", ttl: 0 });
  cleanup.push(close);
  const item = document.querySelector(".toast-item");
  assert.equal(item.querySelector(".toast-title").textContent, "T");
  assert.equal(item.querySelector(".toast-msg").textContent, "M");
});

test("info kind is supported and unstyled default kind has no icon", () => {
  const info = toast("info!", { kind: "info", ttl: 0 });
  cleanup.push(info);
  assert.ok(document.querySelector(".toast-item.info svg.toast-icon"));

  const plain = toast("plain", { ttl: 0 });
  cleanup.push(plain);
  const items = document.querySelectorAll(".toast-item");
  const plainItem = items[items.length - 1];
  assert.equal(plainItem.className, "toast-item");
  assert.equal(plainItem.querySelector("svg.toast-icon"), null);
});

test("sticky toast (ttl=0) never auto-dismisses and shows no progress bar", async () => {
  const close = toast("forever", { ttl: 0 });
  cleanup.push(close);
  const item = document.querySelector(".toast-item");
  assert.equal(item.querySelector(".toast-progress"), null);
  await sleep(80);
  assert.equal(document.querySelectorAll(".toast-item").length, 1);
});

test("auto-dismissing toast shows a progress bar sized to ttl", () => {
  const close = toast("tick", { ttl: 1234 });
  cleanup.push(close);
  const item = document.querySelector(".toast-item");
  const bar = item.querySelector(".toast-progress > i");
  assert.ok(bar, "progress bar present for ttl>0");
  assert.equal(bar.style.animationDuration, "1234ms");
});

test("returned close() handle removes the toast", async () => {
  const close = toast("bye", { ttl: 0 });
  assert.equal(typeof close, "function");
  close();
  const item = document.querySelector(".toast-item");
  assert.ok(item.classList.contains("toast-leave"), "slide-out class added");
  await sleep(260);
  assert.equal(document.querySelectorAll(".toast-item").length, 0);
});

test("stack caps at MAX=4, evicting the oldest", async () => {
  for (let i = 0; i < 6; i++) cleanup.push(toast("n" + i, { ttl: 0 }));
  await sleep(260); // evicted entries finish their slide-out
  assert.equal(document.querySelectorAll(".toast-item").length, 4);
});
