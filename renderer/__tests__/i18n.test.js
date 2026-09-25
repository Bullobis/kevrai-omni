// renderer/__tests__/i18n.test.js — i18n 模块单测：嵌套 key、{param} 插值、
// 字典回退、缺失/空值、registerDict 合并、localStorage 持久化、bindI18n DOM 绑定。
"use strict";
import { test, beforeEach } from "node:test";
import assert from "node:assert/strict";
import { setupDom } from "./helpers/dom.js";

// i18n 在模块加载时探测 document / localStorage，因此先装 jsdom 再动态 import。
const { window, document } = setupDom();
// jsdom 默认 opaque origin，访问其 localStorage 会抛 SecurityError；用确定性
// 内存 shim 代替（i18n 只要求 localStorage 存在且 getItem/setItem 可用）。
const lsStore = new Map();
const ls = {
  getItem: (k) => (lsStore.has(k) ? lsStore.get(k) : null),
  setItem: (k, v) => lsStore.set(k, String(v)),
  removeItem: (k) => lsStore.delete(k),
  clear: () => lsStore.clear(),
};
globalThis.localStorage = ls;
const i18n = await import("../modules/i18n.js");

const zh = {
  app: { title: "凯睿", greet: "你好 {name}" },
  simple: "简单",
  withnum: "共 {count} 项",
  empty: null,
};
const en = {
  app: { title: "Kevrai" },
  hello: "Hello",
};

beforeEach(async () => {
  i18n.registerDict("zh-CN", zh);
  i18n.registerDict("en-US", en);
  await i18n.setLocale("zh-CN");
});

test("嵌套 key 点号路径取值", () => {
  assert.equal(i18n.t("app.title"), "凯睿");
  assert.equal(i18n.t("simple"), "简单");
});

test("{param} 插值替换；缺失占位符原样保留", () => {
  assert.equal(i18n.t("app.greet", { name: "小明" }), "你好 小明");
  assert.equal(i18n.t("app.greet"), "你好 {name}");
  assert.equal(i18n.t("withnum", { count: 12 }), "共 12 项");
});

test("切到 en-US：命中英文；英文缺失时回退 zh-CN", async () => {
  await i18n.setLocale("en-US");
  assert.equal(i18n.getLocale(), "en-US");
  assert.equal(i18n.t("app.title"), "Kevrai"); // 命中英文
  assert.equal(i18n.t("simple"), "简单");       // 英文没有 → 回退中文
  assert.equal(i18n.t("hello"), "Hello");
});

test("两种语言都没有的 key 返回 key 本身；null 值也回退为 key", () => {
  assert.equal(i18n.t("no.such.key"), "no.such.key");
  assert.equal(i18n.t("empty"), "empty");
});

test("registerDict 浅合并不覆盖同级其它键", async () => {
  i18n.registerDict("zh-CN", { another: "新增" });
  assert.equal(i18n.t("another"), "新增");
  assert.equal(i18n.t("app.title"), "凯睿"); // 合并后仍在
});

test("setLocale 把偏好写入 localStorage", async () => {
  await i18n.setLocale("en-US");
  assert.equal(ls.getItem("kevrai-locale"), "en-US");
});

test("t 返回的插值只是字符串，经 textContent 绑定不会产生 HTML 元素", () => {
  const raw = i18n.t("app.greet", { name: "<b>x</b>" });
  assert.ok(raw.includes("<b>x</b>")); // 原样字符，未被当标签
  const host = document.createElement("div");
  host.textContent = raw;
  assert.equal(host.querySelector("b"), null); // textContent 转义，无 <b> 子元素
});

test("bindI18n：data-i18n→textContent，data-i18n-<attr>→属性", () => {
  document.body.innerHTML = `
    <span id="t1" data-i18n="app.title"></span>
    <input id="t2" data-i18n-placeholder="simple"/>
    <a id="t3" data-i18n-title="hello" data-i18n="simple"></a>`;
  i18n.bindI18n(document);
  assert.equal(document.getElementById("t1").textContent, "凯睿");
  assert.equal(document.getElementById("t2").getAttribute("placeholder"), "简单");
  const a = document.getElementById("t3");
  assert.equal(a.textContent, "简单");
  // hello 仅存在于 en；当前 zh 环境回退为 key 本身
  assert.equal(a.getAttribute("title"), "hello");
});
