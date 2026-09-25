// renderer/modules/data-portability.js — 用户数据可移植性：导出 / 导入。
//
// 数据分散在两处：
//   1. 后端设置（api.getSettings / api.putSettings）：模型目录、引擎目录、主题、
//      硬件加速、遥测、白名单、HF / ModelScope Token。
//   2. localStorage：kevrai:favorites（收藏）、kevrai:recent（最近使用）、
//      kevrai:cmd-recent（最近命令）、kevrai:sidebar-expanded（侧边栏）、
//      kevrai-locale（语言）。
//
// preload 没有文件保存 API，所以导出走纯前端 Blob + <a download> 方案。
// 导入流程严格先校验、后写入：校验失败时抛错，绝不触碰现有数据。
"use strict";
import { api } from "./api.js";
import { state } from "./state.js";
import { applyTheme } from "./theme.js";

export const EXPORT_VERSION = 1;
export const RECENT_MAX = 20;

const FAV_KEY = "kevrai:favorites";
const RECENT_KEY = "kevrai:recent";
const CMD_RECENT_KEY = "kevrai:cmd-recent";
const SIDEBAR_KEY = "kevrai:sidebar-expanded";
const LOCALE_KEY = "kevrai-locale";

// ── 安全存储读写（与 favorites.js 同样的惰性解析 + try/catch）─────────────────
// 隐私模式 / opaque origin 下 localStorage 会抛 SecurityError，这里退化为静默 no-op。
function getStorage() {
  try {
    if (typeof localStorage !== "undefined" && localStorage) return localStorage;
  } catch (_) { /* fall through */ }
  try {
    if (typeof window !== "undefined" && window.localStorage) return window.localStorage;
  } catch (_) { /* ignore */ }
  return null;
}

function safeGet(key) {
  const s = getStorage();
  if (!s) return null;
  try { return s.getItem(key); } catch (_) { return null; }
}

function safeSet(key, value) {
  const s = getStorage();
  if (!s) return;
  try { s.setItem(key, value); } catch (_) { /* quota / privacy */ }
}

// 读 string[] 类型的 localStorage 键，非字符串元素被过滤（与 favorites.js 一致）。
function readStringArray(key) {
  const raw = safeGet(key);
  if (!raw) return [];
  try {
    const arr = JSON.parse(raw);
    return Array.isArray(arr) ? arr.filter((x) => typeof x === "string" && x) : [];
  } catch (_) { return []; }
}

function readObject(key) {
  const raw = safeGet(key);
  if (!raw) return {};
  try {
    const obj = JSON.parse(raw);
    return obj && typeof obj === "object" && !Array.isArray(obj) ? obj : {};
  } catch (_) { return {}; }
}

// 尝试拿到应用版本号：preload → DOM(#version-line) → "unknown"。
async function getAppVersion() {
  try {
    const b = (typeof window !== "undefined") ? window.kevrai : null;
    if (b && typeof b.getAppVersion === "function") {
      const v = await b.getAppVersion();
      if (typeof v === "string" && v) return v;
    }
  } catch (_) { /* old preload */ }
  try {
    const el = document.getElementById("version-line");
    if (el && el.textContent) return el.textContent.trim().replace(/^v/, "");
  } catch (_) { /* no DOM */ }
  return "unknown";
}

