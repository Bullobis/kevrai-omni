// renderer/__tests__/data-portability.test.js — 数据导出/导入逻辑。
// 与 favorites.test.js 同样的手法：jsdom 默认 opaque origin 会让 localStorage 抛错，
// 所以这里安装一个内存 shim 作为 globalThis.localStorage；preload bridge 走 window.kevrai。
import { test, beforeEach, afterEach } from "node:test";
import assert from "node:assert/strict";
import { setupDom } from "./helpers/dom.js";

setupDom();

// 内存 localStorage shim
function makeStorage() {
  const map = new Map();
  return {
    getItem: (k) => (map.has(k) ? map.get(k) : null),
    setItem: (k, v) => { map.set(k, String(v)); },
    removeItem: (k) => { map.delete(k); },
    clear: () => map.clear(),
    _map: map,
  };
}

let store;
let putCalls;

beforeEach(() => {
  store = makeStorage();
  globalThis.localStorage = store;
  putCalls = [];
  // preload bridge mock
  window.kevrai = {
    getSettings: async () => ({ theme: "dark", modelDir: "/models", hfToken: "hf_abc" }),
    putSettings: async (s) => { putCalls.push(s); return s; },
    getAppVersion: async () => "3.4.2",
  };
});

afterEach(() => {
  delete globalThis.localStorage;
  delete window.kevrai;
});

const dp = await import("../modules/data-portability.js");

// ── exportData ───────────────────────────────────────────────────────────────
test("exportData 生成正确结构（version / timestamp / appVersion / data）", async () => {
  store.setItem("kevrai:favorites", JSON.stringify(["a", "b"]));
  store.setItem("kevrai:recent", JSON.stringify(["c"]));
  store.setItem("kevrai:cmd-recent", JSON.stringify({ cmd1: 111 }));
  store.setItem("kevrai:sidebar-expanded", "1");
  store.setItem("kevrai-locale", "zh-CN");

  const out = await dp.exportData();
  assert.equal(out.kevraiExportVersion, 1);
  assert.match(out.exportedAt, /^\d{4}-\d{2}-\d{2}T/);
  assert.equal(out.appVersion, "3.4.2");
  assert.ok(out.data, "data 对象存在");
  assert.equal(out.data.settings.theme, "dark");
  assert.equal(out.data.settings.hfToken, "hf_abc");
  assert.deepEqual(out.data.favorites, ["a", "b"]);
  assert.deepEqual(out.data.recent, ["c"]);
  assert.deepEqual(out.data.cmdRecent, { cmd1: 111 });
  assert.equal(out.data.preferences.theme, "dark");
  assert.equal(out.data.preferences.sidebarExpanded, true);
  assert.equal(out.data.preferences.locale, "zh-CN");
});

// ── parseImport：合法文件 ─────────────────────────────────────────────────────
test("parseImport 合法备份文件通过校验并规范化", () => {
  const text = JSON.stringify({
    kevraiExportVersion: 1,
    exportedAt: "2026-09-25T14:00:00.000Z",
    appVersion: "3.4.2",
    data: {
      settings: { theme: "light" },
      favorites: ["x", "y"],
      recent: ["z"],
      cmdRecent: { cmd: 5 },
      preferences: { theme: "light", sidebarExpanded: false, locale: "en-US" },
    },
  });
  const parsed = dp.parseImport(text);
  assert.equal(parsed.version, 1);
  assert.deepEqual(parsed.data.favorites, ["x", "y"]);
  assert.deepEqual(parsed.data.recent, ["z"]);
  assert.equal(parsed.data.settings.theme, "light");
  assert.deepEqual(parsed.data.cmdRecent, { cmd: 5 });
});

test("parseImport 缺失 favorites/recent 时规范化为空数组", () => {
  const parsed = dp.parseImport(JSON.stringify({
    kevraiExportVersion: 1,
    data: { settings: { theme: "dark" } },
  }));
  assert.deepEqual(parsed.data.favorites, []);
  assert.deepEqual(parsed.data.recent, []);
  assert.deepEqual(parsed.data.cmdRecent, {});
});

// ── parseImport：损坏 / 不合法 ───────────────────────────────────────────────
test("parseImport 损坏 JSON 抛错", () => {
  assert.throws(() => dp.parseImport("{ not json"), /不是有效的 JSON/);
});

test("parseImport 缺失 kevraiExportVersion 抛错", () => {
  assert.throws(() => dp.parseImport(JSON.stringify({ data: {} })), /缺少导出版本号/);
});

test("parseImport 版本 >1 给出不兼容错误", () => {
  assert.throws(
    () => dp.parseImport(JSON.stringify({ kevraiExportVersion: 2, data: {} })),
    /不支持的导出版本 v2/
  );
});

