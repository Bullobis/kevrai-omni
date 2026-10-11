// renderer/__tests__/environments-mirrors.test.js — selectable model mirrors
// must only reference reachable hosts; ModelScope uses the no-www canonical host.
import test from "node:test";
import assert from "node:assert/strict";

import { KNOWN_MODEL_MIRRORS } from "../modules/environments.js";

const PHANTOM_HOSTS = ["hf-mirror.us", "hf-cn-mirror.com", "huggingface.dl.in.tel"];

test("no non-resolving phantom mirror is selectable", () => {
  for (const m of KNOWN_MODEL_MIRRORS) {
    for (const ph of PHANTOM_HOSTS) assert.ok(!m.url.includes(ph), `listed ${ph}`);
  }
});

test("known-good mirrors are present", () => {
  const urls = KNOWN_MODEL_MIRRORS.map((m) => m.url);
  assert.ok(urls.includes("https://huggingface.co"));
  assert.ok(urls.includes("https://hf-mirror.com"));
});

test("ModelScope uses the canonical no-www host", () => {
  const ms = KNOWN_MODEL_MIRRORS.find((m) => m.id === "modelscope");
  assert.ok(ms);
  assert.equal(ms.url, "https://modelscope.cn");
});

test("every mirror id is unique and has a label/url", () => {
  const ids = KNOWN_MODEL_MIRRORS.map((m) => m.id);
  assert.equal(new Set(ids).size, ids.length);
  for (const m of KNOWN_MODEL_MIRRORS) {
    assert.ok(m.label && /^https?:\/\//.test(m.url));
  }
});
