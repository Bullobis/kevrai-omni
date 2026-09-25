// renderer/modules/settings.js — Settings overlay (open / save).
"use strict";
import { api } from "./api.js";
import { toast } from "./toast.js";
import { state, setState } from "./state.js";
import { applyTheme } from "./theme.js";
import { showGenerationWait, hideGenerationWait } from "./generation-wait.js";
import { recordFocus, restoreFocus, trapFocus, registerEsc } from "./focus-return.js";
import { overlayOpen, overlayClose } from "./overlay-fx.js";
import {
  downloadExport, parseImport, importData, applyImportedPreferences,
} from "./data-portability.js";
import { t, getLocale, setLocale } from "./i18n.js";

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
  overlayOpen(overlay);   // 淡入+上移过渡（替代直接 removeAttribute hidden）

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
  // 焦点立即归还（不延迟），hidden 由 overlay-fx 在淡出后设置。
  restoreFocus(savedTrigger);
  savedTrigger = null;
  overlayClose(overlay);
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
    ? t("settings.allowlistEnabledHint")
    : t("settings.allowlistDisabledHint");
  const localeEl = $("#set-locale");
  if (localeEl) localeEl.value = getLocale();
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
      ? t("settings.allowlistEnabledHint")
      : t("settings.allowlistDisabledHint");
  });

  // 语言切换：立即应用（setLocale 会重新 bindI18n 并派发事件）。
  $("#set-locale")?.addEventListener("change", (e) => {
    setLocale(e.target.value);
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
      toast(t("settings.saved"), { kind: "ok" });
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
      toast(t("settings.resetDone"), { kind: "ok" });
    } catch (_) {}
  });

  // ── 数据管理：导出 / 导入 ──────────────────────────────────────────────
  // 已选中并通过校验的备份内容（在文件选择后暂存，等待用户选合并/替换）。
  let pendingParsed = null;
  const importConfirm = $("#import-confirm");
  const importConfirmText = $("#import-confirm-text");
  const importFileInput = $("#import-file-input");

  const hideImportConfirm = () => {
    pendingParsed = null;
    if (importConfirm) importConfirm.hidden = true;
    if (importFileInput) importFileInput.value = "";
  };

  $("#btn-export-data")?.addEventListener("click", async () => {
    const ok = await downloadExport();
    if (ok) toast(t("settings.exportOk"), { kind: "ok" });
    else toast(t("settings.exportFailed"), { kind: "err" });
  });

  $("#btn-import-data")?.addEventListener("click", () => {
    if (importFileInput) importFileInput.click();
  });

  importFileInput?.addEventListener("change", async () => {
    const file = importFileInput.files && importFileInput.files[0];
    if (!file) return;
    try {
      const text = await file.text();
      pendingParsed = parseImport(text);
      const d = pendingParsed.data;
      if (importConfirmText) {
        importConfirmText.textContent = t("settings.importConfirmText", {
          file: file.name,
          favs: (d.favorites || []).length,
          recents: (d.recent || []).length,
        });
      }
      if (importConfirm) importConfirm.hidden = false;
    } catch (err) {
      pendingParsed = null;
      toast(err.message || t("settings.importFailed"), { kind: "err" });
      importFileInput.value = "";
    }
  });

  const runImport = async (mode) => {
    if (!pendingParsed) return;
    try {
      const stats = await importData(pendingParsed, mode);
      applyImportedPreferences(pendingParsed);
      toast(t("settings.importOk", { favs: stats.favorites, recents: stats.recent }), { kind: "ok" });
      hideImportConfirm();
    } catch (err) {
      toast(err.message || t("settings.importFailed"), { kind: "err" });
    }
  };

  $("#btn-import-merge")?.addEventListener("click", () => { runImport("merge").catch(() => {}); });
  $("#btn-import-replace")?.addEventListener("click", () => { runImport("replace").catch(() => {}); });
  $("#btn-import-cancel")?.addEventListener("click", hideImportConfirm);

  // Preview creative generation-wait animation
  $("#btn-preview-genwait")?.addEventListener("click", () => {
    closeSettings();
    const wait = showGenerationWait({
      title: t("settings.genWaitTitle"),
      captions: [
        t("settings.genWaitCaption1"), t("settings.genWaitCaption2"),
        t("settings.genWaitCaption3"), t("settings.genWaitCaption4"),
        t("settings.genWaitCaption5"),
      ],
      showProgress: true,
      showCancel: true,
      indeterminate: false,
      onCancel: () => toast(t("settings.genWaitCanceled"), { kind: "warn" }),
    });
    // Demo progress: 0 → 100 over 8 s, then auto-hide
    let p = 0;
    const timer = setInterval(() => {
      p += Math.random() * 9 + 2;
      if (p >= 100) {
        p = 100;
        wait.setProgress(p, t("settings.genWaitDone"));
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