test("parseImport favorites 不是数组抛错", () => {
  assert.throws(
    () => dp.parseImport(JSON.stringify({
      kevraiExportVersion: 1, data: { favorites: "nope" },
    })),
    /favorites 不是数组/
  );
});

test("parseImport settings 不是对象抛错", () => {
  assert.throws(
    () => dp.parseImport(JSON.stringify({
      kevraiExportVersion: 1, data: { settings: [1, 2] },
    })),
    /settings 不是对象/
  );
});

// ── importData：merge ────────────────────────────────────────────────────────
test("importData merge：favorites 合并去重、recent 合并去重", async () => {
  store.setItem("kevrai:favorites", JSON.stringify(["a", "b"]));
  store.setItem("kevrai:recent", JSON.stringify(["r-a", "r-b"]));
  store.setItem("kevrai:cmd-recent", JSON.stringify({ old: 1 }));
  // @ts-ignore — state 来自模块单例
  const { state } = await import("../modules/state.js");
  state.settings = { theme: "dark", modelDir: "/cur" };

  const parsed = dp.parseImport(JSON.stringify({
    kevraiExportVersion: 1,
    data: {
      settings: { theme: "light" },           // 覆盖现有 theme
      favorites: ["b", "c", "d"],              // b 重复
      recent: ["r-c", "r-b"],                  // r-b 重复
      cmdRecent: { new: 2 },
    },
  }));

  const stats = await dp.importData(parsed, "merge");
  // favorites: 导入优先去重 + 现有补后 → b,c,d,a
  const favs = JSON.parse(store.getItem("kevrai:favorites"));
  assert.deepEqual(favs, ["b", "c", "d", "a"]);
  // recent: 导入优先去重 + 现有补后 → r-c, r-b, r-a
  const recent = JSON.parse(store.getItem("kevrai:recent"));
  assert.deepEqual(recent, ["r-c", "r-b", "r-a"]);
  // settings 合并：导入 theme=light 覆盖，modelDir 保留
  assert.equal(putCalls.length, 1);
  assert.equal(putCalls[0].theme, "light");
  assert.equal(putCalls[0].modelDir, "/cur");
  // cmdRecent 合并
  const cmd = JSON.parse(store.getItem("kevrai:cmd-recent"));
  assert.deepEqual(cmd, { old: 1, new: 2 });
  assert.equal(stats.favorites, 4);
  assert.equal(stats.recent, 3);
  assert.equal(stats.settings, true);
});

test("importData merge：recent 上限 20 条", async () => {
  const existing = Array.from({ length: 10 }, (_, i) => "e" + i);
  const incoming = Array.from({ length: 15 }, (_, i) => "i" + i);
  store.setItem("kevrai:recent", JSON.stringify(existing));
  const { state } = await import("../modules/state.js");
  state.settings = {};

  const parsed = dp.parseImport(JSON.stringify({
    kevraiExportVersion: 1,
    data: { recent: incoming },
  }));
  const stats = await dp.importData(parsed, "merge");
  const recent = JSON.parse(store.getItem("kevrai:recent"));
  assert.equal(recent.length, 20, "recent capped at 20");
  assert.equal(stats.recent, 20);
  // 导入项排前
  assert.deepEqual(recent.slice(0, 15), incoming);
});

// ── importData：replace ──────────────────────────────────────────────────────
test("importData replace：完全替换 favorites/recent/settings", async () => {
  store.setItem("kevrai:favorites", JSON.stringify(["a", "b"]));
  store.setItem("kevrai:recent", JSON.stringify(["r-a"]));
  const { state } = await import("../modules/state.js");
  state.settings = { theme: "dark", modelDir: "/old" };

  const parsed = dp.parseImport(JSON.stringify({
    kevraiExportVersion: 1,
    data: {
      settings: { theme: "light" },
      favorites: ["x", "y"],
      recent: ["r-x"],
    },
  }));
  const stats = await dp.importData(parsed, "replace");

  assert.deepEqual(JSON.parse(store.getItem("kevrai:favorites")), ["x", "y"]);
  assert.deepEqual(JSON.parse(store.getItem("kevrai:recent")), ["r-x"]);
  // settings 整体替换：不再有 modelDir
  assert.equal(putCalls.length, 1);
  assert.deepEqual(putCalls[0], { theme: "light" });
  assert.equal(stats.favorites, 2);
  assert.equal(stats.recent, 1);
});

test("导入失败（parseImport 抛错）不触碰现有数据", () => {
  store.setItem("kevrai:favorites", JSON.stringify(["keep"]));
  assert.throws(() => dp.parseImport("{bad"), /不是有效的 JSON/);
  assert.deepEqual(JSON.parse(store.getItem("kevrai:favorites")), ["keep"]);
});
