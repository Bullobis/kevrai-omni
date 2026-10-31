// renderer/__tests__/i18n-landing.test.js — i18n 落地的端到端验证。
// 覆盖：t() 基本取词、回退链（当前语言 → zh-CN → key）、{param} 占位、
// setLocale 即时切换、bindI18n 对 data-i18n 与 data-i18n-<attr> 的处理、
// 以及 zh-CN / en-US 字典 key 集合对齐（en 必须是 zh 的子集）。
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync, readdirSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { setupDom } from "./helpers/dom.js";

// 必须先建立 DOM，i18n.js 在模块加载时捕获 hasDocument。
setupDom();
globalThis.CustomEvent = window.CustomEvent;
const i18n = await import("../modules/i18n.js");

const { t, registerDict, setLocale, getLocale, bindI18n } = i18n;

// 小型专用字典（功能用例）。
registerDict("zh-CN", {
  app: { title: "Kevrai", welcome: "你好 {name}", empty: "暂无数据" },
  nav: { market: "市场", settings: "设置" },
});
registerDict("en-US", {
  app: { title: "Kevrai", welcome: "Hello {name}", missingKey: "present-only-in-en" },
  nav: { market: "Market" },
});

test("t() 基本取当前语言文案（默认 zh-CN）", () => {
  assert.equal(getLocale(), "zh-CN");
  assert.equal(t("app.title"), "Kevrai");
  assert.equal(t("nav.market"), "市场");
});

test("嵌套 key 按点号路径取值", () => {
  registerDict("zh-CN", { nested: { deep: { value: "深值" } } });
  assert.equal(t("nested.deep.value"), "深值");
});

test("{param} 占位符替换，缺失占位符原样保留", () => {
  assert.equal(t("app.welcome", { name: "Sam" }), "你好 Sam");
  registerDict("zh-CN", { templated: "a={a} b={b}" });
  // 只传 a，b 保留原样。
  assert.equal(t("templated", { a: "1" }), "a=1 b={b}");
});

test("当前语言缺 key 时回退到 zh-CN", async () => {
  await setLocale("en-US");
  // en-US 没有 nav.settings，回退 zh-CN → "设置"。
  assert.equal(t("nav.settings"), "设置");
  await setLocale("zh-CN");
});

test("当前语言与 zh-CN 都缺 key 时返回 key 本身", async () => {
  await setLocale("en-US");
  assert.equal(t("app.does.not.exist"), "app.does.not.exist");
  await setLocale("zh-CN");
});

test("setLocale 切换后 t() 立即返回对应语言", async () => {
  assert.equal(t("nav.market"), "市场");
  await setLocale("en-US");
  assert.equal(getLocale(), "en-US");
  assert.equal(t("nav.market"), "Market");
  assert.equal(t("app.welcome", { name: "Sam" }), "Hello Sam");
  await setLocale("zh-CN");
  assert.equal(t("nav.market"), "市场");
});

test("bindI18n 处理 data-i18n（写入 textContent）", () => {
  const host = document.createElement("div");
  host.innerHTML = '<span data-i18n="nav.market"></span>';
  document.body.appendChild(host);
  try {
    bindI18n(host);
    const el = host.querySelector("span");
    assert.equal(el.textContent, "市场");
  } finally {
    host.remove();
  }
});

test("bindI18n 处理 data-i18n-placeholder（写入任意属性）", () => {
  const host = document.createElement("div");
  host.innerHTML = '<input data-i18n-placeholder="app.empty" data-i18n-title="nav.settings">';
  document.body.appendChild(host);
  try {
    bindI18n(host);
    const el = host.querySelector("input");
    assert.equal(el.getAttribute("placeholder"), "暂无数据");
    assert.equal(el.getAttribute("title"), "设置");
  } finally {
    host.remove();
  }
});

test("setLocale 后派发 kevrai:locale-changed 事件", async () => {
  let fired = null;
  window.addEventListener("kevrai:locale-changed", (e) => { fired = e.detail; }, { once: true });
  await setLocale("en-US");
  assert.deepEqual(fired, { locale: "en-US" });
  await setLocale("zh-CN");
});

