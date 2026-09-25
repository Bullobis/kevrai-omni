// renderer/__tests__/command-palette-extra.test.js — 命令面板增强的纯函数单测。
// 覆盖：iconSvg、registerAction 扩展签名（icon/group）、filterActions 对带
// group/icon 动作的过滤排序、subsequenceMatch 边界、以及默认动作注册完整性。
// 这些都是纯函数/模块级状态，不依赖 DOM；command-palette.js 底部会自动调用
// registerDefaults()，import 后即可断言默认动作。
import { test } from "node:test";
import assert from "node:assert/strict";

import {
  subsequenceMatch,
  filterActions,
  registerAction,
  iconSvg,
  __getRegisteredAction,
} from "../modules/command-palette.js";

// ── iconSvg ──────────────────────────────────────────────────────────────────
test("iconSvg returns a well-formed inline svg for known names", () => {
  for (const name of ["search", "settings", "download", "gpu", "refresh",
    "update", "globe", "sun", "moon", "monitor", "folder", "panel-left",
    "keyboard", "navigate"]) {
    const svg = iconSvg(name);
    assert.ok(svg.startsWith('<svg class="cmdpal-icon"'), `svg wrapper for ${name}`);
    assert.ok(svg.includes('viewBox="0 0 24 24"'), `viewBox for ${name}`);
    assert.ok(svg.includes('stroke="currentColor"'), `stroke for ${name}`);
    assert.ok(svg.includes("</svg>"), `closing tag for ${name}`);
  }
});

test("iconSvg falls back to a neutral dot for unknown names", () => {
  const svg = iconSvg("does-not-exist");
  assert.ok(svg.includes('<circle cx="12" cy="12" r="5"/>'), "fallback circle present");
  assert.ok(svg.startsWith("<svg"), "still wrapped as svg");
});

// ── registerAction 扩展签名 ──────────────────────────────────────────────────
test("registerAction stores icon and group; old signature defaults them", () => {
  registerAction("zz-ext-a", "Enhanced Act", () => {}, ["kw"], "sun", "主题");
  const a = __getRegisteredAction("zz-ext-a");
  assert.equal(a.icon, "sun");
  assert.equal(a.group, "主题");
  assert.ok(Array.isArray(a.keywords));
  assert.deepEqual(a.keywords, ["kw"]);

  // 向后兼容：旧调用方不传 icon/group。
  registerAction("zz-ext-old", "Legacy Act", () => {});
  const o = __getRegisteredAction("zz-ext-old");
  assert.equal(o.icon, "", "icon defaults to empty string");
  assert.equal(o.group, "", "group defaults to empty string");

  // 旧调用方传字符串 keywords 也仍被包成数组。
  registerAction("zz-ext-kw", "Legacy Kw", () => {}, "singlekw");
  const k = __getRegisteredAction("zz-ext-kw");
  assert.deepEqual(k.keywords, ["singlekw"]);
});

// ── filterActions 对带 group/icon 的动作 ──────────────────────────────────────
test("filterActions filters by label+keywords and preserves icon/group fields", () => {
  const list = [
    { id: "a", label: "Refresh Models", keywords: ["refresh", "reload"], icon: "refresh", group: "操作" },
    { id: "b", label: "Dark Mode", keywords: ["dark", "night"], icon: "moon", group: "主题" },
    { id: "c", label: "Open Settings", keywords: ["settings", "设置"], icon: "settings", group: "操作" },
  ];
  const byLabel = filterActions(list, "refresh");
  assert.equal(byLabel.length, 1);
  assert.equal(byLabel[0].id, "a");
  assert.equal(byLabel[0].icon, "refresh");
  assert.equal(byLabel[0].group, "操作");

  const byKw = filterActions(list, "night");
  assert.equal(byKw.length, 1);
  assert.equal(byKw[0].id, "b");

  // 无查询时原样返回（顺序保持）。
  const all = filterActions(list, "");
  assert.equal(all.length, 3);
});

test("filterActions ranks exact substring above loose subsequence", () => {
  const list = [
    { id: "loose", label: "Foo Bar Baz", keywords: [] },
    { id: "exact", label: "Settings", keywords: [] },
  ];
  const out = filterActions(list, "set");
  assert.equal(out[0].id, "exact", "substring match ranks first");
});

// ── subsequenceMatch 边界 ─────────────────────────────────────────────────────
test("subsequenceMatch: empty query, substring, subsequence, no-match, case", () => {
  // 空 query 直接命中（布尔 true，filterActions 另有空查询短路）。
  assert.equal(subsequenceMatch("", "anything"), true);

  // 子串命中：高分。
  const sub = subsequenceMatch("ref", "refresh models");
  assert.equal(sub.hit, true);
  assert.ok(sub.score >= 90, "substring scores high");

  // 子序列命中：score=50。
  const seq = subsequenceMatch("rm", "refresh models");
  assert.equal(seq.hit, true);
  assert.equal(seq.score, 50);

  // 完全不命中。
  const miss = subsequenceMatch("xyz", "refresh models");
  assert.equal(miss.hit, false);
  assert.equal(miss.score, 0);

  // 大小写不敏感。
  assert.equal(subsequenceMatch("REF", "Refresh").hit, true);
  assert.equal(subsequenceMatch("中文", "中文主题").hit, true);
});

// ── 默认动作注册完整性（registerDefaults 已在 import 时执行）──────────────────
test("default actions carry icon/group/shortcut as wired", () => {
  const settings = __getRegisteredAction("openSettings");
  assert.ok(settings, "openSettings registered");
  assert.equal(settings.group, "操作");
  assert.equal(settings.icon, "settings");
  assert.equal(settings.shortcut, "⌘,");

  const sidebar = __getRegisteredAction("toggleSidebar");
  assert.equal(sidebar.shortcut, "⌘B");
  assert.equal(sidebar.icon, "panel-left");

  const theme = __getRegisteredAction("themeDark");
  assert.equal(theme.group, "主题");
  assert.equal(theme.icon, "moon");

  const shortcuts = __getRegisteredAction("showShortcuts");
  assert.equal(shortcuts.group, "系统");
  assert.equal(shortcuts.icon, "keyboard");

  // 导航类动作都分到「导航」并带快捷键。
  const nav = __getRegisteredAction("goMarket");
  assert.equal(nav.group, "导航");
  assert.equal(nav.shortcut, "⌘1");
});
