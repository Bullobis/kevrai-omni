// renderer/__tests__/revision-picker.test.js — revision/branch-tag picker
// logic: parsing, default selection, skip-vs-pick, cancel/fallback, and the
// revision threading into the download payload.
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  DEFAULT_REVISION,
  parseRevisions,
  hasRevisionChoices,
  groupRevisionOptions,
  buildDownloadArgs,
  pickRevision,
} from "../modules/revision-picker.js";
import { pickQuantization } from "../modules/quant-picker.js";
import { setupDom } from "./helpers/dom.js";

const tick = async (ms = 5) => new Promise((r) => setTimeout(r, ms));

// ---------------------------------------------------------------------------
// parseRevisions
// ---------------------------------------------------------------------------
test("parseRevisions normalizes branches/tags", () => {
  const p = parseRevisions({
    repo: "org/quant-model",
    branches: [{ name: "main", ref: "refs/heads/main" }, { name: "dev", ref: "refs/heads/dev" }],
    tags: [{ name: "v1.0", ref: "refs/tags/v1.0" }],
  });
  assert.equal(p.repo, "org/quant-model");
  assert.deepEqual(p.branches.map((b) => b.name), ["main", "dev"]);
  assert.deepEqual(p.tags.map((t) => t.name), ["v1.0"]);
});

test("parseRevisions unwraps the { status, body } IPC envelope", () => {
  const p = parseRevisions({ status: 200, body: { repo: "org/x", branches: [{ name: "main" }], tags: [] } });
  assert.equal(p.repo, "org/x");
  assert.equal(p.branches.length, 1);
});

test("parseRevisions tolerates garbage input", () => {
  for (const bad of [null, undefined, 42, "nope", { branches: "x", tags: 1 }]) {
    const p = parseRevisions(bad);
    assert.equal(p.repo, "");
    assert.deepEqual(p.branches, []);
    assert.deepEqual(p.tags, []);
  }
});

test("parseRevisions drops entries without a usable name", () => {
  const p = parseRevisions({
    branches: [{ name: "main" }, null, { ref: "refs/heads/z" }, { name: "" }],
    tags: [{ name: "v2" }, { name: 123 }],
  });
  assert.deepEqual(p.branches.map((b) => b.name), ["main"]);
  assert.deepEqual(p.tags.map((t) => t.name), ["v2"]);
});

// ---------------------------------------------------------------------------
// hasRevisionChoices / groupRevisionOptions
// ---------------------------------------------------------------------------
test("hasRevisionChoices: only the default branch → no picker", () => {
  assert.equal(hasRevisionChoices({ repo: "x", branches: [{ name: "main" }], tags: [] }), false);
});

test("hasRevisionChoices: a non-main branch → picker", () => {
  assert.equal(hasRevisionChoices({ repo: "x", branches: [{ name: "main" }, { name: "dev" }], tags: [] }), true);
});

test("hasRevisionChoices: any tag → picker", () => {
  assert.equal(hasRevisionChoices({ repo: "x", branches: [{ name: "main" }], tags: [{ name: "v1" }] }), true);
});

test("hasRevisionChoices: empty/unknown → no picker", () => {
  assert.equal(hasRevisionChoices({ repo: "", branches: [], tags: [] }), false);
  assert.equal(hasRevisionChoices(null), false);
});

test("groupRevisionOptions: main first + non-main branches + tags, no dup", () => {
  const opts = groupRevisionOptions({
    repo: "x",
    branches: [{ name: "main" }, { name: "dev" }, { name: "main" }],
    tags: [{ name: "v1.0" }, { name: "v1.1" }],
  });
  assert.deepEqual(opts.map((o) => o.value), ["main", "dev", "v1.0", "v1.1"]);
  assert.equal(opts[0].isDefault, true);
  assert.equal(opts[0].kind, "branch");
  assert.equal(opts[2].kind, "tag");
});

// ---------------------------------------------------------------------------
// buildDownloadArgs (revision → hubDownload contract)
// ---------------------------------------------------------------------------
test("buildDownloadArgs threads revision into the hub download payload", () => {
  const args = buildDownloadArgs({ repo: "org/r", files: ["a.gguf"], revision: "v2.0" });
  assert.deepEqual(args, {
    hub: "hf", repo: "org/r", files: ["a.gguf"], auto_pick: false, revision: "v2.0",
  });
});

test("buildDownloadArgs defaults a missing revision to main", () => {
  assert.equal(buildDownloadArgs({ repo: "x", files: ["a"] }).revision, DEFAULT_REVISION);
});