test("字典对齐：en-US 的 key 集合是 zh-CN 的子集（递归）", () => {
  const here = dirname(fileURLToPath(import.meta.url));
  const zh = JSON.parse(readFileSync(join(here, "..", "locales", "zh-CN.json"), "utf8"));
  const en = JSON.parse(readFileSync(join(here, "..", "locales", "en-US.json"), "utf8"));
  const flat = (obj, prefix = "") => {
    const out = [];
    for (const k of Object.keys(obj)) {
      const path = prefix ? `${prefix}.${k}` : k;
      if (obj[k] && typeof obj[k] === "object") out.push(...flat(obj[k], path));
      else out.push(path);
    }
    return out;
  };
  const zhKeys = new Set(flat(zh));
  const enKeys = new Set(flat(en));
  const missingInEn = [...zhKeys].filter((k) => !enKeys.has(k));
  assert.equal(missingInEn.length, 0, `en-US 缺少 zh-CN 已有的 key: ${missingInEn.join(", ")}`);
});

test("字典覆盖所有 t()/data-i18n 字面键（防缺键回归）", () => {
  // 扫描 renderer 下所有 JS：每个 `t("literal")` 调用与 `data-i18n[-attr]="literal"`
  // 的键都必须存在于 zh-CN 和 en-US。缺键会让 t() 直接返回 key 本身（如
  // 3d.* → threed.* 那类缺陷），用户看到原始键名而非文案。
  const here = dirname(fileURLToPath(import.meta.url));
  const zh = JSON.parse(readFileSync(join(here, "..", "locales", "zh-CN.json"), "utf8"));
  const en = JSON.parse(readFileSync(join(here, "..", "locales", "en-US.json"), "utf8"));
  const flat = (obj, prefix = "") => {
    const out = [];
    for (const k of Object.keys(obj)) {
      const path = prefix ? `${prefix}.${k}` : k;
      if (obj[k] && typeof obj[k] === "object") out.push(...flat(obj[k], path));
      else out.push(path);
    }
    return out;
  };
  const zhKeys = new Set(flat(zh));
  const enKeys = new Set(flat(en));
  const files = [
    ...readdirSync(join(here, "..")).filter((f) => f.endsWith(".js")).map((f) => join(here, "..", f)),
    ...readdirSync(join(here, "..", "modules")).filter((f) => f.endsWith(".js")).map((f) => join(here, "..", "modules", f)),
  ];
  // 键只由字母数字/点/下划线组成（点号路径）；动态键（如 `t("k" + x)`、
  // 模板键 `t(\`a.${x}\`)`）无法静态校验，跳过。
  const keyRe = /\bt\(\s*(["'])([a-zA-Z0-9_.]+)\1\s*(?:,|\))/g;
  const attrRe = /\bdata-i18n(?:-[a-z]+)?\s*=\s*(["'])([a-zA-Z0-9_.]+)\1/g;
  const missZh = new Set();
  const missEn = new Set();
  for (const f of files) {
    let s;
    try { s = readFileSync(f, "utf8"); } catch { continue; }
    // 剥掉注释，避免文档示例（如 i18n.js 里 data-i18n="key" 示例）误报。
    s = s.replace(/\/\*[\s\S]*?\*\//g, "").replace(/\/\/[^\n]*/g, "");
    for (const re of [keyRe, attrRe]) {
      let m;
      re.lastIndex = 0;
      while ((m = re.exec(s))) {
        const k = m[2].trim();
        if (!zhKeys.has(k)) missZh.add(k);
        if (!enKeys.has(k)) missEn.add(k);
      }
    }
  }
  assert.equal(missZh.size, 0, `zh-CN 缺少被引用的 key: ${[...missZh].join(", ")}`);
  assert.equal(missEn.size, 0, `en-US 缺少被引用的 key: ${[...missEn].join(", ")}`);
});

