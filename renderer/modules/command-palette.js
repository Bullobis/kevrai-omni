// renderer/modules/command-palette.js — 命令面板（Ctrl/⌘+K）+ 全局快捷键体系。
//
// 零依赖、纯 ES module、可独立 import。
//   - openCommandPalette()   手动唤起
//   - registerAction(id, label, handler, keywords?, icon?, group?)  注册/覆盖动作
//   - initCommandPalette(container?)  绑定全局快捷键并挂载 overlay
//   - iconSvg(name)          返回内联 lucide 风格 SVG 字符串（16px 用）
//
// 与 app.js 解耦：内置动作默认通过 window.dispatchEvent(CustomEvent) 发事件，
// 不直接调用 app.js 内部函数。app.js 也可用 registerAction(id, ...) 覆盖 handler
// 接入真实业务。
"use strict";

import { t, getLocale, setLocale } from "./i18n.js";

const hasDocument = typeof document !== "undefined";
const hasWindow = typeof window !== "undefined";

/** 动作表：id -> { id, label, keywords:[], handler, icon, group, shortcut } */
const actions = new Map();
let overlay = null;
let inputEl = null;
let listEl = null;
let selectedIdx = 0;
let rendered = []; // 当前过滤后的动作列表
let inited = false;
// 打开浮层前聚焦的元素（可访问性：关闭时归还焦点）。
let returnFocusTo = null;

// Recently-used commands (persisted; only read/written in the browser).
const RECENT_KEY = "kevrai:cmd-recent";
function getRecent() {
  try { return JSON.parse(localStorage.getItem(RECENT_KEY) || "{}"); } catch (_) { return {}; }
}
function bumpRecent(id) {
  try {
    const r = getRecent();
    r[id] = Date.now();
    const top = Object.entries(r).sort((a, b) => b[1] - a[1]).slice(0, 8);
    localStorage.setItem(RECENT_KEY, JSON.stringify(Object.fromEntries(top)));
  } catch (_) {}
}

// ── 纯函数：模糊匹配（可在 Node 下单测）──────────────────────────────────────
/**
 * subsequenceMatch(query, target) — 子序列匹配：query 的每个字符按顺序出现在 target 中即命中。
 * 同时对连续子串命中给更高分。大小写不敏感。
 */
export function subsequenceMatch(query, target) {
  if (!query) return true;
  const q = String(query).toLowerCase();
  const s = String(target || "").toLowerCase();
  // 子串命中直接通过
  if (s.includes(q)) return { score: 100 - s.indexOf(q), hit: true };
  // 子序列
  let qi = 0;
  for (let i = 0; i < s.length && qi < q.length; i++) {
    if (s[i] === q[qi]) qi++;
  }
  return { score: qi === q.length ? 50 : 0, hit: qi === q.length };
}

/**
 * filterActions(actionList, query) — 按 label + keywords 过滤并按分数排序。
 * actionList: [{id,label,keywords}]
 */
export function filterActions(actionList, query) {
  const q = (query || "").trim().toLowerCase();
  if (!q) return actionList.slice();
  const scored = [];
  for (const a of actionList) {
    const labelHit = subsequenceMatch(q, a.label);
    let best = labelHit.score;
    let hit = labelHit.hit;
    for (const kw of a.keywords || []) {
      const k = subsequenceMatch(q, kw);
      if (k.score > best) best = k.score;
      if (k.hit) hit = true;
    }
    if (hit) scored.push({ a, best });
  }
  scored.sort((x, y) => y.best - x.best);
  return scored.map((x) => x.a);
}

