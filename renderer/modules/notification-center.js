// renderer/modules/notification-center.js — 通知中心：铃铛 + 未读 badge + 历史面板。
//
// 自动收录每条 toast（toast.js 在创建时调用 recordNotification）。历史持久化到
// localStorage，支持单条删除、全部标记已读、清空、上限裁剪。相同“签名”（kind+
// title+body）的通知自动合并计数并置顶（显示 ×N），避免重复警告淹没有效通知。
// 核心状态操作为纯函数（便于单测），DOM 渲染与交互在 init 中。
"use strict";

import { t } from "./i18n.js";

const MAX_ITEMS = 50;
const STORE_KEY = "kevrai:notifications";

// ── 纯函数（无副作用，测试入口）──────────────────────────────────────────────
let _seq = 0;
function nextId() { _seq += 1; return `n${Date.now().toString(36)}-${_seq}`; }

// 归一化：去首尾空白、压缩内部空白。不区分大小写——大小写不同视为不同内容，避免误合并。
function norm(s) { return String(s == null ? "" : s).trim().replace(/\s+/g, " "); }

// 通知签名：kind + title + body。时间戳、计数等可变量不参与，使网络抖动产生的
// 同一条“来源不可达”警告可被识别为同一通知。
export function signOf(entry) {
  const e = entry || {};
  return `${norm(e.kind)}${norm(e.title)}${norm(e.body)}`;
}

// 在历史头部插入一条；若与已有条目签名相同则合并：计数 +1、时间刷新、置顶，
// 并视为新事件重新置为未读（重复到达应再次提醒，但只占一条记录）。
// 不同签名的通知各自独立。返回新数组，并裁剪到 max（丢弃最旧）。
export function addItem(items, entry, max = MAX_ITEMS) {
  const e = entry || {};
  const prior = Array.isArray(items) ? items.slice() : [];
  const now = Number.isFinite(e.ts) ? e.ts : Date.now();
  const sig = signOf(e);
  const idx = prior.findIndex((x) => signOf(x) === sig);
  if (idx >= 0) {
    const hit = prior[idx];
    const merged = {
      ...hit,
      count: (Number(hit.count) > 0 ? Number(hit.count) : 1) + 1,
      ts: now,
      read: false,
    };
    prior.splice(idx, 1);
    return [merged, ...prior].slice(0, max);
  }
  const rec = {
    id: e.id || nextId(),
    kind: String(e.kind || ""),
    title: String(e.title || ""),
    body: String(e.body || ""),
    ts: now,
    read: false,
    count: 1,
  };
  return [rec, ...prior].slice(0, max);
}

export function withRead(items, id) {
  return (items || []).map((x) => (x.id === id ? { ...x, read: true } : x));
}

export function withAllRead(items) {
  return (items || []).map((x) => ({ ...x, read: true }));
}

export function withoutItem(items, id) {
  return (items || []).filter((x) => x.id !== id);
}

export function countUnread(items) {
  return (items || []).filter((x) => !x.read).length;
}

// 相对时间（本地化）。
export function relTime(ts, now = Date.now()) {
  const s = Math.max(0, Math.floor((now - ts) / 1000));
  if (s < 10) return t("notification.justNow");
  if (s < 60) return t("notification.secondsAgo", { s });
  const m = Math.floor(s / 60);
  if (m < 60) return t("notification.minutesAgo", { m });
  const h = Math.floor(m / 60);
  if (h < 24) return t("notification.hoursAgo", { h });
  const d = new Date(ts);
  return `${d.getMonth() + 1}月${d.getDate()}日`;
}

// ── 实际状态与持久化 ─────────────────────────────────────────────────────────
let items = load();
let badgeEl = null;
let listEl = null;
let panelEl = null;
let bellEl = null;
let open = false;

function load() {
  try {
    const raw = localStorage.getItem(STORE_KEY);
    const arr = raw ? JSON.parse(raw) : [];
    return Array.isArray(arr) ? arr.slice(0, MAX_ITEMS) : [];
  } catch (_) { return []; }
}

function persist() {
  try { localStorage.setItem(STORE_KEY, JSON.stringify(items)); } catch (_) {}
}

export function recordNotification(entry) {
  items = addItem(items, entry);
  persist();
  render();
}

export function getNotifications() { return items.slice(); }

function markRead(id) { items = withRead(items, id); persist(); render(); }
function markAll() { items = withAllRead(items); persist(); render(); }
function removeOne(id) { items = withoutItem(items, id); persist(); render(); }
function clearAll() { items = []; persist(); render(); }

