// renderer/modules/focus-return.js — 模态/浮层共享的焦点工具：
//
//   recordFocus()            记录当前触发元素，供关闭后归还
//   restoreFocus(saved)      把焦点还给记录的元素（若它已被移除则安全放弃）
//   trapFocus(root)          Tab 在 root 内循环；打开时聚焦首个可聚焦元素
//   registerEsc({...})       按优先级栈统一处理 Esc：最上层浮层先关
//
// 纯 DOM 逻辑，无业务依赖，可在 jsdom 下单测。
"use strict";

const hasDoc = typeof document !== "undefined";

// 可聚焦元素（与 settings.js 原 trapFocus 保持一致的选择器）。
const FOCUSABLE_SEL =
  'a[href], button:not([disabled]), textarea:not([disabled]), ' +
  'input:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])';

/**
 * 记录当前活动元素。返回该元素（或 null），稍后传给 restoreFocus。
 * body/html 没有归还意义，记录为 null。
 */
export function recordFocus() {
  if (!hasDoc) return null;
  const el = document.activeElement;
  if (!el || el === document.body || el === document.documentElement) return null;
  return el;
}

/**
 * 把焦点归还给 saved 元素。
 * 元素仍在 DOM 中时 focus()；已被移除则安静放弃（调用方通常落在 body 上）。
 * 返回 true 表示成功归还。
 */
export function restoreFocus(saved) {
  if (!saved) return false;
  // 元素可能在浮层打开期间被重渲染移除（如下载任务被 dismiss）。
  if (!saved.isConnected) return false;
  if (typeof saved.focus === "function") {
    saved.focus();
    return true;
  }
  return false;
}

/** 收集 root 内「可见」的可聚焦元素（hidden 祖先里的控件不进 Tab 序）。 */
export function getFocusables(root) {
  if (!root || !root.querySelectorAll) return [];
  return Array.from(root.querySelectorAll(FOCUSABLE_SEL))
    .filter((el) => !el.closest("[hidden]"));
}

/**
 * Tab 焦点陷阱：打开时聚焦首个可聚焦元素，之后 Tab/Shift+Tab 在首尾循环。
 * 返回 detach 函数（关闭浮层时可移除监听；不移除也无害，因为浮层 hidden
 * 后其内部控件不响应键盘）。
 */
export function trapFocus(root) {
  if (!root) return () => {};
  // 同一 root 重复打开时不重复绑监听器，只把焦点重新送回首元素。
  if (root._kovatrapDetach) {
    const again = getFocusables(root);
    if (again[0]) again[0].focus();
    return root._kovatrapDetach;
  }
  const list = getFocusables(root);
  if (list[0]) list[0].focus();
  const handler = (e) => {
    if (e.key !== "Tab") return;
    const f = getFocusables(root);
    if (!f.length) return;
    const first = f[0], last = f[f.length - 1];
    if (e.shiftKey && document.activeElement === first) {
      last.focus(); e.preventDefault();
    } else if (!e.shiftKey && document.activeElement === last) {
      first.focus(); e.preventDefault();
    }
  };
  root.addEventListener("keydown", handler);
  const detach = () => {
    root.removeEventListener("keydown", handler);
    delete root._kovatrapDetach;
  };
  root._kovatrapDetach = detach;
  return detach;
}

// ── Esc 优先级栈 ────────────────────────────────────────────────────────────
// 各浮层按 order 注册：order 大者在上。Esc 按下时，capture 阶段找到第一个
// 「当前可见」的浮层并关闭它；其它下层浮层不动。
//   editableFirst: true 时，若焦点落在该浮层内的 input/textarea/select，
//                  第一次 Esc 只 blur（表单编辑不丢失焦点），第二次才关闭。
const escStack = [];
let escWired = false;

function escDispatch(e) {
  if (e.key !== "Escape") return;
  for (const entry of escStack) {
    if (!entry.isOpen()) continue;
    // 表单类浮层：先让输入框失焦，不关闭。
    if (entry.editableFirst && entry.root) {
      const t = document.activeElement;
      if (t && /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName)
          && entry.root.contains(t)) {
        t.blur();
        e.preventDefault();
        return;
      }
    }
    e.preventDefault();
    e.stopPropagation();
    try { entry.close(); } catch (_) {}
    return; // 只关最上层
  }
}

/**
 * 注册一个 Esc 关闭入口。
 *   { order, root, isOpen, close, editableFirst? }
 * 返回取消注册函数。
 */
export function registerEsc({ order = 0, root = null, isOpen, close, editableFirst = false }) {
  const entry = { order, root, isOpen, close, editableFirst };
  escStack.push(entry);
  escStack.sort((a, b) => b.order - a.order);
  if (!escWired && hasDoc) {
    escWired = true;
    // capture：先于浮层内部 input 的 Esc 处理，保证「最上层先关」。
    document.addEventListener("keydown", escDispatch, true);
  }
  return () => {
    const i = escStack.indexOf(entry);
    if (i >= 0) escStack.splice(i, 1);
  };
}
