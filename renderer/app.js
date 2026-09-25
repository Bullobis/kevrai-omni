"use strict";
/**
 * Kevrai Omni — renderer entry.
 *
 * Talks only to `window.kevrai` (preload bridge).
 * Coordinates the modules under renderer/modules/.
 */

import { api } from "./modules/api.js";
import { toast } from "./modules/toast.js";
import { escapeHtml } from "./modules/net.js";
import { renderEnvironmentsPage } from "./modules/environments.js";
import { renderHardwarePage } from "./modules/hardware.js";
import { renderMnnPage } from "./modules/mnn.js";
import { initAgent } from "./modules/agent.js";
import { state, setState } from "./modules/state.js";
import { applyTheme, wireThemeListener } from "./modules/theme.js";
import { initModels, renderModelGrid, populateCategoryFilter,
         wireLogoFallbacks, getVgrid, hideGridSkeleton } from "./modules/models.js";
import { initSearch, runSearch } from "./modules/search.js";
import { initLtx } from "./modules/ltx.js";
import { renderEngines, wireEngineUpdates } from "./modules/engines.js";
import { wireSettings, openSettings, closeSettings } from "./modules/settings.js";
import { wireDownloads, showDownloads } from "./modules/downloads.js";
import { wireDragDrop } from "./modules/dragdrop.js";
import { wireOnboarding } from "./modules/onboarding.js";
import { wireUpdate } from "./modules/update.js";
import { initCommandPalette } from "./modules/command-palette.js";
import { initI18n, t } from "./modules/i18n.js";
import { createEmptyState } from "./modules/empty-state.js";
import { whenIdle } from "./modules/idle.js";

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
    const [settings, cats, ms, ens, locs, h] = await Promise.all([
      api.getSettings(),
      api.categories(),
      api.models({}),
      api.engines(),
      api.localModels(),
      api.health().catch(() => ({ body: { version: "?", app_root: "unreachable" } })),
    ]);
    setState({
      settings: settings || {},
      categories: cats?.body?.categories || cats?.categories || [],
      models:     ms?.body?.models     || ms?.models     || [],
      engines:    ens?.body?.engines   || ens?.engines   || [],
      local:      locs?.body?.local    || locs?.local    || [],
    });
    // GGUF 枚举需要触网，不阻塞首屏：后台加载，就绪后单独渲染。
    api.ggufRepos()
      .then((r) => { setState({ ggufRepos: r?.body?.repos || r?.repos || [] }); renderGGUF(); })
      .catch(() => {});
    populateCategoryFilter();
    // 首屏关键路径：只铺市场外壳（更新计数 + 收起 HTML 骨架）。虚拟网格的
    // 卡片窗口由下方 runSearch() 唯一挂载——这里再 setItems 一次会在搜索结果
    // 到达后立即被重挂，白白多一次布局+渲染。
    renderModelGrid();
    applyTheme();
    setHealthOk(`sidecar v${h?.body?.version || "?"}`);
    // 非首屏面板（引擎 / 本地模型 / GGUF / 待开源）在空闲期渲染，让出主线程给
    // 首屏可交互；这些面板默认隐藏，用户切过去之前渲染即可。
    whenIdle(() => { renderEngines(); renderLocal(); renderGGUF(); renderPending(); });
    // v2.4.0 — drive the market grid through the super search (facets, sort).
    runSearch({ resetPage: true }).catch(() => {});
  } catch (e) {
    setHealthErr(String(e?.message || e));
    // Don't leave the market stuck on the skeleton if loading failed.
    hideGridSkeleton();
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
  if (!repos.length) {
    const es = createEmptyState({
      icon: "packageOpen",
      title: t("gguf.emptyTitle"),
      hint: t("gguf.emptyHint"),
      actionLabel: t("header.refresh"),
    });
    es.querySelector(".es-action")?.addEventListener("click", () => loadAll());
    el.replaceChildren(es);
    return;
  }
  el.innerHTML = repos.map((r) => {
    if (r.error) {
      return `
    <div class="row">
      <div class="grow">
        <div class="name">${escapeHtml(r.name || r.owner_repo)}</div>
        <div class="sub">${escapeHtml(r.owner_repo || "")}</div>
        <div class="sub">${t("gguf.cannotConnect", { err: escapeHtml(r.error) })}</div>
      </div>
      <span class="pill">${t("gguf.offline")}</span>
    </div>`;
    }
    const files = (r.files || []).slice(0, 8).map((f) => `
      <div class="sub">· ${escapeHtml(f.path)} (${((f.size || 0) / 1e9).toFixed(2)} GB)</div>`).join("");
    const more = (r.files || []).length > 8
      ? `<div class="sub mut">${t("gguf.moreFiles", { n: r.count || (r.files || []).length })}</div>` : "";
    return `
    <div class="row">
      <div class="grow">
        <div class="name">${escapeHtml(r.name || r.owner_repo)}</div>
        <div class="sub">${escapeHtml(r.owner_repo || "")}</div>
        ${files}${more}
      </div>
      <span class="pill ok">${t("gguf.filesCount", { n: r.count || (r.files || []).length })}</span>
    </div>`;
  }).join("");
}

