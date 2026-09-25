// renderer/__tests__/search-models-helpers.test.js
// Coverage for two previously-untested pure helpers: search.highlight and
// models.categoryLabel.
import { test, beforeEach } from "node:test";
import assert from "node:assert/strict";
import { setupDom } from "./helpers/dom.js";

let highlight;
let categoryLabel;

beforeEach(async () => {
  setupDom("<div></div>");
  ({ highlight } = await import("../modules/search.js"));
  ({ categoryLabel } = await import("../modules/models.js"));
});

// ── highlight ──────────────────────────────────────────────────────────────
test("with no highlights the text is returned escaped", () => {
  assert.equal(highlight("plain text", null), "plain text");
  assert.equal(highlight("a < b", []), "a &lt; b");
});

test("a matching range is wrapped in <mark>", () => {
  const h = [{ field: "name", start: 0, end: 3 }];
  assert.equal(highlight("abcdef", h), "<mark>abc</mark>def");
});

test("ranges on unhandled fields are ignored", () => {
  const h = [{ field: "unknown", start: 0, end: 3 }];
  assert.equal(highlight("abcdef", h), "abcdef");
});

test("text inside and around the match is HTML-escaped", () => {
  const h = [{ field: "description", start: 2, end: 5 }];
  // text: a< b& cdef  (positions: a=0,<=1,space=2,b=3,&=4,space=5,c=6...)
  const out = highlight("a<b&cdef", h);
  assert.ok(out.includes("<mark>"), out);
  assert.ok(!out.includes("<b&"), "raw markup must be escaped");
  assert.ok(out.includes("&lt;"), out);
});

test("a range ending beyond the text is clamped", () => {
  const h = [{ field: "name", start: 2, end: 999 }];
  const out = highlight("abcd", h);
  assert.equal(out, "ab<mark>cd</mark>");
});

test("a range starting beyond the text is not rendered", () => {
  const h = [{ field: "name", start: 50, end: 60 }];
  assert.equal(highlight("abcd", h), "abcd");
});

test("multiple ranges produce multiple marks", () => {
  const h = [
    { field: "name", start: 0, end: 1 },
    { field: "name", start: 2, end: 3 },
  ];
  assert.equal(highlight("abcd", h), "<mark>a</mark>b<mark>c</mark>d");
});

// ── categoryLabel ──────────────────────────────────────────────────────────
test("a known category id returns a translated, non-empty label", () => {
  const label = categoryLabel("llm");
  assert.equal(typeof label, "string");
  assert.ok(label.length > 0);
  assert.notEqual(label, "llm", "should be a readable label, not the raw id");
});

test("an unknown category id falls back to the id itself", () => {
  assert.equal(categoryLabel("totally-unknown"), "totally-unknown");
});

test("an empty id returns an empty string", () => {
  assert.equal(categoryLabel(""), "");
});
