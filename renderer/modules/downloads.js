// renderer/modules/downloads.js — overlay + subscribe to main progress events.
"use strict";
import { api } from "./api.js";
import { toast } from "./toast.js";
import { unwrap, escapeHtml } from "./net.js";
import { state, setState } from "./state.js";
import { recordFocus, restoreFocus, trapFocus, registerEsc } from "./focus-return.js";
import { overlayOpen, overlayClose } from "./overlay-fx.js";

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
      <header><h2 id="download-overlay-title">下载任务</h2>
        <button class="ghost" data-action="close-overlay" aria-label="关闭下载面板">×</button></header>
      <section class="src-test" aria-labelledby="src-test-title">
        <div class="src-test-head">
          <h3 id="src-test-title">源测速</h3>
          <button class="ghost" data-action="remeasure-sources">一键重新测速</button>
        </div>
        <div id="source-list" class="src-list" aria-live="polite">
          <div class="hint">点击「一键重新测速」以比较各下载源的延迟与速度。</div>
        </div>
      </section>
      <div id="download-list" class="list" aria-live="polite"></div>
      <p class="hint" style="margin-top:12px">仅展示活跃和最近任务；完成后将保留 1 小时。</p>
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
    box.innerHTML = `<div class="hint">暂无测速数据。点击「一键重新测速」开始。</div>`;
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
    const tag = cooling ? "冷却中" : (ok ? "可用" : "不可用");
    const color = srcColor(ok, cooling);
    return `
    <div class="src-row" data-sid="${escapeHtml(r.source_id || r.host || "")}">
      <div class="src-name" title="${escapeHtml(r.url || "")}">
        ${escapeHtml(r.source_type || r.host || "源")} · ${escapeHtml(tag)}
      </div>
      <div class="src-bars">
        <div class="src-bar-wrap"><span class="src-lab">延迟</span>
          <div class="src-bar"><i style="width:${latPct.toFixed(1)}%;background:${color}"></i></div>
          <span class="src-val">${lat.toFixed(0)} ms</span></div>
        <div class="src-bar-wrap"><span class="src-lab">速度</span>
          <div class="src-bar"><i style="width:${spdPct.toFixed(1)}%;background:${color}"></i></div>
          <span class="src-val">${spd.toFixed(1)} MB/s</span></div>
      </div>
      <div class="src-actions">
        <button class="ghost" data-action="lock-source" data-sid="${escapeHtml(r.source_id || "")}">锁定</button>
      </div>
    </div>`;
  }).join("");
  if (lastSkipped.length) {
    box.insertAdjacentHTML("beforeend",
      `<p class="hint">已在冷却期跳过 ${lastSkipped.length} 个源。</p>`);
  }
}

async function remeasureSources() {
  if (!overlayEl) return;
  const box = overlayEl.querySelector("#source-list");
  if (box) box.innerHTML = `<div class="hint">测速中…</div>`;
  try {
    const reg = unwrap(await api.getSourceRegistry());
    const urls = [];
    for (const s of (reg && reg.sources) || []) {
      if (s.enabled && s.origin && /^https?:\/\//.test(s.origin)) {
        urls.push(s.origin);
      }
    }
    if (!urls.length) {
      if (box) box.innerHTML = `<div class="hint">没有已启用的源可测速。</div>`;
      return;
    }
    const r = unwrap(await api.measureSources({ urls, force: true }));
    lastRanking = (r && r.ranking) || [];
    lastSkipped = (r && r.skipped) || [];
    renderSources();
  } catch (_) {
    if (box) box.innerHTML = `<div class="hint">测速失败，请稍后重试。</div>`;
  }
}

async function lockSource(sourceId) {
  try {
    await api.lockSource(sourceId || "");
    toast(sourceId ? `已锁定源：${sourceId}` : "已取消源锁定", { kind: "ok" });
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
      toast(`下载已开始：${dest}`, { kind: "ok" });
      showDownloads();
    }
  } catch (_) { throw new Error("startDownload failed"); }
}
