// renderer/theme-init.js — 首绘前同步确定主题（消除首启动深色闪烁/卡死）。
// 在首绘前应用缓存主题，避免主题闪烁（FOUC）。
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
