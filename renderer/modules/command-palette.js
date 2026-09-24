// renderer/modules/command-palette.js — 命令面板（Ctrl/⌘+K）。
//
// 零依赖、纯 ES module、可独立 import。
//   - openCommandPalette()   手动唤起
//   - registerAction(id, label, handler, keywords?)  注册/覆盖动作
//   - initCommandPalette(container?)  绑定全局快捷键并挂载 overlay
//
// 与 app.js 解耦：内置动作默认通过 window.dispatchEvent(CustomEvent) 发事件，
// 不直接调用 app.js 内部函数。app.js 也可用 registerAction(id, ...) 覆盖 handler
// 接入真实业务。
"use strict";

import { t, getLocale, setLocale } from "./i18n.js";

const hasDocument = typeof document !== "undefined";
const hasWindow = typeof window !== "undefined";

/** 动作表：id -> { id, label, keywords:[], handler } */
const actions = new Map();
let overlay = null;
let inputEl = null;
let listEl = null;
let selectedIdx = 0;
let rendered = []; // 当前过滤后的动作列表
let inited = false;

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

// ── 动作注册 ──────────────────────────────────────────────────────────────────
export function registerAction(id, label, handler, keywords) {
  if (!id || typeof handler !== "function") return;
  actions.set(id, {
    id,
    label: typeof label === "function" ? label : () => String(label ?? id),
    keywords: Array.isArray(keywords) ? keywords : keywords ? [keywords] : [],
    handler,
  });
}

// ── 默认动作（事件解耦）───────────────────────────────────────────────────────
function emit(name, detail) {
  if (!hasWindow) return;
  window.dispatchEvent(new CustomEvent(name, { detail: detail || {} }));
}

function registerDefaults() {
  // 切 pane：dispatch kevrai:navigate { tab }
  const panes = [
    ["goMarket", "cmdpal.actions.goMarket", "market", ["models", "market", "模型", "模型市场", "home"]],
    ["goHardware", "cmdpal.actions.goHardware", "hardware", ["hardware", "gpu", "硬件", "推荐"]],
    ["goEngines", "cmdpal.actions.goEngines", "engines", ["engines", "engine", "引擎", "llama.cpp"]],
    ["goMnn", "cmdpal.actions.goMnn", "mnn", ["mnn", "引擎"]],
    ["goAgent", "cmdpal.actions.goAgent", "agent", ["agent", "assistant", "智能体", "助手"]],
    ["goLtx", "cmdpal.actions.goLtx", "ltx", ["ltx", "video", "视频", "短剧"]],
    ["goLocal", "cmdpal.actions.goLocal", "local", ["local", "本地", "import", "导入"]],
    ["goGguf", "cmdpal.actions.goGguf", "gguf", ["gguf", "仓库"]],
    ["goPending", "cmdpal.actions.goPending", "pending", ["pending", "待开源", "wait"]],
    ["goEnvironments", "cmdpal.actions.goEnvironments", "environments", ["environments", "env", "环境"]],
  ];
  for (const [id, labelKey, tab, kws] of panes) {
    registerAction(id, () => t(labelKey), () => emit("kevrai:navigate", { tab }), kws);
  }
  registerAction("openSettings", () => t("cmdpal.actions.openSettings"),
    () => emit("kevrai:open-settings"), ["settings", "设置", "preferences", "偏好"]);
  registerAction("openDownloads", () => t("cmdpal.actions.openDownloads"),
    () => emit("kevrai:open-downloads"), ["downloads", "下载", "tasks", "任务"]);
  registerAction("detectGpu", () => t("cmdpal.actions.detectGpu"),
    () => emit("kevrai:detect-gpu"), ["gpu", "硬件", "detect", "检测"]);
  registerAction("refreshModels", () => t("cmdpal.actions.refreshModels"),
    () => emit("kevrai:refresh"), ["refresh", "reload", "刷新", "重载", "models"]);
  registerAction("checkUpdates", () => t("cmdpal.actions.checkUpdates"),
    () => emit("kevrai:check-updates"), ["update", "更新", "upgrade", "升级"]);
  registerAction("switchLocale", () => t("cmdpal.actions.switchLocale"),
    async () => {
      const next = getLocale() === "zh-CN" ? "en-US" : "zh-CN";
      await setLocale(next);
      renderList();
    }, ["locale", "language", "i18n", "语言", "en", "zh", "中文", "english"]);
}

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
  }));
}

function renderList() {
  if (!listEl) return;
  const q = inputEl ? inputEl.value : "";
  rendered = filterActions(currentActionList(), q);
  selectedIdx = 0;
  listEl.replaceChildren();
  if (!rendered.length) {
    const empty = document.createElement("div");
    empty.className = "cmdpal-empty";
    empty.textContent = t("cmdpal.empty");
    listEl.appendChild(empty);
    return;
  }
  rendered.forEach((a, i) => {
    const row = document.createElement("div");
    row.className = "cmdpal-item" + (i === 0 ? " active" : "");
    row.setAttribute("role", "option");
    row.dataset.id = a.id;
    const label = document.createElement("span");
    label.className = "cmdpal-item-label";
    label.textContent = a.label;
    const hint = document.createElement("span");
    hint.className = "cmdpal-item-hint";
    hint.textContent = a.id;
    row.appendChild(label);
    row.appendChild(hint);
    row.addEventListener("mousedown", (e) => {
      e.preventDefault();
      runSelected(i);
    });
    listEl.appendChild(row);
  });
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
}

export function isOpen() {
  return !!overlay && !overlay.hidden;
}

// ── init：绑定全局快捷键 ─────────────────────────────────────────────────────
export function initCommandPalette(container) {
  if (inited) return;
  inited = true;
  registerDefaults();
  if (!hasWindow || !hasDocument) return;
  buildOverlay();
  // Ctrl+K / Meta+K 唤起；再按一次关闭
  window.addEventListener("keydown", (e) => {
    const mod = e.ctrlKey || e.metaKey;
    if (mod && (e.key === "k" || e.key === "K")) {
      e.preventDefault();
      if (isOpen()) closeCommandPalette();
      else openCommandPalette();
    }
  });
}

// Node 环境下也注册默认动作（便于单测 filterActions / registerAction）
registerDefaults();
