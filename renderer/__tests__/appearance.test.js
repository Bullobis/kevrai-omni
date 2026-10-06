// renderer/__tests__/appearance.test.js — 强调色 + 界面密度个性化。
import { test, beforeEach, afterEach } from "node:test";
import assert from "node:assert/strict";
import { setupDom } from "./helpers/dom.js";
import { registerAction, __getRegisteredAction } from "../modules/command-palette.js";

const BODY = `
<div id="accent-swatches">
  <button data-accent-choice="green"></button>
  <button data-accent-choice="blue"></button>
  <button data-accent-choice="violet"></button>
  <button data-accent-choice="orange"></button>
  <button data-accent-choice="pink"></button>
  <button data-accent-choice="cyan"></button>
</div>
<div id="density-toggle">
  <button data-density-choice="comfortable"></button>
  <button data-density-choice="compact"></button>
</div>`;

let ap;
beforeEach(async () => {
  setupDom(BODY);
  const map = new Map();
  globalThis.localStorage = {
    getItem: (k) => (map.has(k) ? map.get(k) : null),
    setItem: (k, v) => { map.set(k, String(v)); },
    removeItem: (k) => { map.delete(k); },
    clear: () => map.clear(),
  };
  ap = await import(`../modules/appearance.js?x=${Math.random()}`);
});
afterEach(() => { delete globalThis.localStorage; });

test("init applies defaults (green / comfortable)", () => {
  ap.initAppearance();
  assert.equal(document.documentElement.getAttribute("data-accent"), "green");
  assert.equal(document.documentElement.getAttribute("data-density"), "comfortable");
  const green = document.querySelector('[data-accent-choice="green"]');
  assert.equal(green.classList.contains("is-active"), true);
  assert.equal(green.getAttribute("aria-checked"), "true");
});

test("clicking a swatch sets accent, active state and persists", () => {
  ap.initAppearance();
  document.querySelector('[data-accent-choice="blue"]').click();
  assert.equal(document.documentElement.getAttribute("data-accent"), "blue");
  const blue = document.querySelector('[data-accent-choice="blue"]');
  assert.equal(blue.classList.contains("is-active"), true);
  assert.equal(document.querySelector('[data-accent-choice="green"]').classList.contains("is-active"), false);
  assert.equal(globalThis.localStorage.getItem("kevrai:accent"), "blue");
});

test("clicking compact sets density, persists and fires a resize", async () => {
  setupDom(BODY); // fresh window so we can attach the resize listener before init
  let resized = 0;
  window.addEventListener("resize", () => { resized += 1; });
  ap = await import(`../modules/appearance.js?x=${Math.random()}`);
  ap.initAppearance();
  document.querySelector('[data-density-choice="compact"]').click();
  assert.equal(document.documentElement.getAttribute("data-density"), "compact");
  assert.equal(globalThis.localStorage.getItem("kevrai:density"), "compact");
  const compact = document.querySelector('[data-density-choice="compact"]');
  assert.equal(compact.classList.contains("is-active"), true);
  assert.ok(resized >= 1, "resize dispatched to relayout grid");
});

test("persisted preferences are restored on init", () => {
  globalThis.localStorage.setItem("kevrai:accent", "pink");
  globalThis.localStorage.setItem("kevrai:density", "compact");
  ap.initAppearance();
  assert.equal(document.documentElement.getAttribute("data-accent"), "pink");
  assert.equal(document.documentElement.getAttribute("data-density"), "compact");
});

test("invalid values are rejected", () => {
  ap.initAppearance();
  ap.setAccent("hacker");
  assert.notEqual(document.documentElement.getAttribute("data-accent"), "hacker");
  ap.setDensity("ultra");
  assert.notEqual(document.documentElement.getAttribute("data-density"), "ultra");
  assert.equal(ap.isValidAccent("blue"), true);
  assert.equal(ap.isValidDensity("compact"), true);
});

test("command-palette actions can drive accent and density", async () => {
  setupDom(BODY);
  const ap2 = await import(`../modules/appearance.js?y=${Math.random()}`);
  ap2.initAppearance();
  registerAction("zz-accent-blue", "b", () => ap2.setAccent("blue"), [], "droplet", "主题");
  __getRegisteredAction("zz-accent-blue").handler();
  assert.equal(document.documentElement.getAttribute("data-accent"), "blue");
  registerAction("zz-density-compact", "c", () => ap2.setDensity("compact"), [], "rows", "主题");
  __getRegisteredAction("zz-density-compact").handler();
  assert.equal(document.documentElement.getAttribute("data-density"), "compact");
});
