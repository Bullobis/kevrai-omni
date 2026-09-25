// renderer/modules/search.js — super search experience.
// Weighted fuzzy search via /api/search, with facets, sort, recent queries,
// did-you-mean suggestions, match highlighting, and keyboard navigation.
"use strict";
import { api } from "./api.js";
import { toast } from "./toast.js";
import { state, setState } from "./state.js";
import { debounce } from "./debounce.js";
import { escapeHtml } from "./net.js";
import { emptyStateIconSvg } from "./empty-state.js";
import { isFavorite, getRecent } from "./favorites.js";
import { t } from "./i18n.js";

const $ = (s) => document.querySelector(s);

let vgrid = null;
let searchState = {
  q: "", cat: "", engine: "", license: "", sizeBucket: "",
  trendingOnly: false, sort: "relevance", page: 1, pageSize: 60,
  items: [], facets: null, suggestions: [], recent: [], elapsedMs: 0, count: 0,
  loading: false,
  // v2.8.0 — hub cursor pagination
  sources: ["curated", "hf", "modelscope"],
  cursor: "",              // opaque cursor for the next page ("" = none)
  hasMore: false,
  loadingMore: false,      // re-entrancy guard for loadMore()
  seenKeys: new Set(),     // cross-page dedupe (hub + ":" + repo)
  degraded: false,
  warnings: [],
  reqSeq: 0,               // request-generation guard (H5)
  lastError: null,
  usingHub: false,         // true when the last successful search used hub
};
let highlightIdx = -1;
let recentDropdown = null;

export function initSearch(grid) {
  vgrid = grid;
  // v2.8.0 — incremental loading: pull the next page when the grid nears its
  // bottom edge. loadMore() self-guards against re-entrancy and end-of-list.
  if (vgrid) vgrid.onNearEnd = () => { loadMore().catch(() => {}); };
  wireToolbar();
  // Load recent searches once
  api.searchRecent().then((r) => {
    searchState.recent = (r?.body?.recent || r?.recent || []);
  }).catch(() => {});
  // v2.9.0 — drop any source this network cannot reach, so the market never
  // shows an empty section for a down source. Runs async and never blocks the
  // UI; a failure just leaves the default source list untouched.
  probeSources().catch(() => {});
}

// v2.9.0 — source reachability. `/api/hub/health` probes each remote source
// once (HF + 魔搭) and reports `online`. Unreachable sources are removed from
// `searchState.sources`, which is what the next search sends upstream.
async function probeSources() {
  if (!window.kevrai || typeof window.kevrai.hubHealth !== "function") return null;
  let body = null;
  try {
    const r = await window.kevrai.hubHealth();
    body = (r && r.body) ? r.body : r;
  } catch (_) {
    return null;   // probe is advisory only
  }
  if (!body || !body.sources) return body;
  const online = Array.isArray(body.online) ? body.online : [];
  const before = searchState.sources.slice();
  // `curated` is local and always available; only remote sources can be dropped.
  searchState.sources = searchState.sources.filter(
    (s) => s === "curated" || !(s in body.sources) || online.includes(s)
  );
  // Never end up with zero sources — fall back to curated rather than erroring.
  if (!searchState.sources.length) searchState.sources = ["curated"];
  const dropped = before.filter((s) => !searchState.sources.includes(s));
  if (dropped.length) {
    const names = dropped.map((s) => (s === "hf" ? "Hugging Face" : "ModelScope"));
    toast(t("market.sourceUnreachable", { names: names.join(" / ") }), { kind: "warn" });
  }
  return body;
}

function wireToolbar() {
  const search = $("#search");
  const cat = $("#cat-filter");
  const sort = $("#sort-filter");
  const trending = $("#trending-filter");
  const fav = $("#fav-filter");

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
  if (fav) fav.addEventListener("change", () => {
    setState({ favFilter: fav.value });
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
    html = `<div class="search-dd-label">${t("market.recentSearches")}</div>` +
      searchState.recent.slice(0, 6).map((rq) =>
        `<div class="search-dd-item" data-search-q="${escapeAttr(rq)}" role="option">🕘 ${escapeHtml(rq)}</div>`
      ).join("") +
      `<div class="search-dd-item search-dd-clear" data-action="clear-recent">${t("market.clearSearchHistory")}</div>`;
  } else if (q && searchState.suggestions && searchState.suggestions.length) {
    html = `<div class="search-dd-label">${t("market.didYouMean")}</div>` +
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
      toast(t("market.searchHistoryCleared"), { kind: "ok" });
    }).catch(() => {});
  });
}

