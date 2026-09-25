// renderer/modules/favorites.js — model favorites + recent usage, persisted to
// localStorage under the "kevrai:" key prefix.
//
// Pure-ish storage helpers: every localStorage access is wrapped in try/catch so
// private/incognito windows (where localStorage throws SecurityError) degrade to
// a silent in-session no-op instead of breaking the market. The functions here
// only touch storage + arrays (no document/window) so they are easy to unit-test
// under node --test.
"use strict";
import { toast } from "./toast.js";
import { t } from "./i18n.js";

const FAV_KEY = "kevrai:favorites";   // string[], most-recently-favorited first
const RECENT_KEY = "kevrai:recent";   // string[], most-recently-used first

export const FAV_MAX = 200;
export const RECENT_MAX = 20;

// Resolve the storage backend lazily every call. Bare `localStorage` is the
// renderer global; `window.localStorage` covers the jsdom test harness (which
// installs window but not a bare global). Returns null when unavailable.
function getStorage() {
  try {
    if (typeof localStorage !== "undefined" && localStorage) return localStorage;
  } catch (_) { /* opaque origin / disabled — fall through */ }
  try {
    if (typeof window !== "undefined" && window.localStorage) return window.localStorage;
  } catch (_) { /* ignore */ }
  return null;
}

function readList(key) {
  const s = getStorage();
  if (!s) return [];
  try {
    const raw = s.getItem(key);
    if (!raw) return [];
    const arr = JSON.parse(raw);
    return Array.isArray(arr) ? arr.filter((x) => typeof x === "string" && x) : [];
  } catch (_) {
    return [];
  }
}

function writeList(key, arr) {
  const s = getStorage();
  if (!s) return;
  try {
    s.setItem(key, JSON.stringify(arr));
  } catch (_) { /* quota / privacy mode — keep in-memory state only */ }
}

function normalizeId(modelId) {
  return String(modelId || "").trim();
}

/**
 * Toggle a model's favorite state. Returns the NEW state (true = now favorited).
 * Respecting FAV_MAX: favoriting beyond the cap shows a toast and leaves the
 * model unfavorited.
 */
export function toggleFavorite(modelId) {
  const id = normalizeId(modelId);
  if (!id) return false;
  const list = readList(FAV_KEY);
  const idx = list.indexOf(id);
  if (idx !== -1) {
    list.splice(idx, 1);
    writeList(FAV_KEY, list);
    return false;
  }
  if (list.length >= FAV_MAX) {
    try { toast(t("toast.favLimit", { n: FAV_MAX }), { kind: "warn" }); } catch (_) {}
    return false;
  }
  list.unshift(id);
  writeList(FAV_KEY, list);
  return true;
}

export function isFavorite(modelId) {
  const id = normalizeId(modelId);
  if (!id) return false;
  return readList(FAV_KEY).includes(id);
}

// Favorite modelIds, most-recently-favorited first.
export function getFavorites() {
  return readList(FAV_KEY);
}

// Record that the user opened this model's detail. Newest first, capped at
// RECENT_MAX (older entries drop off). Re-bumping an id moves it to the front.
export function bumpRecent(modelId) {
  const id = normalizeId(modelId);
  if (!id) return;
  const list = readList(RECENT_KEY);
  const idx = list.indexOf(id);
  if (idx !== -1) list.splice(idx, 1);
  list.unshift(id);
  while (list.length > RECENT_MAX) list.pop();
  writeList(RECENT_KEY, list);
}

// Recently-used modelIds, most-recently-used first.
export function getRecent() {
  return readList(RECENT_KEY);
}

export function clearRecent() {
  writeList(RECENT_KEY, []);
}
