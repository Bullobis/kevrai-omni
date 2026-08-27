// renderer/modules/downloads.js — overlay + subscribe to main progress events.
"use strict";
import { api } from "./api.js";
import { toast } from "./toast.js";
import { state, setState } from "./state.js";

function fmtBytes(n) {
  if (n == null || isNaN(n)) return "?";
  const u = ["B", "KB", "MB", "GB", "TB"];
  let i = 0; let v = n;
  while (v >= 1024 && i < u.length - 1) { v /= 1024; i++; }
  return `${v.toFixed(1)} ${u[i]}`;
}

let overlayEl = null;
let unsub = null;

export function wireDownloads() {
  overlayEl = ensureOverlay();

  // Listen to progress events from the main process.
  unsub?.();
  unsub = api.onDownloadProgress((e) => {
    if (!e || !e.taskId) return;
    state.downloads[e.taskId] = { ...(state.downloads[e.taskId] || {}), ...e };
    setState({ downloads: { ...state.downloads } });
    renderOverlay();
  });

  document.addEventListener("click", (e) => {
    const t = e.target.closest("[data-action=open-downloads]");
    if (t) { e.preventDefault(); showDownloads(); }
  });
}

function ensureOverlay() {
  let el = document.getElementById("download-overlay");
  if (el) return el;
  el = document.createElement("div");
  el.id = "download-overlay";
  el.className = "overlay";
  el.setAttribute("role", "dialog");
  el.setAttribute("aria-modal", "true");
  el.setAttribute("aria-labelledby", "download-overlay-title");
  el.innerHTML = `
    <div class="overlay-card" role="document">
      <header><h2 id="download-overlay-title">下载任务</h2>
        <button class="ghost" data-action="close-overlay" aria-label="关闭下载面板">×</button></header>
      <div id="download-list" class="list" aria-live="polite"></div>
      <p class="hint" style="margin-top:12px">仅展示活跃和最近任务；完成后将保留 1 小时。</p>
    </div>
  `;
  el.addEventListener("click", (e) => {
    if (e.target === el) el.setAttribute("hidden", "");
    if (e.target.closest("[data-action=close-overlay]")) el.setAttribute("hidden", "");
  });
  document.body.appendChild(el);
  el.setAttribute("hidden", ""); // do not auto-show on creation
  return el;
}

export function showDownloads() {
  if (!overlayEl) overlayEl = ensureOverlay();
  overlayEl.removeAttribute("hidden");
  renderOverlay();
}

function renderOverlay() {
  if (!overlayEl) return;
  const list = overlayEl.querySelector("#download-list");
  const tasks = Object.values(state.downloads || {})
    .sort((a, b) => (b.taskId || "").localeCompare(a.taskId || ""));

  if (tasks.length === 0) {
    list.innerHTML = `<div class="hint">当前没有下载任务。</div>`;
    return;
  }

  list.innerHTML = tasks.map((t) => {
    const pct = (t.total > 0) ? Math.min(100, (t.downloaded / t.total) * 100) : 0;
    const filt = (t.filename || t.taskId || "任务");
    const status = t.status || "active";
    const isDone = status === "completed" || status === "failed" || status === "cancelled";
    return `
    <div class="dl-row" data-tid="${t.taskId}">
      <div class="dl-name" title="${escapeHtml(filt)}">${escapeHtml(filt)}</div>
      <div class="dl-progress"><div class="dl-bar" style="width:${pct.toFixed(1)}%"></div></div>
      <div class="dl-meta">
        <span>${fmtBytes(t.downloaded)} / ${fmtBytes(t.total)}</span>
        <span aria-label="状态">${escapeHtml(status)}</span>
      </div>
      <div class="dl-actions">
        ${!isDone
          ? `<button class="danger" data-action="cancel" data-tid="${t.taskId}">取消</button>`
          : `<button class="ghost" data-action="dismiss" data-tid="${t.taskId}">移除</button>`}
      </div>
    </div>`;
  }).join("");

  list.querySelectorAll("button[data-action=cancel]").forEach((b) =>
    b.addEventListener("click", async () => {
      const tid = b.dataset.tid;
      try {
        await api.cancelDownload(tid);
        toast("已请求取消任务 " + tid, { kind: "ok" });
      } catch (_) { /* toast already shown */ }
    })
  );
  list.querySelectorAll("button[data-action=dismiss]").forEach((b) =>
    b.addEventListener("click", () => {
      const tid = b.dataset.tid;
      delete state.downloads[tid];
      setState({ downloads: { ...state.downloads } });
      renderOverlay();
    })
  );
  overlayEl.removeAttribute("hidden");
}

export async function startDownloadFromUrl(url) {
  let parsed;
  try { parsed = new URL(url); } catch (_) { throw new Error("URL 不合法"); }
  if (parsed.protocol !== "https:") throw new Error("仅支持 https 链接");
  const dest = parsed.pathname.split("/").pop() || "download.bin";
  try {
    const r = await api.startDownload({ url, dest_filename: dest });
    if (r && r.taskId) {
      state.downloads[r.taskId] = { taskId: r.taskId, filename: dest, downloaded: 0, total: 0, status: "queued" };
      setState({ downloads: { ...state.downloads } });
      toast("下载已开始：${dest}".replace("${dest}", dest), { kind: "ok" });
      showDownloads();
    }
  } catch (_) { throw new Error("startDownload failed"); }
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (m) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[m]));
}