// ── 内联 SVG 图标（lucide 风格，currentColor 描边，16px 展示用）──────────────
// 每条目是 <svg> 内部内容（path/circle/rect…），外层统一包成规范 svg。
const ICON_PATHS = {
  search: '<circle cx="11" cy="11" r="8"/><path d="m21 21-4.3-4.3"/>',
  settings: '<path d="M12.22 2h-.44a2 2 0 0 0-2 2v.18a2 2 0 0 1-1 1.73l-.43.25a2 2 0 0 1-2 0l-.15-.08a2 2 0 0 0-2.73.73l-.22.38a2 2 0 0 0 .73 2.73l.15.1a2 2 0 0 1 1 1.72v.51a2 2 0 0 1-1 1.74l-.15.09a2 2 0 0 0-.73 2.73l.22.38a2 2 0 0 0 2.73.73l.15-.08a2 2 0 0 1 2 0l.43.25a2 2 0 0 1 1 1.73V20a2 2 0 0 0 2 2h.44a2 2 0 0 0 2-2v-.18a2 2 0 0 1 1-1.73l.43-.25a2 2 0 0 1 2 0l.15.08a2 2 0 0 0 2.73-.73l.22-.39a2 2 0 0 0-.73-2.73l-.15-.08a2 2 0 0 1-1-1.74v-.5a2 2 0 0 1 1-1.74l.15-.09a2 2 0 0 0 .73-2.73l-.22-.38a2 2 0 0 0-2.73-.73l-.15.08a2 2 0 0 1-2 0l-.43-.25a2 2 0 0 1-1-1.73V4a2 2 0 0 0-2-2z"/><circle cx="12" cy="12" r="3"/>',
  download: '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><path d="m7 10 5 5 5-5"/><path d="M12 15V3"/>',
  gpu: '<rect x="4" y="4" width="16" height="16" rx="2"/><rect x="9" y="9" width="6" height="6"/><path d="M15 2v2"/><path d="M15 20v2"/><path d="M2 15h2"/><path d="M2 9h2"/><path d="M20 15h2"/><path d="M20 9h2"/><path d="M9 2v2"/><path d="M9 20v2"/>',
  refresh: '<path d="M3 12a9 9 0 0 1 9-9 9.75 9.75 0 0 1 6.74 2.74L21 8"/><path d="M21 3v5h-5"/><path d="M21 12a9 9 0 0 1-9 9 9.75 9.75 0 0 1-6.74-2.74L3 16"/><path d="M8 16H3v5"/>',
  update: '<path d="M21 12a9 9 0 1 1-9-9c2.52 0 4.93 1 6.74 2.74L21 8"/><path d="M21 3v5h-5"/>',
  globe: '<circle cx="12" cy="12" r="10"/><path d="M12 2a14.5 14.5 0 0 0 0 20 14.5 14.5 0 0 0 0-20"/><path d="M2 12h20"/>',
  sun: '<circle cx="12" cy="12" r="4"/><path d="M12 2v2"/><path d="M12 20v2"/><path d="m4.93 4.93 1.41 1.41"/><path d="m17.66 17.66 1.41 1.41"/><path d="M2 12h2"/><path d="M20 12h2"/><path d="m6.34 17.66-1.41 1.41"/><path d="m19.07 4.93-1.41 1.41"/>',
  moon: '<path d="M12 3a6 6 0 0 0 9 9 9 9 0 1 1-9-9Z"/>',
  monitor: '<rect width="20" height="14" x="2" y="3" rx="2"/><path d="M8 21h8"/><path d="M12 17v4"/>',
  folder: '<path d="M20 20a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-7.9a2 2 0 0 1-1.69-.9L9.6 3.9A2 2 0 0 0 7.93 3H4a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2Z"/>',
  "panel-left": '<rect width="18" height="18" x="3" y="3" rx="2"/><path d="M9 3v18"/>',
  keyboard: '<rect width="20" height="16" x="2" y="4" rx="2"/><path d="M6 8h.01"/><path d="M10 8h.01"/><path d="M14 8h.01"/><path d="M18 8h.01"/><path d="M6 12h.01"/><path d="M18 12h.01"/><path d="M8 16h8"/>',
  navigate: '<path d="m9 18 6-6-6-6"/>',
  market: '<path d="m2 7 4.41-4.41A2 2 0 0 1 7.83 2h8.34a2 2 0 0 1 1.42.59L22 7"/><path d="M4 12v8a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-8"/><path d="M15 22v-4a2 2 0 0 0-2-2h-2a2 2 0 0 0-2 2v4"/><path d="M2 7h20"/>',
  cpu: '<rect x="4" y="4" width="16" height="16" rx="2"/><rect x="9" y="9" width="6" height="6"/><path d="M15 2v2"/><path d="M15 20v2"/><path d="M2 15h2"/><path d="M2 9h2"/><path d="M20 15h2"/><path d="M20 9h2"/><path d="M9 2v2"/><path d="M9 20v2"/>',
  layers: '<path d="m12.83 2.18a2 2 0 0 0-1.66 0L2.6 6.08a1 1 0 0 0 0 1.83l8.58 3.91a2 2 0 0 0 1.66 0l8.58-3.9a1 1 0 0 0 0-1.83Z"/><path d="m22 17.65-9.17 4.16a2 2 0 0 1-1.66 0L2 17.65"/><path d="m22 12.65-9.17 4.16a2 2 0 0 1-1.66 0L2 12.65"/>',
  bot: '<path d="M12 8V4H8"/><rect width="16" height="12" x="4" y="8" rx="2"/><path d="M2 14h2"/><path d="M20 14h2"/><path d="M15 13v2"/><path d="M9 13v2"/>',
  video: '<path d="m16 13 5.223 3.482a.5.5 0 0 0 .777-.416V7.87a.5.5 0 0 0-.752-.432L16 10.5"/><rect x="2" y="6" width="14" height="12" rx="2"/>',
  "hard-drive": '<line x1="22" x2="2" y1="12" y2="12"/><path d="M5.45 5.11 2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.45-6.89A2 2 0 0 0 16.76 4H7.24a2 2 0 0 0-1.79 1.11z"/><line x1="6" x2="6.01" y1="16" y2="16"/><line x1="10" x2="10.01" y1="16" y2="16"/>',
  package: '<path d="M11 21.73a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73z"/><path d="M12 22V12"/><path d="m3.3 7 7.703 4.734a2 2 0 0 0 1.994 0L20.7 7"/><path d="m7.5 4.27 9 5.15"/>',
  clock: '<circle cx="12" cy="12" r="10"/><path d="M12 6v6l4 2"/>',
  terminal: '<path d="m4 17 6-6-6-6"/><path d="M12 19h8"/>',
};

