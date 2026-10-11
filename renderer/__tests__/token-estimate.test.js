// renderer/__tests__/token-estimate.test.js — heuristic token estimator and the
// composer counter wiring.
import { test } from "node:test";
import assert from "node:assert/strict";
import { setupDom } from "./helpers/dom.js";

const html = `<div><input id="box" type="text" /></div>`;
const { window, document } = setupDom(html);
globalThis.localStorage = window.localStorage;
globalThis.CustomEvent = window.CustomEvent;

const {
  estimateTokens, formatTokenCount, attachTokenCounter,
  tokenEstimateEnabled, setTokenEstimateEnabled, TOKEN_PREF_KEY,
} = await import("../modules/token-estimate.js");

test("empty / whitespace is zero", () => {
  assert.equal(estimateTokens(""), 0);
  assert.equal(estimateTokens("   \n\t"), 0);
});

test("pure Latin text ~ chars/4", () => {
  // 8 visible chars -> ceil(8/4) = 2
  assert.equal(estimateTokens("abcdefgh"), 2);
  // spaces ignored as non-visible
  assert.equal(estimateTokens("abcd efgh"), 2);
});

test("CJK chars ~ one token each", () => {
  assert.equal(estimateTokens("你好世界"), 4);
  assert.equal(estimateTokens("カナ"), 2);
});

test("mixed CJK and Latin", () => {
  // 2 CJK + ceil(4 latin /4)=1 -> 3
  assert.equal(estimateTokens("你好 abcd"), 3);
});

test("formatTokenCount uses a tilde prefix", () => {
  assert.equal(formatTokenCount(0), "~0");
  assert.equal(formatTokenCount(12), "~12");
});

test("attachTokenCounter renders and updates live", () => {
  const box = document.getElementById("box");
  box.value = "你好";
  const label = attachTokenCounter(box);
  assert.ok(label, "label created");
  assert.equal(label.textContent, "~2 tokens");
  box.value = "abcdefgh";
  box.dispatchEvent(new window.Event("input", { bubbles: true }));
  assert.equal(label.textContent, "~2 tokens");
});

test("counter hides when the preference is off", () => {
  const box = document.getElementById("box");
  box.value = "你好";
  const label = attachTokenCounter(box);
  setTokenEstimateEnabled(false);
  assert.equal(label.style.display, "none");
  setTokenEstimateEnabled(true);
  assert.notEqual(label.style.display, "none");
  assert.equal(tokenEstimateEnabled(), true);
  assert.equal(localStorage.getItem(TOKEN_PREF_KEY), "1");
});

test("attach is idempotent", () => {
  const box = document.getElementById("box");
  const first = attachTokenCounter(box);
  const second = attachTokenCounter(box);
  assert.equal(first, second);
});
