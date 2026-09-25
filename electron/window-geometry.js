"use strict";
/**
 * Pure window-geometry helpers for window-state persistence — NO electron
 * import, so they can be unit-tested in plain Node.
 */
const DEFAULTS = { width: 1380, height: 900, minWidth: 1024, minHeight: 700 };

function isInt(v) {
  return typeof v === "number" && Number.isFinite(v) && Math.floor(v) === v;
}

/** Validate a bounds object; return a clean copy or null when malformed. */
function validRect(r) {
  if (!r || typeof r !== "object") return null;
  const { x, y, width, height } = r;
  if (![x, y, width, height].every(isInt)) return null;
  if (width < DEFAULTS.minWidth || height < DEFAULTS.minHeight) return null;
  return { x, y, width, height };
}

/** True if the rect overlaps at least one display workArea. */
function rectVisibleOnAny(rect, areas) {
  return areas.some(
    (a) =>
      rect.x < a.x + a.width &&
      rect.x + rect.width > a.x &&
      rect.y < a.y + a.height &&
      rect.y + rect.height > a.y,
  );
}

/**
 * Return the rect if it is visible on a current display; if it was saved on a
 * now-missing monitor (fully off-screen), move it onto the first display.
 * Returns null for malformed input.
 */
function clampRect(rect, areas) {
  const v = validRect(rect);
  if (!v) return null;
  if (!areas.length) return v;
  if (rectVisibleOnAny(v, areas)) return v;
  const a = areas[0];
  return {
    x: a.x,
    y: a.y,
    width: Math.min(v.width, a.width),
    height: Math.min(v.height, a.height),
  };
}

module.exports = { DEFAULTS, isInt, validRect, rectVisibleOnAny, clampRect };
