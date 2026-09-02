"use strict";
/**
 * Kevrai Omni — renderer entry.
 *
 * Talks only to `window.kevrai` (preload bridge).
 * Coordinates the modules under renderer/modules/.
 */

import { api } from "./modules/api.js";
import { toast } from "./modules/toast.js";
import { renderEnvironmentsPage } from "./modules/environments.js";
import { renderHardwarePage } from "./modules/hardware.js";
import { renderMnnPage } from "./modules/mnn.js";
import { renderDramaPage } from "./modules/drama.js";
import { state, setState } from "./modules/state.js";
import { applyTheme, wireThemeListener } from "./modules/theme.js";
import { initModels, renderModelGrid, populateCategoryFilter,
         wireModelGrid, getVgrid } from "./modules/models.js";
import { initSearch, runSearch } from "./modules/search.js";
import { initLtx } from "./modules/ltx.js";
import { renderEngines, wireEngineUpdates } from "./modules/engines.js";
import { wireSettings, openSettings, closeSettings } from "./modules/settings.js";
import { wireDownloads, showDownloads } from "./modules/downloads.js";
import { wireDragDrop } from "./modules/dragdrop.js";
import { wireOnboarding } from "./modules/onboarding.js";

const $  = (s) => document.querySelector(s);
const $$ = (s) => Array.from(document.querySelectorAll(s));

let healthTimer = null;

// Logo fallback: if the brand image is missing, show the text glyph instead.
// (Wired here instead of an inline onerror="" attribute — inline event
// handlers are blocked by the CSP `script-src 'self'` policy.)
{
  const img = document.getElementById("brand-logo");
  const fallback = document.getElementById("logo-fallback");
  if (img && fallback) {
    img.addEventListener("error", () => {
      img.hidden = true;
      fallback.hidden = false;
    }, { once: true });
  }
}

export async function loadAll() {
  try {
    const [settings, cats, ms, gg, ens, locs, h] = await Promise.all([
      api.getSettings(),
      api.categories(),
      api.models({}),
      api.ggufRepos(),
      api.engines(),
      api.localModels(),
      api.health().catch(() => ({ body: { version: "?", app_root: "unreachable" } })),
    ]);
    setState({
      settings: settings || {},
      categories: cats?.body?.categories || cats?.categories || [],
      models:     ms?.body?.models     || ms?.models     || [],
      ggufRepos:  gg?.body?.repos      || gg?.repos      || [],
      engines:    ens?.body?.engines   || ens?.engines   || [],
      local:      locs?.body?.local    || locs?.local    || [],
    });
    populateCategoryFilter();
    renderModelGrid();
    renderEngines();
    renderLocal();
    renderGGUF();
    applyTheme();
    setHealthOk(`sidecar v${h?.body?.version || "?"}`);
    // v2.4.0 — drive the market grid through the super search (facets, sort).
    runSearch({ resetPage: true }).catch(() => {});
  } catch (e) {
    setHealthErr(String(e?.message || e));
  }
}

function setHealthOk(msg) {
  const dot = $("#health-dot"), text = $("#health-text");
  dot.className = "dot ok"; text.textContent = msg;
  // Keep the sidebar version line in sync with the sidecar version.
  const v = /v(\d+\.\d+\.\d+)/.exec(msg || "");
  const vline = $("#version-line");
  if (vline) vline.textContent = v ? `v${v[1]}` : "v?";
}
function setHealthErr(msg) {
  const dot = $("#health-dot"), text = $("#health-text");
  dot.className = "dot err"; text.textContent = "sidecar: ✗ " + msg;
}

