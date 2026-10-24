// renderer/__tests__/sidebar-groups.test.js — 侧边栏分组 / 折叠 / 按项隐藏。
//
// 19 个入口平铺时找不到重点，这里借鉴 Cherry Studio：侧栏只显示需要的项，
// 其余由用户自己收回或放出。
//
// 测试分两层（与 sidebar-collapse.test.js 同一原则）：
//   1) 驱动真实模块，断言 data-* 状态与持久化；
//   2) 直接读 styles.css，断言分组/折叠的样式规则确实存在——只测属性名的话
//      把整段 CSS 删掉测试依然全绿。
import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { setupDom } from "./helpers/dom.js";

const here = path.dirname(fileURLToPath(import.meta.url));
const STYLES = path.resolve(here, "..", "styles.css");

const NAV_HTML = `
<div class="app-shell">
  <aside class="sidebar">
    <nav>
      <div class="nav-group" data-group="start">
        <button class="nav-group-head" type="button" data-group-toggle="start" aria-expanded="true">
          <span class="grp-label">开始使用</span><span class="grp-chevron"></span>
        </button>
        <div class="nav-group-body" id="nav-group-body-start">
          <button class="pane-tab active" data-tab="market" title="模型市场"><span class="tab-label">模型市场</span></button>
          <button class="pane-tab" data-tab="local" title="本地模型"><span class="tab-label">本地模型</span></button>
        </div>
      </div>
      <div class="nav-group" data-group="system">
        <button class="nav-group-head" type="button" data-group-toggle="system" aria-expanded="true">
          <span class="grp-label">硬件与系统</span><span class="grp-chevron"></span>
        </button>
        <div class="nav-group-body" id="nav-group-body-system">
          <button class="pane-tab" data-tab="hardware" title="硬件推荐"><span class="tab-label">硬件推荐</span></button>
          <button class="pane-tab" data-tab="engines" title="AI 引擎"><span class="tab-label">AI 引擎</span></button>
        </div>
      </div>
    </nav>
  </aside>
</div>`;

async function load() {
  const { window } = setupDom(NAV_HTML);
  globalThis.window = window;
  globalThis.document = window.document;
  globalThis.localStorage = window.localStorage;
  globalThis.HTMLInputElement = window.HTMLInputElement;
  const mod = await import("../modules/sidebar-groups.js");
  return { mod, window, doc: window.document };
}

// ---------------------------------------------------------------------------
// 纯函数：切换逻辑
// ---------------------------------------------------------------------------

test("toggleGroupCollapsed 折叠再展开", async () => {
  const { mod } = await load();
  const a = mod.toggleGroupCollapsed([], "start");
  assert.deepEqual(a, ["start"]);
  const b = mod.toggleGroupCollapsed(a, "start");
  assert.deepEqual(b, []);
});

test("toggleTabHidden 隐藏再显示", async () => {
  const { mod } = await load();
  const a = mod.toggleTabHidden([], "market", 4);
  assert.deepEqual(a.hidden, ["market"]);
  assert.equal(a.changed, true);
  const b = mod.toggleTabHidden(a.hidden, "market", 3);
  assert.deepEqual(b.hidden, []);
});

test("最后一个可见入口不允许被隐藏", async () => {
  // 全部隐藏之后界面没有任何入口可点，且用户无法自救——管理面板本身在侧栏里
  const { mod } = await load();
  const r = mod.toggleTabHidden([], "market", 1);
  assert.equal(r.changed, false);
  assert.equal(r.blocked, true);
  assert.deepEqual(r.hidden, []);
});

test("有两个可见时允许隐藏其中一个", async () => {
  const { mod } = await load();
  const r = mod.toggleTabHidden([], "market", 2);
  assert.equal(r.changed, true);
  assert.deepEqual(r.hidden, ["market"]);
});

// ---------------------------------------------------------------------------
// DOM：状态应用
// ---------------------------------------------------------------------------

