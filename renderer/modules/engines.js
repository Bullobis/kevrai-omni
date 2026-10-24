// renderer/modules/engines.js — engine panel rendering + install/uninstall/update.
"use strict";
import { api } from "./api.js";
import { toast } from "./toast.js";
import { state } from "./state.js";
import { escapeHtml } from "./net.js";
import { createEmptyState } from "./empty-state.js";
import { t } from "./i18n.js";

const $ = (s) => document.querySelector(s);

// 安装进度轮询：引擎二进制几百 MB，旧逻辑只把按钮改成"安装中"，
// 用户看不到任何动静，很容易以为卡死直接关窗口。这里每 1.5s 拉一次
// /api/engines，把目标卡片的进度条、已下/总 MB、当前镜像实时刷出来。
let installPollTimer = null;

function fmtMB(bytes) {
  if (!bytes || bytes < 0) return "?";
  if (bytes >= 1024 * 1024 * 1024) return (bytes / 1024 / 1024 / 1024).toFixed(2) + " GB";
  return Math.max(1, Math.round(bytes / 1024 / 1024)) + " MB";
}
function activeHost(url) {
  try { return new URL(url).host; } catch (_) { return url || ""; }
}
function stopInstallPoll() {
  if (installPollTimer) { clearInterval(installPollTimer); installPollTimer = null; }
}
function startInstallPoll(id) {
  stopInstallPoll();
  installPollTimer = setInterval(() => {
    api.engines().then((r) => {
      const list = (r && (r.body ? r.body.engines : r.engines)) || [];
      const st = list.find((x) => x.id === id);
      const card = document.querySelector(`.engine-card[data-engine-id="${CSS.escape(id)}"]`);
      if (!st || !card) return;
      const bar = card.querySelector(".engine-progress");
      if (!bar) return;
      const dl = st.downloaded_bytes || 0;
      const total = st.total_bytes || 0;
      const prog = (typeof st.progress === "number") ? st.progress : 0;
      const pct = total > 0 ? Math.round((dl / total) * 100)
        : (prog >= 0 ? Math.round(prog * 100) : -1);
      bar.hidden = false;
      bar.querySelector(".ep-bar-fill").style.width = (pct >= 0 ? pct : 2) + "%";
      const label = (pct >= 0 ? pct + "% · " : "") + fmtMB(dl)
        + (total > 0 ? " / " + fmtMB(total) : "");
      bar.querySelector(".ep-label").textContent = label;
      const host = activeHost(st.active_url);
      bar.querySelector(".ep-host").textContent = host ? t("engines.dlFrom", { host }) : "";
    }).catch(() => {});
  }, 1500);
}


export function renderEngines() {
  const el = $(".engine-grid");
  if (!el) return;
  const engines = state.engines || [];
  if (!engines.length) {
    const es = createEmptyState({
      icon: "cpu",
      title: t("engines.emptyTitle"),
      hint: t("engines.emptyHint"),
      actionLabel: t("header.refresh"),
    });
    es.querySelector(".es-action")?.addEventListener("click", () => renderEngines());
    el.replaceChildren(es);
    return;
  }
  el.replaceChildren(...engines.map(engineRow));
  el.querySelectorAll("button[data-action=install]").forEach((b) =>
    b.addEventListener("click", async () => {
      const id = b.dataset.id;
      b.disabled = true; b.textContent = t("engines.installing");
      startInstallPoll(id);
      try {
        await api.installEngine(id);
        stopInstallPoll();
        // Optimistic local flip; full reload happens via main.js's loadAll
        const e = (state.engines || []).find((x) => x.id === id);
        if (e) e.installed = true;
        toast(t("engines.installDone", { id }), { kind: "ok" });
        renderEngines();
      } catch (_) { /* toast shown */ stopInstallPoll(); }
      b.disabled = false;
      b.textContent = t("market.reinstall");
    })
  );
  el.querySelectorAll("button[data-action=uninstall]").forEach((b) =>
    b.addEventListener("click", async () => {
      const id = b.dataset.id;
      b.disabled = true;
      try {
        await api.uninstallEngine(id);
        const e = (state.engines || []).find((x) => x.id === id);
        if (e) e.installed = false;
        toast(t("engines.uninstallDone", { id }), { kind: "ok" });
        renderEngines();
      } catch (_) {}
      b.disabled = false;
    })
  );
  // v2.4.1 — one-click update to the newest GitHub release.
  el.querySelectorAll("button[data-action=update]").forEach((b) =>
    b.addEventListener("click", async () => {
      const id = b.dataset.id;
      b.disabled = true; b.textContent = t("engines.updating");
      try {
        await api.updateEngine(id);
        const e = (state.engines || []).find((x) => x.id === id);
        if (e) {
          e.version = e.latest_tag || e.version;
          e.update_available = false;
        }
        toast(e && e.latest_tag ? t("engines.updateDone", { id, v: e.latest_tag }) : t("engines.updateDoneNoVer", { id }), { kind: "ok" });
        renderEngines();
      } catch (_) { /* toast shown */ }
      b.disabled = false;
      b.textContent = t("engines.update");
    })
  );
}

