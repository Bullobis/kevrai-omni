// renderer/modules/debounce.js — tiny debounce utility.
"use strict";
export function debounce(fn, ms = 200) {
  let t = null;
  const debounced = (...args) => {
    if (t) clearTimeout(t);
    t = setTimeout(() => { t = null; try { fn(...args); } catch (_) {} }, ms);
  };
  debounced.cancel = () => { if (t) { clearTimeout(t); t = null; } };
  return debounced;
}
