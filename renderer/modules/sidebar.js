// renderer/modules/sidebar.js — 侧栏展开/折叠（v3.0.0）。
//
// 抽出成独立模块有两个原因：一是 app.js 是带副作用的启动文件，单元测试
// 无法直接 import；二是有段历史——JS 一直在切 `.sidebar-expanded`类名，
// 样式表里却没有对应规则，折叠按钮点了没反应。逻辑与样式必须一起被测，
// 所以这里导出纯函数，测试直接驱动它。
"use strict";

const SIDEBAR_KEY = "kevrai:sidebar-expanded";
const SIDEBAR_KEY_V2 = "kevrai:sidebar-expanded:v2";

/** 读取持久化的展开态；首次启动默认展开，localStorage 不可用时也展开。
 *
 * v2 key: the old build defaulted to the 64px rail, so legacy users have a
 * stored "0" they never explicitly chose. Ignoring that legacy value once is
 * safer than bringing the unlabeled icon rail back after this UI refresh.
 */
export function readSidebarExpanded() {
  try {
    const stored = localStorage.getItem(SIDEBAR_KEY_V2);
    if (stored !== null) return stored !== "0";
    return true;
  } catch (_) { return true; }
}

/** 把展开态应用到 DOM，并同步按钮文案与 aria-label。 */
export function applySidebarState({ appShell, sidebar, toggle, expanded, t = (k) => k }) {
  if (!appShell || !sidebar || !toggle) return false;
  appShell.classList.toggle("sidebar-expanded", expanded);
  sidebar.classList.toggle("expanded", expanded);
  toggle.textContent = expanded ? "‹" : "☰";
  toggle.setAttribute("aria-label", expanded ? t("app.collapseSidebar") : t("app.expandSidebar"));
  toggle.setAttribute("aria-expanded", String(expanded));
  return true;
}

/**
 * 绑定折叠按钮。返回 false 表示缺少必要 DOM，调用方可据此跳过。
 * 启动时先用持久化值渲染一次，避免刷新后状态跳回。
 */
export function initSidebarToggle(t) {
  const toggle = document.getElementById("sidebar-toggle");
  const appShell = document.querySelector(".app-shell");
  const sidebar = document.querySelector(".sidebar");
  if (!toggle || !appShell || !sidebar) return false;

  const els = { appShell, sidebar, toggle };
  applySidebarState({ ...els, expanded: readSidebarExpanded(), t });
  toggle.addEventListener("click", () => {
    const expanded = !appShell.classList.contains("sidebar-expanded");
    applySidebarState({ ...els, expanded, t });
    try {
      localStorage.setItem(SIDEBAR_KEY, expanded ? "1" : "0");
      localStorage.setItem(SIDEBAR_KEY_V2, expanded ? "1" : "0");
    } catch (_) {}
  });
  return true;
}