// v2.4.1 — toolbar: explicit update check against GitHub releases.
// Idempotent: deferred to idle at startup, but safe to re-invoke.
let engineUpdatesWired = false;
export function wireEngineUpdates() {
  if (engineUpdatesWired) return;
  engineUpdatesWired = true;
  const btn = $("#btn-engines-check-updates");
  if (!btn) return;
  btn.addEventListener("click", async () => {
    const hint = $("#engines-update-hint");
    btn.disabled = true;
    if (hint) hint.textContent = t("engines.checking");
    try {
      const r = await api.checkEngineUpdates({ force: true });
      const results = (r && (r.body ? r.body.results : r.results)) || [];
      const errs = results.filter((x) => x.error);
      const ups = results.filter((x) => x.update_available);
      // Refresh the panel with the new cache-backed flags.
      try {
        const list = await api.engines();
        state.engines = (list && (list.body ? list.body.engines : list.engines)) || state.engines;
      } catch (_) {}
      renderEngines();
      if (hint) {
        hint.textContent = ups.length
          ? t("engines.checkFound", { n: ups.length })
          : errs.length
            ? t("engines.checkOkWithErrs", { n: results.length, errs: errs.length })
            : t("engines.checkOk", { n: results.length });
      }
    } catch (_) {
      if (hint) hint.textContent = t("engines.checkFailed");
    }
    btn.disabled = false;
  });
}

function engineRow(e) {
  const card = document.createElement("div");
  card.className = "engine-card";
  card.dataset.engineId = e.id;
  const updatePill = (e.installed && e.update_available)
    ? `<span class="pill warn">${t("engines.hasNewVersion", { v: escapeHtml(e.latest_tag || "") })}</span>`
    : (e.installed && e.version
      ? `<span class="pill">${escapeHtml(e.version)}</span>`
      : "");
  card.innerHTML = `
    <div class="card-head">
      <div class="card-title">${escapeHtml(e.name || e.id)}</div>
      <span class="pill ${e.installed ? "ok" : ""}">${e.installed ? t("market.installed") : t("market.notInstalled")}</span>
      ${updatePill}
    </div>
    <p class="card-desc">${escapeHtml(e.description || "")}</p>
    ${e.github
      ? `<p class="mut">github.com/${escapeHtml(e.github)}</p>`
      : ""}
    <div class="engine-progress" hidden>
      <div class="ep-bar"><div class="ep-bar-fill"></div></div>
      <div class="ep-label muted"></div>
      <div class="ep-host muted"></div>
    </div>
    <div class="card-foot">
      <button class="primary" data-action="install" data-id="${escapeHtml(e.id)}">
        ${e.installed ? t("market.reinstall") : t("market.install")}
      </button>
      ${e.installed && e.update_available
        ? `<button class="secondary" data-action="update" data-id="${escapeHtml(e.id)}">${t("engines.update")}</button>`
        : ""}
      ${e.installed
        ? `<button class="danger" data-action="uninstall" data-id="${escapeHtml(e.id)}">${t("market.uninstall")}</button>`
        : ""}
    </div>
  `;
  return card;
}
