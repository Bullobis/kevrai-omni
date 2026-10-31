// renderer/__tests__/notification-center.test.js — 通知中心：纯函数 + DOM 集成。
import { test, beforeEach, afterEach } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { setupDom } from "./helpers/dom.js";
import { registerDict } from "../modules/i18n.js";

// 加载真实 zh-CN 字典，使 notification-center 的 relTime t() 返回真实文案。
const _here = dirname(fileURLToPath(import.meta.url));
registerDict("zh-CN", JSON.parse(readFileSync(join(_here, "..", "locales", "zh-CN.json"), "utf8")));

// ── 纯函数（静态 import 的模块实例）──
import {
  addItem, withRead, withAllRead, withoutItem, countUnread, relTime, signOf,
} from "../modules/notification-center.js";

test("addItem prepends, assigns id/ts, caps to max", () => {
  const r = addItem([], { kind: "ok", title: "T", body: "B" });
  assert.equal(r.length, 1);
  assert.ok(r[0].id);
  assert.equal(r[0].title, "T");
  assert.equal(r[0].read, false);
  assert.ok(r[0].ts > 0);
  let many = [];
  for (let i = 0; i < 5; i++) many = addItem(many, { title: "x" + i }, 3);
  assert.equal(many.length, 3);
  assert.equal(many[0].title, "x4"); // newest first
});

test("withRead marks one; withAllRead marks all", () => {
  let r = addItem(addItem([], { title: "a" }), { title: "b" });
  const one = withRead(r, r[0].id);
  assert.equal(one[0].read, true);
  assert.equal(one[1].read, false);
  assert.equal(countUnread(withAllRead(r)), 0);
});

test("withoutItem removes by id", () => {
  let r = addItem(addItem([], { title: "a" }), { title: "b" });
  assert.equal(withoutItem(r, r[0].id).length, 1);
});

test("countUnread counts only unread", () => {
  let r = addItem(addItem(addItem([], { title: "a" }), { title: "b" }), { title: "c" });
  assert.equal(countUnread(r), 3);
  assert.equal(countUnread(withAllRead(r)), 0);
});

// ── 去重合并（coalesce）──────────────────────────────────────────────────────
test("signOf ignores whitespace differences but not content", () => {
  assert.equal(
    signOf({ kind: "warn", title: "来源不可达", body: "  ModelScope   unreachable " }),
    signOf({ kind: "warn", title: "来源不可达", body: "ModelScope unreachable" }),
  );
  // kind 或正文不同 → 不同签名
  assert.notEqual(
    signOf({ kind: "warn", title: "t", body: "a" }),
    signOf({ kind: "err", title: "t", body: "a" }),
  );
  assert.notEqual(
    signOf({ kind: "warn", title: "t", body: "a" }),
    signOf({ kind: "warn", title: "t", body: "b" }),
  );
});

test("duplicate notification coalesces: count+1, moved to front, refreshed ts, re-unread", () => {
  let r = addItem([], { kind: "warn", title: "来源不可达", body: "ModelScope", ts: 1000 });
  r = addItem(r, { kind: "ok", title: "保存成功", body: "已写入", ts: 2000 });
  assert.equal(r.length, 2);
  // 再发一次相同的“来源不可达”
  r = addItem(r, { kind: "warn", title: "来源不可达", body: "ModelScope", ts: 3000 });
  assert.equal(r.length, 2, "合并后仍为两条");
  assert.equal(r[0].title, "来源不可达", "合并条目置顶");
  assert.equal(r[0].count, 2);
  assert.equal(r[0].ts, 3000, "时间刷新");
  assert.equal(r[0].read, false);
  assert.equal(r[1].title, "保存成功");
});

test("read duplicate re-arrival resurfaces as unread but counts once in badge", () => {
  let r = addItem([], { kind: "warn", title: "来源不可达", body: "ModelScope" });
  r = withAllRead(r);
  assert.equal(countUnread(r), 0);
  // 已读后再次到达同一条 → 合并 + 重新未读，但只占一条未读
  r = addItem(r, { kind: "warn", title: "来源不可达", body: "ModelScope" });
  assert.equal(r.length, 1);
  assert.equal(r[0].count, 2);
  assert.equal(countUnread(r), 1);
});

test("distinct notifications are never merged", () => {
  let r = addItem([], { kind: "ok", title: "A", body: "x" });
  r = addItem(r, { kind: "err", title: "B", body: "y" });
  r = addItem(r, { kind: "info", title: "C", body: "z" });
  assert.equal(r.length, 3);
  assert.ok(r.every((x) => x.count === 1));
});

