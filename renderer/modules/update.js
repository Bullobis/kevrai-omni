// renderer/modules/update.js — auto-update UI (electron-updater ↔ GitHub releases).
//
// Flow: header "check updates" button -> open overlay -> check -> if available
// show version + notes + [下载更新] -> download with progress bar ->
// [重启并安装] -> quitAndInstall. All main-process results are non-throwing;
// errors arrive as {error} and are shown as toasts. In dev (unpackaged) the
// main process returns {dev:true} and we say so instead of pretending.
"use strict";

import { api } from "./api.js";
import { toast } from "./toast.js";
import { recordFocus, restoreFocus, trapFocus, registerEsc } from "./focus-return.js";
import { overlayOpen, overlayClose } from "./overlay-fx.js";

const $ = (s) => document.querySelector(s);

// a11y — 打开更新面板前的触发元素，关闭时归还焦点。
let savedTrigger = null;

function openOverlay() {
  const el = $("#update-overlay");
  if (!el) return;
  savedTrigger = recordFocus();
  overlayOpen(el);
  trapFocus(el);
}
function closeOverlay() {
  const el = $("#update-overlay");
  if (!el) return;
  restoreFocus(savedTrigger);   // 焦点立即归还
  savedTrigger = null;
  overlayClose(el);
}

function setStatus(text) {
  const s = $("#update-status");
  if (s) s.textContent = text;
}

function showProgress(show) {
  const w = $("#update-progress-wrap");
  if (w) w.hidden = !show;
}
function setProgress(percent, transferred, total) {
  const fill = $("#update-progress-fill");
  const txt = $("#update-progress-text");
  const p = Math.max(0, Math.min(100, Number(percent) || 0));
  if (fill) fill.style.width = p + "%";
  if (txt) {
    let extra = "";
    if (total > 0) {
      const mb = (n) => (n / 1048576).toFixed(1) + " MB";
      extra = `  (${mb(transferred)} / ${mb(total)})`;
    }
    txt.textContent = p + "%" + extra;
  }
}

function showInfo(show) {
  const i = $("#update-info");
  if (i) i.hidden = !show;
}

function setDownloadButton(show) {
  const b = $("#btn-update-download");
  if (b) b.hidden = !show;
}
function setInstallButton(show) {
  const b = $("#btn-update-install");
  if (b) b.hidden = !show;
}

async function runCheck() {
  openOverlay();
  showProgress(false);
  showInfo(false);
  setDownloadButton(false);
  setInstallButton(false);
  setStatus("正在检查更新…");

  let r;
  try {
    r = await api.checkUpdates();
  } catch (e) {
    setStatus("检查失败");
    toast("检查更新失败：" + (e?.message || e), { kind: "err" });
    return;
  }

  if (r?.dev) {
    setStatus("开发模式下不检查更新（打包后可用）");
    return;
  }
  if (r?.busy) {
    setStatus("正在检查，请稍候…");
    return;
  }
  if (r?.error) {
    setStatus("检查失败");
    toast("检查更新失败：" + r.error, { kind: "err" });
    return;
  }
  if (r?.updateAvailable) {
    setStatus("发现新版本");
    showInfo(true);
    const cur = $("#update-current");
    const lat = $("#update-latest");
    if (cur) cur.textContent = r.currentVersion || "—";
    if (lat) lat.textContent = r.version || "—";
    if (r.releaseNotes) {
      const wrap = $("#update-notes-wrap");
      const notes = $("#update-notes");
      if (wrap) wrap.hidden = false;
      // textContent: release notes come from a remote GitHub release and may
      // contain HTML/markup — never inject as innerHTML.
      if (notes) notes.textContent = String(r.releaseNotes);
    }
    setDownloadButton(true);
  } else {
    setStatus(`已是最新版本 (v${r?.currentVersion || "?"})`);
  }
}

async function runDownload() {
  setDownloadButton(false);
  showProgress(true);
  setProgress(0, 0, 0);
  setStatus("正在下载更新…");

  // Progress + downloaded + error events are pushed by the main process.
  const offProgress = api.onUpdateProgress((p) => {
    setProgress(p?.percent || 0, p?.transferred || 0, p?.total || 0);
  });
  const offDownloaded = api.onUpdateDownloaded(() => {
    offProgress();
    offDownloaded();
    offError();
    setProgress(100, 1, 1);
    setStatus("更新已下载，重启后安装");
    setInstallButton(true);
  });
  const offError = api.onUpdateError((e) => {
    offProgress();
    offDownloaded();
    offError();
    setStatus("下载失败");
    toast("下载更新失败：" + (e?.message || "未知错误"), { kind: "err" });
    setDownloadButton(true);
  });

  try {
    const r = await api.downloadUpdate();
    if (r && r.ok === false) {
      offProgress(); offDownloaded(); offError();
      setStatus("下载失败");
      toast("下载更新失败：" + (r.error || "未知错误"), { kind: "err" });
      setDownloadButton(true);
    }
    // On success the "update-downloaded" event will flip the UI; if for some
    // reason it never fires, leave the progress UI visible (timeout-free by
    // design; user can close and retry).
  } catch (e) {
    offProgress(); offDownloaded(); offError();
    setStatus("下载失败");
    toast("下载更新失败：" + (e?.message || e), { kind: "err" });
    setDownloadButton(true);
  }
}

async function runInstall() {
  try {
    await api.installUpdate();
    // quitAndInstall closes the app; if we're still here, show a note.
    setStatus("正在重启安装…");
  } catch (e) {
    toast("安装更新失败：" + (e?.message || e), { kind: "err" });
  }
}

export function wireUpdate() {
  const btn = $("[data-action=check-updates]");
  if (btn) {
    btn.addEventListener("click", (e) => { e.preventDefault(); runCheck(); });
  }
  const dl = $("#btn-update-download");
  if (dl) dl.addEventListener("click", () => runDownload());
  const ins = $("#btn-update-install");
  if (ins) ins.addEventListener("click", () => runInstall());

  // Close on any [data-action=close-update] (header × + footer 关闭) and on
  // backdrop click.
  document.querySelectorAll("[data-action=close-update]").forEach((b) => {
    b.addEventListener("click", () => closeOverlay());
  });
  const overlay = $("#update-overlay");
  if (overlay) {
    overlay.addEventListener("click", (e) => {
      if (e.target === overlay) closeOverlay();
    });
    // Esc 关闭（全局优先级栈最下层之一）。
    registerEsc({
      order: 60,
      root: overlay,
      isOpen: () => !overlay.hasAttribute("hidden"),
      close: closeOverlay,
    });
  }
}
