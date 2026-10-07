// renderer/__tests__/sidebar-collapse.test.js — 侧栏展开/折叠。
//
// 这块功能有过一次「代码看起来有、界面上无反应」：JS 一直切
// `.sidebar-expanded` 类名，样式表里却没有对应规则，点折叠钮只有图标变形、
// 轨道恒为 64px。所以测试分两半，缺一不可：
//
//   1) 驱动真实模块 renderer/modules/sidebar.js（不是测试里重写的副本），
//      断言类名、文案、aria 与持久化；
//   2) 直接读 styles.css，断言展开态规则确实存在——只测类名的话，
//      把整段 CSS 删掉测试依然全绿。
import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { setupDom } from "./helpers/dom.js";

const here = path.dirname(fileURLToPath(import.meta.url));
const STYLES = path.resolve(here, "..", "styles.css");

const bodyHtml = `
<button class="sidebar-toggle" id="sidebar-toggle" aria-label="切换侧边栏">☰</button>
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
      <p class="mut tiny" id="version-line">v4.0.1</p>
    </div>
  </aside>
</div>`;

const { window } = setupDom(bodyHtml);
const $ = (s) => window.document.querySelector(s);

// jsdom 无 origin 时不提供 localStorage；sidebar.js 本身对访问做了 try/catch，
// 这里装一个内存替身，行为与真机一致。
const store = new Map();
const localStorage = {
  getItem: (k) => (store.has(k) ? store.get(k) : null),
  setItem: (k, v) => store.set(k, String(v)),
  removeItem: (k) => store.delete(k),
  clear: () => store.clear(),
};
Object.defineProperty(window, "localStorage", { configurable: true, value: localStorage });
globalThis.localStorage = localStorage;

const { initSidebarToggle, applySidebarState, readSidebarExpanded } =
  await import("../modules/sidebar.js");

// 固定翻译函数，避免测试依赖 i18n 文案库。
const t = (k) => k;

const readCss = () => fs.readFileSync(STYLES, "utf8");
const resetDom = () => {
  store.clear();
  const shell = $(".app-shell");
  shell.classList.remove("sidebar-expanded");
  $(".sidebar").classList.remove("expanded");
  const btn = $("#sidebar-toggle");
  btn.textContent = "☰";
  btn.removeAttribute("aria-expanded");
  return { appShell: shell, sidebar: $(".sidebar"), toggle: btn };
};

test("initSidebarToggle: 点击切换类名/文案/aria，并写入 localStorage", () => {
  const els = resetDom();
  const ok = initSidebarToggle(t);
  assert.equal(ok, true, "DOM 齐备时应返回 true");
  assert.equal(els.toggle.textContent, "☰", "默认折叠");

  els.toggle.click();
  assert.equal(els.appShell.classList.contains("sidebar-expanded"), true, "展开类已加");
  assert.equal(els.sidebar.classList.contains("expanded"), true, "侧栏自身的类也已加");
  assert.equal(els.toggle.textContent, "⟨", "按钮文案切为折叠符号");
  assert.equal(els.toggle.getAttribute("aria-label"), "app.collapseSidebar");
  assert.equal(els.toggle.getAttribute("aria-expanded"), "true");
  assert.equal(localStorage.getItem("kevrai:sidebar-expanded"), "1", "展开态已持久化");

  els.toggle.click();
  assert.equal(els.appShell.classList.contains("sidebar-expanded"), false, "再点一次收起");
  assert.equal(els.toggle.textContent, "☰");
  assert.equal(els.toggle.getAttribute("aria-label"), "app.expandSidebar");
  assert.equal(els.toggle.getAttribute("aria-expanded"), "false");
  assert.equal(localStorage.getItem("kevrai:sidebar-expanded"), "0", "折叠态也应持久化");
});

test("initSidebarToggle: 启动时按持久化值恢复，不跳回默认", () => {
  localStorage.setItem("kevrai:sidebar-expanded", "1");
  assert.equal(readSidebarExpanded(), true);
  const els = resetDom();
  store.set("kevrai:sidebar-expanded", "1");
  initSidebarToggle(t);
  assert.equal(els.appShell.classList.contains("sidebar-expanded"), true,
    "刷新后应保持展开，而不是先收起再被点击覆盖");
  assert.equal(els.toggle.textContent, "⟨");
});