test("cap drops oldest unique entries; duplicates never grow list", () => {
  let r = [];
  for (let i = 0; i < 5; i++) r = addItem(r, { title: "u" + i }, 3);
  assert.equal(r.length, 3);
  assert.equal(r[0].title, "u4");
  assert.equal(r[2].title, "u2", "最旧的 u0/u1 被裁剪");
  // 对最前面那条重复 100 次 → 列表长度不增，仍受上限保护
  for (let i = 0; i < 100; i++) r = addItem(r, { title: "u4" }, 3);
  assert.equal(r.length, 3);
  assert.equal(r[0].count, 101);
});

test("coalesced unread counts as a single badge unit", () => {
  let r = [];
  for (let i = 0; i < 50; i++) r = addItem(r, { kind: "warn", title: "来源不可达", body: "ModelScope" });
  assert.equal(r.length, 1, "50 次重复只占一条");
  assert.equal(r[0].count, 50);
  assert.equal(countUnread(r), 1, "角标只计 1 条未读而非 50");
});

test("relTime buckets", () => {
  const now = 1_000_000_000_000;
  assert.equal(relTime(now, now), "刚刚");
  assert.equal(relTime(now - 30_000, now), "30 秒前");
  assert.equal(relTime(now - 5 * 60_000, now), "5 分钟前");
  assert.equal(relTime(now - 3 * 3600_000, now), "3 小时前");
  assert.ok(relTime(now - 2 * 86400_000, now).includes("月"));
});

// ── DOM 集成（每例新鲜模块 + localStorage shim）──
let nc;
beforeEach(async () => {
  setupDom('<button id="notif-bell" data-action="open-notifications">b'
    + '<span id="notif-badge" hidden></span></button>');
  const map = new Map();
  globalThis.localStorage = {
    getItem: (k) => (map.has(k) ? map.get(k) : null),
    setItem: (k, v) => { map.set(k, String(v)); },
    removeItem: (k) => { map.delete(k); },
    clear: () => map.clear(),
  };
  nc = await import(`../modules/notification-center.js?x=${Math.random()}`);
});
afterEach(() => { delete globalThis.localStorage; });

test("init builds panel; badge hidden when empty", () => {
  nc.initNotificationCenter();
  assert.ok(document.querySelector(".notif-panel"));
  assert.equal(document.getElementById("notif-badge").hidden, true);
});

test("recordNotification updates badge + list; opening marks all read", () => {
  nc.initNotificationCenter();
  nc.recordNotification({ kind: "ok", title: "保存成功", body: "已写入" });
  assert.equal(document.getElementById("notif-badge").hidden, false);
  assert.equal(document.getElementById("notif-badge").textContent, "1");
  assert.ok(document.querySelector(".notif-row"));
  assert.equal(document.querySelector(".notif-text").textContent, "已写入");
  nc.togglePanel();
  assert.equal(document.getElementById("notif-badge").hidden, true);
  assert.ok(document.querySelector(".notif-panel").classList.contains("open"));
});

test("duplicate toasts coalesce into one row with ×N badge; badge stays 1", () => {
  nc.initNotificationCenter();
  for (let i = 0; i < 5; i++) {
    nc.recordNotification({ kind: "warn", title: "来源不可达", body: "ModelScope" });
  }
  const rows = document.querySelectorAll(".notif-row");
  assert.equal(rows.length, 1, "重复警告合并为一行");
  const pill = rows[0].querySelector(".notif-count");
  assert.ok(pill);
  assert.equal(pill.textContent, "×5");
  // 角标只计 1 条未读
  assert.equal(document.getElementById("notif-badge").textContent, "1");
  assert.equal(nc.getNotifications().length, 1);
});

test("different notifications render as separate rows", () => {
  nc.initNotificationCenter();
  nc.recordNotification({ kind: "ok", title: "保存成功", body: "已写入" });
  nc.recordNotification({ kind: "err", title: "失败", body: "网络错误" });
  assert.equal(document.querySelectorAll(".notif-row").length, 2);
  assert.equal(document.getElementById("notif-badge").textContent, "2");
});

test("clear button empties list", () => {
  nc.initNotificationCenter();
  nc.recordNotification({ title: "a" });
  nc.recordNotification({ title: "b" });
  document.getElementById("notif-clear").click();
  assert.equal(nc.getNotifications().length, 0);
  assert.ok(document.querySelector(".notif-empty"));
});

test("delete button removes one row", () => {
  nc.initNotificationCenter();
  nc.recordNotification({ title: "a" });
  nc.recordNotification({ title: "b" });
  const rows = document.querySelectorAll(".notif-row");
  rows[1].querySelector(".notif-x").click();
  assert.equal(nc.getNotifications().length, 1);
});
