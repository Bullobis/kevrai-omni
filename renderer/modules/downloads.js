// renderer/modules/downloads.js — overlay + subscribe to main progress events.
"use strict";
import { api } from "./api.js";
import { toast } from "./toast.js";
import { unwrap, escapeHtml } from "./net.js";
import { state, setState } from "./state.js";
import { recordFocus, restoreFocus, trapFocus, registerEsc } from "./focus-return.js";
import { overlayOpen, overlayClose } from "./overlay-fx.js";
import { t as translate } from "./i18n.js";

function fmtBytes(n) {
  if (n == null || isNaN(n)) return "?";
  const u = ["B", "KB", "MB", "GB", "TB"];
  let i = 0; let v = n;
  while (v >= 1024 && i < u.length - 1) { v /= 1024; i++; }
  return `${v.toFixed(1)} ${u[i]}`;
}

let overlayEl = null;
let unsub = null;
// v2.8.1 — source speed-test panel state.
let lastRanking = [];
let lastSkipped = [];
// a11y — 打开面板时的触发元素，关闭时归还焦点。
let savedTrigger = null;

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

  // Esc 关闭：优先级栈中位于设置之下。
  registerEsc({
    order: 70,
    root: overlayEl,
    isOpen: () => !!overlayEl && !overlayEl.hasAttribute("hidden"),
    close: closeDownloads,
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
      <header><h2 id="download-overlay-title">${translate("downloads.title")}</h2>
        <button class="ghost" data-action="close-overlay" aria-label="${translate("downloads.closePanel")}">×</button></header>
      <section class="src-test" aria-labelledby="src-test-title">
        <div class="src-test-head">
          <h3 id="src-test-title">${translate("downloads.sourceTest")}</h3>
          <button class="ghost" data-action="remeasure-sources">${translate("downloads.remeasure")}</button>
        </div>
        <div id="source-list" class="src-list" aria-live="polite">
          <div class="hint">${translate("downloads.sourceTestHint")}</div>
        </div>
      </section>
      <div id="download-list" class="list" aria-live="polite"></div>
      <p class="hint" style="margin-top:12px">${translate("downloads.activeNote")}</p>
    </div>
  `;
  el.addEventListener("click", (e) => {
    if (e.target === el) closeDownloads();
    if (e.target.closest("[data-action=close-overlay]")) closeDownloads();
    const re = e.target.closest("[data-action=remeasure-sources]");
    if (re) { e.preventDefault(); remeasureSources(); }
    const lock = e.target.closest("[data-action=lock-source]");
    if (lock) { e.preventDefault(); lockSource(lock.dataset.sid || ""); }
    const unlock = e.target.closest("[data-action=unlock-source]");
    if (unlock) { e.preventDefault(); lockSource(""); }
  });
  document.body.appendChild(el);
  el.setAttribute("hidden", ""); // do not auto-show on creation
  return el;
}

// v2.8.1 — source speed-test panel (design T04). Framework-free: plain DOM +
// CSS bars. Data comes from /api/sources/measure (ranked) and
// /api/sources/registry (enabled/cooling state).

function srcColor(ok, cooling) {
  if (cooling) return "var(--warn, #d08700)";
  if (!ok) return "var(--err, #d33)";
  return "var(--ok, #2e9e4f)";
}

function renderSources() {
  if (!overlayEl) return;
  const box = overlayEl.querySelector("#source-list");
  if (!box) return;
  if (!lastRanking.length) {
    box.innerHTML = `<div class="hint">${translate("downloads.noSourceData")}</div>`;
    return;
  }
  const maxLat = Math.max(
    ...lastRanking.map((r) => (r && r.latency_ms) || 0), 1);
  const maxSpd = Math.max(
    ...lastRanking.map((r) => (r && r.speed_mbps) || 0), 1);
  box.innerHTML = lastRanking.map((r) => {
    const ok = !!(r && r.ok);
    const cooling = !!(r && r.cooling);
    const lat = (r && r.latency_ms) || 0;
    const spd = (r && r.speed_mbps) || 0;
    const latPct = Math.min(100, (lat / maxLat) * 100);
    const spdPct = Math.min(100, (spd / maxSpd) * 100);
    const tag = cooling ? translate("downloads.cooling") : (ok ? translate("downloads.available") : translate("downloads.unavailable"));
    const color = srcColor(ok, cooling);
    return `
    <div class="src-row" data-sid="${escapeHtml(r.source_id || r.host || "")}">
      <div class="src-name" title="${escapeHtml(r.url || "")}">
        ${escapeHtml(r.source_type || r.host || "源")} · ${escapeHtml(tag)}
      </div>
      <div class="src-bars">
        <div class="src-bar-wrap"><span class="src-lab">${translate("downloads.latency")}</span>
          <div class="src-bar"><i style="width:${latPct.toFixed(1)}%;background:${color}"></i></div>
          <span class="src-val">${lat.toFixed(0)} ms</span></div>
        <div class="src-bar-wrap"><span class="src-lab">${translate("downloads.speed")}</span>
          <div class="src-bar"><i style="width:${spdPct.toFixed(1)}%;background:${color}"></i></div>
          <span class="src-val">${spd.toFixed(1)} MB/s</span></div>
      </div>
      <div class="src-actions">
        <button class="ghost" data-action="lock-source" data-sid="${escapeHtml(r.source_id || "")}">${translate("downloads.lock")}</button>
      </div>
    </div>`;
  }).join("");
  if (lastSkipped.length) {
    box.insertAdjacentHTML("beforeend",
      `<p class="hint">${translate("downloads.skippedCooling", { n: lastSkipped.length })}</p>`);
  }
}

async function remeasureSources() {
  if (!overlayEl) return;
  const box = overlayEl.querySelector("#source-list");
  if (box) box.innerHTML = `<div class="hint">${translate("downloads.measuring")}</div>`;
  try {
    const reg = unwrap(await api.getSourceRegistry());
    const urls = [];
    for (const s of (reg && reg.sources) || []) {
      if (s.enabled && s.origin && /^https?:\/\//.test(s.origin)) {
        urls.push(s.origin);
      }
    }
    if (!urls.length) {
      if (box) box.innerHTML = `<div class="hint">${translate("downloads.noEnabledSource")}</div>`;
      return;
    }
    const r = unwrap(await api.measureSources({ urls, force: true }));
    lastRanking = (r && r.ranking) || [];
    lastSkipped = (r && r.skipped) || [];
    renderSources();
  } catch (_) {
    if (box) box.innerHTML = `<div class="hint">${translate("downloads.measureFailed")}</div>`;
  }
}

async function lockSource(sourceId) {
  try {
    await api.lockSource(sourceId || "");
    toast(sourceId ? translate("downloads.locked", { id: sourceId }) : translate("downloads.unlocked"), { kind: "ok" });
  } catch (_) { /* toast already shown */ }
}

export function showDownloads() {
  if (!overlayEl) overlayEl = ensureOverlay();
  savedTrigger = recordFocus();
  overlayOpen(overlayEl);
  renderOverlay();
  trapFocus(overlayEl);
}

export function closeDownloads() {
  if (!overlayEl) return;
  restoreFocus(savedTrigger);   // 焦点立即归还
  savedTrigger = null;
  overlayClose(overlayEl);
}

function renderOverlay() {
  if (!overlayEl) return;
  renderSources();
  const list = overlayEl.querySelector("#download-list");
  const tasks = Object.values(state.downloads || {})
    .sort((a, b) => (b.taskId || "").localeCompare(a.taskId || ""));

  if (tasks.length === 0) {
    list.innerHTML = `<div class="hint">${translate("downloads.noTasks")}</div>`;
    return;
  }

  list.innerHTML = tasks.map((task) => {
    const pct = (task.total > 0) ? Math.min(100, (task.downloaded / task.total) * 100) : 0;
    const filt = (task.filename || task.taskId || translate("downloads.taskFallback"));
    const status = task.status || "active";
    const isDone = status === "completed" || status === "failed" || status === "cancelled";
    return `
    <div class="dl-row" data-tid="${task.taskId}">
      <div class="dl-name" title="${escapeHtml(filt)}">${escapeHtml(filt)}</div>
      <div class="dl-progress"><div class="dl-bar" style="width:${pct.toFixed(1)}%"></div></div>
      <div class="dl-meta">
        <span>${fmtBytes(task.downloaded)} / ${fmtBytes(task.total)}</span>
        <span aria-label="${translate("downloads.statusAria")}">${escapeHtml(status)}</span>
      </div>
      <div class="dl-actions">
        ${!isDone
          ? `<button class="danger" data-action="cancel" data-tid="${task.taskId}">${translate("downloads.cancel")}</button>`
          : `<button class="ghost" data-action="dismiss" data-tid="${task.taskId}">${translate("downloads.dismiss")}</button>`}
      </div>
    </div>`;
  }).join("");

  list.querySelectorAll("button[data-action=cancel]").forEach((b) =>
    b.addEventListener("click", async () => {
      const tid = b.dataset.tid;
      try {
        await api.cancelDownload(tid);
        toast(translate("downloads.cancelRequested", { id: tid }), { kind: "ok" });
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

export async function startDownloadFromUrl(url, opts) {
  let parsed;
  try { parsed = new URL(url); } catch (_) { throw new Error("URL 不合法"); }
  if (parsed.protocol !== "https:") throw new Error("仅支持 https 链接");
  const dest = parsed.pathname.split("/").pop() || "download.bin";
  try {
    // v2.4.1 — gated repos (e.g. LTX-2.5): sidecar attaches the user's HF
    // token and returns a friendly 422 when it's missing.
    const body = { url, dest_filename: dest };
    if (opts && opts.gated) body.gated = true;
    const r = unwrap(await api.startDownload(body));
    // 后端字段名是 task_id（python/app/main.py:1396），此处原先读 r.taskId
    // 恒为 undefined → 下载开始后进度条不出现。两者都兼容。
    const taskId = r && (r.task_id || r.taskId);
    if (taskId) {
      state.downloads[taskId] = { taskId, filename: dest, downloaded: 0, total: 0, status: "queued" };
      setState({ downloads: { ...state.downloads } });
      toast(translate("downloads.downloadStarted", { file: dest }), { kind: "ok" });
      showDownloads();
    }
  } catch (_) { throw new Error("startDownload failed"); }
}
