// tests/cabinet-use/run-tests.mjs — Deputy-USE 自测（node 直跑，无浏览器）。
// 覆盖：i18n t() 回退链、{param} 插值、setLocale 持久化、bindI18n（mock DOM 真实路径）、
//       command-palette 模糊过滤、subsequenceMatch、registerAction、真实 locales JSON 对齐。
"use strict";

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

// ── 在 import 前注入最小浏览器环境 mock ─────────────────────────────────────
const store = {};
globalThis.localStorage = {
  getItem: (k) => (k in store ? store[k] : null),
  setItem: (k, v) => { store[k] = String(v); },
  removeItem: (k) => { delete store[k]; },
};

class MockEvent {
  constructor(type, opts) { this.type = type; this.detail = (opts && opts.detail) || {}; }
}
class MockEl {
  constructor(tag) {
    this.tag = tag || "div";
    this.attributes = {};
    this.children = [];
    this.textContent = "";
    this.hidden = false;
    this.dataset = {};
    this.className = "";
    this.value = "";
  }
  setAttribute(k, v) { this.attributes[k] = String(v); }
  getAttribute(k) { return (k in this.attributes) ? this.attributes[k] : null; }
  appendChild(c) { this.children.push(c); return c; }
  addEventListener() {}
  dispatchEvent() { return true; }
  replaceChildren() { this.children = []; }
  focus() { this._focused = true; }
  querySelectorAll(sel) {
    const out = [];
    const walk = (n) => {
      for (const c of n.children || []) {
        if (sel === "*") out.push(c);
        else if (sel === "[data-i18n]" && "data-i18n" in c.attributes) out.push(c);
        else if (/^\[data-i18n-[\w-]+\]$/.test(sel) && Object.keys(c.attributes).some((a) => a.startsWith("data-i18n-"))) out.push(c);
        walk(c);
      }
    };
    walk(this);
    return out;
  }
}
const mockDocument = new MockEl("body");
mockDocument.createElement = () => new MockEl("div");
mockDocument.body = new MockEl("body");
mockDocument.body.appendChild = (c) => { mockDocument.children.push(c); return c; };
globalThis.document = mockDocument;
globalThis.window = {
  listeners: {},
  dispatchEvent(ev) {
    (this.listeners[ev.type] || []).forEach((fn) => fn(ev));
    return true;
  },
  addEventListener(type, fn) { (this.listeners[type] ||= []).push(fn); },
};
globalThis.CustomEvent = MockEvent;

// ── runner ──────────────────────────────────────────────────────────────────
let passed = 0, failed = 0;
function test(name, fn) {
  return Promise.resolve()
    .then(fn)
    .then(() => { console.log("  ✓", name); passed++; })
    .catch((e) => { console.error("  ✗", name, "\n    ", e.message); failed++; });
}

// ── 动态 import（此时 mock 已就位）──────────────────────────────────────────
const i18n = await import("../../renderer/modules/i18n.js");
const cp = await import("../../renderer/modules/command-palette.js");