// v2.8.0 — single dedupe key for cross-page / cross-source results.
function keyOf(m) {
  return `${m.hub || "curated"}:${(m.repo || m.id || "").toLowerCase()}`;
}

// Client-side overlay on top of the server result set, driven by state.favFilter.
//   "favorites" → keep only favorited cards
//   "recent"    → keep recently-used cards, re-ordered by recency (newest first)
//   ""          → no-op (server results pass through untouched)
function applyFavFilter(items) {
  const f = state.favFilter || "";
  if (!f || !Array.isArray(items)) return items || [];
  if (f === "favorites") {
    return items.filter((m) => m && m.id && isFavorite(m.id));
  }
  if (f === "recent") {
    const order = new Map(getRecent().map((id, i) => [id, i]));
    return items
      .filter((m) => m && m.id && order.has(m.id))
      .sort((a, b) => order.get(a.id) - order.get(b.id));
  }
  return items;
}

// v2.8.0 — three-fold backward-compat bridge (§3.5):
//   1) old preload without hubSearch  → fall back to api.search()
//   2) sidecar without /api/hub/*      → fall back to api.search()
// /api/hub/search itself never 5xx's, so (2) is a rare safety net.
async function callHubSearch(params) {
  // `callHubSearch` hands back a wrapper ({fallback, r}) so the caller can tell
  // whether the hub path or the legacy /api/search path produced the result.
  // The IPC bridge resolves `{status, body}`, so the payload the caller wants
  // is `r.r.body`. Expose it as `body` too, because both call sites below
  // unwrap with the shared `r?.body || r` idiom — without this the grid would
  // read `{fallback, r}` and always end up with zero items.
  const wrap = (fallback, r) => ({
    fallback,
    r,
    body: (r && r.body) || r || {},
  });
  if (!window.kevrai || typeof window.kevrai.hubSearch !== "function") {
    return wrap(true, await api.search(toLegacyParams(params)));
  }
  try {
    return wrap(false, await api.hubSearch(params));
  } catch (_) {
    return wrap(true, await api.search(toLegacyParams(params)));
  }
}

function toLegacyParams(params) {
  return {
    q: params.q, category: params.category, engine: params.engine,
    license: params.license, size_bucket: searchState.sizeBucket,
    trending: searchState.trendingOnly ? 1 : 0, sort: params.sort,
    page: 1, page_size: searchState.pageSize,
  };
}

export async function runSearch(opts = {}) {
  if (opts.resetPage) searchState.page = 1;
  const mySeq = ++searchState.reqSeq;   // H5: generation guard
  searchState.loading = true;
  searchState.lastError = null;
  updateCount(t("market.searching"));
  clearLoadMoreBar();
  // v3.0.0 — show skeleton cards in the grid while searching
  if (vgrid && typeof vgrid.setLoading === "function") vgrid.setLoading(true);

  const params = {
    q: searchState.q,
    sources: searchState.sources,
    category: searchState.cat,
    engine: searchState.engine,
    license: searchState.license,
    sort: searchState.sort,
    page_size: searchState.pageSize,
  };

  let r;
  try {
    r = await callHubSearch(params);
  } catch (e) {
    if (mySeq !== searchState.reqSeq) return;   // stale — discard
    searchState.loading = false;
    searchState.lastError = String(e && e.message || e);
    updateCount(t("market.searchFailed"));
    if (vgrid && typeof vgrid.setLoading === "function") vgrid.setLoading(false);
    renderLoadError();
    return;
  }

  // Discard out-of-order responses (fast typing: a slow early response must
  // not overwrite a newer one).
  if (mySeq !== searchState.reqSeq) return;

  const body = r?.body || r || {};
  const isHub = !r.fallback;
  searchState.usingHub = isHub;

  // Client-side favorites/recent overlay sits on top of whatever the server
  // returned (the server has no knowledge of local favorites).
  searchState.items = applyFavFilter(body.items || []);
  searchState.facets = body.facets || null;
  searchState.suggestions = body.suggestions || [];
  // When the local overlay is active, #models-count must reflect the filtered
  // (displayed) length rather than the server's total match count.
  searchState.count = state.favFilter
    ? searchState.items.length
    : (body.count != null ? body.count : (body.items ? body.items.length : 0));
  searchState.elapsedMs = body.elapsed_ms || 0;
  searchState.cursor = body.next_cursor || "";
  searchState.hasMore = !!body.has_more;
  searchState.degraded = !!body.degraded;
  searchState.warnings = body.warnings || [];
  searchState.loading = false;

  // Reset cross-page dedupe for the new result set.
  searchState.seenKeys = new Set(searchState.items.map(keyOf));

  setState({ searchResults: searchState.items });
  vgrid.setItems(searchState.items);
  if (vgrid && typeof vgrid.setLoading === "function") vgrid.setLoading(false);
  updateCount(formatCount());
  renderFacets();
  renderDegradedBanner();
  renderNoResults();
  updateLoadMoreBar();
  if (searchState.q.trim()) toggleRecentDropdown(true);
}

