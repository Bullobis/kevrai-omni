// renderer/modules/idle.js — defer non-critical startup work to idle time.
//
// requestIdleCallback lets us run low-priority initialization (onboarding guide,
// update-check wiring, secondary pane renders) in the gaps between frames
// instead of blocking the DOMContentLoaded → first-interactive path. When rIC
// is unavailable (Node, jsdom, older Chromium) we fall back to setTimeout(0),
// which still runs as soon as the current call stack unwinds — never sync.
"use strict";

function hasRic() {
  // Prefer the window-scoped rIC (Electron renderer / browsers). Also accept a
  // bare global (jsdom with pretendToBeVisual does not expose rIC).
  return (typeof window !== "undefined" && typeof window.requestIdleCallback === "function")
      || (typeof requestIdleCallback === "function");
}

function runRic(cb, timeout) {
  const ric = (typeof window !== "undefined" && typeof window.requestIdleCallback === "function")
    ? window.requestIdleCallback.bind(window)
    : requestIdleCallback.bind(globalThis);
  const cancel = (typeof window !== "undefined" && typeof window.cancelIdleCallback === "function")
    ? window.cancelIdleCallback.bind(window)
    : (typeof cancelIdleCallback === "function" ? cancelIdleCallback.bind(globalThis) : () => {});
  const id = ric(() => { try { cb(); } catch (_) { /* never break startup */ } }, { timeout });
  return () => { try { cancel(id); } catch (_) {} };
}

function runTimeout(cb) {
  const t = setTimeout(() => { try { cb(); } catch (_) {} }, 0);
  return () => { clearTimeout(t); };
}

/**
 * Run `cb` when the event loop is idle.
 *
 * @param {Function} cb               work to defer.
 * @param {object}   [opts]
 * @param {number}   [opts.timeout=2000]  force-run deadline for rIC (ms). The
 *                                   setTimeout fallback ignores this and runs
 *                                   on the next macrotask instead.
 * @returns {() => void} cancel — call to skip the deferred work.
 */
export function whenIdle(cb, opts = {}) {
  if (typeof cb !== "function") return () => {};
  const timeout = opts && Number.isFinite(opts.timeout) ? opts.timeout : 2000;
  return hasRic() ? runRic(cb, timeout) : runTimeout(cb);
}
