// renderer/__tests__/search-highlight.test.js — search.highlight 纯函数单测：
// 高亮区间包裹 <mark>、区间排序/去重叠、越界夹紧、HTML 转义防注入、字段过滤。
"use strict";
import { test } from "node:test";
import assert from "node:assert/strict";
import { setupDom } from "./helpers/dom.js";

// search.js 模块加载即引用 document，先装 jsdom。
setupDom();
const search = await import("../modules/search.js");

const H = (field, start, end) => ({ field, start, end });

test("无 highlights / 空：返回转义后的纯文本，无 mark", () => {
  assert.equal(search.highlight("hello", null), "hello");
  assert.equal(search.highlight("hello", []), "hello");
});

test("单个区间用 <mark> 包裹", () => {
  assert.equal(search.highlight("hello world", [H("name", 0, 5)]),
    "<mark>hello</mark> world");
});

test("多个区间分别包裹，中间文本保留", () => {
  assert.equal(search.highlight("abcdef", [H("name", 0, 2), H("name", 4, 6)]),
    "<mark>ab</mark>cd<mark>ef</mark>");
});

test("区间按 start 排序，乱序输入结果正确", () => {
  assert.equal(search.highlight("abcdef", [H("name", 4, 6), H("name", 0, 2)]),
    "<mark>ab</mark>cd<mark>ef</mark>");
});

test("被前一区间包含/重叠的区间被跳过，不产生嵌套 mark", () => {
  // [0,4] 已覆盖 [1,3]
  const r = search.highlight("abcdef", [H("name", 0, 4), H("name", 1, 3)]);
  assert.equal(r, "<mark>abcd</mark>ef");
  assert.ok(!r.includes("<mark><mark>"));
});

test("文本中的 HTML 特殊字符被转义，不发生标签注入", () => {
  const r = search.highlight("<b>x</b>", [H("name", 3, 4)]);
  assert.ok(!r.includes("<b>"));
  assert.ok(r.includes("&lt;b&gt;"));
  assert.ok(r.includes("<mark>x</mark>"));
});

test("end 超过文本长度时夹紧，不抛错", () => {
  const r = search.highlight("abc", [H("name", 0, 99)]);
  assert.equal(r, "<mark>abc</mark>");
});

test("非 name/description/id/tags 字段的高亮被忽略", () => {
  assert.equal(search.highlight("hello", [H("other_field", 0, 2)]), "hello");
});