test("applySidebarGroups 写入折叠与隐藏标记", async () => {
  const { mod, window, doc } = await load();
  localStorage.setItem("kevrai:sidebar-collapsed-groups", JSON.stringify(["system"]));
  localStorage.setItem("kevrai:sidebar-hidden-tabs", JSON.stringify(["engines"]));
  mod.applySidebarGroups(doc);

  const system = doc.querySelector('[data-group="system"]');
  assert.equal(system.dataset.collapsed, "true");
  assert.equal(system.querySelector(".nav-group-head").getAttribute("aria-expanded"), "false");

  const start = doc.querySelector('[data-group="start"]');
  assert.equal(start.dataset.collapsed, "false");

  assert.equal(doc.querySelector('[data-tab="engines"]').dataset.hidden, "true");
  assert.equal(doc.querySelector('[data-tab="market"]').dataset.hidden, undefined);
  window.close();
});

test("countVisibleTabs 只统计未隐藏的", async () => {
  const { mod, window, doc } = await load();
  localStorage.setItem("kevrai:sidebar-hidden-tabs", JSON.stringify(["engines", "local"]));
  mod.applySidebarGroups(doc);
  assert.equal(mod.countVisibleTabs(doc), 2);
  window.close();
});

test("localStorage 不可用时回落为全显示（不抛错）", async () => {
  const { mod, window, doc } = await load();
  const real = Object.getOwnPropertyDescriptor(window, "localStorage");
  // 模拟隐私模式：读取即抛错
  Object.defineProperty(window, "localStorage", {
    configurable: true,
    get() { throw new Error("blocked"); },
  });
  globalThis.localStorage = undefined;
  assert.doesNotThrow(() => mod.applySidebarGroups(doc));
  assert.equal(doc.querySelector('[data-tab="market"]').dataset.hidden, undefined);
  if (real) Object.defineProperty(window, "localStorage", real);
  window.close();
});

test("损坏的持久化数据被忽略而不是崩溃", async () => {
  const { mod, window, doc } = await load();
  localStorage.setItem("kevrai:sidebar-hidden-tabs", "{not json");
  assert.deepEqual(mod.readHiddenTabs(), []);
  localStorage.setItem("kevrai:sidebar-hidden-tabs", JSON.stringify([1, 2, "ok"]));
  assert.deepEqual(mod.readHiddenTabs(), ["ok"]);   // 非字符串被过滤
  window.close();
});

// ---------------------------------------------------------------------------
// 点击组头 → 折叠 + 持久化
// ---------------------------------------------------------------------------

test("点击组头切换折叠并写入 localStorage", async () => {
  const { mod, window, doc } = await load();
  localStorage.clear();
  const un = mod.wireSidebarGroups(doc);
  const head = doc.querySelector('[data-group-toggle="system"]');
  head.dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  assert.equal(doc.querySelector('[data-group="system"]').dataset.collapsed, "true");
  assert.deepEqual(JSON.parse(localStorage.getItem("kevrai:sidebar-collapsed-groups")), ["system"]);
  // 再点一次恢复
  head.dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  assert.equal(doc.querySelector('[data-group="system"]').dataset.collapsed, "false");
  un();
  window.close();
});

// ---------------------------------------------------------------------------
// 管理面板：至少保留一个
// ---------------------------------------------------------------------------

test("全部取消勾选时保存被拒绝，并提示", async () => {
  const { mod, window, doc } = await load();
  localStorage.clear();
  const p = mod.openSidebarManager({ t: (k) => k, root: doc });
  const overlay = doc.querySelector(".overlay");
  assert.ok(overlay, "管理面板应被插入 DOM");
  overlay.querySelectorAll("input[type=checkbox]").forEach((b) => { b.checked = false; });
  overlay.querySelector("[data-act=save]").dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  assert.equal(overlay.querySelector(".hint").textContent, "sidebar.keepOne");
  // 面板仍在（未关闭），localStorage 未被写入
  assert.equal(localStorage.getItem("kevrai:sidebar-hidden-tabs"), null);
  overlay.querySelector("[data-act=close]").dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  await p;
  window.close();
});

