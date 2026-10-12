import test from "node:test";
import assert from "node:assert/strict";
import { renderMarkdown, escapeHtml } from "../modules/md-render.js";

test("raw HTML and scripts are escaped", () => {
  const html = renderMarkdown("<script>alert(1)</script>");
  assert.ok(!html.includes("<script>"), "no raw script tag");
  assert.ok(html.includes("&lt;script&gt;"));
});

test("onerror-style attributes in text are inert", () => {
  const html = renderMarkdown('<img src=x onerror="alert(1)">');
  assert.ok(!html.includes("<img"));
  assert.ok(html.includes("&lt;img"));
});

test("javascript: links are not rendered as anchors", () => {
  const html = renderMarkdown("[click](javascript:alert(1))");
  assert.ok(!html.includes("<a "), "no anchor for javascript:");
});

test("data: links are not rendered as anchors", () => {
  const html = renderMarkdown("[x](data:text/html,<script>)");
  assert.ok(!html.includes("<a "));
});

test("http/https links render safely with noopener", () => {
  const html = renderMarkdown("[site](https://example.com/a)");
  assert.ok(html.includes('href="https://example.com/a"'));
  assert.ok(html.includes('rel="noopener noreferrer"'));
  assert.ok(html.includes('target="_blank"'));
});

test("mailto links are allowed", () => {
  const html = renderMarkdown("[mail](mailto:a@example.com)");
  assert.ok(html.includes('href="mailto:a@example.com"'));
});

test("fenced code block escapes HTML and keeps source verbatim", () => {
  const md = "```js\nconst x = '<img onerror=alert(1)>';\n```";
  const html = renderMarkdown(md);
  assert.ok(html.includes("<pre"));
  assert.ok(!html.includes("<img"));
  assert.ok(html.includes("&lt;img onerror=alert(1)&gt;"));
  assert.ok(html.includes(">js<") || html.includes('>js</span>'));
});

test("markdown inside a fenced block is not interpreted", () => {
  const html = renderMarkdown("```\n**not bold**\n```");
  const pre = html.slice(html.indexOf("<pre"));
  assert.ok(!pre.includes("<strong>"));
  assert.ok(pre.includes("**not bold**"));
});

test("code-block copy data is base64 that decodes to the source", () => {
  const src = "print('你好')";
  const html = renderMarkdown("```py\n" + src + "\n```");
  const m = html.match(/data-code="([A-Za-z0-9+/=]+)"/);
  assert.ok(m, "has data-code");
  const decoded = Buffer.from(m[1], "base64").toString("utf8");
  assert.equal(decoded, src);
});

test("inline code is escaped", () => {
  const html = renderMarkdown("use `<div>` now");
  assert.ok(html.includes("<code"));
  assert.ok(html.includes("&lt;div&gt;"));
});

test("headings render at the right level", () => {
  const html = renderMarkdown("# Title\n## Sub");
  assert.ok(html.includes("<h1"));
  assert.ok(html.includes("<h2"));
  assert.ok(html.includes(">Title<"));
});

test("bold, italic and strikethrough", () => {
  const html = renderMarkdown("**a** and _b_ and ~~c~~");
  assert.ok(html.includes("<strong>a</strong>"));
  assert.ok(html.includes("<em>b</em>"));
  assert.ok(html.includes("<del>c</del>"));
});

test("unordered and ordered lists", () => {
  const html = renderMarkdown("- one\n- two\n\n1. first\n2. second");
  assert.ok(html.includes("<ul"));
  assert.ok(html.includes("<ol"));
  assert.equal((html.match(/<li>/g) || []).length, 4);
});

test("blockquote", () => {
  const html = renderMarkdown("> quoted text");
  assert.ok(html.includes("<blockquote"));
  assert.ok(html.includes("quoted text"));
});

test("table renders header and rows", () => {
  const md = "| A | B |\n|---|---|\n| 1 | 2 |\n| 3 | 4 |";
  const html = renderMarkdown(md);
  assert.ok(html.includes("<table"));
  assert.ok(html.includes("<th>A</th>"));
  assert.ok(html.includes("<td>1</td>"));
  assert.equal((html.match(/<tr>/g) || []).length, 3);
});

test("plain paragraph preserves line breaks", () => {
  const html = renderMarkdown("line one\nline two");
  assert.ok(html.includes("line one<br>line two"));
});

test("escapeHtml handles quotes and ampersand", () => {
  assert.equal(escapeHtml(`<a href="x&y'>`),
    "&lt;a href=&quot;x&amp;y&#39;&gt;");
});
