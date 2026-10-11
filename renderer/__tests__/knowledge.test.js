// renderer/__tests__/knowledge.test.js — pure frontend helpers for the
// knowledge base panel. No DOM / IPC required: these run under node:test.
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  isAllowedFile,
  sanitizeKbName,
  scorePct,
  escapeHtml,
  KB_ALLOWED_EXT,
} from "../modules/knowledge.js";

test("isAllowedFile accepts supported text extensions case-insensitively", () => {
  assert.equal(isAllowedFile("notes.md"), true);
  assert.equal(isAllowedFile("README.MD"), true);
  assert.equal(isAllowedFile("data.csv"), true);
  assert.equal(isAllowedFile("config.JSON"), true);
  assert.equal(isAllowedFile("plain.txt"), true);
  assert.equal(isAllowedFile("spec.markdown"), true);
});

test("isAllowedFile rejects binaries and unknown extensions", () => {
  assert.equal(isAllowedFile("video.mp4"), false);
  assert.equal(isAllowedFile("doc.pdf"), false);
  assert.equal(isAllowedFile("archive.zip"), false);
  assert.equal(isAllowedFile(""), false);
});

test("sanitizeKbName trims and caps length", () => {
  assert.equal(sanitizeKbName("  my notes  "), "my notes");
  assert.equal(sanitizeKbName(""), "");
  assert.equal(sanitizeKbName("   "), "");
  const long = "x".repeat(500);
  assert.equal(sanitizeKbName(long).length, 120);
});

test("scorePct maps cosine to 0-100 percent clamped", () => {
  assert.equal(scorePct(1.0), "100%");
  assert.equal(scorePct(0.5), "50%");
  assert.equal(scorePct(0), "0%");
  assert.equal(scorePct(1.4), "100%");   // over-unity clamped
  assert.equal(scorePct(-0.3), "0%");    // negative clamped
  assert.equal(scorePct(NaN), "0%");
});

test("escapeHtml neutralises markup", () => {
  assert.equal(escapeHtml("<b>&\""), "&lt;b&gt;&amp;&quot;");
  assert.equal(escapeHtml(null), "");
  assert.equal(escapeHtml(undefined), "");
});

test("allowlist is the documented text set", () => {
  assert.deepEqual(
    [...KB_ALLOWED_EXT].sort(),
    [".csv", ".json", ".markdown", ".md", ".txt"].sort()
  );
});
