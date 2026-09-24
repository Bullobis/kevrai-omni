// renderer/modules/i18n.js — 极简 i18n：嵌套 key、{param} 占位符、字典回退、DOM 绑定。
//
// 设计目标：
//   - 纯 ES module，零依赖，无构建步骤。
//   - 在 Node 下也可 import（DOM 相关函数做环境检测，不抛错），方便单元测试。
//   - 字典回退链：当前语言 → zh-CN（兜底）→ key 本身。
//   - 语言记忆：localStorage["kevrai-locale"]，默认 "zh-CN"。
//
// 浏览器里字典由 locales/<lang>.json 通过 fetch 加载（见 loadLocale）；
// Node/测试环境下可直接 registerDict(lang, obj) 注入字典。
"use strict";

const STORAGE_KEY = "kevrai-locale";
const FALLBACK = "zh-CN";
const DEFAULT_LOCALE = "zh-CN";

/** @type {Record<string, Record<string, any>>} */
const dicts = {};
let currentLocale = DEFAULT_LOCALE;
let readyPromise = null;

// ── 环境检测 ────────────────────────────────────────────────────────────────
const hasLocalStorage = (() => {
  try {
    return typeof localStorage !== "undefined";
  } catch (_) {
    return false;
  }
})();
const hasDocument = typeof document !== "undefined";
const hasFetch = typeof fetch === "function";

// ── 内部工具 ────────────────────────────────────────────────────────────────
/** 按点号路径取嵌套值，找不到返回 undefined。 */
function lookup(dict, path) {
  if (!dict || typeof dict !== "object") return undefined;
  const parts = String(path).split(".");
  let node = dict;
  for (const p of parts) {
    if (node && typeof node === "object" && p in node) node = node[p];
    else return undefined;
  }
  return node;
}

/** 把 "你好 {name}" 里的 {name} 用 params 替换；缺失占位符原样保留。 */
function interpolate(str, params) {
  if (!params || typeof params !== "object") return str;
  return String(str).replace(/\{(\w+)\}/g, (m, name) => {
    return Object.prototype.hasOwnProperty.call(params, name) ? String(params[name]) : m;
  });
}

// ── 公开 API ────────────────────────────────────────────────────────────────

/**
 * t(key, params?) — 取当前语言文案。
 * 回退：当前语言 → zh-CN → key 本身。params 用于 {name} 占位符替换。
 */
export function t(key, params) {
  let raw = lookup(dicts[currentLocale], key);
  if (raw === undefined && currentLocale !== FALLBACK) {
    raw = lookup(dicts[FALLBACK], key);
  }
  if (raw === undefined || raw === null) return String(key);
  if (typeof raw !== "string") raw = String(raw);
  return params ? interpolate(raw, params) : raw;
}

/** 注册/覆盖某语言字典（合并式：浅合并到已有 dict 上）。测试与浏览器启动都可用。 */
export function registerDict(lang, dict) {
  if (!lang || !dict || typeof dict !== "object") return;
  if (!dicts[lang]) dicts[lang] = {};
  Object.assign(dicts[lang], dict);
}

/**
 * setLocale(lang) — 切换语言。会写 localStorage（若可用）。
 * 浏览器下若该语言字典尚未加载，会触发 fetch 加载；返回 Promise 表示加载完成。
 * 切换后自动重新 bindI18n(document)。
 */
export function setLocale(lang) {
  if (!lang) return Promise.resolve();
  currentLocale = lang;
  if (hasLocalStorage) {
    try { localStorage.setItem(STORAGE_KEY, lang); } catch (_) {}
  }
  const p = dicts[lang] ? Promise.resolve() : loadLocale(lang);
  return p.then(() => {
    if (hasDocument) {
      try { bindI18n(document); } catch (_) {}
    }
    // 通知外部（如命令面板标签重渲染）
    if (hasDocument && typeof window !== "undefined") {
      try { window.dispatchEvent(new CustomEvent("kevrai:locale-changed", { detail: { locale: lang } })); } catch (_) {}
    }
  });
}

/** getLocale() — 当前语言。 */
export function getLocale() {
  return currentLocale;
}

/**
 * loadLocale(lang) — 浏览器下 fetch locales/<lang>.json 并注册。
 * Node 下无 fetch 时返回 resolved（调用方应改用 registerDict）。
 */
export function loadLocale(lang) {
  if (dicts[lang]) return Promise.resolve(dicts[lang]);
  if (!hasFetch || !hasDocument) return Promise.resolve(null);
  // locales/ 与 modules/ 同级：renderer/locales/<lang>.json
  const url = new URL("../locales/" + lang + ".json", import.meta.url).href;
  return fetch(url)
    .then((r) => { if (!r.ok) throw new Error("locale " + lang + " HTTP " + r.status); return r.json(); })
    .then((dict) => { registerDict(lang, dict); return dict; })
    .catch(() => null);
}

/**
 * bindI18n(root) — 扫描 root 下所有 [data-i18n] / [data-i18n-*] 并本地化。
 *   <span data-i18n="settings.title"></span>           → textContent = t(...)
 *   <input data-i18n-placeholder="search.placeholder"> → placeholder = t(...)
 * 任何 data-i18n-<attr> 形式都会把 <attr> 当作元素属性名写入。
 * 无 DOM 环境时静默返回。
 */
export function bindI18n(root) {
  if (!hasDocument) return;
  const scope = root || document;
  // 扫描 scope 下所有元素，按属性分发：
  //   data-i18n="key"            → textContent = t(key)
  //   data-i18n-<attr>="key"     → <attr> = t(key)
  // （用通配 "*" 而不是 [data-i18n]，否则只有 data-i18n-placeholder 的元素会被漏掉。）
  const nodes = scope.querySelectorAll ? scope.querySelectorAll("*") : [scope];
  const list = (scope === document) ? [document, ...Array.from(nodes)] : Array.from(nodes);
  for (const el of list) {
    if (!el.attributes) continue;
    // getAttributeNames() 在真实浏览器返回 ["data-i18n", ...]；
    // Node mock 下兜底用 Object.keys(attributes)。
    const names = typeof el.getAttributeNames === "function"
      ? el.getAttributeNames()
      : Object.keys(el.attributes);
    for (const name of names) {
      const key = el.getAttribute(name);
      if (!key) continue;
      if (name === "data-i18n") {
        el.textContent = t(key);
      } else {
        const m = /^data-i18n-([\w-]+)$/.exec(name);
        if (m) el.setAttribute(m[1], t(key));
      }
    }
  }
}

/**
 * initI18n() — 浏览器启动入口：读 localStorage 偏好 → 加载 zh-CN 兜底 → 加载当前语言 → bind。
 * 返回 Promise。Node 下调用是安全的（无 DOM/fetch 时直接 resolve）。
 */
export function initI18n() {
  let stored = DEFAULT_LOCALE;
  if (hasLocalStorage) {
    try { stored = localStorage.getItem(STORAGE_KEY) || DEFAULT_LOCALE; } catch (_) {}
  }
  currentLocale = stored;
  const chain = [];
  if (stored !== FALLBACK) chain.push(loadLocale(FALLBACK));
  chain.push(loadLocale(stored));
  readyPromise = Promise.all(chain).then(() => {
    if (hasDocument) bindI18n(document);
  });
  return readyPromise;
}

// 模块加载时若在浏览器里，自动读 localStorage（不自动 fetch，由 initI18n 触发）。
if (hasLocalStorage) {
  try {
    const s = localStorage.getItem(STORAGE_KEY);
    if (s) currentLocale = s;
  } catch (_) {}
}