function renderLocal() {
  const el = $("#local-list");
  if (!el) return;
  const list = state.local || [];
  if (!list.length) {
    const es = createEmptyState({
      icon: "hardDrive",
      title: t("local.emptyTitle"),
      hint: t("local.emptyHint"),
      actionLabel: t("local.pickFile"),
    });
    es.querySelector(".es-action")?.addEventListener("click", () => $("#btn-import-file")?.click());
    el.replaceChildren(es);
    return;
  }
  el.innerHTML = list.map((m) => {
    // v2.8.0 DIY: compatible_engines comes from /api/models/local (read-time
    // detection) — .gguf → llama.cpp, config.json+*.mnn → mnn, HF dir →
    // transformers/diffusers. GGUF models get a one-click llama-server start.
    const es = m.compatible_engines || [];
    const canLlm = es.includes("llama.cpp");
    const canMnn = es.includes("mnn");
    const running = state.llm && state.llm.running && state.llm.model_path === m.path;
    const engLine = es.length ? `<div class="sub">${t("local.availableEngines", { list: escapeHtml(es.join("、")) })}</div>` : "";
    return `
    <div class="row">
      <div class="grow">
        <div class="name">${escapeHtml(m.name)}</div>
        <div class="sub" title="${escapeHtml(m.path || "")}">${escapeHtml(m.path || "")}</div>
        <div class="sub">${((m.size_bytes || 0) / 1e9).toFixed(2)} GB</div>
        ${engLine}
      </div>
      ${running ? `<span class="pill ok">${t("local.running", { port: state.llm.port })}</span>
        <button class="secondary small" data-action="llm-stop">${t("local.stop")}</button>` :
        canLlm ? `<button class="secondary small" data-action="llm-start"
              data-path="${escapeHtml(m.path || "")}" aria-label="${t("local.startLlamaAria")}">${t("local.start")}</button>` : ""}
      ${canMnn ? `<span class="pill">${t("local.mnnLoadable")}</span>` : ""}
      <span class="pill ok">${t("local.localBadge")}</span>
      ${m.path ? `<button class="secondary small" data-action="reveal-local"
              data-path="${escapeHtml(m.path)}" aria-label="${t("local.revealAria")}">${t("local.locate")}</button>` : ""}
    </div>`;
  }).join("");
}

// 待官方开源列表：无数据时显示统一空状态。
function renderPending() {
  const el = $("#pending-list");
  if (!el) return;
  const list = state.pending || [];
  if (!list.length) {
    const es = createEmptyState({
      icon: "clock",
      title: t("local.pendingEmptyTitle"),
      hint: t("local.pendingEmptyHint"),
    });
    el.replaceChildren(es);
    return;
  }
  el.innerHTML = list.map((m) => {
    const name = m.name || m.id || t("market.unnamed");
    return `
    <div class="row">
      <div class="grow">
        <div class="name">${escapeHtml(name)}</div>
        ${m.description ? `<div class="sub">${escapeHtml(m.description)}</div>` : ""}
      </div>
      <span class="pill warn">${t("local.pendingBadge")}</span>
    </div>`;
  }).join("");
}

