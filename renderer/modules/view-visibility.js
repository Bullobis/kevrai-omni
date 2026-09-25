// renderer/modules/view-visibility.js — 统一的视图可见性工具（R4 资源泄漏审计）。
//
// renderer 用 `.pane.active` 标记当前可见视图，pane 元素 id 固定为
// `pane-<name>`（name 即 sidebar data-tab）。后台轮询型模块（LTX / MNN）
// 在自己的视图隐藏时不应继续打网络请求——否则用户停留在模型市场时，
// LTX 仍每秒轮询任务、MNN 仍每 1.5s 轮询下载状态，白白耗电耗 CPU。
//
// app.js `switchView()` 是唯一的视图切换入口，切换完成后派发：
//   window: CustomEvent("kevrai:view-change", { detail: { view } })
// 本模块把这件事收口成三个小工具，避免各模块各自去读 classList / 绑事件：
//
//   isViewVisible(name)                       同步判断 pane-<name> 是否带 .active
//   onViewChange(cb(view, evt))               订阅视图切换，返回 unsubscribe
//   onViewState(name, { onShow, onHide })     针对单个视图的显隐回调
//
// 注意：onHide 在「切到任意其它视图」时触发（不是只在切走本视图时触发一次），
// 因为切换入口只有一个，每一次切换都会派发一次事件。
"use strict";

const hasDoc = typeof document !== "undefined";
const hasWin = typeof window !== "undefined";

/** pane-<name> 当前是否可见（带 .active）。无 DOM 时一律返回 false。 */
export function isViewVisible(name) {
  if (!hasDoc || !name) return false;
  const pane = document.getElementById("pane-" + name);
  return !!pane && pane.classList.contains("active");
}

/**
 * 订阅视图切换。cb 收到新视图名（detail.view）。
 * 返回 unsubscribe；在非浏览器环境下返回一个 no-op。
 */
export function onViewChange(cb) {
  if (!hasWin || typeof cb !== "function") return () => {};
  const handler = (e) => {
    const view = e && e.detail ? e.detail.view : null;
    try { cb(view, e); } catch (_) { /* 单个模块的回调失败不应影响其它模块 */ }
  };
  window.addEventListener("kevrai:view-change", handler);
  return () => window.removeEventListener("kevrai:view-change", handler);
}

/**
 * 针对单个视图的显隐订阅：
 *   - 切到 name 视图时 onShow()
 *   - 切到任意其它视图时 onHide()
 * 初始加载时不回调（此时本视图默认隐藏，定时器本就不该跑）。
 */
export function onViewState(name, { onShow, onHide } = {}) {
  return onViewChange((view) => {
    if (view === name) {
      if (typeof onShow === "function") onShow();
    } else if (typeof onHide === "function") {
      onHide();
    }
  });
}