// v2.8.0 — fetch and append the next page. No-op when a request is already in
// flight, when there is no cursor, or when the list is exhausted.
export async function loadMore() {
  if (searchState.loading || searchState.loadingMore) return;
  if (!searchState.hasMore || !searchState.cursor) return;
  const mySeq = searchState.reqSeq;      // snapshot; discard if a new search starts
  searchState.loadingMore = true;
  if (vgrid && typeof vgrid.setLoading === "function") vgrid.setLoading(true);
  updateLoadMoreBar();
  try {
    const r = await callHubSearch({
      q: searchState.q,
      sources: searchState.sources,
      category: searchState.cat,
      engine: searchState.engine,
      license: searchState.license,
      sort: searchState.sort,
      page_size: searchState.pageSize,
      cursor: searchState.cursor,
    });
    if (mySeq !== searchState.reqSeq) return;   // query changed mid-flight
    const body = r?.body || r || {};
    const freshRaw = (body.items || []).filter((m) => {
      const k = keyOf(m);
      if (searchState.seenKeys.has(k)) return false;
      searchState.seenKeys.add(k);
      return true;
    });
    // Overlay the favorites/recent filter on the appended page too.
    const fresh = applyFavFilter(freshRaw);
    searchState.items = searchState.items.concat(fresh);
    searchState.cursor = body.next_cursor || "";
    searchState.hasMore = !!body.has_more;
    searchState.degraded = !!body.degraded;
    searchState.warnings = body.warnings || [];
    setState({ searchResults: searchState.items });
    if (vgrid && typeof vgrid.appendItems === "function") {
      vgrid.appendItems(fresh);
    } else if (vgrid) {
      vgrid.setItems(searchState.items);
    }
    updateCount(formatCount());
    renderDegradedBanner();
  } catch (e) {
    if (mySeq !== searchState.reqSeq) return;
    searchState.lastError = String(e && e.message || e);
  } finally {
    // 无论请求是否已被新查询作废，都必须复位 loadingMore —— 否则
    // 搜索中途滚动会让 loadingMore 永久卡 true，此后的 loadMore 全部
    // 在第 316 行早退，分页彻底失效直到重新搜索。
    // UI 的清理只对当前仍然有效的请求做，避免旧请求覆盖新请求的状态。
    searchState.loadingMore = false;
    if (mySeq === searchState.reqSeq) {
      if (vgrid && typeof vgrid.setLoading === "function") vgrid.setLoading(false);
      updateLoadMoreBar();
    }
  }
}

function formatCount() {
  const n = searchState.items.length;
  const base = t("market.count", { n: searchState.count || n });
  const online = searchState.usingHub ? t("market.online") : "";
  const more = searchState.hasMore ? t("market.loadMore") : "";
  return `${base}${online} · ${searchState.elapsedMs}ms${more}`;
}

function clearLoadMoreBar() {
  const el = document.querySelector("#loadmore-bar");
  if (el) { el.hidden = true; el.innerHTML = ""; }
}

// v2.8.0 — load / empty / error / end-of-list indicator under the grid.
function updateLoadMoreBar() {
  let bar = document.querySelector("#loadmore-bar");
  if (!bar) {
    bar = document.createElement("div");
    bar.id = "loadmore-bar";
    bar.className = "vgrid-loadmore";
    const grid = document.querySelector("#models-grid");
    if (grid && grid.parentNode) grid.parentNode.insertBefore(bar, grid.nextSibling);
    else return;
  }
  if (searchState.loadingMore) {
    bar.hidden = false;
    bar.innerHTML = `<span class="mut tiny">${t("market.loadingMore")}</span>`;
    return;
  }
  if (searchState.hasMore) {
    bar.hidden = true;
    bar.innerHTML = "";
    return;
  }
  // Exhausted: show a friendly bottom marker (only if we actually have rows).
  if (searchState.items.length > 0) {
    bar.hidden = false;
    bar.innerHTML = `<span class="mut tiny">${t("market.bottomReached", { n: searchState.items.length })}</span>`;
  } else {
    bar.hidden = true;
    bar.innerHTML = "";
  }
}

