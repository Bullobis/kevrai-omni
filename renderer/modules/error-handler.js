// renderer/modules/error-handler.js — lightweight global error safety net.
//
// Why this exists:
//   Before R6 the renderer had NO window-level error / unhandledrejection
//   listeners. When a module failed to load (script/link onerror) or an
//   uncaught runtime error slipped past a module's own try/catch, the user
//   saw a blank or partially-dead UI with no explanation and no recovery.
//
// What it does (additive layer — it never wraps your code in try/catch):
//   * window "error"        (capture phase) → runtime errors AND resource load
//                           failures (script/link/img errors only reach window
//                           in the capture phase).
//   * window "unhandledrejection"          → rejected promises nobody caught.
//   * Every error is console.error'd (never swallowed, dev & prod alike).
//   * Users get RESTRAINED, generic prompts (no stack traces / file paths):
//       - runtime errors / stray rejections → a single toast
//       - script/module load failures (white-screen risk) → a fixed banner
//         with a "reload" button and a dismiss button (singleton element).
//   * 5s de-dupe window so one root cause does not fire toast + banner twice.
//
// safeImport(path, retries=1):
//   Optional wrapper around dynamic import(). Waits 500ms and retries once on
//   failure before surfacing the load-failure banner. Exported as a utility;
//   the project currently has no dynamic imports, so nothing calls it yet.
"use strict";

import { toast as realToast } from "./toast.js";

// Indirection seam: production uses the real toast(); tests can spy on the sink
// without pulling the whole toast DOM stack into the assertion.
let toastImpl = realToast;
export function __setToast(fn) { toastImpl = typeof fn === "function" ? fn : realToast; }

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------
const DUP_WINDOW_MS = 5000;     // same message suppressed from re-prompting
const SAFE_IMPORT_RETRY_DELAY_MS = 500;
const RUNTIME_TOAST_TTL = 6000;

// ---------------------------------------------------------------------------
// State (module-level; cache-busted per test file via ?x=... imports)
// ---------------------------------------------------------------------------
const seen = new Map();          // key(timestamped dedup) -> last prompt ts
let bannerEl = null;             // singleton #global-error-banner
let initialized = false;

// ---------------------------------------------------------------------------
// De-duplication: returns true when this key was already prompted within the
// window. Tracks the prompt time otherwise.
// ---------------------------------------------------------------------------
function isDup(key) {
  const now = Date.now();
  const last = seen.get(key);
  if (last !== undefined && now - last < DUP_WINDOW_MS) return true;
  seen.set(key, now);
  // Opportunistic eviction so the map cannot grow unbounded over a long session.
  if (seen.size > 64) {
    for (const [k, t] of seen) {
      if (now - t > DUP_WINDOW_MS) seen.delete(k);
    }
  }
  return false;
}

// ---------------------------------------------------------------------------
// Classification
// ---------------------------------------------------------------------------
function eventMessage(type, event) {
  if (type === "error") {
    if (event.message) return event.message;
    const t = event.target;
    if (t && typeof t.tagName === "string") return "resource:" + t.tagName.toLowerCase();
    if (event.error && event.error.message) return event.error.message;
    return "unknown error";
  }
  const r = event.reason;
  if (r == null) return "unhandled rejection";
  if (typeof r === "object" && r.message) return r.message;
  return String(r);
}

// A "fatal" error threatens the loaded page (script/module/stylesheet missing),
// so we escalate to the banner + reload instead of a tiny toast.
function isFatalLoad(event, type) {
  // Resource load failure in the capture phase: target is the failed element.
  const t = event.target;
  if (type === "error" && t && t !== window && typeof t.tagName === "string") {
    const tag = t.tagName.toUpperCase();
    if (tag === "SCRIPT" || tag === "LINK") return true;
  }
  const msg = (
    (type === "error" ? (event.message || "") : (event.reason && event.reason.message) || "")
    + " " + String(event.error && event.error.stack || "")
  ).toLowerCase();
  return /failed to fetch|dynamically imported module|importing a module|error loading|failed to resolve|loading chunk/i.test(msg);
}

// ---------------------------------------------------------------------------
// Banner (singleton, top-of-page fallback strip)
// ---------------------------------------------------------------------------
function bannerRoot() {
  const host = (typeof document !== "undefined" && document.body)
    || (typeof document !== "undefined" && document.documentElement);
  return host;
}

