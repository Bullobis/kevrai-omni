// renderer/__tests__/quant-picker.test.js — GGUF quantization grouping.
import { test } from "node:test";
import assert from "node:assert/strict";
import { quantKeyOf, groupFiles } from "../modules/quant-picker.js";

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