// ── 图标（lucide，与 toast 一致）─────────────────────────────────────────────
const KIND_ICON = {
  ok: '<polyline points="20 6 9 17 4 12"/>',
  warn: '<path d="M10.29 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/>',
  err: '<line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>',
  info: '<circle cx="12" cy="12" r="10"/><line x1="12" y1="16" x2="12" y2="12"/><line x1="12" y1="8" x2="12.01" y2="8"/>',
};
function icon(kind) {
  const inner = KIND_ICON[kind];
  if (!inner) return "";
  return `<svg class="notif-ic" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${inner}</svg>`;
}

// ── DOM 渲染 ────────────────────────────────────────────────────────────────
function renderBadge() {
  if (!badgeEl) return;
  const n = countUnread(items);
  badgeEl.textContent = n > 99 ? "99+" : String(n);
  badgeEl.hidden = n === 0;
}

function renderList() {
  if (!listEl) return;
  if (!items.length) {
    listEl.className = "notif-list empty";
    listEl.innerHTML = `<div class="notif-empty">${t("notif.empty")}</div>`;
    return;
  }
  listEl.className = "notif-list";
  const now = Date.now();
  listEl.innerHTML = items.map((x) => `
    <div class="notif-row${x.read ? " read" : ""} ${x.kind}" data-id="${x.id}">
      ${icon(x.kind)}
      <div class="notif-body">
        <div class="notif-line">
          ${x.title ? `<div class="notif-title"></div>` : ""}
          ${(Number(x.count) || 1) > 1 ? `<span class="notif-count" title="×${x.count}">×${x.count}</span>` : ""}
        </div>
        ${x.body ? `<div class="notif-text"></div>` : ""}
        <div class="notif-time">${relTime(x.ts, now)}</div>
      </div>
      <button class="notif-x" data-del="${x.id}" aria-label="${t("notif.delete")}" title="${t("notif.delete")}">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
      </button>
    </div>`).join("");
  // 安全地填入文本（避免 innerHTML 注入）。
  listEl.querySelectorAll(".notif-row").forEach((row) => {
    const id = row.getAttribute("data-id");
    const rec = items.find((x) => x.id === id);
    if (!rec) return;
    const tt = row.querySelector(".notif-title");
    const tx = row.querySelector(".notif-text");
    if (tt) tt.textContent = rec.title;
    if (tx) tx.textContent = rec.body;
  });
}

function render() { renderBadge(); renderList(); }

function setOpen(v) {
  open = v;
  if (panelEl) panelEl.classList.toggle("open", open);
  if (bellEl) bellEl.classList.toggle("active", open);
  if (open) {
    // 打开时把当前可见项标记已读。
    items = withAllRead(items); persist(); renderBadge(); renderList();
  }
}

export function togglePanel() { setOpen(!open); }

export function initNotificationCenter() {
  bellEl = document.getElementById("notif-bell");
  badgeEl = document.getElementById("notif-badge");
  if (!bellEl) return;

  panelEl = document.createElement("div");
  panelEl.className = "notif-panel";
  panelEl.innerHTML = `
    <div class="notif-head">
      <span class="notif-head-title">${t("notif.title")}</span>
      <div class="notif-head-actions">
        <button class="notif-link" id="notif-read-all" type="button">${t("notif.markAllRead")}</button>
        <button class="notif-link danger" id="notif-clear" type="button">${t("notif.clear")}</button>
      </div>
    </div>`;
  listEl = document.createElement("div");
  listEl.className = "notif-list";
  panelEl.appendChild(listEl);
  bellEl.parentElement.appendChild(panelEl);

  // 事件：铃铛开关
  bellEl.addEventListener("click", (e) => { e.stopPropagation(); togglePanel(); });
  // 列表内：点击单条标记已读、删除按钮
  listEl.addEventListener("click", (e) => {
    const delBtn = e.target.closest("[data-del]");
    if (delBtn) { e.stopPropagation(); removeOne(delBtn.getAttribute("data-del")); return; }
    const row = e.target.closest(".notif-row");
    if (row) markRead(row.getAttribute("data-id"));
  });
  panelEl.querySelector("#notif-read-all").addEventListener("click", () => markAll());
  panelEl.querySelector("#notif-clear").addEventListener("click", () => clearAll());
  // 点击外部关闭、Esc 关闭
  document.addEventListener("click", (e) => {
    if (open && !panelEl.contains(e.target) && !bellEl.contains(e.target)) setOpen(false);
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && open) setOpen(false);
  });

  render();
}