/**
 * iconSvg(name) — 返回内联 SVG 字符串（16px、currentColor）。
 * 未知名返回一个通用圆点占位，永不抛错。
 */
export function iconSvg(name) {
  const inner = ICON_PATHS[name] || '<circle cx="12" cy="12" r="5"/>';
  return `<svg class="cmdpal-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${inner}</svg>`;
}

// ── 动作注册 ──────────────────────────────────────────────────────────────────
export function registerAction(id, label, handler, keywords, icon, group) {
  if (!id || typeof handler !== "function") return;
  actions.set(id, {
    id,
    label: typeof label === "function" ? label : () => String(label ?? id),
    keywords: Array.isArray(keywords) ? keywords : keywords ? [keywords] : [],
    handler,
    icon: typeof icon === "string" && icon ? icon : "",
    group: typeof group === "string" && group ? group : "",
    shortcut: "",
  });
}

/** 供测试读取某动作的注册数据（含 icon/group/shortcut）。 */
export function __getRegisteredAction(id) {
  return actions.get(id) || null;
}

// ── 默认动作（事件解耦）───────────────────────────────────────────────────────
function emit(name, detail) {
  if (!hasWindow) return;
  window.dispatchEvent(new CustomEvent(name, { detail: detail || {} }));
}

// Apply a theme to <html> immediately (persistence is handled by the app via
// the kevrai:set-theme event). Defaults to dark when the OS query is absent.
function applyThemeInstant(theme) {
  if (!hasDocument) return;
  const sysDark = hasWindow && window.matchMedia
    ? window.matchMedia("(prefers-color-scheme: dark)").matches : true;
  const dark = theme === "dark" || (theme === "system" && sysDark);
  document.documentElement.dataset.theme = dark ? "dark" : "light";
}

