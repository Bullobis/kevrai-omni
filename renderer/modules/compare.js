// renderer/modules/compare.js — 模型对比（并排表格 + 差异高亮 + 持久化）。
//
// 设计目标与 favorites.js 对齐：
//   · 只持久化模型 id（不存完整对象，避免 catalog 更新后数据过期）到
//     localStorage["kevrai:compare"]，上限 COMPARE_MAX=4。
//   · 每次 localStorage 访问都包 try/catch（隐身窗口 / opaque origin 降级为
//     会话内内存态，不崩市场）。
//   · 完整模型对象由 models.js 在 renderCard 时通过 registerModel() 注册进
//     模块内的 Map；打开对比模态时按 id 解析。不在当前视图里的模型至少保留 id。
//   · 维度取值 / 差异判定是纯函数（dimensionDisplay / rowHasDiff），无 DOM
//     依赖，便于 node --test 单测。
//   · 模态复用 overlay-fx（过渡）+ focus-return（焦点陷阱 / Esc / 焦点归还）。
"use strict";
import { toast } from "./toast.js";
import { t } from "./i18n.js";
import { escapeHtml } from "./net.js";
import { recordFocus, restoreFocus, trapFocus, registerEsc } from "./focus-return.js";
import { overlayOpen, overlayClose } from "./overlay-fx.js";

const COMPARE_KEY = "kevrai:compare";
export const COMPARE_MAX = 4;
export const COMPARE_MISSING = "—";

// ── storage（惰性解析，与 favorites.js 一致的防御写法）──────────────────────
function getStorage() {
  try {
    if (typeof localStorage !== "undefined" && localStorage) return localStorage;
  } catch (_) { /* opaque origin / disabled */ }
  try {
    if (typeof window !== "undefined" && window.localStorage) return window.localStorage;
  } catch (_) { /* ignore */ }
  return null;
}

function readList() {
  const s = getStorage();
  if (!s) return [];
  try {
    const raw = s.getItem(COMPARE_KEY);
    if (!raw) return [];
    const arr = JSON.parse(raw);
    return Array.isArray(arr) ? arr.filter((x) => typeof x === "string" && x) : [];
  } catch (_) {
    return [];
  }
}

function writeList(arr) {
  const s = getStorage();
  if (!s) return;
  try { s.setItem(COMPARE_KEY, JSON.stringify(arr)); } catch (_) { /* quota/privacy */ }
}

function normalizeId(modelId) {
  return String(modelId || "").trim();
}

// ── 模型注册表：id -> 完整模型对象 ──────────────────────────────────────────
const modelRegistry = new Map();

/** models.js 在 renderCard 时调用，把完整模型对象登记进来供对比模态解析。 */
export function registerModel(model) {
  if (!model || !model.id) return;
  modelRegistry.set(model.id, model);
}

// ── 选择管理（纯 id 数组，新增/移除均广播 kevrai:compare-changed）────────────
function broadcast() {
  if (typeof window === "undefined" || !window) return;
  try { window.dispatchEvent(new CustomEvent("kevrai:compare-changed")); } catch (_) {}
}

/** 当前对比中的模型 id 数组（按加入顺序）。 */
export function getCompareList() {
  return readList();
}

/**
 * 把模型加入对比。返回 true 表示这次确实加入了。
 * 已在列表中 / 空 id / 超过上限 均返回 false（超上限时 toast 提示）。
 */
export function addToCompare(model) {
  const id = normalizeId(model && model.id);
  if (!id) return false;
  const list = readList();
  if (list.includes(id)) return false;
  if (list.length >= COMPARE_MAX) {
    try { toast(t("compare.maxReached", { n: COMPARE_MAX }), { kind: "warn" }); } catch (_) {}
    return false;
  }
  list.push(id);
  writeList(list);
  broadcast();
  return true;
}

/** 按 id 移除。返回是否真的移除了。 */
export function removeFromCompare(modelId) {
  const id = normalizeId(modelId);
  if (!id) return false;
  const list = readList();
  const idx = list.indexOf(id);
  if (idx === -1) return false;
  list.splice(idx, 1);
  writeList(list);
  broadcast();
  return true;
}

/** 清空对比。 */
export function clearCompare() {
  writeList([]);
  broadcast();
}

