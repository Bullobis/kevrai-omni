// renderer/modules/search.js — super search experience.
// Weighted fuzzy search via /api/search, with facets, sort, recent queries,
// did-you-mean suggestions, match highlighting, and keyboard navigation.
"use strict";
import { api } from "./api.js";
import { toast } from "./toast.js";
import { state, setState } from "./state.js";
import { debounce } from "./debounce.js";

const $ = (s) => document.querySelector(s);

let vgrid = null;
let searchState = {
  q: "", cat: "", engine: "", license: "", sizeBucket: "",
  trendingOnly: false, sort: "relevance", page: 1, pageSize: 60,
  items: [], facets: null, suggestions: [], recent: [], elapsedMs: 0, count: 0,
  loading: false,
};
let highlightIdx = -1;
let recentDropdown = null;

export function initSearch(grid) {
  vgrid = grid;
  wireToolbar();
  // Load recent searches once
  api.searchRecent().then((r) => {
    searchState.recent = (r?.body?.recent || r?.recent || []);
  }).catch(() => {});
}

function wireToolbar() {
  const search = $("#search");
  const cat = $("#cat-filter");
  const sort = $("#sort-filter");
  const trending = $("#trending-filter");

  const run = debounce(() => runSearch({ resetPage: true }), 180);

  search.addEventListener("input", () => {
    searchState.q = search.value;
    highlightIdx = -1;
    run();
    toggleRecentDropdown(true);
  });
  search.addEventListener("focus", () => toggleRecentDropdown(true));
  search.addEventListener("blur", () => setTimeout(() => toggleRecentDropdown(false), 200));
  search.addEventListener("keydown", (e) => {
    if (e.key === "Escape") {
      if (recentDropdown && !recentDropdown.hidden) { toggleRecentDropdown(false); return; }
      if (search.value) { search.value = ""; searchState.q = ""; run(); }
    } else if (e.key === "ArrowDown") {
      e.preventDefault(); moveHighlight(1);
    } else if (e.key === "ArrowUp") {
      e.preventDefault(); moveHighlight(-1);
    } else if (e.key === "Enter") {
      e.preventDefault();
      if (highlightIdx >= 0) {
        const items = collectNavItems();
        const it = items[highlightIdx];
        if (it) { it.click(); highlightIdx = -1; }
      } else {
        runSearch({ resetPage: true });
        toggleRecentDropdown(false);
      }
    }
  });

  cat.addEventListener("change", () => {
    searchState.cat = cat.value;
    runSearch({ resetPage: true });
  });
  sort.addEventListener("change", () => {
    searchState.sort = sort.value;
    runSearch({ resetPage: true });
  });
  trending.addEventListener("change", () => {
    searchState.trendingOnly = trending.checked;
    runSearch({ resetPage: true });
  });

  document.addEventListener("keydown", (e) => {
    if (e.key === "/" && document.activeElement !== search && !inOverlay()) {
      e.preventDefault();
      search.focus(); search.select();
    }
  });

  // Click outside closes the recent dropdown
  document.addEventListener("click", (e) => {
    if (!e.target.closest(".search-wrap")) toggleRecentDropdown(false);
  });
}

function inOverlay() {
  return Array.from(document.querySelectorAll(".overlay"))
    .some((o) => !o.hasAttribute("hidden"));
}

function moveHighlight(delta) {
  const items = collectNavItems();
  if (!items.length) return;
  highlightIdx = (highlightIdx + delta + items.length) % items.length;
  items.forEach((it, i) => it.classList.toggle("nav-active", i === highlightIdx));
  const el = items[highlightIdx];
  if (el && el.scrollIntoView) el.scrollIntoView({ block: "nearest" });
}

function collectNavItems() {
  const dd = recentDropdown;
  if (dd && !dd.hidden && dd.children.length) {
    return Array.from(dd.querySelectorAll("[data-search-q]"));
  }
  return Array.from(document.querySelectorAll("#models-grid .model-card"));
}

