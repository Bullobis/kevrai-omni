// renderer/theme-init.js — 首绘前同步确定主题（消除首启动深色闪烁/卡死）。
// CHAIR rootcause（parliament/20260924-chair-theme-rootcause.md）。
// 必须在 <head> 中、styles.css 之前以普通同步脚本加载（同源，符合 CSP script-src 'self'）。
(function () {
  "use strict";
  var KEY = "kevrai.theme";
  var theme = null;
  try { theme = window.localStorage.getItem(KEY); } catch (_) {}
  if (theme !== "light" && theme !== "dark" && theme !== "system") theme = "system";
  var dark = false;
  if (theme === "dark") {
    dark = true;
  } else if (theme === "system") {
    dark = !!(window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches);
  }
  document.documentElement.dataset.theme = dark ? "dark" : "light";
})();
