// renderer/modules/toast.js — top-right toast stack, max 4, auto-dismiss 4s.
"use strict";

const STACK = [];
const MAX = 4;
let hostEl = null;

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
 * toast(msg, options?)
 *   options.kind: "" | "ok" | "warn" | "err"
 *   options.ttl: ms (default 4000, 0 = sticky)
 */
export function toast(msg, options = {}) {
  const { kind = "", ttl = 4000 } = options || {};
  ensureHost();
  const el = document.createElement("div");
  el.className = `toast-item ${kind}`;
  el.setAttribute("role", kind === "err" ? "alert" : "status");
  el.textContent = String(msg);

  const entry = { msg, kind, ttl, el, closed: false };
  STACK.push(entry);
  while (STACK.length > MAX) {
    const oldest = STACK.shift();
    oldest.el.classList.add("toast-leave");
    setTimeout(() => oldest.el.remove(), 240);
  }
  renderStack();

  const close = () => {
    if (entry.closed) return;
    entry.closed = true;
    const idx = STACK.indexOf(entry);
    if (idx >= 0) STACK.splice(idx, 1);
    el.classList.add("toast-leave");
    setTimeout(() => { el.remove(); renderStack(); }, 240);
  };

  if (ttl > 0) setTimeout(close, ttl);

  el.addEventListener("click", close);
  return close;
}