function registerDefaults() {
  // 切 pane：dispatch kevrai:navigate { tab }
  // [id, labelKey, tab, keywords, icon, shortcutLabel]
  const panes = [
    ["goMarket", "cmdpal.actions.goMarket", "market", ["models", "market", "模型", "模型市场", "home"], "market", "⌘1"],
    ["goHardware", "cmdpal.actions.goHardware", "hardware", ["hardware", "gpu", "硬件", "推荐"], "gpu", "⌘2"],
    ["goEngines", "cmdpal.actions.goEngines", "engines", ["engines", "engine", "引擎", "llama.cpp"], "layers", "⌘3"],
    ["goMnn", "cmdpal.actions.goMnn", "mnn", ["mnn", "引擎"], "package", "⌘4"],
    ["goAgent", "cmdpal.actions.goAgent", "agent", ["agent", "assistant", "智能体", "助手"], "bot", "⌘5"],
    ["goLtx", "cmdpal.actions.goLtx", "ltx", ["ltx", "video", "视频", "短剧"], "video", "⌘6"],
    ["goLocal", "cmdpal.actions.goLocal", "local", ["local", "本地", "import", "导入"], "hard-drive", "⌘7"],
    ["goGguf", "cmdpal.actions.goGguf", "gguf", ["gguf", "仓库"], "package", "⌘8"],
    ["goPending", "cmdpal.actions.goPending", "pending", ["pending", "待开源", "wait"], "clock", "⌘9"],
    ["goEnvironments", "cmdpal.actions.goEnvironments", "environments", ["environments", "env", "环境"], "terminal", "⌘0"],
  ];
  for (const [id, labelKey, tab, kws, icon, sc] of panes) {
    registerAction(id, () => t(labelKey), () => emit("kevrai:navigate", { tab }), kws, icon, "导航");
    const a = actions.get(id); if (a) a.shortcut = sc;
  }
  registerAction("openSettings", () => t("cmdpal.actions.openSettings"),
    () => emit("kevrai:open-settings"), ["settings", "设置", "preferences", "偏好"], "settings", "操作");
  actions.get("openSettings").shortcut = "⌘,";
  registerAction("openDownloads", () => t("cmdpal.actions.openDownloads"),
    () => emit("kevrai:open-downloads"), ["downloads", "下载", "tasks", "任务"], "download", "操作");
  actions.get("openDownloads").shortcut = "⌘D";
  registerAction("detectGpu", () => t("cmdpal.actions.detectGpu"),
    () => emit("kevrai:detect-gpu"), ["gpu", "硬件", "detect", "检测"], "gpu", "操作");
  registerAction("refreshModels", () => t("cmdpal.actions.refreshModels"),
    () => emit("kevrai:refresh"), ["refresh", "reload", "刷新", "重载", "models"], "refresh", "操作");
  registerAction("checkUpdates", () => t("cmdpal.actions.checkUpdates"),
    () => emit("kevrai:check-updates"), ["update", "更新", "upgrade", "升级"], "update", "操作");
  registerAction("switchLocale", () => t("cmdpal.actions.switchLocale"),
    async () => {
      const next = getLocale() === "zh-CN" ? "en-US" : "zh-CN";
      await setLocale(next);
      renderList();
    }, ["locale", "language", "i18n", "语言", "en", "zh", "中文", "english"], "globe", "操作");

  // v3.1.0 — theme switching, data folder, sidebar toggle.
  registerAction("themeDark", () => t("cmdpal.actions.themeDark"),
    () => { applyThemeInstant("dark"); emit("kevrai:set-theme", { theme: "dark" }); },
    ["dark", "深色", "theme", "主题", "night"], "moon", "主题");
  registerAction("themeLight", () => t("cmdpal.actions.themeLight"),
    () => { applyThemeInstant("light"); emit("kevrai:set-theme", { theme: "light" }); },
    ["light", "浅色", "theme", "主题", "day"], "sun", "主题");
  registerAction("themeSystem", () => t("cmdpal.actions.themeSystem"),
    () => { applyThemeInstant("system"); emit("kevrai:set-theme", { theme: "system" }); },
    ["system", "系统", "theme", "主题", "auto"], "monitor", "主题");
  registerAction("openDataDir", () => t("cmdpal.actions.openDataDir"),
    () => emit("kevrai:open-data"), ["data", "folder", "数据", "目录", "open"], "folder", "操作");
  registerAction("toggleSidebar", () => t("cmdpal.actions.toggleSidebar"),
    () => {
      const b = hasDocument ? document.getElementById("sidebar-toggle") : null;
      if (b) b.click();
    }, ["sidebar", "侧边栏", "collapse", "折叠", "menu"], "panel-left", "操作");
  actions.get("toggleSidebar").shortcut = "⌘B";
  registerAction("showShortcuts", () => "键盘快捷键",
    () => openHelp(), ["shortcuts", "hotkeys", "快捷键", "键盘", "help", "帮助"], "keyboard", "系统");
  actions.get("showShortcuts").shortcut = "⌘/";
}

