// renderer/modules/sidebar-groups.js — 侧边栏分组、折叠、按项隐藏。
//
// 借鉴 Cherry Studio：它的侧栏不是"把所有功能都列出来"，而是只显示用户
// 钉住（pin）的项，其余通过管理面板放出来或收回去，顺序也能拖动。
// 本项目原先 19 个入口一条平铺，找不到重点；这里补上三件事：
//
//   1. 按用途分组（组头可折叠）
//   2. 单个入口可隐藏/显示
//   3. 状态持久化到 localStorage
//
// 设计约束：
//   * 纯 DOM + localStorage，无框架依赖（与项目其余部分一致）
//   * localStorage 不可用时全部回落为"全展开、全显示"，绝不抛错
//   * 折叠用 display:none 而非 max-height，被隐藏的按钮不进入 Tab 焦点顺序
//   * 至少保留一个可见入口——全部隐藏会让用户彻底找不到界面
"use strict";

const COLLAPSED_KEY = "kevrai:sidebar-collapsed-groups";
const HIDDEN_KEY = "kevrai:sidebar-hidden-tabs";

// --- 纯函数区（便于单测驱动，不碰 DOM） -------------------------------------

function readList(key) {
  try {
    const raw = localStorage.getItem(key);
    if (!raw) return [];
    const v = JSON.parse(raw);
    return Array.isArray(v) ? v.filter((x) => typeof x === "string") : [];
  } catch (_) {
    return [];
  }
}

function writeList(key, list) {
  try { localStorage.setItem(key, JSON.stringify(list)); } catch (_) { /* 隐私模式下静默 */ }
}

/** 读取折叠的组 id 列表。 */
export function readCollapsedGroups() {
  return readList(COLLAPSED_KEY);
}

/** 读取被隐藏的 tab id 列表。 */
export function readHiddenTabs() {
  return readList(HIDDEN_KEY);
}

export function writeCollapsedGroups(ids) {
  writeList(COLLAPSED_KEY, [...new Set(ids)]);
}

export function writeHiddenTabs(ids) {
  writeList(HIDDEN_KEY, [...new Set(ids)]);
}

/**
 * 切换某个 tab 的可见性。
 *
 * 关键约束：**不允许把最后一个可见入口也隐藏掉**。全部隐藏之后用户
 * 没有任何入口可点，界面看起来像坏了，且没有自救的办法（管理面板本身
 * 也在侧栏里）。
 *
 * @returns {{changed:boolean, blocked?:boolean, hidden:string[]}}
 */
export function toggleTabHidden(hidden, tabId, visibleCount) {
  const cur = new Set(hidden);
  if (cur.has(tabId)) {
    cur.delete(tabId);
    return { changed: true, hidden: [...cur] };
  }
  if (visibleCount <= 1) {
    return { changed: false, blocked: true, hidden: [...cur] };
  }
  cur.add(tabId);
  return { changed: true, hidden: [...cur] };
}

/** 切换某个组的折叠态。 */
export function toggleGroupCollapsed(collapsed, groupId) {
  const cur = new Set(collapsed);
  if (cur.has(groupId)) cur.delete(groupId);
  else cur.add(groupId);
  return [...cur];
}

// --- DOM 区 ----------------------------------------------------------------

function groups(root) {
  return Array.from(root.querySelectorAll(".nav-group"));
}

function tabsOfGroup(g) {
  return Array.from(g.querySelectorAll('.pane-tab[data-tab]'));
}

/** 把持久化的折叠 / 隐藏状态应用到 DOM。 */
export function applySidebarGroups(root = document) {
  const collapsed = new Set(readCollapsedGroups());
  const hidden = new Set(readHiddenTabs());
  for (const g of groups(root)) {
    const gid = g.dataset.group || "";
    const isCollapsed = collapsed.has(gid);
    g.dataset.collapsed = isCollapsed ? "true" : "false";
    const head = g.querySelector(".nav-group-head");
    if (head) head.setAttribute("aria-expanded", String(!isCollapsed));
    for (const b of tabsOfGroup(g)) {
      const id = b.dataset.tab || "";
      if (hidden.has(id)) b.dataset.hidden = "true";
      else delete b.dataset.hidden;
    }
  }
  return { collapsed: [...collapsed], hidden: [...hidden] };
}

/** 当前实际可见的 tab 数量（折叠组里的也算可见——它们只是收起来了）。 */
export function countVisibleTabs(root = document) {
  let n = 0;
  for (const g of groups(root)) {
    n += tabsOfGroup(g).filter((b) => b.dataset.hidden !== "true").length;
  }
  return n;
}

/**
 * 绑定组头的点击。返回解绑函数。
 * @param {(group:string, collapsed:boolean)=>void} [onChange] 状态变化回调
 */
