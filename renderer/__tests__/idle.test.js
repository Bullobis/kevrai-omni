// renderer/__tests__/idle.test.js — whenIdle: rIC path, setTimeout fallback,
// async delivery, and cancel.
import { test } from "node:test";
import assert from "node:assert/strict";
import { whenIdle } from "../modules/idle.js";

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

test("whenIdle does not call the callback synchronously", () => {
  let n = 0;
  whenIdle(() => { n += 1; });
  assert.equal(n, 0, "callback must be deferred, not run on the current stack");
});

test("whenIdle delivers via the setTimeout fallback in Node (no rIC)", async () => {
  let n = 0;
  whenIdle(() => { n += 1; });
  await sleep(5);
  assert.equal(n, 1);
});

test("whenIdle swallows callback errors instead of breaking the loop", async () => {
  let after = false;
  whenIdle(() => { throw new Error("boom"); });
  whenIdle(() => { after = true; });
  await sleep(5);
  assert.equal(after, true, "a throwing callback must not prevent later idle work");
});

test("cancel prevents the deferred call", async () => {
  let n = 0;
  const cancel = whenIdle(() => { n += 1; });
  cancel();
  await sleep(10);
  assert.equal(n, 0);
});

test("whenIdle uses window.requestIdleCallback when available", async () => {
  // Arrange a fake window exposing rIC that records the callback and honors timeout.
  const scheduled = [];
  const fakeWindow = {
    requestIdleCallback: (cb, opts) => { scheduled.push({ cb, opts }); return scheduled.length; },
    cancelIdleCallback: (id) => { scheduled[id - 1].cancelled = true; },
  };
  // Inject as a global `window` — idle.js prefers window.requestIdleCallback.
  const prev = globalThis.window;
  globalThis.window = fakeWindow;
  try {
    let ran = false;
    whenIdle(() => { ran = true; }, { timeout: 1234 });
    assert.equal(scheduled.length, 1, "should have scheduled one rIC");
    assert.equal(scheduled[0].opts.timeout, 1234);
    assert.equal(ran, false, "rIC callback is not invoked by us; the host runs it");
    // Simulate the host invoking it.
    scheduled[0].cb();
    assert.equal(ran, true);
  } finally {
    globalThis.window = prev;
  }
});

test("whenIdle returns a no-op cancel for a non-function callback", async () => {
  const cancel = whenIdle(undefined);
  assert.equal(typeof cancel, "function");
  cancel(); // must not throw
  await sleep(5);
});
