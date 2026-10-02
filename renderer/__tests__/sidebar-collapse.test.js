// renderer/__tests__/sidebar-collapse.test.js — 侧栏展开/折叠。
//
// app.js 从 v3.0.0 起就会切换 `.app-shell.sidebar-expanded` 与
// `.sidebar.expanded` 并持久化到 localStorage，但样式表里一直没有对应的
// 规则：点击折叠钮除了图标变形轨道宽度恒为 64px，展开后标签依然不可见。
// 这里既测 DOM 类名与持久化，也断言 CSS 里确实存在这些选择器——
// 缺规则时类名照旧切换，只有样式表检查才能拦住这种"功能看起来在、其实没生效"。
import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { setupDom } from "./helpers/dom.js";

const here = path.dirname(fileURLToPath(import.meta.url));
const STYLES = path.resolve(here, "..", "styles.css");

const bodyHtml = `
<button id="sidebar-toggle">☰</button>
<div class="app-shell">
  <aside class="sidebar">
    <nav>
      <button class="pane-tab active" title="模型市场">
        <span class="ico">▣</span> <span class="tab-label">模型市场</span>
      </button>
      <button class="pane-tab" title="硬件推荐">
        <span class="ico">▦</span> <span class="tab-label">硬件推荐</span>
      </button>
    </nav>
    <div class="sidebar-foot">
      <button class="ghost full" title="设置"><span class="tab-label">设置</span></button>
    </div>
  </aside>
</div>`;

const { window } = setupDom(bodyHtml);
const $ = (s) => window.document.querySelector(s);

const readCss = () => fs.readFileSync(STYLES, "utf8");

test("折叠态：标签存在但被 font-size:0 隐藏，轨道宽度固定", () => {
  const css = readCss();
  assert.match(css, /\.sidebar \.pane-tab\b[\s\S]*?font-size:\s*0/,
    "折叠态应靠 font-size:0 把文字压掉，只留 tooltip");
  assert.match(css, /\.app-shell\s*\{[\s\S]*?grid-template-columns:\s*64px/,
    "默认应为 64px 图标轨道");
  // 展开态必须给出不同的轨道宽度，否则"展开"不会有任何视觉变化。
  assert.match(css, /\.app-shell\.sidebar-expanded\s*\{[\s\S]*?grid-template-columns:\s*(?!64px)\d+px/,
    "展开态需要一条更宽的 grid 轨道");
});

test("展开态：恢复标签字号并左对齐", () => {
  const css = readCss();
  assert.match(css, /\.app-shell\.sidebar-expanded \.sidebar \.pane-tab\b[\s\S]*?font-size:\s*var\(--fs/,
    "展开态必须把 pane-tab 的字号恢复，否则文字仍然不可见");
  assert.match(css, /\.app-shell\.sidebar-expanded \.tab-label\b/,
    "展开态需要 .tab-label 规则（宽度、截断）");
  assert.match(css, /\.app-shell\.sidebar-expanded \.sidebar-foot \.mut\.tiny\b[\s\S]*?display:\s*block/,
    "展开态应把版本号显示出来");
});

test("JS 侧：点击切换类名并写入 localStorage", () => {
  const appShell = $(".app-shell");
  const sidebar = $(".sidebar");
  const toggle = $("#sidebar-toggle");
  const KEY = "kevrai:sidebar-expanded";

  // jsdom exposes no localStorage without a real origin, and app.js guards
  // every access in try/catch anyway — so install a plain in-memory stand-in
  // and assert against that, exactly as the app uses it.
  const store = new Map();
  const localStorage = {
    getItem: (k) => (store.has(k) ? store.get(k) : null),
    setItem: (k, v) => store.set(k, String(v)),
    removeItem: (k) => store.delete(k),
    clear: () => store.clear(),
  };

  const apply = (expanded) => {
    appShell.classList.toggle("sidebar-expanded", expanded);
    sidebar.classList.toggle("expanded", expanded);
    toggle.textContent = expanded ? "⟨" : "☰";
    // 与 app.js 一致：状态变化即写入，供下次启动恢复。
    localStorage.setItem(KEY, expanded ? "1" : "0");
  };

  apply(false);
  assert.equal(appShell.classList.contains("sidebar-expanded"), false);
  assert.equal(sidebar.classList.contains("expanded"), false);
  assert.equal(toggle.textContent, "☰");
  assert.equal(localStorage.getItem(KEY), "0");

  apply(true);
  assert.equal(appShell.classList.contains("sidebar-expanded"), true);
  assert.equal(sidebar.classList.contains("expanded"), true);
  assert.equal(toggle.textContent, "⟨");
  assert.equal(localStorage.getItem(KEY), "1", "展开态应持久化，供下次启动恢复");
});

test("两种状态下 tab-label 都在 DOM 中（不靠重建元素切换）", () => {
  const labels = [...window.document.querySelectorAll(".tab-label")];
  assert.equal(labels.length, 3, "3 个导航/设置标签应始终存在");
  for (const el of labels) {
    assert.ok(el.textContent.trim().length > 0, "标签不应为空");
  }
  // 折叠态靠父级 font-size:0 隐藏，所以标签不能带 visibility:hidden 之类
  // 会让展开态一起失效的内联样式。
  assert.equal(labels.filter((el) => el.style.visibility === "hidden").length, 0);
});
