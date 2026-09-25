// renderer/modules/settings.js — Settings overlay (open / save).
"use strict";
import { api } from "./api.js";
import { toast } from "./toast.js";
import { state, setState } from "./state.js";
import { applyTheme } from "./theme.js";
import { showGenerationWait, hideGenerationWait } from "./generation-wait.js";
import { recordFocus, restoreFocus, trapFocus, registerEsc } from "./focus-return.js";

const $  = (s) => document.querySelector(s);
const $$ = (s) => Array.from(document.querySelectorAll(s));

function formatBytes(b) {
  if (b == null || b < 0) return "";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let i = 0;
  while (b >= 1024 && i < units.length - 1) { b /= 1024; i++; }
  return `${b.toFixed(i === 0 ? 0 : 1)} ${units[i]}`;
}

// 打开设置前记录触发元素，关闭时归还焦点（a11y）。
let savedTrigger = null;

export async function openSettings() {
  const overlay = $("#settings-overlay");
  if (!overlay) return;
  savedTrigger = recordFocus();
  overlay.removeAttribute("hidden");
  overlay.setAttribute("aria-hidden", "false");

  // 每次打开都回到「通用」分类，避免停留在上次关闭时的非可见分类。
  switchSettingsSection("general");
  // 同步关于页版本号（侧栏版本行由 health 轮询写入）。
  const ver = $("#version-line");
  const aboutVer = $("#settings-version");
  if (ver && aboutVer && ver.textContent) aboutVer.textContent = ver.textContent;

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
  // 焦点归还给打开设置前的触发元素。
  restoreFocus(savedTrigger);
  savedTrigger = null;
}

// 设置中心左侧分类切换：高亮对应导航按钮，只显示对应内容分区。
// 其它分区保持 hidden，焦点陷阱与 readForm/fillForm 均按 ID 工作，不受影响。
export function switchSettingsSection(name) {
  const navBtns = $$(".settings-nav-btn");
  const sections = $$(".settings-section");
  if (!navBtns.length || !sections.length) return;
  navBtns.forEach((b) => b.classList.toggle("active", b.dataset.section === name));
  sections.forEach((s) => { s.hidden = s.dataset.section !== name; });
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
  if ($("#set-hf-token")) $("#set-hf-token").value = s.hfToken || "";
  if ($("#set-ms-token")) $("#set-ms-token").value = s.msToken || "";
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
    // v2.8.0 — tokens are persisted by Electron's saveSettings whitelist and
    // synced to the Python sidecar (it makes the remote requests).
    hfToken:         $("#set-hf-token") ? $("#set-hf-token").value.trim() : "",
    msToken:         $("#set-ms-token") ? $("#set-ms-token").value.trim() : "",
    ...(allowlist ? { allowlist } : {}),
  };
}

export function wireSettings() {
  const overlay = $("#settings-overlay");
  if (!overlay) return;

  overlay.addEventListener("click", (e) => {
    if (e.target === overlay) closeSettings();
  });

  // 左侧分类导航：点击切换右侧内容分区。
  overlay.querySelectorAll(".settings-nav-btn").forEach((btn) => {
    btn.addEventListener("click", () => switchSettingsSection(btn.dataset.section));
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

  // Close on Escape（纳入全局优先级栈：命令面板/快捷键面板 > 设置）。
  // editableFirst：焦点在输入框时第一次 Esc 仅失焦，第二次才关设置。
  registerEsc({
    order: 80,
    root: overlay,
    isOpen: () => !overlay.hasAttribute("hidden"),
    close: closeSettings,
    editableFirst: true,
  });
}