function renderGGUF() {
  const el = $("#gguf-repos");
  if (!el) return;
  const repos = state.ggufRepos || [];
  if (!repos.length) { el.innerHTML = `<div class="hint">GGUF 仓库列表为空（sidecar 未返回数据）。</div>`; return; }
  el.innerHTML = repos.map((r) => {
    if (r.error) {
      return `
    <div class="row">
      <div class="grow">
        <div class="name">${escapeHtml(r.name || r.owner_repo)}</div>
        <div class="sub">${escapeHtml(r.owner_repo || "")}</div>
        <div class="sub">无法连接仓库：${escapeHtml(r.error)}</div>
      </div>
      <span class="pill">离线</span>
    </div>`;
    }
    const files = (r.files || []).slice(0, 8).map((f) => `
      <div class="sub">· ${escapeHtml(f.path)} (${((f.size || 0) / 1e9).toFixed(2)} GB)</div>`).join("");
    const more = (r.files || []).length > 8
      ? `<div class="sub mut">… 共 ${r.count || (r.files || []).length} 个文件</div>` : "";
    return `
    <div class="row">
      <div class="grow">
        <div class="name">${escapeHtml(r.name || r.owner_repo)}</div>
        <div class="sub">${escapeHtml(r.owner_repo || "")}</div>
        ${files}${more}
      </div>
      <span class="pill ok">${r.count || (r.files || []).length} 个文件</span>
    </div>`;
  }).join("");
}

function renderLocal() {
  const el = $("#local-list");
  if (!el) return;
  const list = state.local || [];
  if (!list.length) { el.innerHTML = `<div class="hint">还没有本地模型，拖拽文件到窗口或使用下方按钮导入。</div>`; return; }
  el.innerHTML = list.map((m) => `
    <div class="row">
      <div class="grow">
        <div class="name">${escapeHtml(m.name)}</div>
        <div class="sub" title="${escapeHtml(m.path || "")}">${escapeHtml(m.path || "")}</div>
        <div class="sub">${((m.size_bytes || 0) / 1e9).toFixed(2)} GB</div>
      </div>
      <span class="pill ok">本地</span>
      ${m.path ? `<button class="secondary small" data-action="reveal-local"
              data-path="${escapeHtml(m.path)}" aria-label="在文件管理器中定位">定位</button>` : ""}
    </div>
  `).join("");
}

