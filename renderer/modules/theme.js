// renderer/modules/theme.js — applies theme to <html> based on settings.
"use strict";
import { state } from "./state.js";

export function applyTheme() {
  const t = state.settings?.theme || "system";
  // Cache the raw preference so theme-init.js can set the correct theme before
  // first paint on next launch (even when the sidecar is not ready yet).
  try { localStorage.setItem("kevrai.theme", t); } catch (_) {}
  const mql = (typeof window !== "undefined") && window.matchMedia
    ? window.matchMedia("(prefers-color-scheme: dark)") : null;
  const isDark = t === "dark" || (t === "system" && mql && mql.matches);
  document.documentElement.dataset.theme = isDark ? "dark" : "light";
}

export function wireThemeListener() {
  if (!window.matchMedia) return;
  const mql = window.matchMedia("(prefers-color-scheme: dark)");
  const fn = () => { if ((state.settings?.theme || "system") === "system") applyTheme(); };
  mql.addEventListener ? mql.addEventListener("change", fn)
                       : mql.addListener(fn); // legacy
}