// 分组展示顺序（空查询时按此顺序渲染）。
const GROUP_ORDER = ["导航", "操作", "主题", "系统"];

// ── DOM 构建（仅在浏览器环境执行）────────────────────────────────────────────
function buildOverlay() {
  if (!hasDocument) return null;
  if (overlay) return overlay;

  overlay = document.createElement("div");
  overlay.className = "cmdpal-overlay";
  overlay.setAttribute("role", "dialog");
  overlay.setAttribute("aria-modal", "true");
  overlay.setAttribute("aria-label", t("cmdpal.title"));
  overlay.hidden = true;

  const panel = document.createElement("div");
  panel.className = "cmdpal-panel";

  inputEl = document.createElement("input");
  inputEl.className = "cmdpal-input";
  inputEl.type = "text";
  inputEl.setAttribute("autocomplete", "off");
  inputEl.setAttribute("spellcheck", "false");
  inputEl.placeholder = t("cmdpal.placeholder");
  inputEl.addEventListener("input", () => renderList());
  inputEl.addEventListener("keydown", onInputKey);

  listEl = document.createElement("div");
  listEl.className = "cmdpal-list";
  listEl.setAttribute("role", "listbox");

  panel.appendChild(inputEl);
  panel.appendChild(listEl);
  overlay.appendChild(panel);

  overlay.addEventListener("mousedown", (e) => {
    if (e.target === overlay) closeCommandPalette();
  });

  document.body.appendChild(overlay);
  return overlay;
}

function currentActionList() {
  return Array.from(actions.values()).map((a) => ({
    id: a.id,
    label: typeof a.label === "function" ? a.label() : String(a.label),
    keywords: a.keywords,
    handler: a.handler,
    icon: a.icon || "",
    group: a.group || "",
    shortcut: a.shortcut || "",
  }));
}

// 渲染一条命令行（图标 + label + 快捷键/hint）。返回 row 元素。
function buildRow(a, idx) {
  const row = document.createElement("div");
  row.className = "cmdpal-item" + (idx === 0 ? " active" : "");
  row.setAttribute("role", "option");
  row.dataset.id = a.id;

  const iconWrap = document.createElement("span");
  iconWrap.className = "cmdpal-item-icon";
  iconWrap.innerHTML = iconSvg(a.icon);

  const label = document.createElement("span");
  label.className = "cmdpal-item-label";
  label.textContent = a.label;

  const hint = document.createElement("span");
  hint.className = "cmdpal-item-hint";
  // 有关联快捷键显示快捷键，否则回退到动作 id。
  hint.textContent = a.shortcut || a.id;

  row.appendChild(iconWrap);
  row.appendChild(label);
  row.appendChild(hint);
  row.addEventListener("mousedown", (e) => {
    e.preventDefault();
    runSelected(idx);
  });
  return row;
}

function renderGroupHeader(text) {
  const h = document.createElement("div");
  h.className = "cmdpal-group-header";
  h.textContent = text;
  return h;
}

