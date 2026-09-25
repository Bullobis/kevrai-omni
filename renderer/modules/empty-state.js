// renderer/modules/empty-state.js — 统一空状态组件（Unified Empty States）。
//
// 对标 Cherry Studio：每个可空视图在无数据时不再是一片空白或一行灰字，
// 而是一个居中、带 lucide 风格图标、标题 + 辅助说明 + 可选主操作按钮的
// 空状态。颜色全部走 CSS 变量（--mut / --fg / --acc），深色与浅色主题
// 自动协调。
//
// 用法：
//   import { createEmptyState } from "./empty-state.js";
//   host.replaceChildren(createEmptyState({
//     icon: "inbox",
//     title: "还没有本地模型",
//     hint: "从硬盘导入 GGUF / safetensors，或把文件拖进窗口。",
//     actionLabel: "选择文件",
//   }));
//   host.querySelector(".es-action")?.addEventListener("click", ...);
"use strict";

// lucide 风格图标路径（stroke=currentColor，与现有 .ic 图标一致）。
// viewBox="0 0 24 24"，只放 <path>/<circle>/<rect> 等几何片段。
const ICON_PATHS = {
  inbox: '<path d="M22 12h-6l-2 3h-4l-2-3H2"/><path d="M5.45 5.11 2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.45-6.89A2 2 0 0 0 16.76 4H7.24a2 2 0 0 0-1.79 1.11z"/>',
  search: '<circle cx="11" cy="11" r="8"/><path d="m21 21-4.3-4.3"/>',
  hardDrive: '<line x1="22" x2="2" y1="12" y2="12"/><path d="M5.45 5.11 2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.45-6.89A2 2 0 0 0 16.76 4H7.24a2 2 0 0 0-1.79 1.11z"/><line x1="6" x2="6.01" y1="16" y2="16"/><line x1="10" x2="10.01" y1="16" y2="16"/>',
  clock: '<circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/>',
  cpu: '<rect width="16" height="16" x="4" y="4" rx="2"/><rect width="6" height="6" x="9" y="9" rx="1"/><path d="M15 2v2"/><path d="M15 20v2"/><path d="M2 15h2"/><path d="M2 9h2"/><path d="M20 15h2"/><path d="M20 9h2"/><path d="M9 2v2"/><path d="M9 20v2"/>',
  mousePointer: '<path d="m9 9 5 12 1.8-5.2L21 14Z"/><path d="M7.2 2.2 8 5.1"/><path d="M5.1 8l-2.9-.8"/><path d="M14 4.1 12 6"/><path d="M6 12l-1.9 2"/>',
  image: '<rect width="18" height="18" x="3" y="3" rx="2" ry="2"/><circle cx="9" cy="9" r="2"/><path d="m21 15-3.086-3.086a2 2 0 0 0-2.828 0L6 21"/>',
  film: '<rect width="18" height="18" x="3" y="3" rx="2"/><path d="M7 3v18"/><path d="M3 7.5h4"/><path d="M3 12h18"/><path d="M3 16.5h4"/><path d="M17 3v18"/><path d="M17 7.5h4"/><path d="M17 16.5h4"/>',
  packageOpen: '<path d="M12 22v-9"/><path d="M15.17 2.21a2 2 0 0 1 1.66.38l3.96 2.84a2 2 0 0 1 .72 2.18l-2.9 10.12a2 2 0 0 1-1.31 1.41L7 22"/><path d="m7 22-1.76-6.13a2 2 0 0 1 .71-2.17l10.05-7.76"/>',
  layers: '<path d="m12.83 2.18a2 2 0 0 0-1.66 0L2.6 6.08a1 1 0 0 0 0 1.83l8.58 3.91a2 2 0 0 0 1.66 0l8.58-3.9a1 1 0 0 0 0-1.83Z"/><path d="m22 17.65-9.17 4.16a2 2 0 0 1-1.66 0L2 17.65"/><path d="m22 12.65-9.17 4.16a2 2 0 0 1-1.66 0L2 12.65"/>',
  circleDot: '<circle cx="12" cy="12" r="10"/><circle cx="12" cy="12" r="1"/>',
};

// 返回某个图标名的内联 SVG 字符串（供静态 HTML / innerHTML 使用）。
// 未知图标名回退到 inbox，保证不会渲染出空图标。
export function emptyStateIconSvg(name) {
  const inner = ICON_PATHS[name] || ICON_PATHS.inbox;
  return `<svg class="ic empty-state-icon" viewBox="0 0 24 24" fill="none" `
    + `stroke="currentColor" stroke-width="2" stroke-linecap="round" `
    + `stroke-linejoin="round" aria-hidden="true">${inner}</svg>`;
}

/**
 * 创建一个统一空状态 DOM 元素。
 *
 * @param {object}   opts
 * @param {string}   [opts.icon="inbox"]  图标名（见 ICON_PATHS）。
 * @param {string}   opts.title            一行标题。
 * @param {string}   [opts.hint=""]        一行辅助说明（可选）。
 * @param {string}   [opts.actionLabel=""] 主操作按钮文字（可选，留空则不渲染按钮）。
 * @param {string}   [opts.actionClass="primary small"] 按钮 class。
 * @returns {HTMLElement} 一个 .empty-state 容器；主操作按钮在 .es-action 上。
 */
export function createEmptyState({
  icon = "inbox",
  title = "",
  hint = "",
  actionLabel = "",
  actionClass = "primary small",
} = {}) {
  const wrap = document.createElement("div");
  wrap.className = "empty-state";
  wrap.setAttribute("role", "status");

  const holder = document.createElement("div");
  holder.className = "empty-state-iconwrap";
  holder.innerHTML = emptyStateIconSvg(icon);
  wrap.appendChild(holder);

  if (title) {
    const h = document.createElement("h3");
    h.className = "empty-state-title";
    h.textContent = title;
    wrap.appendChild(h);
  }
  if (hint) {
    const p = document.createElement("p");
    p.className = "empty-state-hint";
    p.textContent = hint;
    wrap.appendChild(p);
  }
  if (actionLabel) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = actionClass + " es-action";
    btn.textContent = actionLabel;
    wrap.appendChild(btn);
  }
  return wrap;
}
