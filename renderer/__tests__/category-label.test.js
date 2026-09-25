// renderer/__tests__/category-label.test.js — models.categoryLabel 纯函数单测：
// 已知分类返回中文标签；未知分类回退为 id；空值回退为空串。
"use strict";
import { test } from "node:test";
import assert from "node:assert/strict";
import { setupDom } from "./helpers/dom.js";

// models.js 模块加载引用 document，先装 jsdom。
setupDom();
const models = await import("../modules/models.js");

test("已知分类 id 返回中文标签", () => {
  assert.equal(models.categoryLabel("llm"), "文本生成");
  assert.equal(models.categoryLabel("tts"), "语音合成");
  assert.equal(models.categoryLabel("video"), "视频生成");
  assert.equal(models.categoryLabel("image"), "图片生成");
  assert.equal(models.categoryLabel("superres"), "超分辨率");
  assert.equal(models.categoryLabel("3d"), "3D 生成");
  assert.equal(models.categoryLabel("vision"), "多模态");
  assert.equal(models.categoryLabel("other"), "其他");
});

test("未知分类 id 原样返回", () => {
  assert.equal(models.categoryLabel("custom-cat"), "custom-cat");
});

test("空 / null id 回退为空串，不抛错", () => {
  assert.equal(models.categoryLabel(""), "");
  assert.equal(models.categoryLabel(null), "");
  assert.equal(models.categoryLabel(undefined), "");
});
