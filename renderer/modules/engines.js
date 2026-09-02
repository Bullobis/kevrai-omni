// renderer/modules/engines.js — engine panel rendering + install/uninstall/update.
"use strict";
import { api } from "./api.js";
import { toast } from "./toast.js";
import { state } from "./state.js";

const $ = (s) => document.querySelector(s);

export function renderEngines() {
  const el = $(".engine-grid");
  if (!el) return;
  const engines = state.engines || [];
  el.replaceChildren(...engines.map(engineRow));
  el.querySelectorAll("button[data-action=install]").forEach((b) =>
    b.addEventListener("click", async () => {
      const id = b.dataset.id;
      b.disabled = true; b.textContent = "安装中…";
      try {
        await api.installEngine(id);
        // Optimistic local flip; full reload happens via main.js's loadAll
        const e = (state.engines || []).find((x) => x.id === id);
        if (e) e.installed = true;
        toast(`${id} 安装完成`, { kind: "ok" });
        renderEngines();
      } catch (_) { /* toast shown */ }
      b.disabled = false;
      b.textContent = "重装";
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
        toast(`${id} 已卸载`, { kind: "ok" });
        renderEngines();
      } catch (_) {}
      b.disabled = false;
    })
  );
  // v2.4.1 — one-click update to the newest GitHub release.
  el.querySelectorAll("button[data-action=update]").forEach((b) =>
    b.addEventListener("click", async () => {
      const id = b.dataset.id;
      b.disabled = true; b.textContent = "更新中…";
      try {
        await api.updateEngine(id);
        const e = (state.engines || []).find((x) => x.id === id);
        if (e) {
          e.version = e.latest_tag || e.version;
          e.update_available = false;
        }
        toast(`${id} 已更新${e && e.latest_tag ? "到 " + e.latest_tag : ""}`, { kind: "ok" });
        renderEngines();
      } catch (_) { /* toast shown */ }
      b.disabled = false;
      b.textContent = "更新";
    })
  );
}

// v2.4.1 — toolbar: explicit update check against GitHub releases.
export function wireEngineUpdates() {
  const btn = $("#btn-engines-check-updates");
  if (!btn) return;
  btn.addEventListener("click", async () => {
    const hint = $("#engines-update-hint");
    btn.disabled = true;
    if (hint) hint.textContent = "正在检查已安装引擎的新版本…";
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
          ? `发现 ${ups.length} 个引擎有新版本，点卡片上的「更新」升级`
          : `已检查 ${results.length} 个已安装引擎，均为最新${errs.length ? `（${errs.length} 个查询失败）` : ""}`;
      }
    } catch (_) {
      if (hint) hint.textContent = "检查更新失败，请稍后重试";
    }
    btn.disabled = false;
  });
}

function engineRow(e) {
  const card = document.createElement("div");
  card.className = "engine-card";
  const updatePill = (e.installed && e.update_available)
    ? `<span class="pill warn">有新版 ${escapeHtml(e.latest_tag || "")}</span>`
    : (e.installed && e.version
      ? `<span class="pill">${escapeHtml(e.version)}</span>`
      : "");
  card.innerHTML = `
    <div class="card-head">
      <div class="card-title">${escapeHtml(e.name || e.id)}</div>
      <span class="pill ${e.installed ? "ok" : ""}">${e.installed ? "已安装" : "未安装"}</span>
      ${updatePill}
    </div>
    <p class="card-desc">${escapeHtml(e.description || "")}</p>
    ${e.github
      ? `<p class="mut">github.com/${escapeHtml(e.github)}</p>`
      : ""}
    <div class="card-foot">
      <button class="primary" data-action="install" data-id="${escapeHtml(e.id)}">
        ${e.installed ? "重装" : "安装"}
      </button>
      ${e.installed && e.update_available
        ? `<button class="secondary" data-action="update" data-id="${escapeHtml(e.id)}">更新</button>`
        : ""}
      ${e.installed
        ? `<button class="danger" data-action="uninstall" data-id="${escapeHtml(e.id)}">卸载</button>`
        : ""}
    </div>
  `;
  return card;
}

function escapeHtml(s) {
  return String(s || "").replace(/[&<>"']/g, (m) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[m]));
}
