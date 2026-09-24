// renderer/__tests__/net.test.js — IPC unwrapping and text escaping.
import { test } from "node:test";
import assert from "node:assert/strict";
import { unwrap, escapeHtml, escapeAttr } from "../modules/net.js";

test("unwrap returns body for wrapped responses", () => {
  assert.deepEqual(unwrap({ status: 200, body: { a: 1 } }), { a: 1 });
});

test("unwrap passes bare objects through", () => {
  assert.deepEqual(unwrap({ a: 1 }), { a: 1 });
});

test("unwrap honours arrays (no body field)", () => {
  assert.deepEqual(unwrap([1, 2]), [1, 2]);
});

test("unwrap uses fallback for null/undefined", () => {
  assert.deepEqual(unwrap(null), {});
  assert.deepEqual(unwrap(undefined, []), []);
});

test("unwrap uses fallback when body is null", () => {
  assert.deepEqual(unwrap({ status: 204, body: null }, []), []);
});

test("escapeHtml escapes the five characters", () => {
  assert.equal(escapeHtml(`<a href="x">&'`), "&lt;a href=&quot;x&quot;&gt;&amp;&#39;");
});

test("escapeHtml handles nullish safely", () => {
  assert.equal(escapeHtml(null), "");
  assert.equal(escapeHtml(undefined), "");
});

test("escapeAttr additionally escapes backticks", () => {
  assert.equal(escapeAttr("`x`"), "&#96;x&#96;");
});
