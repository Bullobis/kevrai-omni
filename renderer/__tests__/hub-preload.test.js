// Regression: model-market auto-pick must reach the sidecar.
//
// planInstall sends {files: [], auto_pick:true} for the 165 catalog models
// without gguf_repo. The preload used to reject an empty files array
// unconditionally, so installs failed before IPC. This test loads the real
// preload with a stubbed electron module and exercises the bridge itself.
import { test } from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { planInstall, PLAN } from "../modules/plan-install.js";

const require = createRequire(import.meta.url);
const Module = require("node:module");

function loadPreload(invoke) {
  let exposed = null;
  const originalLoad = Module._load;
  Module._load = function (request, parent, isMain) {
    if (request === "electron") {
      return {
        contextBridge: {
          exposeInMainWorld: (name, api) => { exposed = api; },
        },
        ipcRenderer: {
          invoke,
          on: () => {},
          send: () => {},
          removeListener: () => {},
        },
        webUtils: { getPathForFile: () => "" },
      };
    }
    return originalLoad.call(this, request, parent, isMain);
  };
  try {
    const p = require.resolve("../../electron/preload.js");
    delete require.cache[p];
    require(p);
  } finally {
    Module._load = originalLoad;
  }
  if (!exposed) throw new Error("preload did not expose kevrai");
  return exposed;
}

test("preload allows empty files when auto_pick is enabled", async () => {
  let seen = null;
  const api = loadPreload(async (channel, payload) => {
    seen = { channel, payload };
    return { status: 200, body: { job_id: "j1", tasks: [] } };
  });
  const res = await api.hubDownload({
    hub: "hf", repo: "org/repo", files: [], auto_pick: true, revision: "main",
  });
  assert.equal(res.status, 200);
  assert.equal(seen.channel, "kevrai:hub-download");
  assert.deepEqual(seen.payload.files, []);
  assert.equal(seen.payload.auto_pick, true);
});

test("preload still rejects empty files when auto_pick is disabled", async () => {
  const api = loadPreload(async () => ({ status: 200 }));
  assert.throws(
    () => api.hubDownload({ hub: "hf", repo: "org/repo", files: [], auto_pick: false }),
    /files: at least one path required/,
  );
});

test("explicit shard list is preserved", async () => {
  let seen = null;
  const api = loadPreload(async (_channel, payload) => { seen = payload; return {}; });
  await api.hubDownload({
    hub: "hf", repo: "org/repo", files: ["a.gguf"], auto_pick: false,
  });
  assert.deepEqual(seen.files, ["a.gguf"]);
});

test("a real non-GGUF market plan passes the preload bridge", async () => {
  const plan = planInstall({
    id: "pp-ocrv6-det",
    repo: "PaddlePaddle/PP-OCRv6_medium_det_onnx",
  });
  assert.equal(plan.kind, PLAN.AUTO);
  assert.deepEqual(plan.args.files, []);
  let seen = null;
  const api = loadPreload(async (_channel, payload) => { seen = payload; return { status: 200 }; });
  await api.hubDownload(plan.args);
  assert.equal(seen.repo, "PaddlePaddle/PP-OCRv6_medium_det_onnx");
  assert.deepEqual(seen.files, []);
  assert.equal(seen.auto_pick, true);
});
