// renderer/modules/appearance.js — 外观个性化：强调色 + 界面密度。
//
// 强调色与密度都通过 <html> 上的 data-* attribute 表达，具体颜色/几何在
// styles/appearance.css 中按主题覆盖；本模块只负责应用持久化偏好、同步控件
// active 态，并在密度变化时触发 resize 让虚拟网格重算几何。
"use strict";

import {
  tokenEstimateEnabled, setTokenEstimateEnabled,
} from "./token-estimate.js";

const ACCENT_KEY = "kevrai:accent";
const DENSITY_KEY = "kevrai:density";
const SCALE_KEY = "kevrai:ui-scale";
export const ACCENTS = ["green", "blue", "violet", "orange", "pink", "cyan"];
export const DENSITIES = ["comfortable", "compact"];
// Whole-window zoom for large displays. 100% is the default; the list is
// deliberately short — five discrete steps are easier to reason about (and to
// hit with a mouse) than a free slider.
export const UI_SCALES = [0.9, 1, 1.1, 1.25, 1.5];

export function isValidAccent(id) { return ACCENTS.includes(id); }
export function isValidDensity(d) { return DENSITIES.includes(d); }
export function isValidScale(s) {
  const n = Number(s);
  return Number.isFinite(n) && UI_SCALES.includes(n);
}

function currentAccent() {
  return document.documentElement.getAttribute("data-accent") || "green";
}
function currentDensity() {
  return document.documentElement.getAttribute("data-density") || "comfortable";
}
function currentScale() {
  const raw = document.documentElement.getAttribute("data-ui-scale");
  const n = Number(raw);
  return Number.isFinite(n) && n > 0 ? n : 1;
}

// Apply the zoom through the preload bridge. Guarded because unit tests run
// the renderer without window.kevrai, and a browser-only dev session may too.
function applyScale(scale) {
  const api = globalThis.window && globalThis.window.kevrai;
  if (api && typeof api.setUiScale === "function") {
    try {
      Promise.resolve(api.setUiScale(scale)).catch(() => {});
    } catch (_) { /* bridge absent or closing */ }
  }
}

function syncSwatchUI() {
  const host = document.getElementById("accent-swatches");
  if (!host) return;
  const acc = currentAccent();
  host.querySelectorAll("[data-accent-choice]").forEach((b) => {
    const on = b.dataset.accentChoice === acc;
    b.classList.toggle("is-active", on);
    b.setAttribute("aria-checked", on ? "true" : "false");
  });
}

function syncDensityUI() {
  const host = document.getElementById("density-toggle");
  if (!host) return;
  const den = currentDensity();
  host.querySelectorAll("[data-density-choice]").forEach((b) => {
    const on = b.dataset.densityChoice === den;
    b.classList.toggle("is-active", on);
    b.setAttribute("aria-checked", on ? "true" : "false");
  });
}

function syncScaleUI() {
  const host = document.getElementById("scale-toggle");
  if (!host) return;
  const scale = currentScale();
  host.querySelectorAll("[data-scale-choice]").forEach((b) => {
    const on = Number(b.dataset.scaleChoice) === scale;
    b.classList.toggle("is-active", on);
    b.setAttribute("aria-checked", on ? "true" : "false");
  });
}

export function setAccent(id) {
  if (!isValidAccent(id)) return;
  document.documentElement.setAttribute("data-accent", id);
  try { localStorage.setItem(ACCENT_KEY, id); } catch (_) {}
  syncSwatchUI();
}

export function setDensity(d) {
  if (!isValidDensity(d)) return;
  document.documentElement.setAttribute("data-density", d);
  try { localStorage.setItem(DENSITY_KEY, d); } catch (_) {}
  syncDensityUI();
  // 密度改变了网格列宽/行高 CSS 变量，派发 resize 让虚拟网格重新布局。
  try { window.dispatchEvent(new Event("resize")); } catch (_) {}
}

export function setUiScale(scale) {
  if (!isValidScale(scale)) return;
  const n = Number(scale);
  document.documentElement.setAttribute("data-ui-scale", String(n));
  try { localStorage.setItem(SCALE_KEY, String(n)); } catch (_) {}
  applyScale(n);
  syncScaleUI();
  // Chromium reflows the whole document when the zoom factor changes; fire a
  // resize so the virtual grid recomputes its column count for the new width.
  try { window.dispatchEvent(new Event("resize")); } catch (_) {}
}

export function initAppearance() {
  let acc = "green";
  let den = "comfortable";
  let scale = 1;
  try {
    acc = localStorage.getItem(ACCENT_KEY) || "green";
    den = localStorage.getItem(DENSITY_KEY) || "comfortable";
    scale = Number(localStorage.getItem(SCALE_KEY) || 1);
  } catch (_) {}
  if (isValidAccent(acc)) document.documentElement.setAttribute("data-accent", acc);
  if (isValidDensity(den)) document.documentElement.setAttribute("data-density", den);
  if (isValidScale(scale)) {
    document.documentElement.setAttribute("data-ui-scale", String(scale));
    applyScale(scale);
  }

  const sw = document.getElementById("accent-swatches");
  if (sw) {
    sw.addEventListener("click", (e) => {
      const b = e.target.closest ? e.target.closest("[data-accent-choice]") : null;
      if (b) setAccent(b.dataset.accentChoice);
    });
  }
  const dt = document.getElementById("density-toggle");
  if (dt) {
    dt.addEventListener("click", (e) => {
      const b = e.target.closest ? e.target.closest("[data-density-choice]") : null;
      if (b) setDensity(b.dataset.densityChoice);
    });
  }
  const st = document.getElementById("scale-toggle");
  if (st) {
    st.addEventListener("click", (e) => {
      const b = e.target.closest ? e.target.closest("[data-scale-choice]") : null;
      if (b) setUiScale(b.dataset.scaleChoice);
    });
  }
  syncSwatchUI();
  syncDensityUI();
  syncScaleUI();

  const te = document.getElementById("token-estimate-toggle");
  if (te) {
    te.checked = tokenEstimateEnabled();
    te.addEventListener("change", () => setTokenEstimateEnabled(te.checked));
  }
}