function toggleRecentDropdown(show) {
  if (!recentDropdown) {
    recentDropdown = document.createElement("div");
    recentDropdown.className = "search-dropdown";
    recentDropdown.setAttribute("role", "listbox");
    const wrap = document.querySelector(".search-wrap");
    if (wrap) wrap.appendChild(recentDropdown);
  }
  if (!show) { recentDropdown.hidden = true; recentDropdown.innerHTML = ""; return; }
  const q = searchState.q.trim().toLowerCase();
  let html = "";
  if (!q && searchState.recent.length) {
    html = `<div class="search-dd-label">最近搜索</div>` +
      searchState.recent.slice(0, 6).map((rq) =>
        `<div class="search-dd-item" data-search-q="${escapeAttr(rq)}" role="option">🕘 ${escapeHtml(rq)}</div>`
      ).join("") +
      `<div class="search-dd-item search-dd-clear" data-action="clear-recent">✕ 清除搜索历史</div>`;
  } else if (q && searchState.suggestions && searchState.suggestions.length) {
    html = `<div class="search-dd-label">你是不是要找</div>` +
      searchState.suggestions.map((s) =>
        `<div class="search-dd-item" data-search-q="${escapeAttr(s)}" role="option">💡 ${escapeHtml(s)}</div>`
      ).join("");
  }
  if (!html) { recentDropdown.hidden = true; recentDropdown.innerHTML = ""; return; }
  recentDropdown.innerHTML = html;
  recentDropdown.hidden = false;
  recentDropdown.querySelectorAll("[data-search-q]").forEach((el) => {
    el.addEventListener("mousedown", (e) => {
      e.preventDefault();
      const v = el.getAttribute("data-search-q");
      $("#search").value = v;
      searchState.q = v;
      runSearch({ resetPage: true });
      toggleRecentDropdown(false);
    });
  });
  const clearBtn = recentDropdown.querySelector("[data-action=clear-recent]");
  if (clearBtn) clearBtn.addEventListener("mousedown", (e) => {
    e.preventDefault();
    api.searchClearRecent().then(() => {
      searchState.recent = [];
      toggleRecentDropdown(false);
      toast("已清除搜索历史", { kind: "ok" });
    }).catch(() => {});
  });
}

export async function runSearch(opts = {}) {
  if (opts.resetPage) searchState.page = 1;
  searchState.loading = true;
  updateCount("搜索中…");
  let r;
  try {
    r = await api.search({
      q: searchState.q, category: searchState.cat, engine: searchState.engine,
      license: searchState.license, size_bucket: searchState.sizeBucket,
      trending: searchState.trendingOnly ? 1 : 0, sort: searchState.sort,
      page: searchState.page, page_size: searchState.pageSize,
    });
  } catch (e) {
    searchState.loading = false;
    updateCount("搜索失败");
    return;
  }
  const body = r?.body || r || {};
  searchState.items = body.items || [];
  searchState.facets = body.facets || null;
  searchState.suggestions = body.suggestions || [];
  searchState.count = body.count || 0;
  searchState.elapsedMs = body.elapsed_ms || 0;
  searchState.loading = false;

  setState({ searchResults: searchState.items });
  vgrid.setItems(searchState.items);
  updateCount(`${searchState.count} 条 · ${searchState.elapsedMs}ms`);
  renderFacets();
  renderNoResults();
  if (searchState.q.trim()) toggleRecentDropdown(true);
}

function updateCount(text) {
  const el = $("#models-count");
  if (el) el.textContent = text;
}

