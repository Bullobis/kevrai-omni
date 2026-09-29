// renderer/__tests__/markdown.test.js — markdown.js 渲染与 XSS 安全单测。
import { test } from "node:test";
import assert from "node:assert/strict";
import { renderMarkdown } from "../modules/markdown.js";

test("段落与换行", () => {
  const h = renderMarkdown("你好\n世界");
  assert.match(h, /<p class="md-p">你好\n世界<\/p>/);
});

test("标题 h1-h3", () => {
  assert.match(renderMarkdown("# 标题"), /<h1 class="md-h md-h1">标题<\/h1>/);
  assert.match(renderMarkdown("## 二级"), /<h2 class="md-h md-h2">二级<\/h2>/);
  assert.match(renderMarkdown("### 三级"), /<h3 class="md-h md-h3">三级<\/h3>/);
});

test("粗体/斜体/删除线", () => {
  assert.match(renderMarkdown("**粗**"), /<strong>粗<\/strong>/);
  assert.match(renderMarkdown("__粗__"), /<strong>粗<\/strong>/);
  assert.match(renderMarkdown("*斜*"), /<em>斜<\/em>/);
  assert.match(renderMarkdown("~~删~~"), /<del>删<\/del>/);
});

test("无序列表", () => {
  const h = renderMarkdown("- a\n- b\n- c");
  assert.match(h, /<ul class="md-ul"><li>a<\/li><li>b<\/li><li>c<\/li><\/ul>/);
});

test("有序列表", () => {
  const h = renderMarkdown("1. 一\n2. 二");
  assert.match(h, /<ol class="md-ol"><li>一<\/li><li>二<\/li><\/ol>/);
});

test("引用块", () => {
  assert.match(renderMarkdown("> 引用内容"), /<blockquote class="md-quote">引用内容<\/blockquote>/);
});

test("水平线", () => {
  assert.match(renderMarkdown("---"), /<hr class="md-hr">/);
});

test("安全链接 http/https，强制 noopener 与新窗口", () => {
  const h = renderMarkdown("[官网](https://example.com)");
  assert.match(
    h,
    /<a class="md-link" href="https:\/\/example\.com" target="_blank" rel="noopener noreferrer">官网<\/a>/
  );
});

test("围栏代码块：语言类名 + 内容转义 + 不解析内部标记", () => {
  const h = renderMarkdown("```python\nprint('a')\n**不粗**\n```");
  assert.match(h, /<pre class="md-pre"><code class="language-python">/);
  assert.match(h, /print\(&#39;a&#39;\)/);
  // 代码里的 ** 不应变成 <strong>
  assert.ok(!h.includes("<strong>"), "代码内 markdown 被错误解析");
});

test("行内代码转义且不解析", () => {
  const h = renderMarkdown("调用 `x = a < b` 即可");
  assert.match(h, /<code class="md-code">x = a &lt; b<\/code>/);
});

test("表格：表头/对齐/单元格", () => {
  const h = renderMarkdown("| 名称 | 值 |\n| --- | --: |\n| a | 1 |");
  assert.match(h, /<table class="md-table">/);
  assert.match(h, /<th>名称<\/th>/);
  assert.match(h, /<th style="text-align:right">值<\/th>/);
  assert.match(h, /<td>a<\/td>/);
  assert.match(h, /<td style="text-align:right">1<\/td>/);
});

// ---- XSS 安全（核心）----
test("XSS: 原始 <script> 被转义，无可执行脚本", () => {
  const h = renderMarkdown("<script>alert(1)</script>");
  assert.ok(!/<script/i.test(h), "输出包含 <script>");
  assert.match(h, /&lt;script&gt;alert\(1\)&lt;\/script&gt;/);
});

test("XSS: 事件属性 img onerror 整体被转义为文本（无真实标签/属性）", () => {
  const h = renderMarkdown('<img src=x onerror="alert(1)">');
  assert.ok(!/<img/i.test(h), "生成了真实 <img> 标签");
  assert.match(h, /&lt;img src=x onerror=&quot;/); // 整段是转义文本，属性不再可执行
});

test("XSS: javascript: 链接被拒绝（退化为纯文本，无 javascript href）", () => {
  const h = renderMarkdown("[点击](javascript:alert(1))");
  assert.ok(!/href="javascript:/i.test(h), "出现 javascript: href");
  assert.ok(!/<a /i.test(h), "非法 URL 不应生成链接");
  assert.match(h, /点击/);
});

test("XSS: 代码块内 <script> 不逃逸", () => {
  const h = renderMarkdown("```\n<script>alert(1)</script>\n```");
  assert.ok(!/<script/i.test(h));
  assert.match(h, /&lt;script&gt;/);
});

test("XSS: data: URL 链接被拒绝", () => {
  const h = renderMarkdown("[x](data:text/html,foo)");
  assert.ok(!/href="data:/i.test(h));
});
