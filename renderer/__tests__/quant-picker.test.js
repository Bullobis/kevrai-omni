// renderer/__tests__/quant-picker.test.js
// Coverage for the install-time quantization picker: label parsing, rendered
// structure, and the select-all/clear/download interactions.
import { test, beforeEach } from "node:test";
import assert from "node:assert/strict";
import { setupDom } from "./helpers/dom.js";

let models;
let api;

const files = [
  { path: "Qwen3-32B-Q2_K.gguf", size: 12_300_000_000 },
  { path: "Qwen3-32B-Q4_K_M.gguf", size: 19_800_000_000 },
  { path: "sub/Qwen3-32B-IQ4_XS.gguf", size: 18_900_000_000 },
  { path: "Qwen3-32B-F16.gguf", size: 65_000_000_000 },
];
const repo = "unsloth/Qwen3-32B-GGUF";

beforeEach(async () => {
  setupDom("<div id='host'></div>");
  models = await import("../modules/models.js");
  ({ api } = await import("../modules/api.js"));
});

// ── quantLabelOf ────────────────────────────────────────────────────────────
test("parses quantization tag from file name", () => {
  assert.equal(models.quantLabelOf("Qwen3-32B-Q4_K_M.gguf"), "Q4_K_M");
  assert.equal(models.quantLabelOf("dir/Qwen3-32B-IQ4_XS.gguf"), "IQ4_XS");
  assert.equal(models.quantLabelOf("Qwen3-32B-F16.gguf"), "F16");
  assert.equal(models.quantLabelOf("Qwen3-32B-Q2_K.gguf"), "Q2_K");
});

test("returns empty string when no quant tag is present", () => {
  assert.equal(models.quantLabelOf("readme.md"), "");
  assert.equal(models.quantLabelOf(""), "");
});

// ── renderQuantPicker structure ─────────────────────────────────────────────
test("renders empty string for no files or no repo", () => {
  assert.equal(models.renderQuantPicker([], repo), "");
  assert.equal(models.renderQuantPicker(files, ""), "");
  assert.equal(models.renderQuantPicker(null, repo), "");
});

test("renders one checkbox per file with full path as value", () => {
  const html = models.renderQuantPicker(files, repo);
  document.getElementById("host").innerHTML = html;
  const checks = [...document.querySelectorAll(".quant-check")];
  assert.equal(checks.length, 4);
  // Full path (including subdirectory) is preserved for the download call.
  assert.equal(checks[2].value, "sub/Qwen3-32B-IQ4_XS.gguf");
  assert.equal(document.querySelector(".quant-picker").dataset.repo, repo);
  assert.ok(document.querySelector('[data-action=download-quant]'));
});

// ── interactions ─────────────────────────────────────────────────────────────
test("select-all and clear buttons toggle every checkbox", () => {
  document.getElementById("host").innerHTML = models.renderQuantPicker(files, repo);
  models._bindQuantPicker(document.body);
  const checks = () => [...document.querySelectorAll(".quant-check")];

  document.querySelector('[data-action=quant-select-none]').click();
  assert.ok(checks().every((c) => !c.checked));

  document.querySelector('[data-action=quant-select-all]').click();
  assert.ok(checks().every((c) => c.checked));
});

test("download button posts only the checked files via hubDownload", async () => {
  document.getElementById("host").innerHTML = models.renderQuantPicker(files, repo);
  models._bindQuantPicker(document.body);
  let called = null;
  api.hubDownload = async (params) => { called = params; return { ok: true }; };

  // Check the Q4_K_M and IQ4_XS entries.
  const checks = [...document.querySelectorAll(".quant-check")];
  checks[1].checked = true;
  checks[2].checked = true;

  document.querySelector('[data-action=download-quant]').click();
  await Promise.resolve();
  await Promise.resolve();

  assert.deepEqual(called, {
    hub: "hf", repo, revision: "",
    files: ["Qwen3-32B-Q4_K_M.gguf", "sub/Qwen3-32B-IQ4_XS.gguf"],
  });
});

test("download with nothing checked does not call hubDownload", async () => {
  document.getElementById("host").innerHTML = models.renderQuantPicker(files, repo);
  models._bindQuantPicker(document.body);
  let called = false;
  api.hubDownload = async () => { called = true; return {}; };

  document.querySelector('[data-action=download-quant]').click();
  await Promise.resolve();
  assert.equal(called, false);
});
