// v4.18.2 post-release regression: the downloads overlay reads
// downloaded/total, while the sidecar emits downloaded_bytes/total_bytes for a
// single task and bytes_done/bytes_total for a hub job. The main process must
// normalise both, or the bar stays at 0% even while bytes are arriving.
import { test } from "node:test";
import assert from "node:assert/strict";
import mod from "../../electron/download-progress.js";

const { normalizeProgress } = mod;

test("normalises a single-task snapshot", () => {
  const p = normalizeProgress("t1", {
    status: "running", downloaded_bytes: 5242880, total_bytes: 10485760,
    dest_path: "/models/Qwen3-0.6B.gguf",
  });
  assert.equal(p.taskId, "t1");
  assert.equal(p.downloaded, 5242880);
  assert.equal(p.total, 10485760);
  assert.equal(p.filename, "/models/Qwen3-0.6B.gguf");
  assert.equal(p.status, "running");
});

test("normalises a hub-job task row", () => {
  const p = normalizeProgress("t2", {
    task_id: "t2", path: "model-00001-of-00004.safetensors",
    bytes_done: 123, bytes_total: 456, status: "done",
  });
  assert.equal(p.downloaded, 123);
  assert.equal(p.total, 456);
  assert.equal(p.filename, "model-00001-of-00004.safetensors");
});

test("keeps already-normalised values and defaults safely", () => {
  const p = normalizeProgress("t3", { downloaded: 7, total: 9 });
  assert.equal(p.downloaded, 7);
  assert.equal(p.total, 9);
  assert.equal(p.filename, "t3");
  assert.equal(p.status, "running");

  const bad = normalizeProgress("t4", { downloaded_bytes: "oops", total_bytes: -1 });
  assert.equal(bad.downloaded, 0);
  assert.equal(bad.total, 0);
});
