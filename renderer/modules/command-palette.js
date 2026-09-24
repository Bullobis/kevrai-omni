// renderer/modules/command-palette.js
//
// 命令面板（Command Palette）—— 对标 Cherry Studio / Linear / Raycast 的
// 全局快速入口。Ctrl/⌘ + K 唤起，模糊匹配后可：
//   · 快速跳转任意侧边栏标签
//   · 触发常用动作（设置 / 下载 / GPU 检测 / 检查更新 / 刷新）
//   · 搜索模型并跳转到模型市场定位
//
// 自包含设计：模块自行创建 DOM、自行注入 <style>（类名前缀 cmdk-），
// 因此【不需要改动 index.html / styles.css】；app.js 仅需 import 并 init。
// 样式全部引用既有设计令牌（--stack-* / --acc 等），自动适配深色/浅色主题。
"use strict";

import { api } from "./api.js";

/* ------------------------------------------------------------------ */
/* 样式（自注入，跟随设计令牌）                                          */
/* ------------------------------------------------------------------ */

const CSS = `
.cmdk-overlay{position:fixed;inset:0;z-index:9000;display:flex;align-items:flex-start;
  justify-content:center;padding:12vh 16px 16px;background:rgba(0,0,0,.42)}
.cmdk{width:min(640px,100%);background:var(--stack-3);border:1px solid var(--bord);
  border-radius:var(--r-4);box-shadow:0 18px 48px rgba(0,0,0,.45);overflow:hidden;
  display:flex;flex-direction:column;animation:cmdk-in .14s ease}
@keyframes cmdk-in{from{opacity:0;transform:translateY(-6px) scale(.99)}to{opacity:1;transform:none}}
.cmdk-input{width:100%;border:none;outline:none;background:transparent;color:var(--fg);
  font-size:16px;padding:16px 18px 12px;font-family:var(--sans)}
.cmdk-input::placeholder{color:var(--mut)}
.cmdk-list{max-height:52vh;overflow-y:auto;padding:0 8px 8px;scrollbar-width:thin}
.cmdk-group-label{font-size:11px;font-weight:500;text-transform:uppercase;letter-spacing:.5px;
  color:var(--mut);padding:10px 10px 4px}
.cmdk-item{display:flex;align-items:center;gap:10px;width:100%;text-align:left;cursor:pointer;
  background:transparent;color:var(--fg);border:1px solid transparent;border-radius:var(--r-2);
  padding:9px 10px;font-size:14px;font-family:var(--sans)}
.cmdk-item .cmdk-ico{width:18px;text-align:center;flex:0 0 18px;color:var(--mut);font-size:15px}
.cmdk-item .cmdk-title{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.cmdk-item .cmdk-sub{color:var(--mut);font-size:12px;max-width:46%;overflow:hidden;
  text-overflow:ellipsis;white-space:nowrap}
.cmdk-item .cmdk-kbd{color:var(--mut);font-size:11px;border:1px solid var(--bord);
  border-radius:6px;padding:1px 6px;font-family:var(--mono)}
.cmdk-item[aria-selected="true"]{background:var(--sel);border-color:var(--bord)}
.cmdk-item[aria-selected="true"] .cmdk-ico{color:var(--acc)}
.cmdk-item mark{background:transparent;color:var(--acc);font-weight:700}
.cmdk-empty{padding:22px;text-align:center;color:var(--mut);font-size:13px}
.cmdk-foot{display:flex;gap:14px;align-items:center;border-top:1px solid var(--bord);
  padding:8px 14px;color:var(--mut);font-size:11px}
.cmdk-foot kbd{font-family:var(--mono);border:1px solid var(--bord);border-radius:5px;padding:0 5px}
.cmdk-overlay[hidden]{display:none}
@media (prefers-reduced-motion: reduce){.cmdk{animation:none}}
`;

/* ------------------------------------------------------------------ */
/* 命令定义                                                             */
/* ------------------------------------------------------------------ */

// 侧边栏标签（与 index.html 的 data-tab 保持一致）
const TABS = [
  { id: "market",       label: "模型市场",    ico: "◇" },
  { id: "hardware",     label: "硬件推荐",    ico: "⚡" },
  { id: "engines",      label: "AI 引擎",     ico: "⟁" },
  { id: "mnn",          label: "MNN 引擎",    ico: "⬢" },
  { id: "agent",        label: "AI Agent",    ico: "🤖" },
  { id: "ltx",          label: "LTX-2.5 视频", ico: "🎥" },
  { id: "local",        label: "本地模型",    ico: "⛁" },
  { id: "gguf",         label: "GGUF 仓库",   ico: "▤" },
  { id: "pending",      label: "待官方开源",  ico: "⌛" },
  { id: "environments", label: "环境管理",    ico: "⚙" },
];

function clickTab(tabId) {
  const el = document.querySelector(`.pane-tab[data-tab="${tabId}"]`);
  if (el) el.click();
}
function clickAction(name) {
  const el = document.querySelector(`[data-action="${name}"]`);
  if (el) el.click();
}