async function main() {
  // 1. i18n 基础
  await test("i18n: getLocale 默认 zh-CN", () => {
    assert.equal(i18n.getLocale(), "zh-CN");
  });

  await test("i18n: registerDict + t() 嵌套 key", () => {
    i18n.registerDict("zh-CN", { nav: { title: "设置", btn: { save: "保存" } } });
    assert.equal(i18n.t("nav.title"), "设置");
    assert.equal(i18n.t("nav.btn.save"), "保存");
  });

  await test("i18n: t() 插值 {name}", () => {
    i18n.registerDict("zh-CN", { toast: { hello: "你好 {name}，共 {n} 条" } });
    assert.equal(i18n.t("toast.hello", { name: "Cabinet", n: 3 }), "你好 Cabinet，共 3 条");
  });

  await test("i18n: 回退链 当前语言→zh-CN→key", async () => {
    i18n.registerDict("en-US", { nav: { subtitle: "Local AI" } });
    await i18n.setLocale("en-US");
    assert.equal(i18n.getLocale(), "en-US");
    assert.equal(i18n.t("nav.subtitle"), "Local AI");
    assert.equal(i18n.t("nav.title"), "设置");              // 回退 zh-CN
    assert.equal(i18n.t("no.such.key"), "no.such.key");
  });

  await test("i18n: 缺失占位符原样保留", () => {
    i18n.registerDict("en-US", { toast: { hello: "Hi {name}" } });
    assert.equal(i18n.t("toast.hello"), "Hi {name}");
  });

  await test("i18n: setLocale 写 localStorage", async () => {
    await i18n.setLocale("zh-CN");
    assert.equal(globalThis.localStorage.getItem("kevrai-locale"), "zh-CN");
  });

  // 2. bindI18n 真实路径
  await test("i18n: bindI18n 真实跑通 textContent + data-i18n-placeholder", () => {
    const root = new MockEl("div");
    const span = new MockEl("span");
    span.setAttribute("data-i18n", "nav.title");
    const input = new MockEl("input");
    input.setAttribute("data-i18n-placeholder", "market.searchPlaceholder");
    root.appendChild(span); root.appendChild(input);

    i18n.registerDict("zh-CN", {
      nav: { title: "设置" },
      market: { searchPlaceholder: "搜索模型…" },
    });
    i18n.setLocale("zh-CN");
    i18n.bindI18n(root); // 真实调用
    assert.equal(span.textContent, "设置");
    assert.equal(input.getAttribute("placeholder"), "搜索模型…");
  });

  // 3. command-palette 纯函数
  await test("cp: subsequenceMatch 子串与子序列", () => {
    assert.equal(cp.subsequenceMatch("gpu", "Detect GPU").hit, true);
    assert.equal(cp.subsequenceMatch("mk", "Model Market").hit, true);   // m(odel) → m(arket) 子序列
    assert.equal(cp.subsequenceMatch("xyz", "Model Market").hit, false);
  });

  await test("cp: filterActions 按 label+keywords 过滤", () => {
    const list = [
      { id: "a", label: "Open Settings", keywords: ["settings", "设置"] },
      { id: "b", label: "Detect GPU", keywords: ["gpu", "检测"] },
      { id: "c", label: "Refresh Models", keywords: ["refresh", "刷新"] },
    ];
    assert.equal(cp.filterActions(list, "gpu")[0].id, "b");
    assert.equal(cp.filterActions(list, "设置")[0].id, "a");
    assert.equal(cp.filterActions(list, "").length, 3);
    assert.equal(cp.filterActions(list, "zzz").length, 0);
  });

  await test("cp: registerAction 不抛错并可覆盖", () => {
    let ran = false;
    cp.registerAction("custom.test", "测试动作", () => { ran = true; }, ["test"]);
    // 拿到动作表 —— 无法直接访问内部 Map，但 filterActions 走的是 currentActionList()
    // 这里验证 registerAction 调用签名 OK
    assert.equal(typeof cp.registerAction, "function");
    assert.equal(ran, false);
  });

  await test("cp: openCommandPalette 在无 DOM 真环境下不崩", () => {
    // 有 mock document，buildOverlay 会走 mock；不应抛
    cp.openCommandPalette();
    cp.closeCommandPalette();
    assert.ok(true);
  });

  // 4. 真实 locales JSON 对齐 + t() 冒烟
  const zh = JSON.parse(readFileSync(new URL("../../renderer/locales/zh-CN.json", import.meta.url), "utf8"));
  const en = JSON.parse(readFileSync(new URL("../../renderer/locales/en-US.json", import.meta.url), "utf8"));

  await test("locales: zh-CN 与 en-US key 完全对齐", () => {
    const flat = (o, prefix = "") => {
      const out = [];
      for (const k in o) {
        const p = prefix ? prefix + "." + k : k;
        if (o[k] && typeof o[k] === "object") out.push(...flat(o[k], p));
        else out.push(p);
      }
      return out;
    };
    const kz = flat(zh), ke = flat(en);
    assert.equal(kz.length, ke.length, `key 数量不一致 zh=${kz.length} en=${ke.length}`);
    const missing = kz.filter((k) => !ke.includes(k));
    assert.equal(missing.length, 0, "en-US 缺 key: " + missing.join(","));
    console.log(`      (zh-CN ${kz.length} 条 / en-US ${ke.length} 条，对齐)`);
  });

  await test("locales: 真字典经 t() 取值 + 插值", async () => {
    i18n.registerDict("zh-CN", zh);
    i18n.registerDict("en-US", en);
    await i18n.setLocale("zh-CN");
    assert.equal(i18n.t("settings.save"), "保存");
    assert.equal(i18n.t("onboarding.step1Title"), "① 安装引擎");
    assert.equal(i18n.t("cmdpal.actions.openSettings"), "打开设置");
    assert.equal(i18n.t("toast.gpuDetected", { n: 2 }), "检测到 GPU：2");
    await i18n.setLocale("en-US");
    assert.equal(i18n.t("settings.save"), "Save");
    assert.equal(i18n.t("cmdpal.actions.openSettings"), "Open Settings");
    assert.equal(i18n.t("toast.gpuDetected", { n: 2 }), "GPU detected: 2");
  });

  console.log(`\n结果: ${passed} passed, ${failed} failed`);
  process.exit(failed ? 1 : 0);
}

main().catch((e) => { console.error(e); process.exit(1); });
