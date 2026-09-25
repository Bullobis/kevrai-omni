// renderer/__tests__/window-geometry.test.js
// Regression tests for window bounds validation + off-screen clamping.
import { test } from "node:test";
import assert from "node:assert/strict";
import geom from "../../electron/window-geometry.js";

const { validRect, rectVisibleOnAny, clampRect, DEFAULTS } = geom;

const primary = { x: 0, y: 0, width: 1920, height: 1080 };
const second = { x: 1920, y: 0, width: 1920, height: 1080 };
const areas = [primary, second];

test("a valid rect on a display is returned unchanged", () => {
  const r = { x: 100, y: 100, width: 1200, height: 800 };
  assert.deepEqual(clampRect(r, areas), r);
});

test("rect on the second display is accepted", () => {
  const r = { x: 2000, y: 50, width: 1100, height: 700 };
  assert.deepEqual(clampRect(r, areas), r);
  assert.equal(rectVisibleOnAny(r, areas), true);
});

test("rect fully off-screen (missing monitor) is moved to first display", () => {
  const r = { x: 5000, y: 5000, width: 1200, height: 800 };
  const c = clampRect(r, areas);
  assert.notDeepEqual(c, r);
  assert.equal(rectVisibleOnAny(c, areas), true);
  assert.equal(c.x, primary.x);
  assert.equal(c.y, primary.y);
});

test("rect overlapping display edge by even 1px is kept", () => {
  const r = { x: 1919, y: 0, width: 1200, height: 800 };
  assert.deepEqual(clampRect(r, areas), r);
});

test("malformed inputs yield null", () => {
  for (const bad of [null, undefined, {}, { x: 0, y: 0, width: "w", height: 800 },
    { x: 0, y: 0, width: 10, height: 800 }, // below minWidth
    { x: 0, y: 0, width: 1200, height: 10 }, // below minHeight
  ]) {
    assert.equal(clampRect(bad, areas), null, `reject ${JSON.stringify(bad)}`);
  }
});

test("validRect rejects booleans/NaN/floats for coords", () => {
  assert.equal(validRect({ x: NaN, y: 0, width: 1200, height: 800 }), null);
  assert.equal(validRect({ x: 1.5, y: 0, width: 1200, height: 800 }), null);
});

test("with no display info a valid rect is kept", () => {
  const r = { x: 5000, y: 5000, width: 1200, height: 800 };
  assert.deepEqual(clampRect(r, []), r);
});

test("defaults match the documented minimums", () => {
  assert.equal(DEFAULTS.minWidth, 1024);
  assert.equal(DEFAULTS.minHeight, 700);
});