// 静态命令（导航 + 动作）。run 返回 true 表示成功。
function buildStaticCommands() {
  const nav = TABS.map((t) => ({
    group: "跳转",
    title: t.label,
    ico: t.ico,
    keywords: t.id,
    run: () => clickTab(t.id),
  }));
  const actions = [
    { title: "打开设置",   ico: "⚙", keywords: "settings preferences 配置", run: () => clickAction("open-settings") },
    { title: "打开下载面板", ico: "⬇", keywords: "downloads 下载",           run: () => clickAction("open-downloads") },
    { title: "检测 GPU",   ico: "⚡", keywords: "gpu 显卡 硬件检测",        run: () => clickAction("detect-gpu") },
    { title: "检查应用更新", ico: "↻", keywords: "update 更新",             run: () => clickAction("check-updates") },
    { title: "刷新",       ico: "⟳", keywords: "refresh reload 重新加载",  run: () => clickAction("refresh") },
  ].map((c) => ({ group: "动作", ...c }));
  return nav.concat(actions);
}

// 动态拉取模型命令（缓存；失败返回空，不影响面板使用）。
let modelCache = null;
async function getModelCommands() {
  if (modelCache) return modelCache;
  try {
    const r = await api.models({});
    const list = (r && r.body && r.body.models) || (r && r.models) || [];
    modelCache = list.map((m) => ({
      group: "模型",
      title: m.name || m.id,
      sub: m.id || "",
      ico: "◇",
      // 搜索文本同时包含名称、id、描述，便于模糊命中
      keywords: [m.id, m.description, m.category].filter(Boolean).join(" "),
      run: () => focusModel(m),
    }));
  } catch (_) {
    modelCache = [];
  }
  return modelCache;
}

// 跳转到市场并按模型名检索定位。
function focusModel(m) {
  clickTab("market");
  const search = document.querySelector("#search");
  if (!search) return;
  search.value = m.name || m.id || "";
  search.dispatchEvent(new Event("input", { bubbles: true }));
}

/* ------------------------------------------------------------------ */
/* 模糊匹配与打分                                                        */
/* ------------------------------------------------------------------ */

// 针对单个 token 的匹配；返回命中得分（越大越好），不匹配返回 -1。
function tokenScore(text, token) {
  const t = text.toLowerCase();
  const q = token.toLowerCase();
  if (!q) return 0;
  const idx = t.indexOf(q);
  if (idx === 0) return 100;                 // 开头命中
  if (idx > 0) return 60 - Math.min(idx, 20); // 包含命中，越靠前越好
  // 子序列匹配（fuzzy）
  let ti = 0, qi = 0, last = -1, score = 0;
  while (ti < t.length && qi < q.length) {
    if (t[ti] === q[qi]) {
      score += last === -1 ? 20 : (ti === last + 1 ? 6 : 2);
      last = ti; qi++;
    }
    ti++;
  }
  return qi === q.length ? score : -1;
}

// 多 token（空格分隔，AND）；返回总分。
function matchScore(cmd, query) {
  const hay = `${cmd.title} ${cmd.keywords || ""}`;
  const tokens = query.trim().split(/\s+/).filter(Boolean);
  if (!tokens.length) return 0;
  let total = 0;
  for (const tok of tokens) {
    const s = tokenScore(hay, tok);
    if (s < 0) return -1;
    total += s;
  }
  // 标题命中额外加权
  if (cmd.title.toLowerCase().includes(query.toLowerCase())) total += 10;
  return total;
}

/* ------------------------------------------------------------------ */
/* 面板 UI 与交互                                                        */
/* ------------------------------------------------------------------ */

let overlayEl = null;
let inputEl = null;
let listEl = null;
let current = [];     // 当前可见命令（与可点击项顺序一致）
let selected = 0;

function el(tag, cls) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  return e;
}

