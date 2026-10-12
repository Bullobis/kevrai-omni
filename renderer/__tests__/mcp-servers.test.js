// renderer/__tests__/mcp-servers.test.js — pure helpers for the MCP panel.
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  escapeHtml, mcpToolsHtml, parseStdioArgs, parseStdioEnv, sampleArgs,
  stdioCommandLine,
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

test("parseStdioArgs splits lines and drops blanks", () => {
  assert.deepEqual(parseStdioArgs("-y\n\n@modelcontextprotocol/server-x\n  "),
    ["-y", "@modelcontextprotocol/server-x"]);
  assert.deepEqual(parseStdioArgs(""), []);
  assert.deepEqual(parseStdioArgs(null), []);
});

test("parseStdioEnv builds KEY=VALUE map, ignores junk", () => {
  assert.deepEqual(parseStdioEnv('TOKEN=abc\n\nNOEQUALS\nVER = 2\n='),
    { TOKEN: "abc", VER: "2" });
  assert.deepEqual(parseStdioEnv(""), {});
});

test("stdioCommandLine joins command and args", () => {
  assert.equal(
    stdioCommandLine({ command: "npx", args: ["-y", "server-x"] }),
    "npx -y server-x");
  assert.equal(stdioCommandLine({ command: "python", args: [] }), "python");
});
