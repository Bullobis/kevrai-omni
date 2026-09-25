// renderer/modules/toast.js — top-right toast stack, max 4, auto-dismiss.
//
// Enhancements (backward compatible):
//   * inline lucide-style SVG icon per kind (ok / warn / err / info)
//   * { title, msg } layout: bold title above body text; plain toast("text") still works
//   * thin bottom progress bar that shrinks with ttl (hidden for sticky ttl=0)
//   * hover pauses the auto-dismiss timer AND the progress animation
//   * right-side slide-in / slide-out, smoother easing
//
// Signature is unchanged: toast(msg, options) -> close().
//   options.kind: "" | "ok" | "warn" | "err" | "info"   ("error" aliased to "err")
//   options.ttl:  ms (default 4000, 0 = sticky, no progress bar)
//   options.title: optional bold heading line above the body text
//   options.msg:   optional body text when called as toast(null, { title, msg })
"use strict";

const STACK = [];
const MAX = 4;
let hostEl = null;

// lucide-style icons (24x24, stroke=currentColor).
const ICONS = {
  ok:   '<polyline points="20 6 9 17 4 12"/>',
  warn: '<path d="M10.29 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/>'
      + '<line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/>',
  err:  '<line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>',
  info: '<circle cx="12" cy="12" r="10"/><line x1="12" y1="16" x2="12" y2="12"/><line x1="12" y1="8" x2="12.01" y2="8"/>',
};

// Normalize caller kind. "error" (used by ltx.js historically) aliases to "err".
function normalizeKind(kind) {
  const k = String(kind || "");
  if (k === "error") return "err";
  if (k === "ok" || k === "warn" || k === "err" || k === "info") return k;
  return "";
}

function iconSvg(kind) {
  const inner = ICONS[kind];
  if (!inner) return "";
  return `<svg class="toast-icon" viewBox="0 0 24 24" fill="none" `
    + `stroke="currentColor" stroke-width="2" stroke-linecap="round" `
    + `stroke-linejoin="round" aria-hidden="true">${inner}</svg>`;
}

function ensureHost() {
  if (hostEl) return hostEl;
  hostEl = document.createElement("div");
  hostEl.className = "toast-stack";
  hostEl.setAttribute("role", "status");
  hostEl.setAttribute("aria-live", "polite");
  hostEl.setAttribute("aria-atomic", "false");
  document.body.appendChild(hostEl);
  return hostEl;
}

function renderStack() {
  const root = ensureHost();
  // remove & append fresh (so fade-out animation can run on dismiss)
  root.replaceChildren(...STACK.map((t) => t.el));
}

/**
 * toast(msg, options?) -> close()
 *   toast("plain text")
 *   toast("body", { kind: "ok", title: "Saved" })
 *   toast(null, { title: "Headline", msg: "body", ttl: 0 })
 */
export function toast(msg, options = {}) {
  const opts = options || {};
  const kind = normalizeKind(opts.kind);
  const ttl = Number.isFinite(opts.ttl) ? opts.ttl : 4000;
  const title = opts.title ? String(opts.title) : "";
  // Body text: positional msg wins (legacy callers), else options.msg.
  const hasPositional = msg !== undefined && msg !== null && msg !== "";
  const body = String(hasPositional ? msg : (opts.msg || ""));

  ensureHost();
  const el = document.createElement("div");
  el.className = `toast-item${kind ? " " + kind : ""}`;
  el.setAttribute("role", kind === "err" ? "alert" : "status");

  // Icon (when the kind has one) + text content.
  const icon = ICONS[kind] ? iconSvg(kind) : "";
  const titleHtml = title ? `<div class="toast-title">${title}</div>` : "";
  const bodyHtml = body ? `<div class="toast-msg"></div>` : "";
  el.innerHTML = icon
    + `<div class="toast-content">${titleHtml}${bodyHtml}</div>`;
  if (body) el.querySelector(".toast-msg").textContent = body;

  // Progress bar (only for auto-dismissing toasts).
  let progressEl = null;
  if (ttl > 0) {
    progressEl = document.createElement("div");
    progressEl.className = "toast-progress";
    const bar = document.createElement("i");
    bar.style.animationDuration = `${ttl}ms`;
    progressEl.appendChild(bar);
    el.appendChild(progressEl);
  }

  const entry = { kind, ttl, el, progressEl, closed: false, timer: null, endsAt: 0, pausedAt: 0 };
  STACK.push(entry);
  while (STACK.length > MAX) {
    const oldest = STACK.shift();
    dismissEl(oldest);
  }
  renderStack();

  const close = () => {
    if (entry.closed) return;
    entry.closed = true;
    if (entry.timer) { clearTimeout(entry.timer); entry.timer = null; }
    const idx = STACK.indexOf(entry);
    if (idx >= 0) STACK.splice(idx, 1);
    el.classList.add("toast-leave");
    setTimeout(() => { el.remove(); renderStack(); }, 240);
  };

  // Auto-dismiss timer with hover pause support.
  const arm = () => {
    if (entry.closed || entry.ttl <= 0) return;
    entry.endsAt = Date.now() + entry.remaining;
    entry.timer = setTimeout(() => { entry.timer = null; close(); }, entry.remaining);
  };
  entry.remaining = ttl;
  const pause = () => {
    if (entry.closed || entry.ttl <= 0 || !entry.timer) return;
    clearTimeout(entry.timer);
    entry.timer = null;
    entry.pausedAt = Date.now();
    el.classList.add("paused");
  };
  const resume = () => {
    if (entry.closed || entry.ttl <= 0 || entry.timer) return;
    entry.remaining = Math.max(0, entry.endsAt - entry.pausedAt);
    el.classList.remove("paused");
    if (entry.remaining > 0) arm();
  };
  el.addEventListener("mouseenter", pause);
  el.addEventListener("mouseleave", resume);
  el.addEventListener("click", close);

  if (ttl > 0) arm();

  return close;
}

// An entry being evicted by the MAX cap: slide it out and drop it.
function dismissEl(entry) {
  if (entry.timer) { clearTimeout(entry.timer); entry.timer = null; }
  entry.closed = true;
  entry.el.classList.add("toast-leave");
  setTimeout(() => { entry.el.remove(); renderStack(); }, 240);
}
