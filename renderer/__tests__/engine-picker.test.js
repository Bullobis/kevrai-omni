// renderer/__tests__/engine-picker.test.js — 安装模型时的引擎选择。
//
// 真实问题：installItem() 里写死 engines[0]，多引擎模型无法选择。
import { test } from "node:test";
import assert from "node:assert/strict";

import { setupDom } from "./helpers/dom.js";

function withDom() {
  const { window } = setupDom("<div id='host'></div>");
  globalThis.window = window;
  globalThis.document = window.document;
  globalThis.localStorage = window.localStorage;
  return window;
}

async function load() {
  const window = withDom();
  const mod = await import("../modules/engine-picker.js");
  return { mod, window, doc: window.document };
}

// ---------------------------------------------------------------------------
// resolveEngine 纯逻辑
// ---------------------------------------------------------------------------

test("单引擎候选直接返回它", async () => {
  const { mod, window } = await load();
  assert.deepEqual(mod.resolveEngine(["llama.cpp"]), { engine: "llama.cpp", remembered: false });
  window.close();
});

test("无候选返回 null", async () => {
  const { mod, window } = await load();
  assert.equal(mod.resolveEngine([]), null);
  assert.equal(mod.resolveEngine(null), null);
  window.close();
});

test("按模型记忆优先于目录顺序", async () => {
  const { mod, window } = await load();
  const r = mod.resolveEngine(["llama.cpp", "mnn"], {
    modelId: "m1", remembered: { m1: "mnn" },
  });
  assert.equal(r.engine, "mnn");
  assert.equal(r.remembered, true);
  window.close();
});

test("全局默认在没有按模型记忆时生效", async () => {
  const { mod, window } = await load();
  const r = mod.resolveEngine(["llama.cpp", "mnn"], { modelId: "m1", globalDefault: "mnn" });
  assert.equal(r.engine, "mnn");
  window.close();
});

test("按模型记忆优先于全局默认", async () => {
  const { mod, window } = await load();
  const r = mod.resolveEngine(["llama.cpp", "mnn", "transformers"], {
    modelId: "m1", remembered: { m1: "transformers" }, globalDefault: "mnn",
  });
  assert.equal(r.engine, "transformers");
  window.close();
});

test("记忆值已经不在候选里就忽略（模型更新后引擎列表会变）", async () => {
  // 拿一个不存在的引擎去安装会静默失败，这里必须回落而不是照用
  const { mod, window } = await load();
  const r = mod.resolveEngine(["llama.cpp", "mnn"], {
    modelId: "m1", remembered: { m1: "removed-engine" }, globalDefault: "gone",
  });
  assert.equal(r.engine, "llama.cpp");
  assert.equal(r.remembered, false);
  window.close();
});

// ---------------------------------------------------------------------------
// 记忆读写
// ---------------------------------------------------------------------------

test("rememberEngine 写入并可被 readRemembered 读回", async () => {
  const { mod, window } = await load();
  localStorage.clear();
  mod.rememberEngine("m1", "mnn");
  mod.rememberEngine("m2", "llama.cpp");
  const all = mod.readRemembered();
  assert.equal(all.m1, "mnn");
  assert.equal(all.m2, "llama.cpp");
  window.close();
});

test("全局默认可设置与清除", async () => {
  const { mod, window } = await load();
  localStorage.clear();
  mod.setGlobalDefault("mnn");
  assert.equal(mod.readGlobalDefault(), "mnn");
  mod.setGlobalDefault("");
  assert.equal(mod.readGlobalDefault(), "");
  window.close();
});

test("localStorage 不可用时读写都不抛错", async () => {
  const { mod, window } = await load();
  const real = Object.getOwnPropertyDescriptor(window, "localStorage");
  Object.defineProperty(window, "localStorage", {
    configurable: true, get() { throw new Error("blocked"); },
  });
  globalThis.localStorage = undefined;
  assert.doesNotThrow(() => mod.rememberEngine("m1", "mnn"));
  assert.deepEqual(mod.readRemembered(), {});
  assert.equal(mod.readGlobalDefault(), "");
  if (real) Object.defineProperty(window, "localStorage", real);
  window.close();
});

