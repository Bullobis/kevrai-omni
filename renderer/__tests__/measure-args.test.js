// renderer/__tests__/measure-args.test.js
// Regression tests for the measureSources bridge argument normalizer.
// Bug: the downloads page "force remeasure" button passed an OPTIONS OBJECT
// { urls, force: true }, but the bridge only accepted an ARRAY and threw
// "urls must be an array of strings", so the button always failed; force was
// also never forwarded to the sidecar.
import { test } from "node:test";
import assert from "node:assert/strict";
import measureArgsMod from "../../electron/measure-args.js";

const { normalizeMeasureArgs } = measureArgsMod;

test("legacy plain-array call (environment page) still works", () => {
  const p = normalizeMeasureArgs(["https://a.example.com", "https://b.example.org"]);
  assert.deepEqual(p, { urls: ["https://a.example.com", "https://b.example.org"] });
});

test("options object with urls + force is accepted and force forwarded", () => {
  const p = normalizeMeasureArgs({
    urls: ["https://a.example.com"], force: true,
  });
  assert.deepEqual(p, { urls: ["https://a.example.com"], force: true });
});

test("options object without force omits the force flag", () => {
  const p = normalizeMeasureArgs({ urls: ["https://a.example.com"] });
  assert.deepEqual(p, { urls: ["https://a.example.com"] });
  assert.equal("force" in p, false);
});

test("file_size is truncated and forwarded when positive", () => {
  const p = normalizeMeasureArgs({
    urls: ["https://a.example.com"], file_size: 123.9,
  });
  assert.equal(p.file_size, 123);
});

test("non-positive / non-finite file_size is dropped", () => {
  for (const v of [0, -5, NaN]) {
    const p = normalizeMeasureArgs({ urls: ["https://a.example.com"], file_size: v });
    assert.equal("file_size" in p, false, `should drop file_size=${v}`);
  }
});

test("non-array urls in an object throws", () => {
  assert.throws(() => normalizeMeasureArgs({ urls: "https://a.example.com" }),
    /urls must be an array/);
});

test("bare non-object/non-array argument throws", () => {
  assert.throws(() => normalizeMeasureArgs(null), /object or an array/);
  assert.throws(() => normalizeMeasureArgs("nope"), /object or an array/);
});

test("non-http(s) url string throws", () => {
  assert.throws(() => normalizeMeasureArgs(["ftp://a.example.com"]),
    /http\(s\) string/);
  assert.throws(() => normalizeMeasureArgs({ urls: ["not-a-url"] }),
    /http\(s\) string/);
});

test("urls are capped at 32 and length at 2048", () => {
  const many = Array.from({ length: 40 }, (_, i) => `https://h${i}.example.com`);
  const p = normalizeMeasureArgs(many);
  assert.equal(p.urls.length, 32);
  const long = "https://a.example.com/" + "x".repeat(3000);
  const p2 = normalizeMeasureArgs([long]);
  assert.equal(p2.urls[0].length, 2048);
});