function switchView(name) {
  $$(".pane-tab").forEach((b) => b.classList.toggle("active", b.dataset.tab === name));
  $$(".pane").forEach((s) => s.classList.toggle("active", s.id === "pane-" + name));
  // Lazy-render the environments page the first time it's opened.
  if (name === "environments") {
    const root = document.getElementById("env-root");
    if (root && !root.dataset.rendered) {
      root.dataset.rendered = "1";
      renderEnvironmentsPage(root).catch((e) => toast(t("toast.envPageFailed", { err: e.message }), { kind: "err" }));
    }
  }
  // v2.3.0 — hardware recommendation page (re-render on every visit; data is cheap & cached).
  if (name === "hardware") {
    const root = document.getElementById("hw-root");
    if (root && !root.dataset.rendered) {
      root.dataset.rendered = "1";
      renderHardwarePage(root).catch((e) => toast(t("toast.hwPageFailed", { err: e.message }), { kind: "err" }));
    }
  }
  // v2.3.0 — MNN engine page (re-render on every visit to refresh statuses).
  if (name === "mnn") {
    const root = document.getElementById("mnn-root");
    if (root) {
      root.dataset.rendered = "1";
      renderMnnPage(root).catch((e) => toast(t("toast.mnnPageFailed", { err: e.message }), { kind: "err" }));
    }
  }
  // v2.4.0 — LTX-2.5 video generation page (init once).
  if (name === "ltx") {
    const root = document.getElementById("pane-ltx");
    if (root && !root.dataset.rendered) {
      root.dataset.rendered = "1";
      initLtx().catch((e) => toast(t("toast.ltxPageFailed", { err: e.message }), { kind: "err" }));
    }
  }
  // v2.7.0 — Kevrai Agent page (init once).
  if (name === "agent") {
    const root = document.getElementById("pane-agent");
    if (root && !root.dataset.rendered) {
      root.dataset.rendered = "1";
      initAgent().catch((e) => toast(t("toast.agentPageFailed", { err: e.message }), { kind: "err" }));
    }
  }
  // R4 — 统一派发视图切换事件。后台轮询型模块（LTX / MNN）通过
  // modules/view-visibility.js 订阅，在自己的视图隐藏时暂停网络请求，
  // 切回时恢复并立即刷新一次。放在所有 lazy-render 之后，确保订阅者
  // 回调触发时 DOM / 模块状态已就绪。
  window.dispatchEvent(new CustomEvent("kevrai:view-change", { detail: { view: name } }));
}

// ---------------------------------------------------------------------------

