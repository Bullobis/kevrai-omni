// renderer/modules/engines.js — engine panel rendering + install/uninstall.
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
}

function engineRow(e) {
  const card = document.createElement("div");
  card.className = "engine-card";
  card.innerHTML = `
    <div class="card-head">
      <div class="card-title">${escapeHtml(e.name || e.id)}</div>
      <span class="pill ${e.installed ? "ok" : ""}">${e.installed ? "已安装" : "未安装"}</span>
    </div>
    <p class="card-desc">${escapeHtml(e.description || "")}</p>
    ${e.github
      ? `<p class="mut">github.com/${escapeHtml(e.github)}</p>`
      : ""}
    <div class="card-foot">
      <button class="primary" data-action="install" data-id="${escapeHtml(e.id)}">
        ${e.installed ? "重装" : "安装"}
      </button>
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