function escapeHtml(s) {
  return String(s || "").replace(/[&<>"']/g, (m) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[m]));
}

function switchView(name) {
  $$(".pane-tab").forEach((b) => b.classList.toggle("active", b.dataset.tab === name));
  $$(".pane").forEach((s) => s.classList.toggle("active", s.id === "pane-" + name));
  // Lazy-render the environments page the first time it's opened.
  if (name === "environments") {
    const root = document.getElementById("env-root");
    if (root && !root.dataset.rendered) {
      root.dataset.rendered = "1";
      renderEnvironmentsPage(root).catch((e) => toast("环境页加载失败：" + e.message, { kind: "err" }));
    }
  }
  // v2.3.0 — hardware recommendation page (re-render on every visit; data is cheap & cached).
  if (name === "hardware") {
    const root = document.getElementById("hw-root");
    if (root && !root.dataset.rendered) {
      root.dataset.rendered = "1";
      renderHardwarePage(root).catch((e) => toast("硬件推荐页加载失败：" + e.message, { kind: "err" }));
    }
  }
  // v2.3.0 — MNN engine page (re-render on every visit to refresh statuses).
  if (name === "mnn") {
    const root = document.getElementById("mnn-root");
    if (root) {
      root.dataset.rendered = "1";
      renderMnnPage(root).catch((e) => toast("MNN 页加载失败：" + e.message, { kind: "err" }));
    }
  }
  // Drama Agent page (re-render on every visit to refresh model options).
  if (name === "drama") {
    const root = document.getElementById("drama-root");
    if (root) {
      root.dataset.rendered = "1";
      renderDramaPage(root).catch((e) => toast("短剧 Agent 页加载失败：" + e.message, { kind: "err" }));
    }
  }
  // v2.4.0 — LTX-2.5 video generation page (init once).
  if (name === "ltx") {
    const root = document.getElementById("pane-ltx");
    if (root && !root.dataset.rendered) {
      root.dataset.rendered = "1";
      initLtx().catch((e) => toast("LTX-2.5 页加载失败：" + e.message, { kind: "err" }));
    }
  }
}

// ---------------------------------------------------------------------------

function wireGlobalUI() {
  // Sidebar category buttons
  document.addEventListener("click", (e) => {
    const tab = e.target.closest("[data-tab]");
    if (tab && tab.closest(".sidebar")) {
      switchView(tab.dataset.tab);
    }
    // open downloads overlay (anywhere)
    const dl = e.target.closest("[data-action=open-downloads]");
    if (dl) { e.preventDefault(); showDownloads(); }
    // reveal a local model in the OS file manager (restored from v1)
    const reveal = e.target.closest("[data-action=reveal-local]");
    if (reveal) {
      e.preventDefault();
      const p = reveal.dataset.path;
      if (p) api.openPath(p).then(() => toast("已在文件管理器中定位", { kind: "ok" })).catch(() => {});
    }
  });

  // Header buttons
  const refresh = $("[data-action=refresh]");
  if (refresh) refresh.addEventListener("click", () => loadAll());
  const updates = $("[data-action=check-updates]");
  if (updates) updates.addEventListener("click", async () => {
    try {
      const r = await api.checkUpdates();
      if (r?.updateAvailable) toast(`可用新版本：${r.updateAvailable}`, { kind: "ok" });
      else toast(`已是最新版本 (v${r?.currentVersion || "?"})`, { kind: "ok" });
    } catch (_) {}
  });
  const detect = $("[data-action=detect-gpu]");
  if (detect) detect.addEventListener("click", async () => {
    try {
      const r = await api.detectGPU();
      toast(`检测到 GPU：${(r?.body || r || []).length || 0}`, { kind: "ok" });
    } catch (_) {}
  });

  // Import buttons
  const impFolder = $("#btn-import-folder");
  if (impFolder) impFolder.addEventListener("click", async () => {
    const p = await api.pickFolder();
    if (!p) return;
    try { await api.importModel({ path: p, mode: "copy" }); toast("已导入", { kind: "ok" }); loadAll(); }
    catch (_) {}
  });
  const impFile = $("#btn-import-file");
  if (impFile) impFile.addEventListener("click", async () => {
    const p = await api.pickFile();
    if (!p) return;
    try { await api.importModel({ path: p, mode: "copy" }); toast("已导入", { kind: "ok" }); loadAll(); }
    catch (_) {}
  });

  // Reload event from dragdrop
  window.addEventListener("kevrai:models-changed", () => loadAll());

  // Health polling (every 15s)
  healthTimer = setInterval(() => {
    api.health().then((h) => setHealthOk(`sidecar v${h?.body?.version || "?"} · ${h?.body?.app_root || ""}`))
                 .catch((e) => setHealthErr(String(e?.message || e)));
  }, 15_000);
}

async function bootstrap() {
  initModels();
  wireModelGrid();
  initSearch(getVgrid());
  wireSettings();
  wireDownloads();
  wireDragDrop();
  wireGlobalUI();
  wireThemeListener();

  // Initial settings fetch (for theme)
  try {
    const s = await api.getSettings();
    setState({ settings: s || {} });
    applyTheme();
  } catch (_) {}

  // Pre-fill settings form with current settings too.
  document.addEventListener("kevrai:open-settings", () => openSettings().catch(() => {}));

  // Sidebar settings
  const settingsBtn = document.querySelector("[data-action=open-settings]");
  if (settingsBtn) settingsBtn.addEventListener("click", (e) => { e.preventDefault(); openSettings().catch(() => {}); });

  // First render
  loadAll().catch((e) => toast("加载失败：" + (e?.message || e), { kind: "err" }));
}

document.addEventListener("DOMContentLoaded", () => {
  bootstrap().catch((e) => toast("初始化失败：" + (e?.message || e), { kind: "err" }));
});