// Frameless title bar: wire custom minimize / maximize-restore / close controls.
function wireWindowControls() {
  const k = window.kevrai;
  if (!k) return;
  document.body.classList.toggle("is-mac", k.platform === "darwin");
  const btnMin = document.getElementById("win-btn-min");
  const btnMax = document.getElementById("win-btn-max");
  const btnClose = document.getElementById("win-btn-close");
  const ico = document.getElementById("win-ico-max");
  if (btnMin) btnMin.addEventListener("click", () => { k.winMinimize(); });
  if (btnClose) btnClose.addEventListener("click", () => { k.winClose(); });
  const setMax = (isMax) => {
    if (btnMax) {
      btnMax.setAttribute("aria-label", isMax ? t("app.winRestore") : t("app.winMaximize"));
      btnMax.title = isMax ? t("app.winRestore") : t("app.winMaximize");
    }
    if (ico) {
      ico.innerHTML = isMax
        ? '<path d="M8 3v3a2 2 0 0 1-2 2H3"/><path d="M21 8h-3a2 2 0 0 1-2-2V3"/><path d="M3 16h3a2 2 0 0 1 2 2v3"/><path d="M16 21v-3a2 2 0 0 1 2-2h3"/>'
        : '<rect x="5" y="5" width="14" height="14" rx="1"/>';
    }
  };
  if (btnMax) btnMax.addEventListener("click", () => { k.winToggleMaximize(); });
  if (k.onWinMaxChange) k.onWinMaxChange((v) => setMax(!!v));
  if (k.winIsMaximized) k.winIsMaximized().then((v) => setMax(!!v)).catch(() => {});
}

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
      if (p) api.openPath(p).then(() => toast(t("toast.located"), { kind: "ok" })).catch(() => {});
    }
    // DIY: start llama.cpp for a local .gguf model (v2.8.0)
    const llmStartBtn = e.target.closest("[data-action=llm-start]");
    if (llmStartBtn) {
      e.preventDefault();
      const p = llmStartBtn.dataset.path;
      if (!p) return;
      llmStartBtn.disabled = true;
      toast(t("toast.llmStarting"));
      api.llmStart({ model_path: p })
        .then((r) => {
          state.llm = { running: true, port: r.port, model_path: p };
          toast(t("toast.llmStarted", { port: r.port }), { kind: "ok" });
          renderLocal();
        })
        .catch((err) => {
          toast(t("toast.llmStartFailed", { err: err.message }), { kind: "err" });
          llmStartBtn.disabled = false;
        });
    }
    const llmStopBtn = e.target.closest("[data-action=llm-stop]");
    if (llmStopBtn) {
      e.preventDefault();
      api.llmStop()
        .then(() => { state.llm = null; renderLocal(); toast(t("toast.llmStopped"), { kind: "ok" }); })
        .catch((err) => toast(t("toast.llmStopFailed", { err: err.message }), { kind: "err" }));
    }
  });

  // Header buttons
  const refresh = $("[data-action=refresh]");
  if (refresh) refresh.addEventListener("click", () => loadAll());
  // check-updates button is owned by modules/update.js (wireUpdate) — full
  // check -> download -> install flow with progress overlay.
  const detect = $("[data-action=detect-gpu]");
  if (detect) detect.addEventListener("click", async () => {
    try {
      const r = await api.detectGPU();
      toast(t("toast.gpuDetected", { n: (r?.body || r || []).length || 0 }), { kind: "ok" });
    } catch (_) {}
  });

  // Import buttons
  const impFolder = $("#btn-import-folder");
  if (impFolder) impFolder.addEventListener("click", async () => {
    const p = await api.pickFolder();
    if (!p) return;
    try { await api.importModel({ path: p, mode: "copy" }); toast(t("toast.imported"), { kind: "ok" }); loadAll(); }
    catch (_) {}
  });
  const impFile = $("#btn-import-file");
  if (impFile) impFile.addEventListener("click", async () => {
    const p = await api.pickFile();
    if (!p) return;
    try { await api.importModel({ path: p, mode: "copy" }); toast(t("toast.imported"), { kind: "ok" }); loadAll(); }
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

function wirePaletteEvents() {
  // a11y — 「跳到正文」：锚点跳转不会把焦点给到 #main（它是 tabindex=-1 的
  // 容器），这里手动 focus()，否则读屏/键盘用户焦点仍停在 skip-link 上。
  const skip = document.querySelector(".skip-link");
  if (skip) {
    skip.addEventListener("click", (e) => {
      e.preventDefault();
      const main = document.getElementById("main");
      if (main) main.focus();
    });
  }
  // The i18n command palette (Ctrl/⌘+K) is decoupled and talks via window events.
  window.addEventListener("kevrai:navigate", (e) => {
    const tab = e.detail && e.detail.tab;
    if (tab) switchView(tab);
  });
  window.addEventListener("kevrai:open-downloads", () => {
    try { showDownloads(); } catch (_) {}
  });
  window.addEventListener("kevrai:detect-gpu", () => {
    const b = document.querySelector("[data-action=detect-gpu]");
    if (b) b.click();
  });
  window.addEventListener("kevrai:refresh", () => loadAll());
  window.addEventListener("kevrai:check-updates", () => {
    const b = document.querySelector("[data-action=check-updates]");
    if (b) b.click();
  });
  // v3.1.0 — persist theme chosen from the command palette.
  window.addEventListener("kevrai:set-theme", (e) => {
    const theme = e.detail && e.detail.theme;
    if (!theme) return;
    (async () => {
      try {
        const cur = await api.getSettings();
        const s = (cur && (cur.body || cur)) || {};
        s.theme = theme;
        await api.putSettings(s);
      } catch (_) {}
    })();
  });
  // v3.1.0 — reveal the app data folder in the OS file manager.
  window.addEventListener("kevrai:open-data", () => {
    (async () => {
      try {
        const h = await api.health();
        const p = h.app_root || (h.body && h.body.app_root);
        if (p) await api.openPath(p);
      } catch (_) {}
    })();
  });
}

async function bootstrap() {
  initModels();
  initSearch(getVgrid());
  wireSettings();
  wireDownloads();
  wireDragDrop();
  wireGlobalUI();
  wireWindowControls();
  wireThemeListener();
  const i18nReady = initI18n();
  wirePaletteEvents();
  // 命令面板构建时会读取占位文案，须在字典加载完成后再初始化，避免占位符显示原始 key。
  i18nReady.then(() => initCommandPalette());
  // logo / 头像的加载失败降级（替代此前被 CSP 拦截的内联 onerror）
  wireLogoFallbacks();

  // 非关键初始化延迟到空闲期：首启引导、引擎更新按钮、应用更新检查。
  // 它们不在 DOMContentLoaded → 首屏可交互 的关键路径上；rIC（无则 setTimeout 0）
  // 会在首帧绘制后立即执行。三个 wire 函数均已幂等。
  whenIdle(() => { wireOnboarding(); wireEngineUpdates(); wireUpdate(); });
  // 防御：若用户在 idle 跑起来之前就点了 header 的「检查更新」按钮，在捕获阶段
  // 立即补接线（wireUpdate 幂等，且派发过程中新增的监听器会对本次点击生效）。
  document.addEventListener("click", (e) => {
    if (e.target.closest && e.target.closest("[data-action=check-updates]")) {
      wireUpdate();
    }
  }, true);

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

  // v3.0.0 — Sidebar expand/collapse toggle (persisted in localStorage)
  const sidebarToggle = document.getElementById("sidebar-toggle");
  const appShell = document.querySelector(".app-shell");
  const sidebar = document.querySelector(".sidebar");
  if (sidebarToggle && appShell && sidebar) {
    const SIDEBAR_KEY = "kevrai:sidebar-expanded";
    const applySidebar = (expanded) => {
      appShell.classList.toggle("sidebar-expanded", expanded);
      sidebar.classList.toggle("expanded", expanded);
      sidebarToggle.textContent = expanded ? "⟨" : "☰";
      sidebarToggle.setAttribute("aria-label", expanded ? t("app.collapseSidebar") : t("app.expandSidebar"));
    };
    try { applySidebar(localStorage.getItem(SIDEBAR_KEY) === "1"); } catch (_) {}
    sidebarToggle.addEventListener("click", () => {
      const expanded = !appShell.classList.contains("sidebar-expanded");
      applySidebar(expanded);
      try { localStorage.setItem(SIDEBAR_KEY, expanded ? "1" : "0"); } catch (_) {}
    });
  }

  // v3.0.0 — Token field show/hide toggle
  document.querySelectorAll(".token-toggle").forEach((btn) => {
    btn.addEventListener("click", () => {
      const wrap = btn.closest(".token-field-wrap");
      if (!wrap) return;
      const input = wrap.querySelector("input");
      if (!input) return;
      const isPwd = input.type === "password";
      input.type = isPwd ? "text" : "password";
      btn.textContent = isPwd ? "🙈" : "👁";
      btn.setAttribute("aria-label", isPwd ? "隐藏 Token" : "显示 Token");
    });
  });

  // First render
  loadAll().catch((e) => toast(t("toast.loadFailed", { err: e?.message || e }), { kind: "err" }));
}

document.addEventListener("DOMContentLoaded", () => {
  bootstrap().catch((e) => toast(t("toast.initFailed", { err: e?.message || e }), { kind: "err" }));
});
