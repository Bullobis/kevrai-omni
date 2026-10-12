// renderer/__tests__/agent-persona-ui.test.js — persona dropdown option markup.
import { test } from "node:test";
import assert from "node:assert/strict";
import { personaOptionsHtml } from "../modules/agent-persona-ui.js";

test("builds one option per persona", () => {
  const html = personaOptionsHtml([
    { id: "general", label: "通用助手" },
    { id: "coder", label: "代码助手" },
  ]);
  assert.match(html, /<option value="general">通用助手<\/option>/);
  assert.match(html, /<option value="coder">代码助手<\/option>/);
});

test("empty items yields empty string", () => {
  assert.equal(personaOptionsHtml([]), "");
  assert.equal(personaOptionsHtml(undefined), "");
});

test("escapes HTML in labels and ids", () => {
  const html = personaOptionsHtml([{ id: 'a"><b', label: "<b>x</b>" }]);
  assert.match(html, /&lt;b&gt;x&lt;\/b&gt;/);
  assert.ok(!html.includes("<b>"));
});
