// renderer/__tests__/notification-center.test.js — 通知中心：纯函数 + DOM 集成。
import { test, beforeEach, afterEach } from "node:test";
import assert from "node:assert/strict";
import { setupDom } from "./helpers/dom.js";

// ── 纯函数（静态 import 的模块实例）──
import {
  addItem, withRead, withAllRead, withoutItem, countUnread, relTime,
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
  let r = addItem(addItem(addItem([], {}), {}), {});
  assert.equal(countUnread(r), 3);
  assert.equal(countUnread(withAllRead(r)), 0);
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
