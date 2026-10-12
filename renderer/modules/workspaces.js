// renderer/modules/workspaces.js — workspace (profile) management.
//
// Each workspace is an isolated on-disk profile (models/engines/agent memory/
// settings). Switching restarts the sidecar against the chosen profile; after
// the restart the renderer reloads so every panel reflects the new workspace.
"use strict";
import { api } from "./api.js";
import { toast } from "./toast.js";
import { t } from "./i18n.js";
import { escapeHtml, unwrap } from "./net.js";

/** Render the list of workspaces with per-row actions. */
export function workspaceRowsHtml(workspaces, activeId) {
  const list = Array.isArray(workspaces) ? workspaces : [];
  if (!list.length) return "";
  return list.map((w) => {
    const id = escapeHtml(w.id);
    const name = escapeHtml(w.name);
    const isActive = w.id === activeId;
    const head = isActive
      ? `<span class="ws-active-badge">${escapeHtml(t("workspaces.active"))}</span>`
      : "";
    // The default workspace can be renamed but not removed.
    const removeBtn = w.builtin || w.id === "default" ? "" : (
      `<button type="button" class="ghost ws-remove" data-id="${id}">` +
      `${escapeHtml(t("workspaces.remove"))}</button>`
    );
    const switchBtn = isActive ? "" : (
      `<button type="button" class="primary ws-switch" data-id="${id}">` +
      `${escapeHtml(t("workspaces.switch"))}</button>`
    );
    return (
      `<div class="ws-row" data-id="${id}"><div class="ws-row-name">` +
      `${name} ${head}</div><div class="ws-row-actions">` +
      `<button type="button" class="ghost ws-rename" data-id="${id}">` +
      `${escapeHtml(t("workspaces.rename"))}</button>${removeBtn}${switchBtn}` +
      `</div></div>`
    );
  }).join("");
}

let wired = false;

export async function initWorkspaces() {
  const listEl = document.getElementById("ws-list");
  if (!listEl) return;

  async function refresh() {
    const state = unwrap(await api.workspaceList());
    listEl.innerHTML = workspaceRowsHtml(state.workspaces, state.active);
  }
  await refresh();

  if (wired) return;
  wired = true;

  const createBtn = document.getElementById("ws-create-btn");
  const nameInput = document.getElementById("ws-new-name");
  createBtn.addEventListener("click", async () => {
    try {
      unwrap(await api.workspaceCreate(nameInput.value));
      nameInput.value = "";
      await refresh();
    } catch (err) {
      toast(t("workspaces.failed", { err: err.message || err }),
        { kind: "err" });
    }
  });

  listEl.addEventListener("click", async (e) => {
    const btn = e.target.closest("button[data-id]");
    if (!btn) return;
    const id = btn.dataset.id;
    try {
      if (btn.classList.contains("ws-switch")) {
        toast(t("workspaces.switching"), { kind: "info" });
        const res = unwrap(await api.workspaceSwitch(id));
        if (res.restarted) {
          // Sidecar is healthy on the new profile; reload the renderer.
          window.location.reload();
        }
      } else if (btn.classList.contains("ws-rename")) {
        const current = btn.closest(".ws-row")
          .querySelector(".ws-row-name").textContent.trim();
        const nm = window.prompt(t("workspaces.renamePh"), current);
        if (nm && nm.trim()) {
          unwrap(await api.workspaceRename(id, nm));
          await refresh();
        }
      } else if (btn.classList.contains("ws-remove")) {
        if (window.confirm(t("workspaces.confirmRemove"))) {
          unwrap(await api.workspaceRemove(id));
          await refresh();
        }
      }
    } catch (err) {
      toast(t("workspaces.failed", { err: err.message || err }),
        { kind: "err" });
    }
  });
}
