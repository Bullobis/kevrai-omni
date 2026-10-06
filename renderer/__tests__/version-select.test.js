// renderer/__tests__/version-select.test.js — 版本/量化选择器的纯函数逻辑。
// 只测不依赖网络的部分：文件树解析、量化识别、大小格式化、排序、回退判定。
// 面板 DOM / fetchVersions 走真实 bridge，不在此覆盖。
import { test, beforeEach } from "node:test";
import assert from "node:assert/strict";
import { setupDom } from "./helpers/dom.js";

let vs;

beforeEach(async () => {
  setupDom("<div></div>");
  vs = await import("../modules/version-select.js");
});

// ── parseVersions ──────────────────────────────────────────────────────────

test("parseVersions 从后端文件列表提取 .gguf，file 取下载用 path", () => {
  const files = [
    { path: "models/llama-3-8b-q4_k_m.gguf", name: "llama-3-8b-q4_k_m.gguf", size: 4_600_000_000 },
    { path: "models/llama-3-8b-q8_0.gguf", name: "llama-3-8b-q8_0.gguf", size: 8_500_000_000 },
  ];
  const out = vs.parseVersions(files);
  assert.equal(out.length, 2);
  assert.equal(out[0].file, "models/llama-3-8b-q4_k_m.gguf");
  assert.equal(out[0].sizeBytes, 4_600_000_000);
});

test("parseVersions 过滤非 .gguf 文件（config/tokenizer/readme）", () => {
  const files = [
    { path: "config.json", size: 300 },
    { path: "tokenizer.model", size: 2_000_000 },
    { path: "README.md", size: 1000 },
    { path: "model-Q4_K_M.gguf", size: 4_600_000_000 },
    { path: "model.safetensors", size: 9_000_000_000 },
  ];
  const out = vs.parseVersions(files);
  assert.equal(out.length, 1);
  assert.ok(out[0].file.endsWith(".gguf"));
});

test("parseVersions 空/null 输入返回空数组，不去炸", () => {
  assert.deepEqual(vs.parseVersions(null), []);
  assert.deepEqual(vs.parseVersions(undefined), []);
  assert.deepEqual(vs.parseVersions([]), []);
  assert.deepEqual(vs.parseVersions([null, 42, "x"]), []);
});

test("parseVersions 对同名 path 去重，大小写后缀都接受", () => {
  const files = [
    { path: "a/Q4_K_M.GGUF", size: 1 },
    { path: "a/Q4_K_M.GGUF", size: 1 },
    { path: "a/q8_0.gguf", size: 2 },
  ];
  const out = vs.parseVersions(files);
  assert.equal(out.length, 2);
});

// ── extractQuant ────────────────────────────────────────────────────────────

test("extractQuant 识别常见 llama.cpp 量化标签", () => {
  assert.equal(vs.extractQuant("Meta-Llama-3-8B-Instruct-Q4_K_M.gguf"), "Q4_K_M");
  assert.equal(vs.extractQuant("foo-Q5_K_M.gguf"), "Q5_K_M");
  assert.equal(vs.extractQuant("foo-Q6_K.gguf"), "Q6_K");
  assert.equal(vs.extractQuant("foo-Q8_0.gguf"), "Q8_0");
  assert.equal(vs.extractQuant("foo-Q2_K.gguf"), "Q2_K");
  assert.equal(vs.extractQuant("foo-Q3_K_M.gguf"), "Q3_K_M");
  assert.equal(vs.extractQuant("foo-f16.gguf"), "F16");
});

test("extractQuant 无量化标签时返回空串", () => {
  assert.equal(vs.extractQuant("model.gguf"), "");
  assert.equal(vs.extractQuant(""), "");
  assert.equal(vs.extractQuant(undefined), "");
});

// ── formatSize ─────────────────────────────────────────────────────────────

