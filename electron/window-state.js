"use strict";
/**
 * Window size/position/maximized persistence across restarts.
 *
 * The pure geometry helpers (validRect / rectVisibleOnAny / clampRect) take
 * plain rect objects and have NO electron dependency, so they can be unit
 * tested in Node. The load/save/attach/initialOptions functions wire them to
 * Electron (userData file + BrowserWindow events).
 *
 * Restored bounds are always clamped so a window saved on a since-disconnected
 * monitor never reopens fully off-screen.
 */
const { app, screen } = require("electron");
const path = require("node:path");
const fs = require("node:fs");
const {
  DEFAULTS,
  validRect,
  rectVisibleOnAny,
  clampRect,
} = require("./window-geometry");

function stateFile() {
  return path.join(app.getPath("userData"), "window-state.json");
}

function workAreas() {
  return screen.getAllDisplays().map((d) => d.workArea || d.bounds);
}

/** Load + clamp the persisted state. */
function load(areas = workAreas()) {
  let raw = null;
  try {
    raw = JSON.parse(fs.readFileSync(stateFile(), "utf-8"));
  } catch (_) {
    raw = null;
  }
  const rect = clampRect(raw && raw.bounds, areas);
  const maximized = Boolean(raw && raw.maximized);
  return { rect, maximized };
}

/** Atomically persist bounds + maximized; best-effort, never throws. */
function save(bounds, maximized) {
  try {
    const tmp = stateFile() + ".tmp";
    fs.writeFileSync(tmp, JSON.stringify({ bounds, maximized }));
    fs.renameSync(tmp, stateFile());
  } catch (_) {
    /* best-effort */
  }
}

/** BrowserWindow constructor options from the persisted (clamped) state. */
function initialOptions(areas = workAreas()) {
  const { rect } = load(areas);
  const opts = {};
  if (rect) {
    opts.x = rect.x;
    opts.y = rect.y;
    opts.width = rect.width;
    opts.height = rect.height;
  }
  return opts;
}

/** Persist window geometry on move/resize/maximize and on close. */
function attach(win) {
  let timer = null;
  const persistNormal = () => {
    if (win.isDestroyed() || win.isMaximized()) return;
    save(win.getBounds(), false);
  };
  const schedule = () => {
    if (timer) clearTimeout(timer);
    timer = setTimeout(persistNormal, 250);
  };
  win.on("resize", schedule);
  win.on("move", schedule);
  win.on("maximize", () => save(undefined, true));
  win.on("unmaximize", () => {
    if (!win.isDestroyed()) save(win.getBounds(), false);
  });
  win.on("close", () => {
    if (timer) clearTimeout(timer);
    if (win.isDestroyed()) return;
    save(win.isMaximized() ? undefined : win.getBounds(), win.isMaximized());
  });
}

module.exports = {
  DEFAULTS,
  validRect,
  rectVisibleOnAny,
  clampRect,
  load,
  save,
  attach,
  initialOptions,
};