function fileTimestamp(d = new Date()) {
  const p = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}${p(d.getMonth() + 1)}${p(d.getDate())}`
    + `-${p(d.getHours())}${p(d.getMinutes())}${p(d.getSeconds())}`;
}

// ── 导出 ──────────────────────────────────────────────────────────────────────

/**
 * exportData() — 收集设置 + localStorage 数据，返回可序列化的备份对象。
 * 不直接触发下载（见 downloadExport）。后端设置读取失败时退化为 {}，不阻断导出。
 */
export async function exportData() {
  let settings = {};
  try {
    settings = await api.getSettings();
    if (!settings || typeof settings !== "object") settings = {};
  } catch (_) { /* toast 已由 api.wrap 展示；继续导出其余数据 */ }

  const favorites = readStringArray(FAV_KEY);
  const recent = readStringArray(RECENT_KEY);
  const cmdRecent = readObject(CMD_RECENT_KEY);

  const sidebarRaw = safeGet(SIDEBAR_KEY);
  const locale = safeGet(LOCALE_KEY) || null;

  return {
    kevraiExportVersion: EXPORT_VERSION,
    exportedAt: new Date().toISOString(),
    appVersion: await getAppVersion(),
    data: {
      settings,
      favorites,
      recent,
      cmdRecent,
      preferences: {
        theme: settings.theme || "system",
        sidebarExpanded: sidebarRaw === "1",
        locale,
      },
    },
  };
}

/**
 * downloadExport() — 导出并通过 <a download> 触发浏览器下载。
 * 返回 Promise<boolean>：成功 true，失败 false。
 */
export async function downloadExport() {
  try {
    const payload = await exportData();
    const blob = new Blob([JSON.stringify(payload, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `kevrai-backup-${fileTimestamp()}.json`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => { try { URL.revokeObjectURL(url); } catch (_) {} }, 1000);
    return true;
  } catch (_) {
    return false;
  }
}

// ── 导入：解析与校验（纯同步，失败抛带中文消息的 Error）─────────────────────────

/**
 * parseImport(fileText) — 严格校验备份文件文本。
 * 校验通过返回规范化对象；任何一步失败抛 Error，消息面向用户。
 * 此函数绝不写入任何数据。
 */
export function parseImport(fileText) {
  let obj;
  try {
    obj = JSON.parse(String(fileText));
  } catch (_) {
    throw new Error("文件损坏：不是有效的 JSON");
  }
  if (!obj || typeof obj !== "object") {
    throw new Error("文件损坏：根节点必须是对象");
  }
  if (typeof obj.kevraiExportVersion !== "number" || !Number.isFinite(obj.kevraiExportVersion)) {
    throw new Error("文件损坏：缺少导出版本号（kevraiExportVersion）");
  }
  const version = obj.kevraiExportVersion;
  if (version > EXPORT_VERSION) {
    throw new Error(`不支持的导出版本 v${version}，需要更新应用`);
  }
  if (version < 1) {
    throw new Error(`版本不兼容：需要 v1，得到 v${version}`);
  }
  if (!obj.data || typeof obj.data !== "object" || Array.isArray(obj.data)) {
    throw new Error("文件损坏：缺少 data 对象");
  }
  const d = obj.data;

  // favorites：缺失 → []；存在但不是数组 → 抛错；数组则过滤非字符串。
  let favorites = [];
  if (d.favorites !== undefined) {
    if (!Array.isArray(d.favorites)) throw new Error("数据格式错误：favorites 不是数组");
    favorites = d.favorites.filter((x) => typeof x === "string" && x);
  }
  // recent：同上。
  let recent = [];
  if (d.recent !== undefined) {
    if (!Array.isArray(d.recent)) throw new Error("数据格式错误：recent 不是数组");
    recent = d.recent.filter((x) => typeof x === "string" && x);
  }
  // cmdRecent：缺失 → {}；存在但不是对象 → 抛错。
  let cmdRecent = {};
  if (d.cmdRecent !== undefined) {
    if (!d.cmdRecent || typeof d.cmdRecent !== "object" || Array.isArray(d.cmdRecent)) {
      throw new Error("数据格式错误：cmdRecent 不是对象");
    }
    cmdRecent = d.cmdRecent;
  }
  // settings：存在则必须是对象；缺失保持 undefined（由 importData 决定如何处理）。
  let settings;
  if (d.settings !== undefined) {
    if (!d.settings || typeof d.settings !== "object" || Array.isArray(d.settings)) {
      throw new Error("数据格式错误：settings 不是对象");
    }
    settings = d.settings;
  }
  // preferences：可选对象。
  let preferences;
  if (d.preferences !== undefined) {
    if (!d.preferences || typeof d.preferences !== "object" || Array.isArray(d.preferences)) {
      throw new Error("数据格式错误：preferences 不是对象");
    }
    preferences = d.preferences;
  }

  return { version, data: { settings, favorites, recent, cmdRecent, preferences } };
}

// 合并去重：imported 排在前面，existing 补充其后（保持导入优先级）。
function mergePrepend(imported, existing, cap) {
  const seen = new Set();
  const out = [];
  for (const id of [...imported, ...existing]) {
    if (typeof id !== "string" || !id) continue;
    if (seen.has(id)) continue;
    seen.add(id);
    out.push(id);
    if (cap && out.length >= cap) break;
  }
  return out;
}

/**
 * importData(parsed, mode) — 把已校验的数据写入 localStorage 并持久化后端设置。
 * mode: "merge" | "replace"。
 * 返回 { favorites, recent, settings } 统计。
 */
export async function importData(parsed, mode) {
  if (!parsed || !parsed.data) throw new Error("导入失败：数据未校验");
  const d = parsed.data;

  const curFavorites = readStringArray(FAV_KEY);
  const curRecent = readStringArray(RECENT_KEY);
  const curCmd = readObject(CMD_RECENT_KEY);
  const curSettings = (state && state.settings && typeof state.settings === "object")
    ? state.settings : {};

  let nextFavorites, nextRecent, nextCmd, nextSettings;

  if (mode === "replace") {
    nextFavorites = (d.favorites || []).slice();
    nextRecent = (d.recent || []).slice().slice(0, RECENT_MAX);
    nextCmd = d.cmdRecent ? { ...d.cmdRecent } : {};
    // replace：用导入的 settings 整体替换；若备份未带 settings 则保留现有（避免误清空）。
    nextSettings = d.settings ? { ...d.settings } : { ...curSettings };
  } else {
    // merge：导入优先，现有补充；recent 导入项排前，上限 20。
    nextFavorites = mergePrepend(d.favorites || [], curFavorites);
    nextRecent = mergePrepend(d.recent || [], curRecent, RECENT_MAX);
    nextCmd = { ...curCmd, ...(d.cmdRecent || {}) };
    nextSettings = { ...curSettings, ...(d.settings || {}) };
  }

  // —— 全部计算完成后才写入（写入本身不会再失败抛断，最坏静默）——
  safeSet(FAV_KEY, JSON.stringify(nextFavorites));
  safeSet(RECENT_KEY, JSON.stringify(nextRecent));
  safeSet(CMD_RECENT_KEY, JSON.stringify(nextCmd));

  // preferences（侧边栏 / locale）在写入 localStorage，由 applyImportedPreferences 刷新 UI。
  if (d.preferences) {
    if (typeof d.preferences.sidebarExpanded === "boolean") {
      safeSet(SIDEBAR_KEY, d.preferences.sidebarExpanded ? "1" : "0");
    }
    if (typeof d.preferences.locale === "string" && d.preferences.locale) {
      safeSet(LOCALE_KEY, d.preferences.locale);
    }
  }

  // 持久化后端设置（失败由 api.wrap 弹 toast，但不回滚 localStorage——用户已确认导入）。
  let settingsOk = false;
  try {
    await api.putSettings(nextSettings);
    settingsOk = true;
    // 同步内存状态，让 applyTheme / 设置表单拿到最新值。
    if (state) { try { state.settings = nextSettings; } catch (_) {} }
  } catch (_) { /* toast 已由 api.wrap 展示 */ }

  return { favorites: nextFavorites.length, recent: nextRecent.length, settings: settingsOk };
}

/**
 * applyImportedPreferences(parsed) — 应用主题 / 侧边栏 / locale，并广播事件让各模块刷新。
 * 至少触发：主题重应用、收藏状态刷新（通过事件）。
 */
export function applyImportedPreferences(parsed) {
  const prefs = parsed && parsed.data && parsed.data.preferences;
  if (prefs) {
    // 主题：写入内存状态后重应用（applyTheme 读 state.settings.theme）。
    if (typeof prefs.theme === "string" && state) {
      try {
        if (!state.settings) state.settings = {};
        state.settings.theme = prefs.theme;
      } catch (_) {}
    }
    // 侧边栏：切类（元素不存在则跳过）。
    if (typeof prefs.sidebarExpanded === "boolean") {
      try {
        const shell = document.querySelector(".app-shell");
        const sidebar = document.querySelector(".sidebar");
        const toggle = document.getElementById("sidebar-toggle");
        shell && shell.classList.toggle("sidebar-expanded", prefs.sidebarExpanded);
        sidebar && sidebar.classList.toggle("expanded", prefs.sidebarExpanded);
        if (toggle) {
          toggle.textContent = prefs.sidebarExpanded ? "⟨" : "☰";
          toggle.setAttribute("aria-label",
            prefs.sidebarExpanded ? "收起侧边栏" : "展开侧边栏");
        }
      } catch (_) {}
    }
  }
  // 重应用主题（即使 preferences 缺失也安全：读 state.settings.theme）。
  try { applyTheme(); } catch (_) {}

  // 广播：收藏筛选 / 模型卡片收藏按钮 / 命令面板等各自监听后刷新。
  try {
    window.dispatchEvent(new CustomEvent("kevrai:data-imported", {
      detail: { favorites: true, recent: true, settings: true },
    }));
  } catch (_) {}
}