function renderFacets() {
  let host = $("#facets-bar");
  if (!host) {
    host = document.createElement("div");
    host.id = "facets-bar";
    host.className = "facets-bar";
    const toolbar = document.querySelector("#pane-market .toolbar");
    if (toolbar) toolbar.after(host);
  }
  const f = searchState.facets;
  if (!f) { host.innerHTML = ""; return; }
  const chips = [];
  if (searchState.engine) chips.push({ label: `引擎: ${searchState.engine}`, clear: () => { searchState.engine = ""; } });
  if (searchState.license) chips.push({ label: `许可: ${searchState.license}`, clear: () => { searchState.license = ""; } });
  if (searchState.sizeBucket) chips.push({ label: `大小: ${searchState.sizeBucket}`, clear: () => { searchState.sizeBucket = ""; } });

  const engineTop = (f.engines || []).slice(0, 8);
  const sizeTop = (f.sizes || []).filter((s) => s.count > 0);
  host.innerHTML = `
    ${chips.length ? `<div class="facet-chips">${chips.map((c, i) =>
      `<button class="facet-chip active" data-clear="${i}">${escapeHtml(c.label)} ✕</button>`).join("")}</div>` : ""}
    <div class="facet-row">
      <span class="facet-label">引擎</span>
      ${engineTop.map((e) =>
        `<button class="facet-chip ${searchState.engine === e.value ? "active" : ""}" data-engine="${escapeAttr(e.value)}">${escapeHtml(e.value)} <span class="facet-n">${e.count}</span></button>`
      ).join("")}
    </div>
    <div class="facet-row">
      <span class="facet-label">大小</span>
      ${sizeTop.map((s) =>
        `<button class="facet-chip ${searchState.sizeBucket === s.value ? "active" : ""}" data-size="${escapeAttr(s.value)}">${escapeHtml(s.value)} <span class="facet-n">${s.count}</span></button>`
      ).join("")}
    </div>`;
  host.querySelectorAll("[data-engine]").forEach((b) => b.addEventListener("click", () => {
    searchState.engine = b.getAttribute("data-engine");
    runSearch({ resetPage: true });
  }));
  host.querySelectorAll("[data-size]").forEach((b) => b.addEventListener("click", () => {
    searchState.sizeBucket = b.getAttribute("data-size");
    runSearch({ resetPage: true });
  }));
  host.querySelectorAll("[data-clear]").forEach((b) => b.addEventListener("click", () => {
    chips[+b.getAttribute("data-clear")].clear();
    runSearch({ resetPage: true });
  }));
}

function renderNoResults() {
  let host = $("#no-results");
  if (searchState.count > 0 || !searchState.q.trim()) {
    if (host) host.remove();
    return;
  }
  if (!host) {
    host = document.createElement("div");
    host.id = "no-results";
    host.className = "no-results";
    const grid = $("#models-grid");
    grid.parentNode.insertBefore(host, grid.nextSibling);
  }
  const sug = searchState.suggestions.length
    ? `<p>你是不是要找：${searchState.suggestions.slice(0, 3).map((s) =>
        `<button class="link-btn" data-suggest="${escapeAttr(s)}">${escapeHtml(s)}</button>`).join(" ")}</p>`
    : "";
  host.innerHTML = `
    <div class="no-results-icon">🔍</div>
    <p>没有找到与 "<strong>${escapeHtml(searchState.q)}</strong>" 相关的模型</p>
    ${sug}
    <p class="mut tiny">建议：检查拼写、减少关键词、或清除筛选条件</p>`;
  host.querySelectorAll("[data-suggest]").forEach((b) => b.addEventListener("click", () => {
    $("#search").value = b.getAttribute("data-suggest");
    searchState.q = b.getAttribute("data-suggest");
    runSearch({ resetPage: true });
  }));
}

// Highlight helper used by the card renderer.
export function highlight(text, highlights) {
  if (!text || !highlights || !highlights.length) return escapeHtml(text);
  // Sort by start, merge overlaps
  const ranges = highlights
    .filter((h) => h.field === "name" || h.field === "description" || h.field === "id" || h.field === "tags")
    .map((h) => [h.start, h.end])
    .sort((a, b) => a[0] - b[0]);
  if (!ranges.length) return escapeHtml(text);
  let out = "";
  let pos = 0;
  for (const [s, e] of ranges) {
    if (s < pos) continue;
    if (s > text.length) break;
    out += escapeHtml(text.slice(pos, s));
    out += `<mark>${escapeHtml(text.slice(s, Math.min(e, text.length)))}</mark>`;
    pos = e;
  }
  out += escapeHtml(text.slice(pos));
  return out;
}

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (m) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[m]));
}
function escapeAttr(s) { return escapeHtml(s).replace(/`/g, "&#96;"); }