test("取消最后一个勾选框时立刻弹回", async () => {
  const { mod, window, doc } = await load();
  localStorage.clear();
  const p = mod.openSidebarManager({ t: (k) => k, root: doc });
  const overlay = doc.querySelector(".overlay");
  const boxes = overlay.querySelectorAll("input[type=checkbox]");
  boxes.forEach((b) => { b.checked = false; });
  const last = boxes[boxes.length - 1];
  last.checked = false;
  last.dispatchEvent(new window.Event("change", { bubbles: true }));
  assert.equal(last.checked, true, "最后一个勾选框应被自动弹回选中");
  overlay.querySelector("[data-act=cancel]").dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  await p;
  window.close();
});

test("正常保存会写入隐藏列表并应用到 DOM", async () => {
  const { mod, window, doc } = await load();
  localStorage.clear();
  const p = mod.openSidebarManager({ t: (k) => k, root: doc });
  const overlay = doc.querySelector(".overlay");
  const row = overlay.querySelector('.sidebar-manager-row[data-tab="hardware"]');
  row.querySelector("input[type=checkbox]").checked = false;
  overlay.querySelector("[data-act=save]").dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  await p;
  assert.deepEqual(JSON.parse(localStorage.getItem("kevrai:sidebar-hidden-tabs")), ["hardware"]);
  assert.equal(doc.querySelector('[data-tab="hardware"]').dataset.hidden, "true");
  window.close();
});

test("全部显示按钮勾上所有项", async () => {
  const { mod, window, doc } = await load();
  localStorage.clear();
  const p = mod.openSidebarManager({ t: (k) => k, root: doc });
  const overlay = doc.querySelector(".overlay");
  overlay.querySelectorAll("input[type=checkbox]").forEach((b) => { b.checked = false; });
  overlay.querySelector("[data-act=all]").dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  assert.equal(overlay.querySelectorAll("input[type=checkbox]:checked").length, 4);
  overlay.querySelector("[data-act=cancel]").dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  await p;
  window.close();
});

// ---------------------------------------------------------------------------
// 样式层：属性名对了但 CSS 不存在，界面依然没反应
// ---------------------------------------------------------------------------

test("styles.css 存在分组与折叠的样式规则", () => {
  const css = fs.readFileSync(STYLES, "utf8");
  assert.match(css, /\.sidebar \.nav-group\b/, "缺少 .nav-group 样式");
  assert.match(css, /\.sidebar \.nav-group-head\b/, "缺少组头样式");
  assert.match(css, /\.nav-group\[data-collapsed="true"\] \.nav-group-body\s*\{[^}]*display:\s*none/,
    "折叠态必须 display:none，否则隐藏的按钮仍会进入 Tab 焦点顺序");
  assert.match(css, /\.pane-tab\[data-hidden="true"\]\s*\{[^}]*display:\s*none/,
    "缺少按项隐藏的样式");
});

test("styles.css 展开态下显示分组标题", () => {
  const css = fs.readFileSync(STYLES, "utf8");
  assert.match(css, /sidebar-expanded \.sidebar \.nav-group-head/, "展开态缺少组头规则");
  assert.match(css, /\.grp-label/, "缺少分组标题样式");
});

// ---------------------------------------------------------------------------
// index.html：分组结构完整
// ---------------------------------------------------------------------------

test("index.html 每个 tab 都归属某个分组", () => {
  const html = fs.readFileSync(path.resolve(here, "..", "index.html"), "utf8");
  const seg = html.match(/<aside class="sidebar"[\s\S]*?<\/aside>/)[0];
  const inGroups = seg.match(/<div class="nav-group"[\s\S]*?/g) || [];
  assert.ok(inGroups.length >= 3, "至少应有 3 个分组");
  // 所有 pane-tab 都必须落在 nav-group-body 内
  const tabs = (seg.match(/class="pane-tab/g) || []).length;
  const bodies = seg.split('<div class="nav-group-body"').slice(1);
  const tabsInBodies = bodies.reduce(
    (n, b) => n + (b.split("</div>")[0].match(/class="pane-tab/g) || []).length, 0);
  assert.equal(tabsInBodies, tabs, `有 ${tabs - tabsInBodies} 个 tab 没被分组包住`);
});

test("index.html 有管理侧边栏入口", () => {
  const html = fs.readFileSync(path.resolve(here, "..", "index.html"), "utf8");
  assert.match(html, /data-action="manage-sidebar"/);
});