function highlight(text, query) {
  const safe = text.replace(/[&<>"']/g, (c) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const tokens = query.trim().split(/\s+/).filter(Boolean);
  if (!tokens.length) return safe;
  // 仅对标题做简单高亮：找到最长 token 的连续命中区间
  const lower = safe.toLowerCase();
  let best = -1, bestLen = 0;
  for (const tok of tokens) {
    const i = lower.indexOf(tok.toLowerCase());
    if (i >= 0 && tok.length > bestLen) { best = i; bestLen = tok.length; }
  }
  if (best < 0) return safe;
  return `${safe.slice(0, best)}<mark>${safe.slice(best, best + bestLen)}</mark>${safe.slice(best + bestLen)}`;
}

function render(query) {
  listEl.innerHTML = "";
  const all = current;
  const scored = all
    .map((c) => ({ c, s: matchScore(c, query) }))
    .filter((x) => x.s >= 0)
    .sort((a, b) => b.s - a.s)
    .map((x) => x.c);

  if (!scored.length) {
    const empty = el("div", "cmdk-empty");
    empty.textContent = `没有匹配「${query}」的命令`;
    listEl.appendChild(empty);
    selected = 0;
    return;
  }

  // 按分组渲染，同时构建可选项索引
  const selectable = [];
  let lastGroup = null;
  for (const c of scored) {
    if (c.group !== lastGroup) {
      const label = el("div", "cmdk-group-label");
      label.textContent = c.group;
      listEl.appendChild(label);
      lastGroup = c.group;
    }
    const item = el("button", "cmdk-item");
    item.type = "button";
    item.setAttribute("role", "option");
    item.innerHTML =
      `<span class="cmdk-ico">${c.ico || ""}</span>` +
      `<span class="cmdk-title">${highlight(c.title, query)}</span>` +
      (c.sub ? `<span class="cmdk-sub">${c.sub}</span>` : "") +
      (c.hint ? `<span class="cmdk-kbd">${c.hint}</span>` : "");
    const idx = selectable.length;
    item.addEventListener("click", () => execute(idx));
    item.addEventListener("mousemove", () => {
      selected = idx; updateSelection(selectable);
    });
    listEl.appendChild(item);
    selectable.push(item);
  }
  listEl._selectable = selectable;
  selected = 0;
  updateSelection(selectable);
}

function updateSelection(selectable) {
  selectable.forEach((node, i) => node.setAttribute("aria-selected", i === selected ? "true" : "false"));
  const node = selectable[selected];
  if (node) node.scrollIntoView({ block: "nearest" });
}

function execute(idx) {
  const visible = filteredCommands();
  const cmd = visible[idx];
  close();
  if (cmd) {
    try { cmd.run(); } catch (_) { /* 动作失败静默，避免打断 */ }
  }
}

// 当前过滤后的命令顺序（与渲染一致），供 execute 取用。
let lastQuery = "";
function filteredCommands() {
  return current
    .map((c) => ({ c, s: matchScore(c, lastQuery) }))
    .filter((x) => x.s >= 0)
    .sort((a, b) => b.s - a.s)
    .map((x) => x.c);
}

async function open() {
  if (!overlayEl) build();
  // 汇总命令：静态 + 模型（模型可能首次异步加载）
  const staticCmds = buildStaticCommands();
  const modelCmds = await getModelCommands();
  current = staticCmds.concat(modelCmds);
  inputEl.value = "";
  lastQuery = "";
  overlayEl.hidden = false;
  document.body.style.overflow = "hidden";
  render("");
  inputEl.focus();
  // 若模型缓存此前为空、本次才加载完成，刷新一次
  getModelCommands().then((mc) => {
    if (!overlayEl.hidden && mc.length && !current.some((c) => c.group === "模型")) {
      current = buildStaticCommands().concat(mc);
      render(inputEl.value);
    }
  });
}

function close() {
  if (!overlayEl) return;
  overlayEl.hidden = true;
  document.body.style.overflow = "";
}

function build() {
  const style = el("style");
  style.textContent = CSS;
  document.head.appendChild(style);

  overlayEl = el("div", "cmdk-overlay");
  overlayEl.hidden = true;
  const dialog = el("div", "cmdk");
  dialog.setAttribute("role", "dialog");
  dialog.setAttribute("aria-modal", "true");
  dialog.setAttribute("aria-label", "命令面板");

  inputEl = el("input", "cmdk-input");
  inputEl.type = "text";
  inputEl.setAttribute("placeholder", "输入命令或搜索模型…");
  inputEl.setAttribute("aria-label", "命令输入");
  inputEl.addEventListener("input", () => {
    lastQuery = inputEl.value;
    render(lastQuery);
  });
  inputEl.addEventListener("keydown", (e) => {
    const selectable = listEl._selectable || [];
    if (e.key === "ArrowDown") { e.preventDefault(); selected = (selected + 1) % Math.max(selectable.length, 1); updateSelection(selectable); }
    else if (e.key === "ArrowUp") { e.preventDefault(); selected = (selected - 1 + selectable.length) % Math.max(selectable.length, 1); updateSelection(selectable); }
    else if (e.key === "Enter") { e.preventDefault(); execute(selected); }
    else if (e.key === "Escape") { e.preventDefault(); close(); }
  });

  listEl = el("div", "cmdk-list");
  listEl.setAttribute("role", "listbox");

  const foot = el("div", "cmdk-foot");
  foot.innerHTML =
    `<span><kbd>↑</kbd><kbd>↓</kbd> 选择</span>` +
    `<span><kbd>↵</kbd> 执行</span>` +
    `<span><kbd>esc</kbd> 关闭</span>` +
    `<span style="margin-left:auto"><kbd>ctrl</kbd>+<kbd>K</kbd> 唤起</span>`;

  dialog.appendChild(inputEl);
  dialog.appendChild(listEl);
  dialog.appendChild(foot);
  overlayEl.appendChild(dialog);
  // 点击遮罩关闭；点击面板内部不关闭
  overlayEl.addEventListener("click", (e) => { if (e.target === overlayEl) close(); });
  document.body.appendChild(overlayEl);
}

/* ------------------------------------------------------------------ */
/* 初始化                                                               */
/* ------------------------------------------------------------------ */

export function initCommandPalette() {
  document.addEventListener("keydown", (e) => {
    if ((e.ctrlKey || e.metaKey) && (e.key === "k" || e.key === "K")) {
      e.preventDefault();
      if (!overlayEl || overlayEl.hidden) open();
      else close();
    }
  });
}