export function wireSidebarGroups(root = document, onChange) {
  const onClick = (e) => {
    const head = e.target.closest("[data-group-toggle]");
    if (!head) return;
    const gid = head.dataset.groupToggle;
    if (!gid) return;
    const next = toggleGroupCollapsed(readCollapsedGroups(), gid);
    writeCollapsedGroups(next);
    applySidebarGroups(root);
    if (onChange) onChange(gid, next.includes(gid));
  };
  root.addEventListener("click", onClick);
  return () => root.removeEventListener("click", onClick);
}

// --- 管理面板 --------------------------------------------------------------

/**
 * 打开"侧边栏管理"面板：逐项勾选显示/隐藏。
 *
 * 用 Promise 而不是回调，方便调用方在关闭后刷新一次状态。
 * @param {{t:(k:string)=>string, root?:ParentNode}} opts
 */
export function openSidebarManager(opts = {}) {
  const { t = (k) => k, root = document } = opts;
  return new Promise((resolve) => {
    const hidden = new Set(readHiddenTabs());

    const overlay = document.createElement("div");
    overlay.className = "overlay";
    overlay.setAttribute("role", "dialog");
    overlay.setAttribute("aria-modal", "true");
    overlay.setAttribute("aria-label", t("sidebar.manageAria"));

    const sections = groups(root).map((g) => {
      const rows = tabsOfGroup(g).map((b) => {
        const id = b.dataset.tab || "";
        const label = (b.querySelector(".tab-label")?.textContent || "").trim()
          || b.getAttribute("title") || id;
        const checked = !hidden.has(id);
        return `<label class="sidebar-manager-row" data-tab="${esc(id)}">
          <input type="checkbox" ${checked ? "checked" : ""} />
          <span class="sm-label">${esc(label)}</span>
        </label>`;
      }).join("");
      const title = (g.querySelector(".grp-label")?.textContent || "").trim()
        || g.dataset.group || "";
      return `<div class="sidebar-manager-group">${esc(title)}</div>${rows}`;
    }).join("");

    overlay.innerHTML = `
      <div class="overlay-card" role="document">
        <header>
          <h2>${esc(t("sidebar.manageTitle"))}</h2>
          <button class="ghost icon" type="button" data-act="close" aria-label="${esc(t("app.close"))}">×</button>
        </header>
        <p class="hint">${esc(t("sidebar.manageHint"))}</p>
        <div class="sidebar-manager-list">${sections}</div>
        <div class="sidebar-manager-foot">
          <button class="secondary" type="button" data-act="all">${esc(t("sidebar.showAll"))}</button>
          <span class="spacer"></span>
          <button class="secondary" type="button" data-act="cancel">${esc(t("app.cancel"))}</button>
          <button class="primary" type="button" data-act="save">${esc(t("app.save"))}</button>
        </div>
      </div>`;

    const close = () => {
      overlay.remove();
      document.removeEventListener("keydown", onKey);
      overlay.removeEventListener("change", onToggle);
      resolve();
    };
    const onKey = (e) => { if (e.key === "Escape") close(); };
    // 勾掉最后一个时立刻弹回并提示，而不是等到点保存才说不行。
    const onToggle = (e) => {
      if (!(e.target instanceof HTMLInputElement)) return;
      const boxes = Array.from(overlay.querySelectorAll("input[type=checkbox]"));
      if (boxes.some((b) => b.checked)) return;
      e.target.checked = true;
      const hint = overlay.querySelector(".hint");
      if (hint) hint.textContent = t("sidebar.keepOne");
    };
    overlay.addEventListener("change", onToggle);

    overlay.addEventListener("click", (e) => {
      if (e.target === overlay) { close(); return; }
      if (e.target.closest("[data-act=close]") || e.target.closest("[data-act=cancel]")) { close(); return; }
      if (e.target.closest("[data-act=all]")) {
        overlay.querySelectorAll('input[type=checkbox]').forEach((c) => { c.checked = true; });
        return;
      }
      if (e.target.closest("[data-act=save]")) {
        const nextHidden = [];
        overlay.querySelectorAll(".sidebar-manager-row").forEach((row) => {
          const box = row.querySelector("input[type=checkbox]");
          if (box && !box.checked) nextHidden.push(row.dataset.tab || "");
        });
        // 安全网：一个都不显示等于把界面锁死，这里直接拒绝保存。
        const total = groups(root).reduce((n, g) => n + tabsOfGroup(g).length, 0);
        if (total > 0 && nextHidden.length >= total) {
          overlay.querySelector(".hint").textContent = t("sidebar.keepOne");
          return;
        }
        writeHiddenTabs(nextHidden);
        applySidebarGroups(root);
        close();
      }
    });
    document.addEventListener("keydown", onKey);
    document.body.appendChild(overlay);
  });
}

function esc(s) {
  return String(s == null ? "" : s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}
