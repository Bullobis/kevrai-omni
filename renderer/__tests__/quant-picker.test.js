// renderer/__tests__/quant-picker.test.js — GGUF quantization grouping.
import { test } from "node:test";
import assert from "node:assert/strict";
import { quantKeyOf, groupFiles, classifyFit, defaultIndex } from "../modules/quant-picker.js";

test("quantKeyOf reads a flat bartowski filename", () => {
  assert.equal(
    quantKeyOf("mistralai_Mistral-Small-24B-Base-2501-Q4_K_M.gguf"),
    "Q4_K_M",
  );
});

test("quantKeyOf reads an unsloth directory + shard layout", () => {
  assert.equal(
    quantKeyOf("IQ4_NL/Mistral-Medium-IQ4_NL-00001-of-00003.gguf"),
    "IQ4_NL",
  );
});

test("quantKeyOf prefers the longer quant token", () => {
  // Must not collapse Q4_K_M to a bare Q4.
  assert.equal(quantKeyOf("model-Q5_K_M.gguf"), "Q5_K_M");
});

test("quantKeyOf returns null for an unrecognised file", () => {
  assert.equal(quantKeyOf("README.md"), null);
});

test("groupFiles aggregates shards into one quant and sums sizes", () => {
  const files = [
    { path: "IQ4_NL/m-IQ4_NL-00001-of-00003.gguf", size: 25e9 },
    { path: "IQ4_NL/m-IQ4_NL-00002-of-00003.gguf", size: 25e9 },
    { path: "IQ4_NL/m-IQ4_NL-00003-of-00003.gguf", size: 26e9 },
  ];
  const groups = groupFiles(files);
  assert.equal(groups.length, 1);
  assert.equal(groups[0].key, "IQ4_NL");
  assert.equal(groups[0].files.length, 3);
  assert.equal(groups[0].size, 76e9);
});

test("groupFiles orders by the QUANT_META display order", () => {
  const files = [
    { path: "m-Q2_K.gguf", size: 6e9 },
    { path: "m-Q4_K_M.gguf", size: 14e9 },
    { path: "m-Q8_0.gguf", size: 25e9 },
  ];
  const keys = groupFiles(files).map((g) => g.key);
  assert.deepEqual(keys, ["Q8_0", "Q4_K_M", "Q2_K"]);
});

test("groupFiles ignores non-gguf entries", () => {
  const groups = groupFiles([
    { path: "README.md", size: 100 },
    { path: "m-Q4_K_M.gguf", size: 14e9 },
  ]);
  assert.equal(groups.length, 1);
  assert.equal(groups[0].key, "Q4_K_M");
});

// --- hardware fit classification ---

const gpuBox = { gpu_best_vram_gb: 24, gpu_total_vram_gb: 24, ram_total_gb: 64 };

test("classifyFit: model fitting in GPU VRAM is green", () => {
  assert.equal(classifyFit(18, gpuBox).level, "ok");
});

test("classifyFit: model just over VRAM but within RAM is yellow (offload)", () => {
  assert.equal(classifyFit(30, gpuBox).level, "warn");
});

test("classifyFit: model exceeding RAM is red", () => {
  assert.equal(classifyFit(70, gpuBox).level, "no");
});

test("classifyFit: no GPU but within RAM is yellow (CPU)", () => {
  const cpu = { gpu_best_vram_gb: 0, gpu_total_vram_gb: 0, ram_total_gb: 32 };
  assert.equal(classifyFit(8, cpu).level, "warn");
});

test("classifyFit: no GPU and exceeding RAM is red", () => {
  const cpu = { gpu_best_vram_gb: 0, gpu_total_vram_gb: 0, ram_total_gb: 16 };
  assert.equal(classifyFit(20, cpu).level, "no");
});

test("classifyFit: reserves headroom so a 23GB file on a 24GB GPU is not green", () => {
  assert.notEqual(classifyFit(23, gpuBox).level, "ok");
});

test("classifyFit: missing hardware yields no level", () => {
  assert.equal(classifyFit(10, null).level, "");
});

test("classifyFit: multi-GPU total VRAM spreads a large model (yellow)", () => {
  const multi = { gpu_best_vram_gb: 24, gpu_total_vram_gb: 48, ram_total_gb: 64 };
  assert.equal(classifyFit(40, multi).level, "warn");
});

// --- default selection ---

function mkGroups(keys) {
  return keys.map((key) => ({ key, files: [], size: 0 }));
}

test("defaultIndex picks the recommended quant when it fits", () => {
  const groups = mkGroups(["Q8_0", "Q4_K_M", "Q2_K"]);
  const fits = [{ level: "ok" }, { level: "ok" }, { level: "ok" }];
  assert.equal(defaultIndex(groups, fits), 1); // Q4_K_M is the rec
});

test("defaultIndex avoids a recommended quant that does not fit, picks green", () => {
  const groups = mkGroups(["Q8_0", "Q4_K_M", "Q2_K"]);
  const fits = [{ level: "ok" }, { level: "no" }, { level: "warn" }];
  assert.equal(defaultIndex(groups, fits), 0);
});

test("defaultIndex falls back to yellow when nothing is green", () => {
  const groups = mkGroups(["Q8_0", "Q4_K_M"]);
  const fits = [{ level: "no" }, { level: "warn" }];
  assert.equal(defaultIndex(groups, fits), 1);
});

test("defaultIndex never returns a red-only index", () => {
  const groups = mkGroups(["Q8_0", "Q4_K_M", "Q2_K"]);
  const fits = [{ level: "no" }, { level: "no" }, { level: "warn" }];
  assert.equal(defaultIndex(groups, fits), 2);
});
