// renderer/__tests__/image.test.js — ComfyUI 图片面板的纯函数逻辑。
//
// DOM/IPC 走实机走查；这里只锁定最容易写错的两处：分辨率字符串解析、
// 出图前的输入校验（避免把空提示词 / 空模型名发给后端再吃 400）。
import { test } from "node:test";
import assert from "node:assert/strict";

import { setupDom } from "./helpers/dom.js";

async function load() {
  const { window } = setupDom("<div id='host'></div>");
  globalThis.window = window;
  globalThis.document = window.document;
  globalThis.localStorage = window.localStorage;
  const mod = await import("../modules/image.js");
  return { mod, window };
}

test("分辨率 '512x768' 解析为 [512,768]", async () => {
  const { mod, window } = await load();
  assert.deepEqual(mod.parseDimensions("512x768"), [512, 768]);
  assert.deepEqual(mod.parseDimensions("1024x1024"), [1024, 1024]);
  window.close();
});

test("非法分辨率回落到 [512,512]", async () => {
  const { mod, window } = await load();
  assert.deepEqual(mod.parseDimensions(""), [512, 512]);
  assert.deepEqual(mod.parseDimensions("abc"), [512, 512]);
  assert.deepEqual(mod.parseDimensions("512x"), [512, 512]);
  window.close();
});

test("空提示词 → prompt 错误", async () => {
  const { mod, window } = await load();
  assert.equal(mod.validateInputs("", "sd15.safetensors"), "prompt");
  assert.equal(mod.validateInputs("   ", "sd15.safetensors"), "prompt");
  window.close();
});

test("空模型名 → ckpt 错误", async () => {
  const { mod, window } = await load();
  assert.equal(mod.validateInputs("a cat", ""), "ckpt");
  assert.equal(mod.validateInputs("a cat", "   "), "ckpt");
  window.close();
});

test("提示词与模型都齐全 → 可用", async () => {
  const { mod, window } = await load();
  assert.equal(mod.validateInputs("a cat", "sd15.safetensors"), null);
  window.close();
});
