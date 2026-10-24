// renderer/__tests__/engine-route.test.js — 引擎路由显示层。
import { test } from "node:test";
import assert from "node:assert/strict";

import { setupDom } from "./helpers/dom.js";

function withDom() {
  const { window } = setupDom("<div id='host'></div>");
  globalThis.window = window;
  globalThis.document = window.document;
  return window;
}

async function load() {
  const window = withDom();
  const mod = await import("../modules/engine-route.js");
  return { mod, window, doc: window.document };
}

const T = (k) => k;

// ---------------------------------------------------------------------------
// describeRuntime
// ---------------------------------------------------------------------------

test("mnn 运行时给出端点说明", async () => {
  const { mod, window } = await load();
  const s = mod.describeRuntime("mnn", "/api/mnn/load", T);
  assert.equal(s, "engineRoute.runtimeMnn");
  window.close();
});

test("无端点的引擎如实说明，不假装能跑", async () => {
  const { mod, window } = await load();
  assert.equal(mod.describeRuntime("other", null, T), "engineRoute.noEndpoint");
  window.close();
});

test("未知 runtime 说未知，不编造", async () => {
  const { mod, window } = await load();
  assert.equal(mod.describeRuntime("???", null, T), "engineRoute.unknown");
  window.close();
});

// ---------------------------------------------------------------------------
// describeSource
// ---------------------------------------------------------------------------

test("来源都能翻成人话", async () => {
  const { mod, window } = await load();
  assert.equal(mod.describeSource("explicit", T), "engineRoute.srcExplicit");
  assert.equal(mod.describeSource("default", T), "engineRoute.srcDefault");
  assert.equal(mod.describeSource("catalog", T), "engineRoute.srcCatalog");
  assert.equal(mod.describeSource("none", T), "engineRoute.srcNone");
  window.close();
});

// ---------------------------------------------------------------------------
// fetchEngineRoute：失败必须返回 ok:false，绝不猜
// ---------------------------------------------------------------------------

// 注意：这里 mock 的是真实调用签名 api.engineRoute({engine, model_id})。
// 早期版本写成 api.get(url)——而 api.js 从来没有 get 方法，测试之所以"通过"
// 只是因为 mock 恰好提供了同名函数，掩盖了线上必崩的事实。
test("网络失败返回 ok:false 而不是猜一个引擎", async () => {
  const { mod, window } = await load();
  const api = { engineRoute: async () => { throw new Error("boom"); } };
  const r = await mod.fetchEngineRoute(api, ["mnn"]);
  assert.equal(r.ok, false);
  assert.match(String(r.error), /boom/);
  window.close();
});

test("正常响应返回后端结果", async () => {
  const { mod, window } = await load();
  const api = {
    engineRoute: async () => ({ body: { engine: "mnn", runtime: "mnn", source: "default" } }),
  };
  const r = await mod.fetchEngineRoute(api, ["mnn", "llama.cpp"]);
  assert.equal(r.ok, true);
  assert.equal(r.data.engine, "mnn");
  window.close();
});

test("空候选也照常请求（由后端决定）", async () => {
  const { mod, window } = await load();
  let seen = null;
  const api = {
    engineRoute: async (p) => { seen = p; return { body: { source: "none" } }; },
  };
  const r = await mod.fetchEngineRoute(api, []);
  assert.equal(r.ok, true);
  assert.ok(seen && Array.isArray(seen.engine), "必须带上 engine 数组");
  assert.equal(seen.engine.length, 0);
  window.close();
});

test("非字符串候选被过滤掉", async () => {
  const { mod, window } = await load();
  let seen = null;
  const api = { engineRoute: async (p) => { seen = p; return { body: {} }; } };
  await mod.fetchEngineRoute(api, ["mnn", null, 42, "  "]);
  assert.deepEqual(seen.engine, ["mnn"], "只留非空字符串候选");
  window.close();
});

// ---------------------------------------------------------------------------
// renderRouteRow
// ---------------------------------------------------------------------------

test("失败时显示未知，不显示猜出来的引擎", async () => {
  const { mod, window } = await load();
  const html = mod.renderRouteRow({ ok: false }, T);
  assert.match(html, /engineRoute\.unknown/);
  assert.ok(!html.includes("llama.cpp"), "不能凭空出现引擎名");
  window.close();
});

test("有端点用 ok 样式，无端点用 warn 样式", async () => {
  const { mod, window } = await load();
  const ok = mod.renderRouteRow({ ok: true, data: { engine: "mnn", runtime: "mnn", source: "default" } }, T);
  const warn = mod.renderRouteRow({ ok: true, data: { engine: "diffusers", runtime: "other", source: "catalog", note: "n" } }, T);
  assert.match(ok, /class="engine-route-row ok"/);
  assert.match(warn, /class="engine-route-row warn"/);
  assert.match(warn, /diffusers/, "应显示是哪个引擎没有端点");
  window.close();
});

test("引擎名经过转义", async () => {
  const { mod, window } = await load();
  const html = mod.renderRouteRow(
    { ok: true, data: { engine: "<img src=x>", runtime: "other", source: "catalog" } }, T);
  assert.ok(!html.includes("<img"), "必须转义，防注入");
  window.close();
});

// ---------------------------------------------------------------------------
// 样式
// ---------------------------------------------------------------------------

test("styles.css 有引擎路由行样式", async () => {
  const fs = await import("node:fs");
  const path = await import("node:path");
  const url = await import("node:url");
  const here = path.dirname(url.fileURLToPath(import.meta.url));
  const css = fs.readFileSync(path.resolve(here, "..", "styles.css"), "utf8");
  assert.match(css, /\.engine-route-row/);
});
