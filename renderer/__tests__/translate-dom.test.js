// renderer/__tests__/translate-dom.test.js — DOM behavior for the translation
// panel: language selects are populated, swap exchanges languages + boxes, and
// run() posts the chosen source/target and renders the returned translation.
import { test } from "node:test";
import assert from "node:assert/strict";
import { setupDom } from "./helpers/dom.js";

const html = `
  <section id="pane-translate">
    <p id="translate-status"></p>
    <select id="translate-src-lang"></select>
    <select id="translate-tgt-lang"></select>
    <button id="translate-swap" type="button"></button>
    <textarea id="translate-source"></textarea>
    <textarea id="translate-target" readonly></textarea>
    <button id="translate-run" type="button"></button>
    <button id="translate-copy" type="button"></button>
  </section>`;
const { window, document } = setupDom(html);

let sent = null;
let clipboardText = null;
window.kevrai = {
  translate: async (payload) => {
    sent = payload;
    return { body: { translation: "Hello, world", model: "Qwen/Qwen3-0.6B" } };
  },
};
navigator.clipboard = {
  writeText: async (text) => { clipboardText = text; },
};

const mod = await import(`../modules/translate.js?dom=${Date.now()}`);
await mod.initTranslate();

test("source select offers auto-detect plus the common languages", () => {
  const src = document.getElementById("translate-src-lang");
  assert.equal(src.options[0].value, "auto");
  // auto + 8 languages
  assert.equal(src.options.length, 9);
});

test("target select does not offer auto-detect", () => {
  const tgt = document.getElementById("translate-tgt-lang");
  assert.equal(tgt.options.length, 8);
  for (const o of tgt.options) assert.notEqual(o.value, "auto");
});

test("run posts source/target and renders the translation", async () => {
  document.getElementById("translate-source").value = "你好世界";
  document.getElementById("translate-src-lang").value = "zh";
  document.getElementById("translate-tgt-lang").value = "en";
  document.getElementById("translate-run").click();
  await new Promise((r) => setTimeout(r, 20));
  assert.ok(sent);
  assert.equal(sent.text, "你好世界");
  assert.equal(sent.source_lang, "zh");
  assert.equal(sent.target_lang, "en");
  assert.equal(document.getElementById("translate-target").value, "Hello, world");
});

test("swap exchanges languages and moves output back to source", async () => {
  document.getElementById("translate-target").value = "moved translation";
  document.getElementById("translate-src-lang").value = "zh";
  document.getElementById("translate-tgt-lang").value = "en";
  document.getElementById("translate-swap").click();
  assert.equal(document.getElementById("translate-src-lang").value, "en");
  assert.equal(document.getElementById("translate-tgt-lang").value, "zh");
  assert.equal(document.getElementById("translate-source").value, "moved translation");
  assert.equal(document.getElementById("translate-target").value, "");
});

test("copy writes the translation to the clipboard", async () => {
  document.getElementById("translate-target").value = "copied text";
  document.getElementById("translate-copy").click();
  await new Promise((r) => setTimeout(r, 5));
  assert.equal(clipboardText, "copied text");
});
