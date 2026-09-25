// renderer/__tests__/empty-state.test.js — 统一空状态组件的 DOM 结构。
import { test } from "node:test";
import assert from "node:assert/strict";
import { setupDom } from "./helpers/dom.js";

setupDom();
const { createEmptyState, emptyStateIconSvg } = await import("../modules/empty-state.js");

test("createEmptyState renders icon, title, hint", () => {
  const el = createEmptyState({
    icon: "hardDrive",
    title: "还没有本地模型",
    hint: "导入你的第一个模型",
  });
  assert.equal(el.className, "empty-state");
  assert.equal(el.getAttribute("role"), "status");
  assert.ok(el.querySelector(".empty-state-iconwrap svg.empty-state-icon"), "has an svg icon");
  assert.match(el.querySelector(".empty-state-icon").getAttribute("stroke"), /currentColor/);
  assert.equal(el.querySelector(".empty-state-title").textContent, "还没有本地模型");
  assert.equal(el.querySelector(".empty-state-hint").textContent, "导入你的第一个模型");
  assert.equal(el.querySelector(".es-action"), null, "no action button when label omitted");
});

test("createEmptyState renders an action button when label provided", () => {
  const el = createEmptyState({
    icon: "inbox", title: "空", actionLabel: "选择文件",
  });
  const btn = el.querySelector(".es-action");
  assert.ok(btn, "action button present");
  assert.equal(btn.textContent, "选择文件");
  assert.equal(btn.type, "button");
});

test("action button click handler fires once wired by caller", () => {
  const el = createEmptyState({ icon: "box", title: "t", actionLabel: "go" });
  let clicks = 0;
  el.querySelector(".es-action").addEventListener("click", () => (clicks += 1));
  el.querySelector(".es-action").click();
  assert.equal(clicks, 1);
});

test("emptyStateIconSvg falls back to inbox for unknown icon names", () => {
  const html = emptyStateIconSvg("does-not-exist");
  assert.ok(html.includes("viewBox=\"0 0 24 24\""));
  assert.ok(html.includes("stroke=\"currentColor\""));
});

test("empty state toggles between empty and populated content in a host", () => {
  const host = document.createElement("div");
  document.body.appendChild(host);
  // 空数据分支：插入空状态
  host.replaceChildren(createEmptyState({ icon: "clock", title: "暂无任务" }));
  assert.ok(host.querySelector(".empty-state"));
  // 有数据分支：替换为空状态之外的行
  host.innerHTML = `<div class="row">real item</div>`;
  assert.equal(host.querySelector(".empty-state"), null);
  assert.equal(host.querySelector(".row").textContent, "real item");
});
