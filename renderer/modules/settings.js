// renderer/modules/settings.js — Settings overlay (open / save).
"use strict";
import { api } from "./api.js";
import { toast } from "./toast.js";
import { state, setState } from "./state.js";
import { applyTheme } from "./theme.js";
import { showGenerationWait, hideGenerationWait } from "./generation-wait.js";

const $  = (s) => document.querySelector(s);
const $$ = (s) => Array.from(document.querySelectorAll(s));

function formatBytes(b) {
  if (b == null || b < 0) return "";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let i = 0;
  while (b >= 1024 && i < units.length - 1) { b /= 1024; i++; }
  return `${b.toFixed(i === 0 ? 0 : 1)} ${units[i]}`;
}

export async function openSettings() {
  const overlay = $("#settings-overlay");
  if (!overlay) return;
  overlay.removeAttribute("hidden");
  overlay.setAttribute("aria-hidden", "false");

  // Pull fresh settings (in case another instance edited them).
  const fresh = await api.getSettings();
  setState({ settings: fresh });
  fillForm(fresh);
  trapFocus(overlay);
}

export function closeSettings() {
  const overlay = $("#settings-overlay");
  if (!overlay) return;
  overlay.setAttribute("hidden", "");
  overlay.setAttribute("aria-hidden", "true");
}

function fillForm(s) {
  $("#set-model-dir").value       = s.modelDir || "";
  $("#set-engine-dir").value      = s.engineDir || "";
  $("#set-theme").value           = s.theme || "system";
  $("#set-hwaccel").value         = s.hardwareAccel || "auto";
  $("#set-telemetry").checked     = !!s.telemetry;
  $("#set-allowlist-advanced").checked = !!s.allowlistAdvanced;
  $("#set-allowlist").value       = (s.allowlist || []).join(", ");
  $("#set-allowlist").disabled    = !s.allowlistAdvanced;
  $("#set-allowlist-hint").textContent = s.allowlistAdvanced
    ? "Editing host allowlist affects future downloads."
    : "Advanced editing is disabled. Enable above to modify.";
}

function trapFocus(root) {
  const sel = 'a[href], button:not([disabled]), textarea:not([disabled]), input:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])';
  const focusables = Array.from(root.querySelectorAll(sel));
  if (focusables[0]) focusables[0].focus();
  const handler = (e) => {
    if (e.key !== "Tab") return;
    const list = Array.from(root.querySelectorAll(sel));
    if (!list.length) return;
    const first = list[0], last = list[list.length - 1];
    if (e.shiftKey && document.activeElement === first) { last.focus(); e.preventDefault(); }
    else if (!e.shiftKey && document.activeElement === last) { first.focus(); e.preventDefault(); }
  };
  root._trapHandler = handler;
  root.addEventListener("keydown", handler);
}

function readForm() {
  const allowAdvanced = $("#set-allowlist-advanced").checked;
  const allowRaw = $("#set-allowlist").value || "";
  const allowlist = allowAdvanced
    ? allowRaw.split(/[,\s]+/).map((h) => h.trim().toLowerCase()).filter(Boolean)
    : null;          // null = keep what's in main
  return {
    modelDir:        $("#set-model-dir").value.trim(),
    engineDir:       $("#set-engine-dir").value.trim(),
    theme:           $("#set-theme").value,
    hardwareAccel:   $("#set-hwaccel").value,
    telemetry:       $("#set-telemetry").checked,
    allowlistAdvanced: allowAdvanced,
    ...(allowlist ? { allowlist } : {}),
  };
}

export function wireSettings() {
  const overlay = $("#settings-overlay");
  if (!overlay) return;

  overlay.addEventListener("click", (e) => {
    if (e.target === overlay) closeSettings();
  });

  $("#set-allowlist-advanced").addEventListener("change", (e) => {
    $("#set-allowlist").disabled = !e.target.checked;
    $("#set-allowlist-hint").textContent = e.target.checked
      ? "Editing host allowlist affects future downloads."
      : "Advanced editing is disabled. Enable above to modify.";
  });

  $("#btn-pick-model-dir").addEventListener("click", async () => {
    const p = await api.pickFolder();
    if (p) $("#set-model-dir").value = p;
  });
  $("#btn-pick-engine-dir").addEventListener("click", async () => {
    const p = await api.pickFolder();
    if (p) $("#set-engine-dir").value = p;
  });

  $("#btn-settings-cancel").addEventListener("click", closeSettings);
  $("#btn-settings-save").addEventListener("click", async () => {
    const next = readForm();
    try {
      const saved = await api.putSettings(next);
      setState({ settings: saved });
      applyTheme();
      toast("设置已保存", { kind: "ok" });
      closeSettings();
    } catch (_) { /* toast already shown */ }
  });

  $("#btn-settings-reset").addEventListener("click", async () => {
    try {
      const saved = await api.putSettings({
        theme: "system", hardwareAccel: "auto", telemetry: false,
        allowlistAdvanced: false, allowlist: [], modelDir: "", engineDir: "",
      });
      setState({ settings: saved });
      fillForm(saved);
      applyTheme();
      toast("设置已重置", { kind: "ok" });
    } catch (_) {}
  });

  // Preview creative generation-wait animation
  $("#btn-preview-genwait")?.addEventListener("click", () => {
    closeSettings();
    const wait = showGenerationWait({
      title: "正在生成",
      captions: ["构思中…", "调用引擎…", "采样像素…", "优化细节…", "即将完成…"],
      showProgress: true,
      showCancel: true,
      indeterminate: false,
      onCancel: () => toast("生成已取消", { kind: "warn" }),
    });
    // Demo progress: 0 → 100 over 8 s, then auto-hide
    let p = 0;
    const timer = setInterval(() => {
      p += Math.random() * 9 + 2;
      if (p >= 100) {
        p = 100;
        wait.setProgress(p, "演示完成");
        clearInterval(timer);
        setTimeout(hideGenerationWait, 600);
      } else {
        wait.setProgress(p, `${formatBytes(Math.floor(p * 1.2e6))} / 120 MB`);
      }
    }, 360);
  });

  // Global open trigger (sidebar / titlebar)
  document.addEventListener("click", (e) => {
    const t = e.target.closest("[data-action=open-settings]");
    if (t) { e.preventDefault(); openSettings().catch(() => {}); }
  });

  // Close on Escape
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && !overlay.hasAttribute("hidden")) closeSettings();
  });
}
