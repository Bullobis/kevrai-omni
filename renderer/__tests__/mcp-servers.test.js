// renderer/__tests__/mcp-servers.test.js — pure helpers for the MCP panel.
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  escapeHtml, mcpToolsHtml, sampleArgs,
} from "../modules/mcp-servers.js";

test("escapeHtml neutralizes markup and quotes", () => {
  assert.equal(escapeHtml(`<a href="x">&'`),
    "&lt;a href=&quot;x&quot;&gt;&amp;&#39;");
  assert.equal(escapeHtml(null), "");
});

test("mcpToolsHtml renders tool name, description and required params", () => {
  const html = mcpToolsHtml([
    {
      name: "add",
      description: "adds two numbers",
      inputSchema: {
        type: "object",
        properties: {
          a: { type: "integer" },
          b: { type: "integer" },
        },
        required: ["a", "b"],
      },
    },
  ]);
  assert.match(html, /🔧 add/);
  assert.match(html, /adds two numbers/);
  assert.match(html, /<code>a \*<\/code>/);
  assert.match(html, /<code>b \*<\/code>/);
});

test("mcpToolsHtml empty list", () => {
  const html = mcpToolsHtml([]);
  assert.match(html, /noTools/);
});

test("sampleArgs fills placeholders by type", () => {
  const args = sampleArgs({
    inputSchema: {
      properties: {
        n: { type: "integer" },
        f: { type: "number" },
        flag: { type: "boolean" },
        name: { type: "string" },
      },
    },
  });
  assert.deepEqual(args, { n: 0, f: 0, flag: false, name: "" });
});

test("sampleArgs honours declared examples", () => {
  const args = sampleArgs({
    inputSchema: { properties: { q: { type: "string", example: "hello" } } },
  });
  assert.equal(args.q, "hello");
});
