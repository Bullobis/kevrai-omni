// renderer/__tests__/compare.test.js — compare selection state + persistence +
// pure dimension/diff logic. Same localStorage shim trick as favorites.test.js:
// jsdom uses an opaque origin so window.localStorage throws; we install an
// in-memory shim on globalThis.localStorage before each test.
import { test, beforeEach, afterEach } from "node:test";
import assert from "node:assert/strict";
import * as cmp from "../modules/compare.js";

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

test("addToCompare adds a model and isInCompare reflects it", () => {
  assert.equal(cmp.isInCompare("llama-3"), false);
  assert.equal(cmp.addToCompare({ id: "llama-3", name: "Llama 3" }), true);
  assert.equal(cmp.isInCompare("llama-3"), true);
  assert.deepEqual(cmp.getCompareList(), ["llama-3"]);
});

test("addToCompare refuses a 5th model (cap 4); existing list intact", () => {
  for (let i = 0; i < cmp.COMPARE_MAX; i++) {
    assert.equal(cmp.addToCompare({ id: "m-" + i }), true);
  }
  assert.equal(cmp.getCompareList().length, 4);
  const rejected = cmp.addToCompare({ id: "overflow" });
  assert.equal(rejected, false, "5th model must not be added");
  assert.equal(cmp.isInCompare("overflow"), false);
  assert.equal(cmp.getCompareList().length, 4, "cap must not grow");
});

test("addToCompare is idempotent: re-adding an existing model is a no-op", () => {
  cmp.addToCompare({ id: "dup" });
  assert.equal(cmp.addToCompare({ id: "dup" }), false);
  assert.deepEqual(cmp.getCompareList(), ["dup"]);
});

test("removeFromCompare removes an id and reports whether it removed anything", () => {
  cmp.addToCompare({ id: "a" });
  cmp.addToCompare({ id: "b" });
  assert.equal(cmp.removeFromCompare("a"), true);
  assert.deepEqual(cmp.getCompareList(), ["b"]);
  assert.equal(cmp.removeFromCompare("nope"), false);
});

test("clearCompare empties the whole list", () => {
  cmp.addToCompare({ id: "x" });
  cmp.addToCompare({ id: "y" });
  assert.equal(cmp.getCompareList().length, 2);
  cmp.clearCompare();
  assert.deepEqual(cmp.getCompareList(), []);
});

test("toggleCompare flips state: add returns true, second call returns false", () => {
  const model = { id: "toggle-me" };
  assert.equal(cmp.toggleCompare(model), true, "first toggle adds");
  assert.equal(cmp.isInCompare("toggle-me"), true);
  assert.equal(cmp.toggleCompare(model), false, "second toggle removes");
  assert.equal(cmp.isInCompare("toggle-me"), false);
});

test("selection persists to localStorage under kevrai:compare and reads back", () => {
  cmp.addToCompare({ id: "persist-1" });
  cmp.addToCompare({ id: "persist-2" });
  const raw = store.getItem("kevrai:compare");
  assert.ok(raw && raw.includes("persist-1") && raw.includes("persist-2"),
    "ids must be stored under the documented key");
  // Reading back goes through the same storage backend (no hidden cache).
  assert.deepEqual(cmp.getCompareList(), ["persist-1", "persist-2"]);
  assert.equal(cmp.isInCompare("persist-1"), true);
});

test("blank / whitespace ids are ignored", () => {
  assert.equal(cmp.addToCompare({ id: "" }), false);
  assert.equal(cmp.addToCompare({ id: "   " }), false);
  assert.equal(cmp.toggleCompare({ id: "" }), false);
  assert.deepEqual(cmp.getCompareList(), []);
});

test("registerModel + getCompareModels resolves full objects; unknown id degrades to {id}", () => {
  cmp.registerModel({ id: "known-1", name: "Known One", size_gb: 7.6 });
  cmp.addToCompare({ id: "known-1" });
  cmp.addToCompare({ id: "ghost-id" });   // never registered
  const models = cmp.getCompareModels();
  assert.equal(models[0].name, "Known One");
  assert.equal(models[1].id, "ghost-id", "unregistered id still appears, missing fields → —");
});

test("rowHasDiff: identical values do NOT differ; differing values DO", () => {
  assert.equal(cmp.rowHasDiff(["MIT", "MIT", "MIT"]), false);
  assert.equal(cmp.rowHasDiff(["7B", "13B", "7B"]), true);
  assert.equal(cmp.rowHasDiff([cmp.COMPARE_MISSING, cmp.COMPARE_MISSING]), false);
  assert.equal(cmp.rowHasDiff([cmp.COMPARE_MISSING, "MIT"]), true);
  assert.equal(cmp.rowHasDiff(["only one"]), false, "single value is not a diff");
  assert.equal(cmp.rowHasDiff([]), false);
});

test("dimensionDisplay: real fields render; missing catalog fields show —", () => {
  const m = {
    id: "qwen", name: "Qwen", category: "llm", task: "text-generation",
    size_gb: 4.7, license: "Apache-2.0", engine: ["llama.cpp"],
    hardware: { vram_gb: 8 }, repo: "Qwen/Qwen2.5", trending: true, tags: ["chat"],
  };
  assert.equal(cmp.dimensionDisplay(m, "name"), "Qwen");
  assert.equal(cmp.dimensionDisplay(m, "category"), "llm / text-generation");
  assert.equal(cmp.dimensionDisplay(m, "size"), "4.7 GB");
  assert.equal(cmp.dimensionDisplay(m, "engine"), "llama.cpp");
  assert.equal(cmp.dimensionDisplay(m, "vram"), "8 GB");
  assert.equal(cmp.dimensionDisplay(m, "trending"), "🔥");
  assert.equal(cmp.dimensionDisplay(m, "tags"), "chat");

  // A sparse catalog entry (no params/date fields — those don't exist in catalog)
  const sparse = { id: "sparse" };
  assert.equal(cmp.dimensionDisplay(sparse, "name"), "sparse", "name falls back to id");
  assert.equal(cmp.dimensionDisplay(sparse, "license"), cmp.COMPARE_MISSING);
  assert.equal(cmp.dimensionDisplay(sparse, "size"), cmp.COMPARE_MISSING);
  assert.equal(cmp.dimensionDisplay(sparse, "vram"), cmp.COMPARE_MISSING);
  assert.equal(cmp.dimensionDisplay(sparse, "tags"), cmp.COMPARE_MISSING);
  assert.equal(cmp.dimensionDisplay(null, "name"), cmp.COMPARE_MISSING);
});

test("description is truncated to ~80 chars; empty shows —", () => {
  const long = { id: "long", description: "x".repeat(200) };
  const out = cmp.dimensionDisplay(long, "description");
  assert.ok(out.length <= 82 && out.endsWith("…"), "long desc truncated with ellipsis");
  assert.equal(cmp.dimensionDisplay({ id: "e" }, "description"), cmp.COMPARE_MISSING);
});
