// renderer/modules/appearance.js — 外观个性化：强调色 + 界面密度。
//
// 强调色与密度都通过 <html> 上的 data-* attribute 表达，具体颜色/几何在
// styles/appearance.css 中按主题覆盖；本模块只负责应用持久化偏好、同步控件
// active 态，并在密度变化时触发 resize 让虚拟网格重算几何。
"use strict";

const ACCENT_KEY = "kevrai:accent";
const DENSITY_KEY = "kevrai:density";
export const ACCENTS = ["green", "blue", "violet", "orange", "pink", "cyan"];
export const DENSITIES = ["comfortable", "compact"];

export function isValidAccent(id) { return ACCENTS.includes(id); }
export function isValidDensity(d) { return DENSITIES.includes(d); }

function currentAccent() {
  return document.documentElement.getAttribute("data-accent") || "green";
}
function currentDensity() {
  return document.documentElement.getAttribute("data-density") || "comfortable";
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

export function initAppearance() {
  let acc = "green";
  let den = "comfortable";
  try {
    acc = localStorage.getItem(ACCENT_KEY) || "green";
    den = localStorage.getItem(DENSITY_KEY) || "comfortable";
  } catch (_) {}
  if (isValidAccent(acc)) document.documentElement.setAttribute("data-accent", acc);
  if (isValidDensity(den)) document.documentElement.setAttribute("data-density", den);

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
  syncSwatchUI();
  syncDensityUI();
}
