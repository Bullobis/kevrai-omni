// v4.18.2 regression: findLlamaServerBinary must unwrap the sidecarFetch
// {status, body} envelope. Before the fix it read st.engines directly, the
// engine list was always empty, and every GGUF chat failed with
// "llama.cpp 引擎未安装" even though the engine was installed.
import { test } from "node:test";
import assert from "node:assert/strict";
import mod from "../../electron/engine-records.js";

const { engineRecordsFromFetch } = mod;

const rec = { id: "llama.cpp", installed: true, install_path: "/x/engines/llama.cpp" };

test("unwraps {status, body:{engines}} envelope", () => {
  const items = engineRecordsFromFetch({ status: 200, body: { engines: [rec] } });
  assert.deepEqual(items, [rec]);
});

test("accepts bare {engines} object", () => {
  assert.deepEqual(engineRecordsFromFetch({ engines: [rec] }), [rec]);
});

test("accepts bare array", () => {
  assert.deepEqual(engineRecordsFromFetch([rec]), [rec]);
});

test("null / undefined / error shapes yield empty list, never throw", () => {
  for (const bad of [null, undefined, {}, { status: 500, body: null }, { body: "oops" }, 42]) {
    assert.deepEqual(engineRecordsFromFetch(bad), []);
  }
});