// ---------------------------------------------------------------------------
// applyEngineChoice
// ---------------------------------------------------------------------------

test("global=true 写全局默认而不是单模型记忆", async () => {
  const { mod, window } = await load();
  localStorage.clear();
  mod.applyEngineChoice({ engine: "mnn", remember: true, global: true }, "m1");
  assert.equal(mod.readGlobalDefault(), "mnn");
  assert.equal(mod.readRemembered().m1, undefined);
  window.close();
});

test("仅 remember 时写单模型记忆", async () => {
  const { mod, window } = await load();
  localStorage.clear();
  mod.applyEngineChoice({ engine: "mnn", remember: true, global: false }, "m1");
  assert.equal(mod.readRemembered().m1, "mnn");
  assert.equal(mod.readGlobalDefault(), "");
  window.close();
});

test("什么都不勾就不写任何东西", async () => {
  const { mod, window } = await load();
  localStorage.clear();
  mod.applyEngineChoice({ engine: "mnn", remember: false, global: false }, "m1");
  assert.deepEqual(mod.readRemembered(), {});
  assert.equal(mod.readGlobalDefault(), "");
  window.close();
});

// ---------------------------------------------------------------------------
// 选择面板交互
// ---------------------------------------------------------------------------

test("单引擎时不弹面板，直接返回结果", async () => {
  const { mod, window } = await load();
  const r = await mod.pickEngine({ engines: ["llama.cpp"], t: (k) => k });
  assert.equal(r.engine, "llama.cpp");
  assert.equal(document.querySelector(".overlay"), null, "不该插入面板");
  window.close();
});

test("多引擎时弹面板，确认后返回所选引擎", async () => {
  const { mod, window, doc } = await load();
  localStorage.clear();
  const p = mod.pickEngine({ engines: ["llama.cpp", "mnn"], modelId: "m1", t: (k) => k });
  const overlay = doc.querySelector(".overlay");
  assert.ok(overlay, "应弹出选择面板");
  const boxes = overlay.querySelectorAll('input[type=radio]');
  assert.equal(boxes.length, 2, "每个候选引擎都要有一项");
  boxes[1].checked = true;
  overlay.querySelector("[data-act=ok]").dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  const r = await p;
  assert.equal(r.engine, "mnn");
  window.close();
});

test("取消返回 null（调用方必须据此中止安装）", async () => {
  const { mod, window, doc } = await load();
  const p = mod.pickEngine({ engines: ["llama.cpp", "mnn"], t: (k) => k });
  const overlay = doc.querySelector(".overlay");
  overlay.querySelector("[data-act=cancel]").dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  assert.equal(await p, null);
  window.close();
});

test("默认预选上次记住的引擎", async () => {
  const { mod, window, doc } = await load();
  localStorage.clear();
  mod.rememberEngine("m1", "mnn");
  const p = mod.pickEngine({ engines: ["llama.cpp", "mnn"], modelId: "m1", t: (k) => k });
  const overlay = doc.querySelector(".overlay");
  const checked = overlay.querySelector("input[type=radio]:checked");
  assert.equal(checked.value, "mnn");
  overlay.querySelector("[data-act=close]").dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  await p;
  window.close();
});

test("Esc 关闭视为取消", async () => {
  const { mod, window, doc } = await load();
  const p = mod.pickEngine({ engines: ["llama.cpp", "mnn"], t: (k) => k });
  doc.dispatchEvent(new window.KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
  assert.equal(await p, null);
  window.close();
});

// ---------------------------------------------------------------------------
// 样式
// ---------------------------------------------------------------------------

test("styles.css 有引擎选择面板样式", async () => {
  const fs = await import("node:fs");
  const path = await import("node:path");
  const url = await import("node:url");
  const here = path.dirname(url.fileURLToPath(import.meta.url));
  const css = fs.readFileSync(path.resolve(here, "..", "styles.css"), "utf8");
  assert.match(css, /\.engine-pick-row/, "缺少候选项样式");
  assert.match(css, /\.engine-pick-list/, "缺少候选列表样式");
});