test("initSidebarToggle: 缺少必要 DOM 时返回 false 而不是抛错", () => {
  const shell = $(".app-shell");
  const sidebar = $(".sidebar");
  const btn = $("#sidebar-toggle");
  btn.remove();
  assert.equal(initSidebarToggle(t), false, "缺按钮时应安全返回 false");
  btn.id = "sidebar-toggle";
  document.body.appendChild(btn);
  sidebar.remove();
  assert.equal(initSidebarToggle(t), false, "缺侧栏时应安全返回 false");
  document.body.appendChild(sidebar);
  assert.ok(shell, "app-shell 仍在");
  resetDom();
});

test("applySidebarState: DOM 缺失时返回 false 而不是抛异常", () => {
  const els = resetDom();
  // 只传部分元素时不应把属性写入弄崩
  assert.equal(applySidebarState({ appShell: els.appShell, sidebar: els.sidebar, toggle: null, expanded: true, t }),
    false, "缺按钮应安全返回 false");
  assert.equal(applySidebarState({ appShell: null, sidebar: null, toggle: null, expanded: true, t }),
    false, "全缺也应安全返回 false");
  assert.equal(applySidebarState({ ...els, expanded: true, t }), true, "齐备时返回 true");
});

test("样式表：折叠态靠 font-size:0 隐藏文字，轨道固定 64px", () => {
  const css = readCss();
  assert.match(css, /\.sidebar \.pane-tab\b[\s\S]*?font-size:\s*0/,
    "折叠态应靠 font-size:0 把文字压掉，只留 tooltip");
  assert.match(css, /\.app-shell\s*\{[\s\S]*?grid-template-columns:\s*64px/,
    "默认应为 64px 图标轨道");
});

test("样式表：展开态规则必须存在（删掉它测试就要转红）", () => {
  const css = readCss();
  assert.match(css, /\.app-shell\.sidebar-expanded\s*\{[\s\S]*?grid-template-columns:\s*(?!64px)\d+px/,
    "展开态需要一条更宽的 grid 轨道，否则「展开」没有任何视觉变化");
  assert.match(css, /\.app-shell\.sidebar-expanded \.sidebar \.pane-tab\b[\s\S]*?font-size:\s*var\(--fs/,
    "展开态必须恢复 pane-tab 的字号，否则文字仍然不可见");
  assert.match(css, /\.app-shell\.sidebar-expanded \.tab-label\b/,
    "展开态需要 .tab-label 规则（宽度、截断）");
  assert.match(css, /\.app-shell\.sidebar-expanded \.sidebar-foot \.mut\.tiny\b[\s\S]*?display:\s*block/,
    "展开态应把版本号显示出来");
});

test("真实 index.html 里每个导航项都带 tab-label（折叠态才有东西可展开）", () => {
  const indexHtml = fs.readFileSync(path.resolve(here, "..", "index.html"), "utf8");
  const labelCount = (indexHtml.match(/class="tab-label"/g) || []).length;
  const tabCount = (indexHtml.match(/class="pane-tab/g) || []).length;
  assert.ok(tabCount >= 10, `导航项应至少有 10 个，实际 ${tabCount}`);
  assert.equal(labelCount, tabCount + 1,
    "每个 pane-tab 加底部设置按钮都应有 tab-label，缺一个展开后就是空白项");
});

test("语义色：浅色主题必须有 --ok/--warn/--err 覆写值", () => {
  const css = readCss();
  // 不能用 indexOf("}") 截取：块内注释里含有 '}'，会提前截断而误报缺失。
  const start = css.indexOf('html[data-theme="light"]');
  assert.ok(start > 0, "应存在浅色主题块");
  let depth = 0, end = -1, inComment = false;
  for (let i = start; i < css.length; i++) {
    if (inComment) {
      if (css.startsWith("*/", i)) { inComment = false; i++; }
      continue;
    }
    if (css.startsWith("/*", i)) { inComment = true; i++; continue; }
    if (css[i] === "{") depth++;
    else if (css[i] === "}") { depth--; if (depth === 0) { end = i; break; } }
  }
  assert.ok(end > start, "浅色主题块应正常闭合");
  const block = css.slice(start, end);
  for (const name of ["--ok", "--warn", "--err"]) {
    assert.ok(block.includes(`${name}:`),
      `浅色主题缺少 ${name} 覆写，会沿用深底亮度导致对比度不足`);
  }
  // 徽章底色必须是主题感知的变量，不能是白底叠白。
  assert.doesNotMatch(css, /\.pill\s*\{[^}]*background:\s*rgba\(255,\s*255,\s*255/,
    ".pill 用白色叠加做底色在浅色主题下不可见");
});