function showErrorBanner(userMsg) {
  if (bannerEl) {
    // Reuse the existing strip: refresh the text and make sure it is visible.
    bannerEl.classList.remove("gerr-out");
    const msgEl = bannerEl.querySelector(".gerr-msg");
    if (msgEl) msgEl.textContent = userMsg;
    return bannerEl;
  }
  const host = bannerRoot();
  if (!host) return null;

  bannerEl = document.createElement("div");
  bannerEl.id = "global-error-banner";
  bannerEl.className = "global-error-banner";
  bannerEl.setAttribute("role", "alert");

  const msg = document.createElement("span");
  msg.className = "gerr-msg";
  msg.textContent = userMsg;

  const actions = document.createElement("span");
  actions.className = "gerr-actions";

  const reloadBtn = document.createElement("button");
  reloadBtn.type = "button";
  reloadBtn.className = "secondary gerr-reload";
  reloadBtn.textContent = "重新加载";
  reloadBtn.addEventListener("click", () => {
    try { window.location.reload(); } catch (_) {}
  });

  const closeBtn = document.createElement("button");
  closeBtn.type = "button";
  closeBtn.className = "secondary gerr-close";
  closeBtn.setAttribute("aria-label", "关闭");
  closeBtn.textContent = "✕";
  closeBtn.addEventListener("click", dismissBanner);

  actions.appendChild(reloadBtn);
  actions.appendChild(closeBtn);
  bannerEl.appendChild(msg);
  bannerEl.appendChild(actions);
  host.appendChild(bannerEl);

  // Force a reflow so the slide-in transition runs on the next frame.
  void bannerEl.offsetHeight;
  bannerEl.classList.add("gerr-in");
  return bannerEl;
}

function dismissBanner() {
  if (!bannerEl) return;
  const el = bannerEl;
  bannerEl = null;             // allow a fresh banner for a later, different error
  el.classList.remove("gerr-in");
  el.classList.add("gerr-out");
  setTimeout(() => { try { el.remove(); } catch (_) {} }, 320);
}

// ---------------------------------------------------------------------------
// Event handlers
// ---------------------------------------------------------------------------
function onWindowError(event) {
  // Always log the full detail — never swallow the real error.
  console.error("[global-error] uncaught exception", {
    message: event.message,
    filename: event.filename,
    lineno: event.lineno,
    colno: event.colno,
    error: event.error,
  });

  const key = eventMessage("error", event);
  if (isDup(key)) return;

  if (isFatalLoad(event, "error")) {
    showErrorBanner("页面加载不完整，部分功能可能无法使用。可尝试重新加载。");
  } else {
    toastImpl("发生了一个错误，部分功能可能受影响", { kind: "err", ttl: RUNTIME_TOAST_TTL });
  }
}

function onUnhandledRejection(event) {
  console.error("[global-error] unhandled rejection", { reason: event.reason });

  const key = eventMessage("rejection", event);
  if (isDup(key)) return;

  if (isFatalLoad(event, "rejection")) {
    showErrorBanner("模块加载失败，部分功能可能无法使用。可尝试重新加载。");
  } else {
    toastImpl("发生了一个错误，部分功能可能受影响", { kind: "err", ttl: RUNTIME_TOAST_TTL });
  }
}

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------
/**
 * initErrorHandler() -> teardown()
 * Registers the window error + unhandledrejection listeners. Idempotent:
 * calling twice is a no-op. Call this as the FIRST step of bootstrap so that
 * any later module-initialization error is already covered.
 */
export function initErrorHandler() {
  if (initialized) return () => {};
  initialized = true;
  // capture:true is essential: resource load failures (script/link/img) do NOT
  // bubble, they only reach window in the capture phase.
  window.addEventListener("error", onWindowError, true);
  window.addEventListener("unhandledrejection", onUnhandledRejection);
  return () => {
    window.removeEventListener("error", onWindowError, true);
    window.removeEventListener("unhandledrejection", onUnhandledRejection);
    initialized = false;
  };
}

/**
 * safeImport(path, retries = 1) -> module namespace
 * Dynamic import with one automatic retry (after 500ms) for transient load
 * failures. On final failure it surfaces the fallback banner and rethrows so
 * callers can still handle it.
 */
export async function safeImport(path, retries = 1) {
  let lastErr = null;
  for (let attempt = 0; attempt <= retries; attempt++) {
    try {
      return await import(path);
    } catch (e) {
      lastErr = e;
      if (attempt < retries) {
        await new Promise((r) => setTimeout(r, SAFE_IMPORT_RETRY_DELAY_MS));
      }
    }
  }
  showErrorBanner("模块加载失败，部分功能可能无法使用。可尝试重新加载。");
  throw lastErr;
}
