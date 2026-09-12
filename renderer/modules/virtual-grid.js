// renderer/modules/virtual-grid.js — DOM-based "windowing" for the model grid.
//
// Why windowing without IntersectionObserver: we have only ~62 items today,
// but the API is generic so it scales later. Items render with absolute
// positioning; only items in the current scroll window are mounted.
"use strict";

export class VirtualGrid {
  constructor(host, opts = {}) {
    this.host = host;
    this.itemHeight     = opts.itemHeight     || 168;
    this.colsOfWidth    = opts.colsOfWidth    || (() => this._defaultCols());
    this.gap            = opts.gap            || 14;
    this.padding        = opts.padding        || 16;
    this.viewportClass  = opts.viewportClass  || "vgrid-viewport";
    this.itemClass      = opts.itemClass      || "vgrid-item";
    this.renderItem     = opts.renderItem     || (() => document.createElement("div"));
    this.onItemClick    = opts.onItemClick    || (() => {});
    // v2.8.0 — incremental loading. Both have defaults; existing callers that
    // omit them keep the exact previous behaviour (setItems only).
    this.onNearEnd      = opts.onNearEnd      || (() => {});
    this.nearEndRows    = opts.nearEndRows    || 2;
    this._nearEndFired  = false;
    this.loading        = false;   // set true by the owner while prefetching

    this.host.classList.add("vgrid");
    this.viewport = document.createElement("div");
    this.viewport.className = this.viewportClass;
    this.spacer   = document.createElement("div");
    this.spacer.className   = "vgrid-spacer";
    this.host.appendChild(this.viewport);
    this.viewport.appendChild(this.spacer);

    this.items = [];
    this.scrollHandler = () => this._render();
    this.resizeHandler = () => this._layout();
    this.viewport.addEventListener("scroll", this.scrollHandler, { passive: true });
    window.addEventListener("resize", this.resizeHandler);
    // Click delegation
    this.viewport.addEventListener("click", (e) => {
      const t = e.target.closest("[data-idx]");
      if (!t) return;
      const idx = +t.dataset.idx;
      this.onItemClick(idx, this.items[idx], e);
    });
    this._layout();
  }

  _defaultCols() {
    const w = this.host.clientWidth || 1024;
    const min = 280, pad = this.padding * 2, gap = this.gap;
    return Math.max(1, Math.floor((w - pad + gap) / (min + gap)));
  }

  _cols() { return this.colsOfWidth(); }

  setItems(items) {
    this.items = Array.isArray(items) ? items : [];
    this._nearEndFired = false;
    this._layout();
    this.viewport.scrollTop = 0;
    this._render();
  }

  // v2.8.0 — append a page without resurfacing to the top. The ONLY
  // difference from setItems() is that scrollTop is preserved (setItems()
  // resets it to 0, which is correct for a new query but wrong when we are
  // merely growing the current result set).
  appendItems(items) {
    if (!Array.isArray(items) || !items.length) return;
    const keep = this.viewport.scrollTop;
    this.items = this.items.concat(items);
    this._layout();                  // recompute spacer height
    this.viewport.scrollTop = keep;  // _layout()/_render() must not eat scroll
    this._render();
  }

  // v2.8.0 — allow the owner to flip the "a request is in flight" flag so the
  // near-end callback cannot fire repeatedly while a page is loading.
  setLoading(flag) {
    this.loading = !!flag;
    if (!this.loading) this._nearEndFired = false;
  }

  _layout() {
    const cols = this._cols();
    const rows = Math.ceil(this.items.length / cols);
    const rowH = this.itemHeight + this.gap;
    this.spacer.style.height = `${Math.max(1, rows * rowH + this.padding)}px`;
    this.viewport.style.padding = `${this.padding}px`;
    this.colsNow = cols;
    this._render();
  }

  _render() {
    const cols = this.colsNow || 1;
    const rowH = this.itemHeight + this.gap;
    const top = this.viewport.scrollTop;
    const h = this.viewport.clientHeight;
    const firstRow = Math.max(0, Math.floor((top - this.padding) / rowH) - 4);
    const lastRow  = Math.min(
      Math.ceil(this.items.length / cols),
      Math.ceil((top + h - this.padding) / rowH) + 4
    );

    const frag = document.createDocumentFragment();
    for (let r = firstRow; r < lastRow; r++) {
      for (let c = 0; c < cols; c++) {
        const idx = r * cols + c;
        if (idx >= this.items.length) break;
        const node = this.renderItem(this.items[idx], idx);
        node.classList.add(this.itemClass);
        node.dataset.idx = String(idx);
        node.style.position = "absolute";
        node.style.left   = `${this.padding + c * (this.host.clientWidth - this.padding*2) / cols}px`;
        node.style.width  = `${(this.host.clientWidth - this.padding*2) / cols - this.gap}px`;
        node.style.top    = `${this.padding + r * rowH}px`;
        node.style.height = `${this.itemHeight}px`;
        frag.appendChild(node);
      }
    }
    // Hard-clear children before re-mounting. Cheap at 62 items.
    this.viewport.replaceChildren(this.spacer, frag);
    this._maybeFireNearEnd();
  }

  // v2.8.0 — trigger onNearEnd once when the viewport nears the bottom, with a
  // re-arm latch so it fires again only after the user scrolls back up (or a
  // page-load completes and resets it). Uses the same scroll listener _render()
  // is already bound to — no IntersectionObserver, no sentinel DOM node.
  _maybeFireNearEnd() {
    if (typeof this.onNearEnd !== "function") return;
    const cols = this.colsNow || 1;
    const rowH = this.itemHeight + this.gap;
    const rows = Math.ceil(this.items.length / cols);
    const spacerH = this.items.length
      ? Math.max(1, rows * rowH + this.padding)
      : 0;
    const top = this.viewport.scrollTop;
    const h = this.viewport.clientHeight;
    const threshold = spacerH - this.nearEndRows * rowH;
    const nearEnd = spacerH > 0 && (top + h) >= threshold;
    if (nearEnd && !this._nearEndFired && !this.loading) {
      this._nearEndFired = true;
      try { this.onNearEnd(); } catch (_) { /* never break rendering */ }
    } else if (!nearEnd) {
      this._nearEndFired = false;
    }
  }

  destroy() {
    this.viewport.removeEventListener("scroll", this.scrollHandler);
    window.removeEventListener("resize", this.resizeHandler);
    this.host.replaceChildren();
  }
}
