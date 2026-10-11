// renderer/__tests__/knowledge.test.js — pure frontend helpers for the
// knowledge base panel. No DOM / IPC required: these run under node:test.
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  isAllowedFile,
  isPdfFile,
  isDocxFile,
  isPptxFile,
  isBinaryDocFile,
  sanitizeKbName,
  scorePct,
  escapeHtml,
  formatExportFilename,
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
  assert.equal(isAllowedFile("report.doc"), false);  // legacy binary .doc
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

test("allowlist is the documented ingest set", () => {
  assert.deepEqual(
    [...KB_ALLOWED_EXT].sort(),
    [".csv", ".docx", ".json", ".markdown", ".md", ".pdf", ".pptx", ".txt"].sort()
  );
});

test("formatExportFilename builds a safe kb-name-date.json", () => {
  assert.equal(formatExportFilename("我的笔记", "2026-10-12"),
    "kb-我的笔记-20261012.json");
  assert.equal(formatExportFilename("a/b:c*?", "20261012"),
    "kb-a-b-c-20261012.json");
  // Blank name / date fall back gracefully.
  assert.equal(formatExportFilename("", ""), "kb-kb-00000000.json");
});

test("isPdfFile detects the PDF extension case-insensitively", () => {
  assert.equal(isPdfFile("paper.pdf"), true);
  assert.equal(isPdfFile("paper.PDF"), true);
  assert.equal(isPdfFile("note.md"), false);
  assert.equal(isPdfFile("data.csv"), false);
});

test("allowlist now includes .pdf", () => {
  assert.ok(KB_ALLOWED_EXT.includes(".pdf"));
  assert.equal(isAllowedFile("paper.pdf"), true);
});

test("isDocxFile detects the DOCX extension case-insensitively", () => {
  assert.equal(isDocxFile("notes.docx"), true);
  assert.equal(isDocxFile("NOTES.DOCX"), true);
  assert.equal(isDocxFile("legacy.doc"), false);
  assert.equal(isDocxFile("paper.pdf"), false);
});

test("allowlist now includes .docx and is treated as a binary doc", () => {
  assert.ok(KB_ALLOWED_EXT.includes(".docx"));
  assert.equal(isAllowedFile("notes.docx"), true);
  assert.equal(isBinaryDocFile("notes.docx"), true);
  assert.equal(isBinaryDocFile("paper.pdf"), true);
  assert.equal(isBinaryDocFile("note.md"), false);
});

test("isPptxFile detects the PPTX extension case-insensitively", () => {
  assert.equal(isPptxFile("deck.pptx"), true);
  assert.equal(isPptxFile("DECK.PPTX"), true);
  assert.equal(isPptxFile("legacy.ppt"), false);
  assert.equal(isPptxFile("paper.pdf"), false);
});

test("allowlist now includes .pptx and is treated as a binary doc", () => {
  assert.ok(KB_ALLOWED_EXT.includes(".pptx"));
  assert.equal(isAllowedFile("deck.pptx"), true);
  assert.equal(isBinaryDocFile("deck.pptx"), true);
});