function renderList() {
  if (!listEl) return;
  const q = inputEl ? inputEl.value : "";
  const filtered = filterActions(currentActionList(), q);
  selectedIdx = 0;
  listEl.replaceChildren();

  if (!filtered.length) {
    const empty = document.createElement("div");
    empty.className = "cmdpal-empty";
    const emoji = document.createElement("span");
    emoji.className = "cmdpal-empty-emoji";
    emoji.textContent = "🔍";
    const msg = document.createElement("span");
    msg.textContent = t("cmdpal.empty");
    empty.appendChild(emoji);
    empty.appendChild(msg);
    listEl.appendChild(empty);
    rendered = [];
    return;
  }

  // ordered[] 与 DOM 行顺序严格一致，键盘导航/执行都按它索引。
  const ordered = [];

  if (!q.trim()) {
    // 空查询：最近使用置顶，其余按 group 分组。
    const r = getRecent();
    const recent = filtered.filter((a) => r[a.id]).sort((a, b) => r[b.id] - r[a.id]);
    const rest = filtered.filter((a) => !r[a.id]);

    if (recent.length) {
      listEl.appendChild(renderGroupHeader("最近使用"));
      ordered.push(...recent);
    }
    for (const g of GROUP_ORDER) {
      const items = rest.filter((a) => (a.group || "操作") === g);
      if (!items.length) continue;
      listEl.appendChild(renderGroupHeader(g));
      ordered.push(...items);
    }
    const knownGroups = new Set([...GROUP_ORDER, ""]);
    const ungrouped = rest.filter((a) => !knownGroups.has(a.group));
    if (ungrouped.length) {
      listEl.appendChild(renderGroupHeader("其他"));
      ordered.push(...ungrouped);
    }
  } else {
    // 有查询：扁平列表（跨组结果不分组）。
    ordered.push(...filtered);
  }

  rendered = ordered;
  rendered.forEach((a, i) => listEl.appendChild(buildRow(a, i)));
}

function moveActive(delta) {
  if (!rendered.length || !listEl) return;
  selectedIdx = (selectedIdx + delta + rendered.length) % rendered.length;
  const rows = listEl.querySelectorAll(".cmdpal-item");
  rows.forEach((r, i) => r.classList.toggle("active", i === selectedIdx));
  const activeRow = rows[selectedIdx];
  if (activeRow && activeRow.scrollIntoView) {
    activeRow.scrollIntoView({ block: "nearest" });
  }
}

function runSelected(i) {
  const idx = (typeof i === "number") ? i : selectedIdx;
  const a = rendered[idx];
  if (!a) return;
  bumpRecent(a.id);
  closeCommandPalette();
  try { a.handler(a); } catch (_) {}
}

function onInputKey(e) {
  if (e.key === "ArrowDown") { e.preventDefault(); moveActive(1); }
  else if (e.key === "ArrowUp") { e.preventDefault(); moveActive(-1); }
  else if (e.key === "Enter") { e.preventDefault(); runSelected(); }
  else if (e.key === "Escape") { e.preventDefault(); closeCommandPalette(); }
}

// ── 公开打开/关闭 ────────────────────────────────────────────────────────────
export function openCommandPalette() {
  if (!hasDocument) return;
  buildOverlay();
  // 记录触发前的焦点元素，关闭时归还（可访问性）。
  if (hasDocument && document.activeElement && document.activeElement !== document.body) {
    returnFocusTo = document.activeElement;
  }
  overlay.hidden = false;
  if (inputEl) {
    inputEl.value = "";
    // 每次打开按当前语言刷新占位文案（防御初始化时序与语言切换）。
    inputEl.placeholder = t("cmdpal.placeholder");
  }
  renderList();
  if (inputEl) inputEl.focus();
}

export function closeCommandPalette() {
  if (!overlay) return;
  overlay.hidden = true;
  // 归还焦点到触发元素（仍在 DOM 中且可聚焦时）。
  const prev = returnFocusTo;
  returnFocusTo = null;
  if (prev && hasDocument && document.contains(prev) && typeof prev.focus === "function") {
    try { prev.focus(); } catch (_) {}
  }
}

export function isOpen() {
  return !!overlay && !overlay.hidden;
}

