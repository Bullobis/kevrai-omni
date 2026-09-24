// renderer/__tests__/virtual-grid.test.js — windowing, rAF throttling, geometry
// source, keyboard activation and near-end incremental loading.
import { test, beforeEach } from "node:test";
import assert from "node:assert/strict";
import { setupDom, stubSize } from "./helpers/dom.js";

let host;
let VirtualGrid;
let flushFrames;

beforeEach(async () => {
  const env = setupDom('<div id="host"></div>');
  host = env.document.getElementById("host");
  flushFrames = env.flushFrames;
  ({ VirtualGrid } = await import("../modules/virtual-grid.js"));
});

function makeItems(n) {
  return Array.from({ length: n }, (_, i) => ({ id: `m${i}`, name: `Model ${i}` }));
}

function newGrid(opts = {}) {
  const grid = new VirtualGrid(host, {
    itemHeight: 100,
    gap: 10,
    padding: 8,
    colsOfWidth: () => 3,
    renderItem: (m) => {
      const d = document.createElement("div");
      d.textContent = m.name;
      return d;
    },
    ...opts,
  });
  // Simulate a laid-out viewport (excluding a ~20px vertical scrollbar).
  stubSize(grid.viewport, { width: 880, height: 220 });
  stubSize(grid.host, { width: 900, height: 600 });
  return grid;
}

test("only the visible window (+overscan) is mounted", () => {
  const grid = newGrid();
  grid.setItems(makeItems(100));
  // h=220,rowH=110 → rows 0..1 + 4 overscan = rows 0..5 → 18 cells
  assert.equal(grid.viewport.querySelectorAll(".vgrid-item").length, 18);
});

test("scrolling re-windows the mounted range, throttled to one frame", async () => {
  const grid = newGrid();
  grid.setItems(makeItems(100));
  grid.viewport.scrollTop = 500;
  grid.viewport.dispatchEvent(new Event("scroll"));
  // Synchronous state is unchanged (deferred to rAF).
  assert.equal(grid.viewport.querySelectorAll(".vgrid-item").length, 18);
  await flushFrames();
  // At scrollTop 500: firstRow = floor((500-8)/110)-4 = 0,
  // lastRow = ceil((500+220-8)/110)+4 = 11 → rows 0..10 = 33 cells
  assert.equal(grid.viewport.querySelectorAll(".vgrid-item").length, 33);
});

test("item geometry uses viewport inner width, not host width", () => {
  const grid = newGrid();
  grid.setItems(makeItems(9));
  const first = grid.viewport.querySelector(".vgrid-item");
  // innerW = 880-16 = 864; col width = 864/3 - 10 = 278
  assert.equal(first.style.width, "278px");
  // col 0 left = 8; col 1 left = 8 + 288 = 296
  const cells = grid.viewport.querySelectorAll(".vgrid-item");
  assert.equal(cells[0].style.left, "8px");
  assert.equal(cells[1].style.left, "296px");
});

test("keyboard Enter on a card activates onItemClick", () => {
  let clicked = null;
  const grid = newGrid({ onItemClick: (idx) => { clicked = idx; } });
  grid.setItems(makeItems(100));
  grid.viewport.querySelector(".vgrid-item")
    .dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
  assert.equal(clicked, 0);
});

test("Space on a card activates onItemClick and is prevented", () => {
  let clicked = null;
  const grid = newGrid({ onItemClick: (idx) => { clicked = idx; } });
  grid.setItems(makeItems(100));
  const card = grid.viewport.querySelectorAll(".vgrid-item")[2];
  const ev = new KeyboardEvent("keydown", { key: " ", bubbles: true, cancelable: true });
  card.dispatchEvent(ev);
  assert.equal(clicked, 2);
  assert.equal(ev.defaultPrevented, true);
});

test("near-end fires onNearEnd once when scrolled to bottom", async () => {
  let calls = 0;
  const grid = newGrid({ onNearEnd: () => { calls += 1; } });
  grid.setItems(makeItems(100));
  // 34 rows → spacerH = 34*110+8 = 3748; viewport h=220.
  grid.viewport.scrollTop = 3748 - 220;
  grid.viewport.dispatchEvent(new Event("scroll"));
  await flushFrames();
  assert.equal(calls, 1);
});

test("near-end does not fire while loading", async () => {
  let calls = 0;
  const grid = newGrid({ onNearEnd: () => { calls += 1; } });
  grid.setItems(makeItems(100));
  grid.setLoading(true);
  grid.viewport.scrollTop = 3748 - 220;
  grid.viewport.dispatchEvent(new Event("scroll"));
  await flushFrames();
  assert.equal(calls, 0);
});

test("appendItems preserves scroll position and grows content", () => {
  const grid = newGrid();
  grid.setItems(makeItems(30));
  grid.viewport.scrollTop = 120;
  grid.appendItems(makeItems(30));
  assert.equal(grid.items.length, 60);
  assert.equal(grid.viewport.scrollTop, 120);
});

test("destroy removes listeners and clears the host", () => {
  const grid = newGrid();
  grid.setItems(makeItems(10));
  grid.destroy();
  assert.equal(host.children.length, 0);
});
