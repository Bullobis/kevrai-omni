// renderer/__tests__/onboarding.test.js — first-run guide behavior:
// shows once; quick-nav buttons dismiss + route to the right sidebar tab;
// the plain start button dismisses without navigating; once flagged, a fresh
// page load removes the guide immediately.
import { test } from "node:test";
import assert from "node:assert/strict";
import { setTimeout as delay } from "node:timers/promises";
import { setupDom } from "./helpers/dom.js";

// --- sidebar tabs stay in the DOM for the whole file ---
const html = `
  <nav class="sidebar">
    <button type="button" class="pane-tab" data-tab="market">market</button>
    <button type="button" class="pane-tab" data-tab="engines">engines</button>
    <button type="button" class="pane-tab" data-tab="hardware">hardware</button>
  </nav>`;
const { window, document } = setupDom(html);

// localStorage shim (jsdom about:blank storage is unreliable).
const store = new Map();
globalThis.localStorage = {
  getItem: (k) => (store.has(k) ? store.get(k) : null),
  setItem: (k, v) => store.set(k, String(v)),
  removeItem: (k) => store.delete(k),
  clear: () => store.clear(),
};
// CSS.escape shim (jsdom lacks a full CSS object).
globalThis.CSS = { escape: (s) => String(s).replace(/[^a-zA-Z0-9_-]/g, "") };

// Record which sidebar tab receives a programmatic click.
const clicked = [];
["market", "engines", "hardware"].forEach((name) => {
  document
    .querySelector(`.sidebar [data-tab="${name}"]`)
    .addEventListener("click", () => clicked.push(name));
});

let seq = 0;
// Build a fresh, attached overlay and a fresh module instance, then wire it.
async function makeFresh() {
  document.getElementById("onboarding-overlay")?.remove();
  const wrap = document.createElement("div");
  wrap.innerHTML = `
    <div id="onboarding-overlay" class="overlay" role="dialog" aria-modal="true" hidden>
      <div class="overlay-card onboarding-card">
        <header><h2 id="onboarding-title">welcome</h2></header>
        <ol class="onboarding-steps"></ol>
        <div class="onboarding-actions">
          <button class="secondary" type="button" data-goto="engines">engines</button>
          <button class="secondary" type="button" data-goto="market">market</button>
          <button class="secondary" type="button" data-goto="hardware">hardware</button>
        </div>
        <footer class="overlay-foot">
          <button class="primary" data-action="close-onboarding">start</button>
        </footer>
      </div>
    </div>`;
  const overlay = wrap.firstElementChild;
  document.body.appendChild(overlay);
  seq += 1;
  const mod = await import(`../modules/onboarding.js?fresh=${seq}`);
  mod.wireOnboarding();
  return overlay;
}

test("first run opens the guide", async () => {
  store.clear();
  const overlay = await makeFresh();
  assert.equal(overlay.hasAttribute("hidden"), false);
});

test("quick-nav dismisses the guide and routes to the named tab", async () => {
  store.clear();
  clicked.length = 0;
  await makeFresh();
  document.querySelector('[data-goto="engines"]').click();
  await delay(220); // overlay-fx close fallback timer, then overlay.remove()
  assert.equal(document.getElementById("onboarding-overlay"), null);
  assert.deepEqual(clicked, ["engines"]);
  assert.equal(store.get("kevrai.onboarded.v1"), "1");
});

test("plain start button dismisses without navigation", async () => {
  store.clear();
  clicked.length = 0;
  await makeFresh();
  document.querySelector('[data-action="close-onboarding"]').click();
  await delay(220);
  assert.equal(document.getElementById("onboarding-overlay"), null);
  assert.deepEqual(clicked, []);
  assert.equal(store.get("kevrai.onboarded.v1"), "1");
});

test("when the flag is already stored, the guide is removed on load", async () => {
  store.clear();
  store.set("kevrai.onboarded.v1", "1");
  const overlay = await makeFresh();
  assert.equal(document.getElementById("onboarding-overlay"), null);
  assert.equal(overlay.isConnected, false);
});