// ── Keyboard shortcuts cheat-sheet (Ctrl/⌘+/) ────────────────────────────────
// 与 initCommandPalette 中实际注册的快捷键严格一一对应，不列不存在的键位。
const SHORTCUTS = [
  { group: "全局", items: [
    ["Ctrl K", "打开 / 关闭命令面板"],
    ["Ctrl /", "打开本快捷键速查"],
    ["Ctrl ,", "打开设置"],
    ["Ctrl D", "打开下载面板"],
    ["Ctrl B", "收起 / 展开侧边栏"],
  ] },
  { group: "导航", items: [
    ["Ctrl 1 … 9", "切换到第 1–9 个页面"],
    ["Ctrl 0", "切换到最后一个页面"],
  ] },
  { group: "搜索", items: [
    ["/", "聚焦搜索框（输入框内除外）"],
    ["Ctrl F", "聚焦搜索框并全选内容"],
  ] },
  { group: "命令面板", items: [
    ["↑ ↓", "选择命令"],
    ["Enter", "执行选中命令"],
    ["Esc", "关闭命令面板"],
  ] },
  { group: "模态操作", items: [
    ["Esc", "关闭速查 / 设置 / 下载 / 更新 / 引导弹窗"],
    ["Esc", "在输入框中先退出输入焦点"],
  ] },
];

let helpOverlay = null;
let helpReturnTo = null;
function buildHelpOverlay() {
  if (helpOverlay) return helpOverlay;
  helpOverlay = document.createElement("div");
  helpOverlay.className = "cmdpal-overlay";
  helpOverlay.setAttribute("role", "dialog");
  helpOverlay.setAttribute("aria-modal", "true");
  helpOverlay.setAttribute("aria-label", "键盘快捷键");
  helpOverlay.hidden = true;

  const panel = document.createElement("div");
  panel.className = "cmdpal-panel cmdpal-help";
  panel.tabIndex = -1;

  const head = document.createElement("div");
  head.className = "cmdpal-help-head";
  const title = document.createElement("div");
  title.className = "cmdpal-help-title";
  title.textContent = "键盘快捷键";
  const closeBtn = document.createElement("button");
  closeBtn.className = "cmdpal-help-close";
  closeBtn.setAttribute("aria-label", "关闭快捷键速查");
  closeBtn.textContent = "×";
  closeBtn.addEventListener("click", closeHelp);
  head.appendChild(title);
  head.appendChild(closeBtn);
  panel.appendChild(head);

  for (const g of SHORTCUTS) {
    const gh = document.createElement("div");
    gh.className = "cmdpal-help-group";
    gh.textContent = g.group;
    panel.appendChild(gh);
    for (const [keys, desc] of g.items) {
      const row = document.createElement("div");
      row.className = "cmdpal-help-row";
      const d = document.createElement("span");
      d.className = "cmdpal-help-desc";
      d.textContent = desc;
      const kbd = document.createElement("kbd");
      kbd.textContent = keys;
      row.appendChild(d);
      row.appendChild(kbd);
      panel.appendChild(row);
    }
  }
  helpOverlay.appendChild(panel);
  helpOverlay.addEventListener("mousedown", (e) => {
    if (e.target === helpOverlay) closeHelp();
  });
  document.body.appendChild(helpOverlay);
  return helpOverlay;
}
function openHelp() {
  if (!hasDocument) return;
  buildHelpOverlay();
  if (hasDocument && document.activeElement && document.activeElement !== document.body) {
    helpReturnTo = document.activeElement;
  }
  helpOverlay.hidden = false;
  if (typeof helpOverlay.focus === "function") helpOverlay.focus();
}
function closeHelp() {
  if (!helpOverlay) return;
  helpOverlay.hidden = true;
  const prev = helpReturnTo;
  helpReturnTo = null;
  if (prev && hasDocument && document.contains(prev) && typeof prev.focus === "function") {
    try { prev.focus(); } catch (_) {}
  }
}
function gotoNth(n) {
  const tabs = document.querySelectorAll(".sidebar .pane-tab");
  const el = tabs[n];
  if (el) el.click();
}

// ── init：绑定全局快捷键 ─────────────────────────────────────────────────────
/** 判断事件目标是否为可输入控件（此时不应劫持单键 /）。 */
function isTypingTarget(el) {
  if (!el || !el.tagName) return false;
  const tag = el.tagName;
  if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return true;
  if (el.isContentEditable) return true;
  return false;
}

