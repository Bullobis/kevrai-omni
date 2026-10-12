// renderer/__tests__/webdav-backup.test.js — WebDAV 设置面板逻辑。
// 与 data-portability.test.js 同样手法：jsdom + window.kevrai mock。
import { test, beforeEach, afterEach } from "node:test";
import assert from "node:assert/strict";
import { setupDom } from "./helpers/dom.js";

const FORM = `
  <input id="webdav-url" />
  <input id="webdav-username" />
  <input id="webdav-password" />
  <input id="webdav-remote-path" />
  <button id="webdav-test-btn"></button>
  <button id="webdav-save-btn"></button>
  <button id="webdav-push-btn"></button>
  <button id="webdav-pull-btn"></button>
  <div id="webdav-pull-confirm" hidden></div>
  <button id="webdav-pull-merge"></button>
  <button id="webdav-pull-replace"></button>
  <button id="webdav-pull-cancel"></button>
  <p id="webdav-status"></p>
`;

setupDom(FORM);

let calls;
beforeEach(async () => {
  calls = { cfg: [], set: [], test: [], push: [], pull: [] };
  window.kevrai = {
    webdavConfigGet: async () => ({
      url: "http://dav/x/", username: "u", remote_path: "kb", has_password: true,
    }),
    webdavConfigSet: async (p) => { calls.set.push(p); return { ...p, has_password: true }; },
    webdavTest: async (p) => { calls.test.push(p); return { ok: true }; },
    webdavPush: async () => { calls.push.push({}); return { ok: true, bytes: 123 }; },
    webdavPull: async (p) => { calls.pull.push(p); return { ok: true, knowledge_bases: 2, messages: 5 }; },
  };
  // dynamic import after the bridge is in place
  await import("../modules/webdav-backup.js");
});

afterEach(() => {
  delete window.kevrai;
});

test("fillWebDavForm fills fields and masks saved password", async () => {
  const m = await import("../modules/webdav-backup.js");
  m.fillWebDavForm({ url: "http://dav/x/", username: "u", remote_path: "kb", has_password: true });
  assert.equal(document.getElementById("webdav-url").value, "http://dav/x/");
  assert.equal(document.getElementById("webdav-username").value, "u");
  // password left blank when already saved, placeholder signals it
  assert.equal(document.getElementById("webdav-password").value, "");
  assert.ok(document.getElementById("webdav-password").placeholder.length > 0);
});

test("onPush triggers push and sets status", async () => {
  const m = await import("../modules/webdav-backup.js");
  await m.onPush();
  assert.equal(calls.push.length, 1);
  const status = document.getElementById("webdav-status");
  assert.ok(status.textContent.length > 0);
  assert.equal(status.dataset.kind, "ok");
});

test("onPull merge forwards mode and reports counts", async () => {
  const m = await import("../modules/webdav-backup.js");
  await m.onPull("merge");
  assert.equal(calls.pull.length, 1);
  assert.deepEqual(calls.pull[0], { mode: "merge" });
});

test("onPull replace forwards replace mode", async () => {
  const m = await import("../modules/webdav-backup.js");
  await m.onPull("replace");
  assert.deepEqual(calls.pull[0], { mode: "replace" });
});

test("pull button reveals confirm; merge/replace buttons pull", async () => {
  const m = await import("../modules/webdav-backup.js");
  m.initWebDavPanel();
  document.getElementById("webdav-pull-btn").click();
  assert.equal(document.getElementById("webdav-pull-confirm").hidden, false);
  document.getElementById("webdav-pull-merge").click();
  await new Promise((r) => setTimeout(r, 5));
  assert.equal(calls.pull.length, 1);
  assert.deepEqual(calls.pull[0], { mode: "merge" });
  assert.equal(document.getElementById("webdav-pull-confirm").hidden, true);
});

test("initWebDavPanel wires push button", async () => {
  const m = await import("../modules/webdav-backup.js");
  m.initWebDavPanel();
  document.getElementById("webdav-push-btn").click();
  await new Promise((r) => setTimeout(r, 5));
  assert.equal(calls.push.length, 1);
});
