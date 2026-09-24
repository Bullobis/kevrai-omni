// renderer/__tests__/debounce.test.js — trailing debounce and cancel.
import { test } from "node:test";
import assert from "node:assert/strict";
import { debounce } from "../modules/debounce.js";

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

test("debounce fires once after the wait with latest args", async () => {
  let got = null;
  const d = debounce((v) => { got = v; }, 20);
  d(1); d(2); d(3);
  await sleep(35);
  assert.equal(got, 3);
});

test("debounce does not fire before the wait", async () => {
  let n = 0;
  const d = debounce(() => { n += 1; }, 30);
  d();
  await sleep(10);
  assert.equal(n, 0);
  await sleep(30);
});

test("cancel prevents the trailing call", async () => {
  let n = 0;
  const d = debounce(() => { n += 1; }, 20);
  d();
  d.cancel();
  await sleep(30);
  assert.equal(n, 0);
});
