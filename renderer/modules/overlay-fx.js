// renderer/modules/overlay-fx.js — 统一的对话框浮层（.overlay）打开/关闭过渡。
//
// 设计目标：
//   · 背景遮罩淡入淡出（opacity，150ms）
//   · 面板从下方轻微上移 + 淡入（translateY(8px)→0 + opacity，200ms）
//   · 全部走 transform/opacity（合成层，不触发 layout/paint）
//   · 关闭时先播淡出，再 set hidden —— 焦点归还由调用方**立即**执行，
//     只延迟 display:none 的替换，因此不会因过渡而丢焦点。
//   · prefers-reduced-motion 下立即切换，不播动画。
//
// 用法：
//   import { overlayOpen, overlayClose } from "./overlay-fx.js";
//   overlayOpen(el);                 // 替代 el.removeAttribute("hidden")
//   overlayClose(el, { done });      // 替代 el.setAttribute("hidden","")
"use strict";

// 关闭过渡总时长（略大于背景 150ms，给面板淡出留尾）。
const CLOSE_MS = 180;

function prefersReducedMotion() {
  return typeof window !== "undefined" &&
    typeof window.matchMedia === "function" &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

// 模态打开计数：嵌套打开时只有最外层关闭才解除 body 滚动锁定。
let openCount = 0;

/**
 * 打开一个 .overlay 浮层：从 opacity:0 / translateY(8px) 过渡到可见。
 * 同步强制 reflow 让「起始态」先落绘，再加 .is-open 触发过渡，
 * 避免一帧白屏闪烁（不依赖 rAF 时序，jsdom 下也可单测）。
 */
export function overlayOpen(el) {
  if (!el) return;
  const wasHidden = el.hasAttribute("hidden");
  // 打断进行中的关闭：若 180ms 内重新打开，取消挂起的 finish，避免把
  // 新打开的浮层误设为 hidden。
  if (el._kovCloseTimer) {
    clearTimeout(el._kovCloseTimer);
    el._kovCloseTimer = null;
  }
  el.classList.remove("is-closing");
  el.classList.add("overlay-anim");   // 起始态：opacity 0 / card 下移
  el.removeAttribute("hidden");
  el.setAttribute("aria-hidden", "false");
  // 强制样式重算，确保起始态已提交，再加 open 类触发过渡。
  void el.offsetHeight;
  el.classList.add("is-open");
  // 仅在原本隐藏时才计入「打开数」——关闭途中重开不重复计数。
  if (wasHidden) {
    openCount += 1;
    document.body.classList.add("modal-open");
  }
}

/**
 * 关闭浮层：播放淡出（.is-closing），过渡结束后再 set hidden。
 * 调用方应在调用本函数前后**立即** restoreFocus()；这里只负责延迟
 * display:none 与清理滚动锁。幂等：重复调用不会叠加定时器。
 *
 * @param {HTMLElement} el   浮层根节点
 * @param {{done?: function}} opts done 在 hidden 真正设置后回调
 */
export function overlayClose(el, { done } = {}) {
  if (!el) return;
  if (el.classList.contains("is-closing")) {
    // 正在关闭中：不再重复播动画，直接补一次 done 调用即可。
    if (done) done();
    return;
  }
  el.classList.remove("is-open");
  el.classList.add("is-closing");
  el.setAttribute("aria-hidden", "true");

  const onEnd = (e) => {
    // 只认浮层根自身的 opacity 过渡，忽略面板/子元素冒泡上来的事件。
    if (e.target !== el) return;
    clearTimeout(el._kovCloseTimer);
    el._kovCloseTimer = null;
    finish();
  };
  el._kovCloseHandler = onEnd;

  const finish = () => {
    if (el._kovCloseTimer) { clearTimeout(el._kovCloseTimer); el._kovCloseTimer = null; }
    if (el._kovCloseHandler) { el.removeEventListener("transitionend", el._kovCloseHandler); el._kovCloseHandler = null; }
    el.setAttribute("hidden", "");
    el.classList.remove("is-closing", "overlay-anim");
    openCount = Math.max(0, openCount - 1);
    if (openCount === 0) document.body.classList.remove("modal-open");
    if (done) done();
  };

  if (prefersReducedMotion()) { finish(); return; }

  const timer = setTimeout(finish, CLOSE_MS);
  el._kovCloseTimer = timer;
  el.addEventListener("transitionend", onEnd);
}
