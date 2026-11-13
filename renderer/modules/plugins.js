// renderer/modules/plugins.js — DSH-compatible plugin manager UI (v4.17.5)
// Loads from Python sidecar /api/plugins via window.kevrai preload bridge.
// Supports install → activate → deactivate → uninstall full lifecycle.
"use strict";
import { api } from "./api.js";
import { escapeHtml } from "./net.js";
import { toast } from "./toast.js";

const PLUGIN_TEMPLATE = (p) => {
  const id = escapeHtml(p.id || "");
  const name = escapeHtml(p.name || p.id || "");
  const version = escapeHtml(p.version || "0.0.0");
  const author = escapeHtml(p.author || "");
  const description = escapeHtml(p.description || "");
  const hooks = (p.hooks || []).map((h) => `<span class="pill">${escapeHtml(h)}</span>`).join("");
  const activeCls = p.active ? " installed active" : " installed";
  const state = p.active
    ? `<span class="pill ok">● 已激活</span>`
    : `<span class="pill">○ 已安装</span>`;
  return `
    <div class="card plugin${activeCls}" data-plugin-id="${id}">
      <div class="card-head">
        <div class="plugin-title">
          <strong>${name}</strong>
          <span class="plugin-ver">v${version}</span>
          ${state}
        </div>
        <div class="plugin-meta">${author ? `@${author}` : ""}</div>
      </div>
      <div class="card-body">
        <p class="plugin-desc">${description || "—"}</p>
        <div class="plugin-tags">
          <span class="pill">${id}</span>
          ${hooks}
        </div>
      </div>
      <div class="card-foot row">
        ${p.active
          ? `<button class="btn ghost" data-action="plugin-deactivate">停用</button>
             <button class="btn ghost danger" data-action="plugin-remove">卸载</button>`
          : `<button class="btn primary" data-action="plugin-activate">激活</button>
             <button class="btn ghost danger" data-action="plugin-remove">卸载</button>`}
      </div>
    </div>`;
};

const MARKET_EMPTY = `
  <div class="empty-state">
    <div class="empty-state-iconwrap">
      <svg class="ic empty-state-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor"
           stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z"/>
        <path d="M3.3 7 12 12l8.7-5"/>
        <path d="M12 22V12"/>
      </svg>
    </div>
    <h3 class="empty-state-title">暂无插件</h3>
    <p class="empty-state-hint">在上方粘贴 Git 仓库地址或 DSH 插件市场链接。</p>
  </div>`;

export async function refreshPlugins() {
  const grid = document.getElementById("plugins-grid");
  if (!grid) return;
  try {
    const resp = await api.pluginsList();
    const list = resp?.plugins || [];
    if (!list.length) {
      grid.innerHTML = MARKET_EMPTY;
      return;
    }
    grid.innerHTML = list.map(PLUGIN_TEMPLATE).join("");
  } catch (e) {
    grid.innerHTML = MARKET_EMPTY;
  }
}

function bindInstall() {
  const btn = document.getElementById("plugins-install-btn");
  const urlInput = document.getElementById("plugins-url");
  if (!btn || !urlInput) return;
  btn.addEventListener("click", async () => {
    const url = urlInput.value.trim();
    if (!url) return toast("请输入插件仓库地址", { kind: "warn" });
    btn.disabled = true; btn.textContent = "安装中…";
    try {
      const resp = await api.pluginInstall(url);
      toast(`已安装 ${resp?.plugin?.name || "插件"}`, { kind: "ok" });
      urlInput.value = "";
      await refreshPlugins();
    } catch {
      // toast already shown by api.wrap()
    } finally {
      btn.disabled = false; btn.textContent = "从 Git 安装";
    }
  });
  urlInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter") btn.click();
  });
}

function bindCardClicks() {
  document.getElementById("plugins-grid")?.addEventListener("click", async (e) => {
    const btn = e.target.closest("[data-action^=plugin-]");
    if (!btn) return;
    const card = btn.closest(".card.plugin");
    const id = card?.dataset.pluginId;
    if (!id) return;
    const action = btn.dataset.action;

    btn.disabled = true;
    const orig = btn.textContent;
    try {
      if (action === "plugin-activate") {
        btn.textContent = "激活中…";
        await api.pluginActivate(id);
        toast(`插件 ${id} 已激活`, { kind: "ok" });
      } else if (action === "plugin-deactivate") {
        btn.textContent = "停用中…";
        await api.pluginDeactivate(id);
        toast(`插件 ${id} 已停用`);
      } else if (action === "plugin-remove") {
        btn.textContent = "卸载中…";
        await api.pluginRemove(id);
        toast(`插件 ${id} 已卸载`);
      }
      await refreshPlugins();
    } catch { /* api.wrap 已 toast */ }
    btn.textContent = orig; btn.disabled = false;
  });
}

export function initPlugins() {
  bindInstall();
  bindCardClicks();
  refreshPlugins();
}

// Expose for switchView refresh — keeps lazy-render contract simple.
if (typeof window !== "undefined") {
  window.__pluginsModule = { refreshPlugins, initPlugins };
}
