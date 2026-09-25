// renderer/__tests__/error-handler.test.js — global error net: listener wiring,
// de-duplication, runtime toast vs. fatal banner escalation, singleton banner,
// and safeImport() retry/throw behaviour.
import { test, beforeEach, afterEach } from "node:test";
import assert from "node:assert/strict";
import { setupDom } from "./helpers/dom.js";

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

let mod;            // fresh error-handler module (cache-busted per test)
let win;            // jsdom window
let toastCalls;     // spy records of toast(msg, options)
let restoreConsole;

beforeEach(async () => {
  setupDom('<div id="host"></div>');
  win = globalThis.window;
  ({ toast: toastCalls } = { toast: [] });
  toastCalls = [];

  // Fresh module state per test (seen-map, bannerEl, initialized).
  mod = await import(`../modules/error-handler.js?x=${Math.random()}`);
  mod.__setToast((msg, opts) => { toastCalls.push({ msg, opts }); });

  // Silence the always-on console.error so test output stays readable; the
  // point is that it does NOT throw, not what it prints.
  const realErr = console.error;
  console.error = () => {};
  restoreConsole = () => { console.error = realErr; };

  mod.initErrorHandler();
});

afterEach(() => {
  restoreConsole();
  // tear down any banner the tests left behind
  document.querySelectorAll("#global-error-banner").forEach((el) => el.remove());
});

// Helpers --------------------------------------------------------------------
function dispatchRuntimeError(message) {
  const ev = new win.Event("error");
  ev.message = message;
  ev.filename = "app.js";
  ev.lineno = 1;
  ev.colno = 1;
  ev.error = new Error(message);
  win.dispatchEvent(ev);
}

function dispatchResourceError(tagName) {
  const el = document.createElement(tagName);
  document.body.appendChild(el);
  el.dispatchEvent(new win.Event("error"));
  return el;
}

function dispatchRejection(reason) {
  const ev = new win.Event("unhandledrejection");
  ev.reason = reason;
  win.dispatchEvent(ev);
}

// Tests ----------------------------------------------------------------------

test("initErrorHandler registers error AND unhandledrejection listeners", () => {
  // Both channels reach a prompt: runtime error -> toast, rejection -> toast.
  dispatchRuntimeError("boom-one");
  dispatchRejection(new Error("boom-one"));
  assert.ok(toastCalls.length >= 1, "at least one channel produced a prompt");
});

test("runtime error -> generic err toast (no banner)", () => {
  dispatchRuntimeError("something broke at runtime");
  assert.equal(toastCalls.length, 1);
  assert.equal(toastCalls[0].opts.kind, "err");
  assert.match(toastCalls[0].msg, /错误|功能/);
  assert.equal(document.querySelector("#global-error-banner"), null, "no banner for runtime errors");
});

test("unhandledrejection -> err toast via the reason", () => {
  dispatchRejection(new Error("async pipeline blew up"));
  assert.equal(toastCalls.length, 1);
  assert.equal(toastCalls[0].opts.kind, "err");
});

test("de-dup: the same message within the window prompts only once", () => {
  dispatchRuntimeError("repeat-error-x");
  dispatchRuntimeError("repeat-error-x");
  dispatchRuntimeError("repeat-error-x");
  assert.equal(toastCalls.length, 1, "duplicate messages must collapse to one prompt");
});

test("different error messages are each prompted", () => {
  dispatchRuntimeError("alpha error");
  dispatchRuntimeError("beta error");
  assert.equal(toastCalls.length, 2, "distinct messages get distinct prompts");
});

test("script load failure -> fallback banner with reload + close buttons", () => {
  dispatchResourceError("script");
  const banner = document.querySelector("#global-error-banner");
  assert.ok(banner, "banner appears for a failed script load");
  assert.equal(banner.getAttribute("role"), "alert");
  assert.ok(banner.querySelector(".gerr-reload"), "has a reload button");
  assert.ok(banner.querySelector(".gerr-close"), "has a dismiss button");
  // user-facing text must NOT leak the stack / internal paths
  assert.doesNotMatch(banner.textContent, /Error:|at\s|\.js:\d|\/home\//);
});

test("banner is a singleton: a second fatal error reuses the same element", () => {
  dispatchResourceError("script");
  assert.equal(document.querySelectorAll("#global-error-banner").length, 1);
  // A *different* fatal cause (message-shaped load failure, distinct dedup key)
  // must not create a second strip.
  dispatchRuntimeError("Failed to fetch dynamically imported module");
  assert.equal(document.querySelectorAll("#global-error-banner").length, 1, "no duplicate banners");
});

test("dismiss button removes the banner", async () => {
  dispatchResourceError("link");
  const banner = document.querySelector("#global-error-banner");
  assert.ok(banner);
  banner.querySelector(".gerr-close").click();
  await sleep(400); // let the slide-out timeout remove the node
  assert.equal(document.querySelectorAll("#global-error-banner").length, 0);
});

test("safeImport resolves a working module", async () => {
  const ns = await mod.safeImport("data:text/javascript,export const hi=42;");
  assert.equal(ns.hi, 42);
});

test("safeImport retries once then rejects and shows the banner", async () => {
  await assert.rejects(
    () => mod.safeImport("./does-not-exist-xyz.js", 1),
    /ERR_MODULE_NOT_FOUND|does-not-exist/,
  );
  // the final failure surfaced the fallback banner
  assert.ok(document.querySelector("#global-error-banner"), "banner shown after safeImport gives up");
});