export function isInCompare(modelId) {
  const id = normalizeId(modelId);
  if (!id) return false;
  return readList().includes(id);
}

/**
 * 切换对比状态。返回切换后的新状态（true = 现在在对比中）。
 * 已在列表中 → 移除；不在 → 尝试加入（受上限约束）。
 */
export function toggleCompare(model) {
  const id = normalizeId(model && model.id);
  if (!id) return false;
  if (isInCompare(id)) {
    removeFromCompare(id);
    return false;
  }
  return addToCompare(model);
}

/**
 * 把对比中的 id 列表解析成完整模型对象数组。
 * 注册表里有的用注册对象；没有的退化为 { id }（模态里缺失字段显示 —）。
 */
export function getCompareModels() {
  return readList().map((id) => modelRegistry.get(id) || { id });
}

// ── 维度取值 / 差异判定（纯函数，无 DOM）─────────────────────────────────────
// 对比维度顺序即模态行顺序。labelKey 指向 i18n compare.dimensions.*。
export const COMPARE_DIMENSIONS = [
  { key: "name",        labelKey: "compare.dimensions.name" },
  { key: "category",    labelKey: "compare.dimensions.category" },
  { key: "size",        labelKey: "compare.dimensions.size" },
  { key: "license",     labelKey: "compare.dimensions.license" },
  { key: "engine",      labelKey: "compare.dimensions.engine" },
  { key: "vram",        labelKey: "compare.dimensions.vram" },
  { key: "repo",        labelKey: "compare.dimensions.repo" },
  { key: "description", labelKey: "compare.dimensions.description" },
  { key: "trending",    labelKey: "compare.dimensions.trending" },
  { key: "tags",        labelKey: "compare.dimensions.tags" },
];

/**
 * 取某个模型在某维度上的展示字符串。catalog 中不存在的字段一律返回 "—"，
 * 绝不编造参数量 / 发布时间等不存在的字段。
 */
export function dimensionDisplay(m, key) {
  if (!m) return COMPARE_MISSING;
  switch (key) {
    case "name":
      return m.name || m.id || COMPARE_MISSING;
    case "category": {
      const parts = [m.category, m.task, m.modality].filter(Boolean);
      return parts.length ? parts.join(" / ") : COMPARE_MISSING;
    }
    case "size":
      return (m.size_gb != null && m.size_gb !== "")
        ? `${Number(m.size_gb).toFixed(1)} GB` : COMPARE_MISSING;
    case "license":
      return m.license || COMPARE_MISSING;
    case "engine": {
      const e = Array.isArray(m.engine) ? m.engine : (m.engine ? [m.engine] : []);
      return e.length ? e.join(", ") : COMPARE_MISSING;
    }
    case "vram": {
      const hw = m.hardware || {};
      const v = hw.vram_gb || hw.min_vram_gb;
      return v ? `${v} GB` : COMPARE_MISSING;
    }
    case "repo":
      return m.repo || m.hub_display || m.hub || COMPARE_MISSING;
    case "description": {
      const d = String(m.description || "").trim();
      if (!d) return COMPARE_MISSING;
      return d.length > 80 ? d.slice(0, 80) + "…" : d;
    }
    case "trending":
      return m.trending ? "🔥" : COMPARE_MISSING;
    case "tags": {
      const tags = Array.isArray(m.tags) ? m.tags : [];
      return tags.length ? tags.join(", ") : COMPARE_MISSING;
    }
    default:
      return COMPARE_MISSING;
  }
}

/**
 * 某一行各模型的值是否「不全相同」→ 决定是否高亮差异行。
 * 少于 2 个值不构成差异；空值归一到 "—" 后比较字符串。
 */
export function rowHasDiff(values) {
  const vals = (values || []).map((v) => (v == null || v === "" ? COMPARE_MISSING : String(v)));
  if (vals.length <= 1) return false;
  const first = vals[0];
  return vals.some((v) => v !== first);
}

// ── 模态（DOM 接线；仅在浏览器环境生效）─────────────────────────────────────
const hasDoc = typeof document !== "undefined";
let savedTrigger = null;
let detachEsc = null;

function overlayEl() { return hasDoc ? document.getElementById("compare-overlay") : null; }

