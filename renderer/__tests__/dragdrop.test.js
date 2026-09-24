// renderer/__tests__/dragdrop.test.js — drop overlay counter lifecycle.
import { test } from "node:test";
import assert from "node:assert/strict";
import { setupDom } from "./helpers/dom.js";

test("dragenter shows the overlay and dragleave clears it", async () => {
  const env = setupDom();
  const { wireDragDrop } = await import("../modules/dragdrop.js");
  wireDragDrop();
  env.window.dispatchEvent(new env.window.Event("dragenter", { cancelable: true }));
  assert.ok(env.document.body.classList.contains("dragging"));
  env.window.dispatchEvent(new env.window.Event("dragleave", { cancelable: true }));
  assert.ok(!env.document.body.classList.contains("dragging"));
});

test("drop always clears the overlay, even after repeated enters", async () => {
  const env = setupDom();
  const { wireDragDrop } = await import("../modules/dragdrop.js");
  wireDragDrop();
  for (let i = 0; i < 5; i += 1) {
    env.window.dispatchEvent(new env.window.Event("dragenter", { cancelable: true }));
  }
  assert.ok(env.document.body.classList.contains("dragging"));
  // A drop with no files still must reset the counter and overlay.
  env.window.dispatchEvent(new env.window.Event("drop", { cancelable: true }));
  assert.ok(!env.document.body.classList.contains("dragging"));
});

test("a single leave after one enter does not leave the overlay stuck", async () => {
  const env = setupDom();
  const { wireDragDrop } = await import("../modules/dragdrop.js");
  wireDragDrop();
  env.window.dispatchEvent(new env.window.Event("dragenter", { cancelable: true }));
  // Simulate several dragover events: these must not move the counter.
  for (let i = 0; i < 8; i += 1) {
    env.window.dispatchEvent(new env.window.Event("dragover", { cancelable: true }));
  }
  env.window.dispatchEvent(new env.window.Event("dragleave", { cancelable: true }));
  assert.ok(!env.document.body.classList.contains("dragging"));
});