function renderLoadError() {
  let bar = document.querySelector("#loadmore-bar");
  if (!bar) {
    bar = document.createElement("div");
    bar.id = "loadmore-bar";
    bar.className = "vgrid-loadmore";
    const grid = document.querySelector("#models-grid");
    if (grid && grid.parentNode) grid.parentNode.insertBefore(bar, grid.nextSibling);
    else return;
  }
  bar.hidden = false;
  bar.innerHTML = `<span class="mut tiny">${t("market.loadError")}<button class="link-btn" id="loadmore-retry">${t("market.retry")}</button></span>`;
  const btn = bar.querySelector("#loadmore-retry");
  if (btn) btn.addEventListener("click", () => runSearch({ resetPage: true }));
}

// v2.8.0 — degraded banner: remote sources are down, only local results show.
function renderDegradedBanner() {
  let host = document.querySelector("#hub-degraded-banner");
  if (!searchState.degraded || !searchState.warnings.length) {
    if (host) host.remove();
    return;
  }
  if (!host) {
    host = document.createElement("div");
    host.id = "hub-degraded-banner";
    host.className = "hub-degraded-banner";
    const facets = document.querySelector("#facets-bar");
    const toolbar = document.querySelector("#pane-market .toolbar");
    if (facets) facets.parentNode.insertBefore(host, facets);
    else if (toolbar) toolbar.after(host);
    else return;
  }
  const labels = searchState.warnings
    .map((w) => `${w.hub || "remote"}（${w.code || "err"}）`).join("、");
  host.innerHTML = `${t("market.degradedBanner", { labels: escapeHtml(labels) })}
    <button class="link-btn" id="hub-degraded-retry">${t("market.retry")}</button>`;
  const btn = host.querySelector("#hub-degraded-retry");
  if (btn) btn.addEventListener("click", () => runSearch({ resetPage: true }));
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
  if (searchState.engine) chips.push({ label: t("market.engineChip", { name: searchState.engine }), clear: () => { searchState.engine = ""; } });
  if (searchState.license) chips.push({ label: t("market.licenseChip", { name: searchState.license }), clear: () => { searchState.license = ""; } });
  if (searchState.sizeBucket) chips.push({ label: t("market.sizeChip", { name: searchState.sizeBucket }), clear: () => { searchState.sizeBucket = ""; } });

  const engineTop = (f.engines || []).slice(0, 8);
  const sizeTop = (f.sizes || []).filter((s) => s.count > 0);
  host.innerHTML = `
    ${chips.length ? `<div class="facet-chips">${chips.map((c, i) =>
      `<button class="facet-chip active" data-clear="${i}">${escapeHtml(c.label)} ✕</button>`).join("")}</div>` : ""}
    <div class="facet-row">
      <span class="facet-label">${t("market.facetEngine")}</span>
      ${engineTop.map((e) =>
        `<button class="facet-chip ${searchState.engine === e.value ? "active" : ""}" data-engine="${escapeAttr(e.value)}">${escapeHtml(e.value)} <span class="facet-n">${e.count}</span></button>`
      ).join("")}
    </div>
    <div class="facet-row">
      <span class="facet-label">${t("market.facetSize")}</span>
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
    ? `<p>${t("market.didYouMean")}：${searchState.suggestions.slice(0, 3).map((s) =>
        `<button class="link-btn" data-suggest="${escapeAttr(s)}">${escapeHtml(s)}</button>`).join(" ")}</p>`
    : "";
  host.innerHTML = `
    <div class="no-results-icon">${emptyStateIconSvg("search")}</div>
    <h3 class="empty-state-title">${t("market.noResults")}</h3>
    <p>${t("market.noResultsBody", { q: `<strong>${escapeHtml(searchState.q)}</strong>` })}</p>
    ${sug}
    <p class="mut tiny">${t("market.suggestionHint")}</p>
    <button type="button" class="secondary small es-clear-search">${t("market.clearSearch")}</button>`;
  host.querySelectorAll("[data-suggest]").forEach((b) => b.addEventListener("click", () => {
    $("#search").value = b.getAttribute("data-suggest");
    searchState.q = b.getAttribute("data-suggest");
    runSearch({ resetPage: true });
  }));
  host.querySelector(".es-clear-search")?.addEventListener("click", () => {
    const box = $("#search");
    if (box) box.value = "";
    searchState.q = "";
    runSearch({ resetPage: true });
  });
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

function escapeAttr(s) { return escapeHtml(s).replace(/`/g, "&#96;"); }
