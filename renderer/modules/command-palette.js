"use strict";
/**
 * Kevrai Omni — 命令面板（Ctrl/Cmd+K）
 *
 * 一个轻量、零依赖的覆盖层面板：
 *   - Ctrl+K（macOS 为 Cmd+K）打开/关闭
 *   - 输入框过滤可跳转标签与常用动作
 *   - ↑/↓ 选择，Enter 执行，Esc 关闭，点击遮罩关闭
 *
 * 设计原则：
 *   - 不直接调用各模块的内部函数，而是复用已有的 `[data-tab]` /
 *     `[data-action]` 钩子——派发一次原生 click，既解耦又不会与
 *     app.js / update.js / settings.js 等的现有监听冲突。
 *   - 所有 DOM 由本模块自建并挂到 <body>，不改动 index.html 结构。
 *   - 严格遵守 CSP `script-src 'self'`：无内联事件、无 eval，列表项
 *     仅用静态中文标题拼 innerHTML，用户输入只写入 input.value。
 */

// 可跳转标签：[data-tab 值, 显示名]。与 index.html 的 .pane-tab 一一对应。
const TABS = [
  ["market",       "模型市场"],
  ["hardware",     "硬件推荐"],
  ["engines",      "AI 引擎"],
  ["mnn",          "MNN 引擎"],
  ["agent",        "AI Agent"],
  ["ltx",          "LTX-2.5 视频"],
  ["local",        "本地模型"],
  ["gguf",         "GGUF 仓库"],
  ["pending",      "待官方开源"],
  ["environments", "环境管理"],
];

// 常用动作：[data-action 值, 显示名, 备注]。均复用 index.html 已有按钮。
const ACTIONS = [
  ["refresh",        "刷新",        "重新加载全部数据"],
  ["detect-gpu",     "检测 GPU",    "探测本机显卡"],
  ["check-updates",  "检查更新",    "检查新版本并下载"],
  ["open-downloads", "打开下载",    "查看下载任务"],
  ["open-settings",  "打开设置",    "偏好与访问令牌"],
];

let overlay = null;
let input = null;
let list = null;
let items = [];          // 当前渲染的命令数组（与 DOM 顺序一致）
let activeIndex = -1;
let open = false;

function buildCommands() {
  const cmds = [];
  for (const [tab, label] of TABS) {
    cmds.push({ kind: "tab", target: tab, title: label, hint: "跳转标签" });
  }
  for (const [act, label, hint] of ACTIONS) {
    cmds.push({ kind: "action", target: act, title: label, hint });
  }
  return cmds;
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

function renderList(filter) {
  const q = (filter || "").trim().toLowerCase();
  const all = buildCommands();
  items = q
    ? all.filter((c) => c.title.toLowerCase().includes(q) || c.hint.toLowerCase().includes(q))
    : all;

  if (!items.length) {
    list.innerHTML = `<li class="cmdpal-empty" role="presentation">无匹配命令</li>`;
    activeIndex = -1;
    return;
  }

  list.innerHTML = items.map((c, i) => `
    <li class="cmdpal-item${i === activeIndex ? " active" : ""}" role="option"
        data-index="${i}" id="cmdpal-item-${i}" aria-selected="${i === activeIndex}">
      <span class="cmdpal-title">${escapeHtml(c.title)}</span>
      <span class="cmdpal-hint">${escapeHtml(c.hint)}</span>
    </li>`).join("");
}

function setActive(i) {
  if (!items.length) { activeIndex = -1; return; }
  activeIndex = Math.max(0, Math.min(items.length - 1, i));
  renderList(input.value);
  const el = list.querySelector(`#cmdpal-item-${activeIndex}`);
  if (el) el.scrollIntoView({ block: "nearest" });
}

function execute(cmd) {
  if (!cmd) return;
  let target = null;
  if (cmd.kind === "tab") {
    target = document.querySelector(`.pane-tab[data-tab="${cmd.target}"]`);
  } else {
    target = document.querySelector(`[data-action="${cmd.target}"]`);
  }
  closePalette();
  if (target) target.click();
}

function openPalette() {
  if (open) return;
  open = true;
  overlay.hidden = false;
  input.value = "";
  activeIndex = 0;
  renderList("");
  // 等一帧再聚焦，确保 overlay 已可见
  requestAnimationFrame(() => input.focus());
}

function closePalette() {
  if (!open) return;
  open = false;
  overlay.hidden = true;
  input.value = "";
  activeIndex = -1;
}

function togglePalette() {
  open ? closePalette() : openPalette();
}

export function initCommandPalette() {
  if (overlay) return; // 幂等

  overlay = document.createElement("div");
  overlay.className = "overlay cmdpal-overlay";
  overlay.id = "cmdpal-overlay";
  overlay.hidden = true;
  overlay.setAttribute("role", "dialog");
  overlay.setAttribute("aria-modal", "true");
  overlay.setAttribute("aria-label", "命令面板");
  overlay.innerHTML = `
    <div class="cmdpal-panel">
      <input id="cmdpal-input" type="search" autocomplete="off"
             placeholder="输入命令，或跳转到标签…" aria-label="命令面板搜索" />
      <ul id="cmdpal-list" role="listbox" aria-label="命令列表"></ul>
      <div class="cmdpal-foot">
        <span><kbd>↑↓</kbd> 选择</span>
        <span><kbd>↵</kbd> 执行</span>
        <span><kbd>esc</kbd> 关闭</span>
        <span class="cmdpal-kbd"><kbd>Ctrl</kbd><kbd>K</kbd></span>
      </div>
    </div>`;
  document.body.appendChild(overlay);

  input = overlay.querySelector("#cmdpal-input");
  list = overlay.querySelector("#cmdpal-list");

  // 全局快捷键：Ctrl+K / Cmd+K 切换
  document.addEventListener("keydown", (e) => {
    const mod = e.ctrlKey || e.metaKey;
    if (mod && e.key.toLowerCase() === "k") {
      e.preventDefault();
      togglePalette();
    }
  });

  // 输入过滤
  input.addEventListener("input", () => { activeIndex = items.length ? 0 : -1; renderList(input.value); });

  // 键盘导航
  input.addEventListener("keydown", (e) => {
    if (e.key === "ArrowDown") { e.preventDefault(); setActive(activeIndex + 1); }
    else if (e.key === "ArrowUp") { e.preventDefault(); setActive(activeIndex - 1); }
    else if (e.key === "Enter") { e.preventDefault(); execute(items[activeIndex]); }
    else if (e.key === "Escape") { e.preventDefault(); closePalette(); }
  });

  // 鼠标点击某项执行
  list.addEventListener("click", (e) => {
    const item = e.target.closest(".cmdpal-item");
    if (!item) return;
    execute(items[Number(item.dataset.index)]);
  });

  // 悬停高亮
  list.addEventListener("mousemove", (e) => {
    const item = e.target.closest(".cmdpal-item");
    if (!item) return;
    const i = Number(item.dataset.index);
    if (i !== activeIndex) setActive(i);
  });

  // 点击遮罩（面板外部）关闭
  overlay.addEventListener("mousedown", (e) => {
    if (e.target === overlay) closePalette();
  });
}
