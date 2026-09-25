// renderer/__tests__/favorites.test.js — localStorage-backed favorites + recent
// usage. jsdom's default JSDOM uses an opaque origin (about:blank), which makes
// window.localStorage throw, so we install a tiny spec-compliant in-memory shim
// on globalThis.localStorage. favorites.js resolves storage lazily per call, so
// swapping the shim in beforeEach gives every test a clean slate.
import { test, beforeEach, afterEach } from "node:test";
import assert from "node:assert/strict";
import * as fav from "../modules/favorites.js";

function makeStorage() {
  const map = new Map();
  return {
    getItem: (k) => (map.has(k) ? map.get(k) : null),
    setItem: (k, v) => { map.set(k, String(v)); },
    removeItem: (k) => { map.delete(k); },
    clear: () => map.clear(),
    _map: map,
  };
}

let store;

beforeEach(() => {
  store = makeStorage();
  globalThis.localStorage = store;
});

afterEach(() => {
  delete globalThis.localStorage;
});

test("toggleFavorite adds a model, returns true; isFavorite reflects it", () => {
  assert.equal(fav.isFavorite("llama-3"), false);
  const now = fav.toggleFavorite("llama-3");
  assert.equal(now, true);
  assert.equal(fav.isFavorite("llama-3"), true);
  assert.deepEqual(fav.getFavorites(), ["llama-3"]);
});

test("toggleFavorite again removes the model, returns false", () => {
  fav.toggleFavorite("a");
  assert.equal(fav.toggleFavorite("a"), false);
  assert.equal(fav.isFavorite("a"), false);
  assert.deepEqual(fav.getFavorites(), []);
});

test("getFavorites is ordered most-recently-favorited first", () => {
  fav.toggleFavorite("a");
  fav.toggleFavorite("b");
  fav.toggleFavorite("c");
  assert.deepEqual(fav.getFavorites(), ["c", "b", "a"]);
  // Un-favorite then re-favorite "a" → it jumps to the front.
  fav.toggleFavorite("a");
  fav.toggleFavorite("a");
  assert.deepEqual(fav.getFavorites(), ["a", "c", "b"]);
});

test("favorites are capped at FAV_MAX=200 (201st refused, existing list intact)", () => {
  for (let i = 0; i < fav.FAV_MAX; i++) fav.toggleFavorite("fav-" + i);
  assert.equal(fav.getFavorites().length, 200);
  const rejected = fav.toggleFavorite("overflow-1");
  assert.equal(rejected, false, "201st favorite must stay unfavorited");
  assert.equal(fav.isFavorite("overflow-1"), false);
  assert.equal(fav.getFavorites().length, 200, "cap must not grow");
});

test("bumpRecent records and orders by recency; re-bump moves to front", () => {
  fav.bumpRecent("a");
  fav.bumpRecent("b");
  fav.bumpRecent("c");
  assert.deepEqual(fav.getRecent(), ["c", "b", "a"]);
  fav.bumpRecent("b");   // bump again → to front, no duplicate
  assert.deepEqual(fav.getRecent(), ["b", "c", "a"]);
});

test("recent usage is capped at RECENT_MAX=20", () => {
  for (let i = 0; i < 25; i++) fav.bumpRecent("r-" + i);
  const recent = fav.getRecent();
  assert.equal(recent.length, 20);
  // Newest is the last bumped (r-24); the oldest (r-0..r-4) fell off.
  assert.equal(recent[0], "r-24");
  assert.ok(!recent.includes("r-0"), "oldest entry evicted");
  assert.ok(recent.includes("r-5"), "boundary entry kept");
});

test("clearRecent empties the recent list", () => {
  fav.bumpRecent("x");
  fav.bumpRecent("y");
  assert.equal(fav.getRecent().length, 2);
  fav.clearRecent();
  assert.deepEqual(fav.getRecent(), []);
});

test("state persists to localStorage under the kevrai: prefix", () => {
  fav.toggleFavorite("persist-me");
  fav.bumpRecent("persist-me");
  // Raw keys exist with the documented prefix.
  assert.ok(store.getItem("kevrai:favorites").includes("persist-me"));
  assert.ok(store.getItem("kevrai:recent").includes("persist-me"));
  // Reading back goes through the same storage backend (no hidden in-memory cache).
  assert.equal(fav.isFavorite("persist-me"), true);
  assert.deepEqual(fav.getRecent(), ["persist-me"]);
});

test("blank / whitespace model ids are ignored by every write", () => {
  assert.equal(fav.toggleFavorite(""), false);
  assert.equal(fav.toggleFavorite("   "), false);
  fav.bumpRecent("");
  fav.bumpRecent("  ");
  assert.deepEqual(fav.getFavorites(), []);
  assert.deepEqual(fav.getRecent(), []);
});