/** 更新工具栏对比按钮上的数量 badge。 */
export function refreshCompareBadge() {
  const badge = hasDoc ? document.getElementById("compare-badge") : null;
  if (!badge) return;
  const n = getCompareList().length;
  badge.textContent = String(n);
  badge.hidden = n === 0;
  const entry = document.getElementById("btn-open-compare");
  if (entry) entry.classList.toggle("has-count", n > 0);
}

function renderCompareTable() {
  const body = hasDoc ? document.getElementById("compare-body") : null;
  const count = document.getElementById("compare-count");
  if (!body) return;
  const models = getCompareModels();
  if (count) {
    count.textContent = t("compare.count", { n: models.length });
  }
  if (!models.length) {
    body.innerHTML =
      `<div class="compare-empty">` +
      `<p class="compare-empty-title">${escapeHtml(t("compare.empty"))}</p>` +
      `<p class="mut compare-empty-hint">${escapeHtml(t("compare.emptyHint"))}</p>` +
      `</div>`;
    return;
  }
  const headCells = models.map((m) => {
    const name = escapeHtml(m.name || m.id || "?");
    return `<th class="compare-col">` +
      `<div class="compare-model-head">` +
      `<span class="compare-model-name" title="${name}">${name}</span>` +
      `<button type="button" class="compare-remove" data-compare-remove="${escapeHtml(m.id || "")}" ` +
      `aria-label="${escapeHtml(t("compare.remove"))} ${name}" title="${escapeHtml(t("compare.remove"))}">×</button>` +
      `</div></th>`;
  }).join("");

  const rows = COMPARE_DIMENSIONS.map((dim) => {
    const displayVals = models.map((m) => dimensionDisplay(m, dim.key));
    const diff = rowHasDiff(displayVals);
    const tds = displayVals.map((v) =>
      `<td class="${v === COMPARE_MISSING ? "mut" : ""}">${escapeHtml(v)}</td>`
    ).join("");
    return `<tr class="${diff ? "compare-diff" : ""}">` +
      `<th class="compare-dim">${escapeHtml(t(dim.labelKey))}</th>${tds}</tr>`;
  }).join("");

  body.innerHTML =
    `<div class="compare-scroll"><table class="compare-table">` +
    `<thead><tr><th class="compare-corner"></th>${headCells}</tr></thead>` +
    `<tbody>${rows}</tbody></table></div>`;

  body.querySelectorAll("[data-compare-remove]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const id = btn.getAttribute("data-compare-remove");
      if (id) removeFromCompare(id);
      renderCompareTable();
      refreshCompareBadge();
    });
  });
}

export function openCompare() {
  const overlay = overlayEl();
  if (!overlay) return;
  savedTrigger = recordFocus();
  overlayOpen(overlay);
  renderCompareTable();
  refreshCompareBadge();
  trapFocus(overlay);
}

export function closeCompare() {
  const overlay = overlayEl();
  if (!overlay) return;
  restoreFocus(savedTrigger);
  savedTrigger = null;
  overlayClose(overlay);
}

/** 接线：工具栏按钮、遮罩点击、清空/关闭按钮、Esc、compare-changed 广播。 */
export function wireCompare() {
  if (!hasDoc) return;
  const overlay = overlayEl();
  if (!overlay) return;

  document.addEventListener("click", (e) => {
    const opener = e.target.closest && e.target.closest("[data-action=open-compare]");
    if (opener) { e.preventDefault(); openCompare(); }
  });

  overlay.addEventListener("click", (e) => {
    if (e.target === overlay) closeCompare();
  });

  document.getElementById("btn-compare-close")?.addEventListener("click", closeCompare);
  document.querySelector("[data-action=close-compare]")?.addEventListener("click", closeCompare);

  document.getElementById("btn-compare-clear")?.addEventListener("click", () => {
    clearCompare();
    closeCompare();
    try { toast(t("compare.cleared"), { kind: "ok" }); } catch (_) {}
  });

  // Esc 关闭（优先级低于命令面板/设置）。
  detachEsc = registerEsc({
    order: 70,
    root: overlay,
    isOpen: () => !overlay.hasAttribute("hidden"),
    close: closeCompare,
  });

  // 卡片上的对比按钮切换后刷新 badge；模态开着则重渲染表格。
  window.addEventListener("kevrai:compare-changed", () => {
    refreshCompareBadge();
    if (!overlay.hasAttribute("hidden")) renderCompareTable();
  });

  refreshCompareBadge();
}
