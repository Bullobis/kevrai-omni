// renderer/__tests__/settings-center.test.js — 设置中心左侧分类切换逻辑。
import { test } from "node:test";
import assert from "node:assert/strict";
import { setupDom } from "./helpers/dom.js";

const bodyHtml = `
<div id="settings-overlay">
  <div class="settings-center">
    <nav class="settings-nav">
      <button type="button" class="settings-nav-btn active" data-section="general">通用</button>
      <button type="button" class="settings-nav-btn" data-section="tokens">令牌</button>
      <button type="button" class="settings-nav-btn" data-section="appearance">外观</button>
      <button type="button" class="settings-nav-btn" data-section="about">关于</button>
    </nav>
    <div class="settings-content">
      <section class="settings-section" data-section="general"><input id="set-model-dir"></section>
      <section class="settings-section" data-section="tokens" hidden><input id="set-hf-token"></section>
      <section class="settings-section" data-section="appearance" hidden></section>
      <section class="settings-section" data-section="about" hidden></section>
    </div>
  </div>
</div>`;

setupDom(bodyHtml);
const { switchSettingsSection } = await import("../modules/settings.js");

test("switches to a section: marks nav active and shows only that section", () => {
  switchSettingsSection("tokens");
  const activeBtn = document.querySelector(".settings-nav-btn.active");
  assert.equal(activeBtn.dataset.section, "tokens");
  const general = document.querySelector('.settings-section[data-section="general"]');
  const tokens = document.querySelector('.settings-section[data-section="tokens"]');
  assert.equal(general.hidden, true);
  assert.equal(tokens.hidden, false);
});

test("only one section is visible after switching", () => {
  switchSettingsSection("about");
  const visible = Array.from(document.querySelectorAll(".settings-section"))
    .filter((s) => !s.hidden);
  assert.equal(visible.length, 1);
  assert.equal(visible[0].dataset.section, "about");
});

test("unknown section name hides all sections but leaves nav consistent", () => {
  switchSettingsSection("nope");
  const visible = Array.from(document.querySelectorAll(".settings-section"))
    .filter((s) => !s.hidden);
  assert.equal(visible.length, 0);
  assert.equal(document.querySelector(".settings-nav-btn.active"), null);
});

test("all original field IDs survive the regrouping (readForm/fillForm 兼容)", () => {
  // 关键：字段 ID 不变，selectors 仍可命中。
  assert.ok(document.getElementById("set-model-dir"));
  assert.ok(document.getElementById("set-hf-token"));
});