/** 聚焦 #search 搜索框；selectAll 时全选已有内容。 */
function focusSearch(selectAll) {
  const s = hasDocument ? document.getElementById("search") : null;
  if (!s) return;
  s.focus();
  if (selectAll && typeof s.select === "function") {
    try { s.select(); } catch (_) {}
  }
}

/** 找到当前可见的应用模态（设置/下载/更新/引导），按打开优先级返回第一个。 */
function firstVisibleModal() {
  if (!hasDocument) return null;
  const ids = ["onboarding-overlay", "settings-overlay", "download-overlay", "update-overlay"];
  for (const id of ids) {
    const el = document.getElementById(id);
    if (el && !el.hasAttribute("hidden")) return el;
  }
  return null;
}

/** 触发模态自带的关闭逻辑（点击其关闭按钮，保持各模块真实 close 行为）。 */
function closeModal(modal) {
  const btn = modal.querySelector(
    '[data-action="close-onboarding"], [data-action="close-settings"], [data-action="close-overlay"], [data-action="close-update"]'
  );
  if (btn && typeof btn.click === "function") {
    btn.click();
  } else {
    modal.setAttribute("hidden", "");
  }
}

export function initCommandPalette(container) {
  if (inited) return;
  inited = true;
  registerDefaults();
  if (!hasWindow || !hasDocument) return;
  buildOverlay();

  // Mod 组合快捷键（Ctrl/⌘）。不与正常打字冲突。
  window.addEventListener("keydown", (e) => {
    const mod = e.ctrlKey || e.metaKey;
    if (!mod) return;
    const k = e.key;
    if (k === "k" || k === "K") {
      e.preventDefault();
      if (isOpen()) closeCommandPalette();
      else openCommandPalette();
    } else if (k === "/") {
      e.preventDefault();
      if (isOpen()) closeCommandPalette();
      openHelp();
    } else if (k === ",") {
      e.preventDefault();
      emit("kevrai:open-settings");
    } else if (k === "d" || k === "D") {
      e.preventDefault();
      emit("kevrai:open-downloads");
    } else if (k === "b" || k === "B") {
      e.preventDefault();
      const b = document.getElementById("sidebar-toggle");
      if (b) b.click();
    } else if (k === "f" || k === "F") {
      // Ctrl+F：聚焦搜索框并全选（Electron 无内置 find 面板）。
      e.preventDefault();
      focusSearch(true);
    } else if (/^[0-9]$/.test(k)) {
      e.preventDefault();
      gotoNth(k === "0" ? 9 : Number(k) - 1);
    }
  });

  // 单键 /：Cherry Studio 风格聚焦搜索框。
  // 仅当焦点不在可输入控件时才拦截——在输入框内 / 应正常输入。
  window.addEventListener("keydown", (e) => {
    if (e.key !== "/" || e.ctrlKey || e.metaKey || e.altKey) return;
    if (isTypingTarget(e.target)) return;
    // 命令面板或速查开着时不劫持。
    if (isOpen() || (helpOverlay && !helpOverlay.hidden)) return;
    e.preventDefault();
    focusSearch(false);
  });

  // 统一 Esc：命令面板 → 速查 → 应用模态（输入框先 blur）。
  window.addEventListener("keydown", (e) => {
    if (e.key !== "Escape") return;
    if (isOpen()) { e.preventDefault(); closeCommandPalette(); return; }
    if (helpOverlay && !helpOverlay.hidden) { e.preventDefault(); closeHelp(); return; }
    const ty = e.target;
    const modal = firstVisibleModal();
    if (modal) {
      e.preventDefault();
      // 输入框中按 Esc：先退出输入焦点，再关模态。
      if (isTypingTarget(ty) && typeof ty.blur === "function") ty.blur();
      closeModal(modal);
    } else if (isTypingTarget(ty) && typeof ty.blur === "function") {
      ty.blur();
    }
  });
}

// Node 环境下也注册默认动作（便于单测 filterActions / registerAction）
registerDefaults();