test("formatSize 按 1024 进制格式化 GB/MB，非法输入返回空串", () => {
  assert.match(vs.formatSize(4_600_000_000), /GB$/);
  assert.match(vs.formatSize(512_000_000), /MB$/);
  assert.equal(vs.formatSize(0), "");
  assert.equal(vs.formatSize(-10), "");
  assert.equal(vs.formatSize("nonsense"), "");
});

// ── sortVersions / recommended ─────────────────────────────────────────────

test("sortVersions 把推荐档 Q4_K_M 排到最前", () => {
  const list = vs.parseVersions([
    { path: "a/Q8_0.gguf", size: 8 },
    { path: "a/Q2_K.gguf", size: 2 },
    { path: "a/Q4_K_M.gguf", size: 4 },
  ]);
  const out = vs.sortVersions(list);
  assert.equal(out[0].quant, "Q4_K_M");
  assert.equal(vs.isRecommended("Q4_K_M"), true);
  assert.equal(vs.isRecommended("q4_k_m"), true);
  assert.equal(vs.isRecommended("Q8_0"), false);
});

test("sortVersions 其余档位按体积/质量序，未知量化排最后，不改原数组", () => {
  const list = vs.parseVersions([
    { path: "a/weird-unknown.gguf", size: 3 },
    { path: "a/Q2_K.gguf", size: 2 },
    { path: "a/Q6_K.gguf", size: 6 },
  ]);
  const out = vs.sortVersions(list);
  assert.equal(out[0].quant, "Q2_K");
  assert.equal(out[1].quant, "Q6_K");
  assert.equal(out[2].quant, "");          // 未知量化收尾
  assert.notEqual(out, list, "返回新数组");
  assert.equal(list.length, 3);           // 原数组未被破坏
});

// ── supportsVersionSelect ─────────────────────────────────────────────────

test("supportsVersionSelect：远程 hub+repo 才支持，其余回退原行为", () => {
  assert.equal(vs.supportsVersionSelect({ hub: "hf", repo: "org/model" }), true);
  assert.equal(vs.supportsVersionSelect({ hub: "modelscope", repo: "org/model" }), true);
  assert.equal(vs.supportsVersionSelect({ hub: "curated", repo: "org/model" }), false);
  assert.equal(vs.supportsVersionSelect({ hub: "hf" }), false);
  assert.equal(vs.supportsVersionSelect({ hub: "hf", repo: "not-a-repo" }), false);
  assert.equal(vs.supportsVersionSelect(null), false);
});

// ── resolveFileTreeSource（curated 目录模型走 gguf_repo）────────────────────

test("resolveFileTreeSource：直接 hf/modelscope 模型用其 hub+repo", () => {
  assert.deepEqual(vs.resolveFileTreeSource({ hub: "hf", repo: "org/model" }),
    { hub: "hf", repo: "org/model" });
  assert.deepEqual(vs.resolveFileTreeSource({ hub: "modelscope", repo: "org/model" }),
    { hub: "modelscope", repo: "org/model" });
});

test("resolveFileTreeSource：curated 模型带 gguf_repo → hf + gguf_repo", () => {
  const item = { hub: "curated", repo: "Qwen/Qwen3-32B", gguf_repo: "unsloth/Qwen3-32B-GGUF" };
  assert.deepEqual(vs.resolveFileTreeSource(item),
    { hub: "hf", repo: "unsloth/Qwen3-32B-GGUF" });
});

test("resolveFileTreeSource：curated 无 gguf_repo、MNN、空值 → null", () => {
  assert.equal(vs.resolveFileTreeSource({ hub: "curated", repo: "org/model" }), null);
  assert.equal(vs.resolveFileTreeSource({ hub: "mnn", repo: "org/model" }), null);
  assert.equal(vs.resolveFileTreeSource(null), null);
  assert.equal(vs.resolveFileTreeSource({}), null);
});

test("supportsVersionSelect：curated 带 gguf_repo 也支持选择", () => {
  assert.equal(vs.supportsVersionSelect({
    hub: "curated", repo: "Qwen/Qwen3-32B", gguf_repo: "unsloth/Qwen3-32B-GGUF",
  }), true);
});
