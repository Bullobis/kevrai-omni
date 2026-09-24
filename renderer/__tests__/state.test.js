// renderer/__tests__/state.test.js — shared store merge and subscriptions.
import { test } from "node:test";
import assert from "node:assert/strict";
import { state, setState, subscribe } from "../modules/state.js";

test("setState merges a patch into state", () => {
  setState({ selectedId: "abc" });
  assert.equal(state.selectedId, "abc");
});

test("setState notifies subscribers with the state", () => {
  let seen = null;
  const off = subscribe((s) => { seen = s; });
  setState({ selectedId: "xyz" });
  assert.equal(seen.selectedId, "xyz");
  off();
});

test("unsubscribed handlers no longer fire", () => {
  let n = 0;
  const off = subscribe(() => { n += 1; });
  off();
  setState({ selectedId: "off" });
  assert.equal(n, 0);
});

test("a throwing subscriber does not break others", () => {
  let ok = false;
  subscribe(() => { throw new Error("boom"); });
  subscribe(() => { ok = true; });
  setState({ selectedId: "resilient" });
  assert.equal(ok, true);
});
