// renderer/__tests__/command-palette.test.js — 命令面板纯逻辑单测：
// subsequenceMatch 子串/子序列匹配打分、filterActions 过滤排序、registerAction 注册校验。
"use strict";
import { test } from "node:test";
import assert from "node:assert/strict";
import { setupDom } from "./helpers/dom.js";

// command-palette 间接 import i18n（模块加载即探测 document/localStorage），先装环境。
const { window } = setupDom();
const lsStore = new Map();
globalThis.localStorage = {
  getItem: (k) => (lsStore.has(k) ? lsStore.get(k) : null),
  setItem: (k, v) => lsStore.set(k, String(v)),
  removeItem: (k) => lsStore.delete(k),
  clear: () => lsStore.clear(),
};
const cp = await import("../modules/command-palette.js");

const noop = () => {};
const list = [
  { id: "market", label: "模型市场", keywords: ["market", "home"], handler: noop },
  { id: "settings", label: "设置", keywords: ["settings", "preferences"], handler: noop },
  { id: "agent", label: "智能体", keywords: ["agent", "assistant"], handler: noop },
];

test("subsequenceMatch：空 query 命中；子串命中且越早分越高", () => {
  assert.equal(cp.subsequenceMatch("", "任意").hit ?? cp.subsequenceMatch("", "任意"), true);
  const early = cp.subsequenceMatch("mar", "market");
  const late = cp.subsequenceMatch("mark", "a market");
  assert.equal(early.hit, true);
  assert.ok(early.score >= late.score); // 起始位置子串得分更高
});

test("subsequenceMatch：非连续但按序出现走子序列（低分）；乱序不命中", () => {
  const sub = cp.subsequenceMatch("mkt", "market"); // m...k...t 按序
  assert.equal(sub.hit, true);
  assert.ok(sub.score < 60);
  assert.equal(cp.subsequenceMatch("xyz", "market").hit, false);
});

test("subsequenceMatch 大小写不敏感", () => {
  assert.equal(cp.subsequenceMatch("MARK", "market").hit, true);
});

test("filterActions：空 query 返回全部且保序", () => {
  const r = cp.filterActions(list, "");
  assert.deepEqual(r.map((a) => a.id), ["market", "settings", "agent"]);
});

test("filterActions：按 label 中文/英文 keyword 命中，无匹配返回空", () => {
  assert.deepEqual(cp.filterActions(list, "设置").map((a) => a.id), ["settings"]);
  assert.deepEqual(cp.filterActions(list, "agent").map((a) => a.id), ["agent"]);
  assert.deepEqual(cp.filterActions(list, "zzz"), []);
});

test("filterActions：子串命中排在子序列之前", () => {
  const r = cp.filterActions(list, "mar"); // market 子串高分
  assert.equal(r[0].id, "market");
});

test("filterActions：query 首尾空白被 trim；任一 keyword 命中即保留", () => {
  assert.deepEqual(cp.filterActions(list, "  home  ").map((a) => a.id), ["market"]);
  assert.deepEqual(cp.filterActions(list, "preferences").map((a) => a.id), ["settings"]);
});

test("registerAction：无效输入（缺 handler/id）被静默拒绝且不抛错", () => {
  assert.doesNotThrow(() => cp.registerAction("bad1", "x", null));
  assert.doesNotThrow(() => cp.registerAction("", "x", noop));
  assert.doesNotThrow(() => cp.registerAction("ok1", "标签", noop, "kw"));
});
