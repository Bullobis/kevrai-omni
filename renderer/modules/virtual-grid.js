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
    this._layout();
    this.viewport.scrollTop = 0;
    this._render();
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
  }

  destroy() {
    this.viewport.removeEventListener("scroll", this.scrollHandler);
    window.removeEventListener("resize", this.resizeHandler);
    this.host.replaceChildren();
  }
}