// ---------------------------------------------------------------------------
// pickRevision UI behaviour (jsdom)
// ---------------------------------------------------------------------------
test("pickRevision: only main → skips the picker, no overlay", async () => {
  setupDom();
  const api = { async modelRevisions() { return { branches: [{ name: "main" }], tags: [] }; } };
  const res = await pickRevision({ id: "m1", name: "M" }, api);
  assert.equal(res.revision, "main");
  assert.equal(res.skipped, true);
  assert.equal(document.querySelector(".overlay"), null);
});

test("pickRevision: lookup fails/timeout → graceful fallback to main, no overlay", async () => {
  setupDom();
  const api = { async modelRevisions() { throw new Error("sidecar 502"); } };
  const res = await pickRevision({ id: "m1", name: "M" }, api);
  assert.equal(res.revision, "main");
  assert.equal(res.fallback, true);
  assert.equal(document.querySelector(".overlay"), null);
});

test("pickRevision: with choices → overlay shows main pre-selected", async () => {
  setupDom();
  const api = {
    async modelRevisions() {
      return { repo: "org/r", branches: [{ name: "main" }, { name: "dev" }], tags: [{ name: "v1.0" }] };
    },
  };
  const pending = pickRevision({ id: "m1", name: "M" }, api);
  await tick();
  const overlay = document.querySelector(".overlay");
  assert.ok(overlay, "overlay should be rendered");
  const opts = overlay.querySelectorAll(".quant-opt");
  assert.equal(opts.length, 3); // main, dev, v1.0
  assert.ok(opts[0].classList.contains("is-sel"), "main is pre-selected");
  // Cancel via the × button → abort.
  overlay.querySelector(".quant-x").click();
  assert.equal(await pending, null);
});

test("pickRevision: Escape cancels (abort install)", async () => {
  setupDom();
  const api = {
    async modelRevisions() { return { branches: [{ name: "main" }], tags: [{ name: "v1" }] }; },
  };
  const pending = pickRevision({ id: "m1", name: "M" }, api);
  await tick();
  assert.ok(document.querySelector(".overlay"));
  document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" }));
  assert.equal(await pending, null);
});

test("pickRevision: backdrop click cancels (abort install)", async () => {
  setupDom();
  const api = {
    async modelRevisions() { return { branches: [{ name: "main" }], tags: [{ name: "v1" }] }; },
  };
  const pending = pickRevision({ id: "m1", name: "M" }, api);
  await tick();
  const overlay = document.querySelector(".overlay");
  overlay.click(); // target === overlay
  assert.equal(await pending, null);
});

test("pickRevision: choose a tag then 继续 → resolves that revision", async () => {
  setupDom();
  const api = {
    async modelRevisions() {
      return { repo: "org/r", branches: [{ name: "main" }], tags: [{ name: "v1.0" }, { name: "v1.1" }] };
    },
  };
  const pending = pickRevision({ id: "m1", name: "M" }, api);
  await tick();
  const overlay = document.querySelector(".overlay");
  // Options: main(0), v1.0(1), v1.1(2). Pick v1.1.
  overlay.querySelector('.quant-opt[data-i="2"]').click();
  overlay.querySelector(".quant-ok").click();
  const res = await pending;
  assert.equal(res.revision, "v1.1");
  assert.equal(res.repo, "org/r");
  assert.equal(document.querySelector(".overlay"), null, "overlay removed after resolve");
});

// ---------------------------------------------------------------------------
// revision threading through pickQuantization → api.modelGgufFiles
// ---------------------------------------------------------------------------
test("pickQuantization forwards the chosen revision to the gguf-files call", async () => {
  setupDom();
  const calls = [];
  const api = {
    async modelGgufFiles(id, rev) {
      calls.push([id, rev]);
      return { body: { files: [{ path: "m-Q4_K_M.gguf", size: 14e9 }], repo: "org/r" } };
    },
  };
  const pending = pickQuantization({ id: "m1", name: "M" }, api, "v1.0");
  await tick();
  assert.deepEqual(calls[0], ["m1", "v1.0"]);
  // Dismiss the quant overlay so the promise settles (user cancelled).
  document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" }));
  assert.equal(await pending, null);
});

test("pickQuantization with empty revision still calls gguf-files", async () => {
  setupDom();
  const calls = [];
  const api = {
    async modelGgufFiles(id, rev) {
      calls.push([id, rev]);
      return { body: { files: [{ path: "m-Q4_K_M.gguf", size: 14e9 }], repo: "org/r" } };
    },
  };
  const pending = pickQuantization({ id: "m1", name: "M" }, api);
  await tick();
  assert.deepEqual(calls[0], ["m1", ""]);
  document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" }));
  await pending;
});
