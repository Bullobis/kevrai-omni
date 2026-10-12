// renderer/modules/webdav-backup.js — WebDAV 云备份同步面板。
//
// 与「导出/导入数据」并列：把后端那份磁盘备份（知识库 / Agent 会话 / 大脑）
// 推送到自托管 WebDAV（如 Nextcloud），或从远端拉回。凭据只保存在后端
// webdav.json，GET 配置不回传密码，UI 仅以占位符提示「已保存密码」。
"use strict";
import { api } from "./api.js";
import { toast } from "./toast.js";
import { t } from "./i18n.js";

const $ = (s) => document.querySelector(s);

// sidecarFetch resolves {status, body}; callers unwrap `.body` (same as
// asr.js / embeddings.js). Tests mock the bridge with a bare object, so fall
// back to the value itself when there is no `.body`.
function unwrap(r) {
  return (r && typeof r === "object" && "body" in r) ? r.body : r;
}

// 读取表单当前值。密码留空表示「沿用已保存密码」。
function readForm() {
  return {
    url: $("#webdav-url")?.value.trim() || "",
    username: $("#webdav-username")?.value.trim() || "",
    password: $("#webdav-password")?.value || "",
    remote_path: $("#webdav-remote-path")?.value.trim() || "",
  };
}

// 把后端配置填回表单。has_password=true 时密码框留空、占位符提示已保存。
export function fillWebDavForm(cfg) {
  if (!cfg || typeof cfg !== "object") return;
  const set = (id, v) => { const el = $(id); if (el) el.value = v || ""; };
  set("#webdav-url", cfg.url);
  set("#webdav-username", cfg.username);
  set("#webdav-remote-path", cfg.remote_path);
  const pw = $("#webdav-password");
  if (pw) {
    pw.value = "";
    pw.placeholder = cfg.has_password
      ? t("webdav.passwordSaved") : t("webdav.passwordPh");
  }
}

export function setWebDavStatus(msg, kind) {
  const el = $("#webdav-status");
  if (!el) return;
  el.textContent = msg || "";
  el.dataset.kind = kind || "";
}

// 加载配置并填表。openSettings 每次调用时执行。
export async function loadWebDavConfig() {
  try {
    const cfg = unwrap(await api.webdavConfigGet());
    fillWebDavForm(cfg);
    setWebDavStatus(cfg && cfg.url ? t("webdav.loaded") : "", "");
  } catch (_) { /* toast 已由 api.wrap 弹出 */ }
}

export async function onTest() {
  setWebDavStatus(t("webdav.testing"), "");
  try {
    await api.webdavTest(readForm());
    setWebDavStatus(t("webdav.testOk"), "ok");
    toast(t("webdav.testOk"), { kind: "ok" });
  } catch (e) {
    const msg = (e && e.message) ? e.message : t("webdav.testFailed");
    setWebDavStatus(msg, "err");
  }
}

export async function onSave() {
  try {
    const cfg = unwrap(await api.webdavConfigSet(readForm()));
    fillWebDavForm(cfg);
    setWebDavStatus(t("webdav.saved"), "ok");
    toast(t("webdav.saved"), { kind: "ok" });
  } catch (_) { /* toast 已弹 */ }
}

export async function onPush() {
  setWebDavStatus(t("webdav.pushing"), "");
  try {
    const r = unwrap(await api.webdavPush());
    const bytes = (r && typeof r.bytes === "number") ? r.bytes : 0;
    setWebDavStatus(t("webdav.pushOk", { bytes }), "ok");
    toast(t("webdav.pushOk", { bytes }), { kind: "ok" });
  } catch (e) {
    const msg = (e && e.message) ? e.message : t("webdav.pushFailed");
    setWebDavStatus(msg, "err");
  }
}

// 拉取需要先让用户选「合并 / 替换」。
export function showPullConfirm(show) {
  const el = $("#webdav-pull-confirm");
  if (el) el.hidden = !show;
}

export async function onPull(mode) {
  showPullConfirm(false);
  setWebDavStatus(t("webdav.pulling"), "");
  try {
    const r = unwrap(await api.webdavPull({ mode }));
    const kbs = (r && typeof r.knowledge_bases === "number") ? r.knowledge_bases : 0;
    const msgs = (r && typeof r.messages === "number") ? r.messages : 0;
    setWebDavStatus(t("webdav.pullOk", { kbs, msgs }), "ok");
    toast(t("webdav.pullOk", { kbs, msgs }), { kind: "ok" });
  } catch (e) {
    const msg = (e && e.message) ? e.message : t("webdav.pullFailed");
    setWebDavStatus(msg, "err");
  }
}

let _wired = false;

export function initWebDavPanel() {
  // Buttons live in the settings overlay, which is always in the DOM — wire the
  // listeners once; every openSettings() call just re-loads the form values.
  if (_wired) { loadWebDavConfig().catch(() => {}); return; }
  _wired = true;
  $("#webdav-test-btn")?.addEventListener("click", () => onTest().catch(() => {}));
  $("#webdav-save-btn")?.addEventListener("click", () => onSave().catch(() => {}));
  $("#webdav-push-btn")?.addEventListener("click", () => onPush().catch(() => {}));
  $("#webdav-pull-btn")?.addEventListener("click", () => showPullConfirm(true));
  $("#webdav-pull-merge")?.addEventListener("click", () => onPull("merge").catch(() => {}));
  $("#webdav-pull-replace")?.addEventListener("click", () => onPull("replace").catch(() => {}));
  $("#webdav-pull-cancel")?.addEventListener("click", () => showPullConfirm(false));
  loadWebDavConfig().catch(() => {});
}
