// renderer/__tests__/scheduler.test.js — 统一周期调度器单元测试。
// 全部用虚拟时钟 + 注入定时器/内存存储，不真打网络、不开真实 setInterval。
"use strict";

import { test } from "node:test";
import assert from "node:assert/strict";
import { createScheduler, INTERVAL_PRESETS_MS, formatInterval } from "../modules/scheduler.js";

// ── 测试装配：可控时钟 + 记账式定时器 + 内存存储 ─────────────────────────────
function makeEnv() {
  let nowMs = 1_000_000;
  const now = () => nowMs;
  const advance = (ms) => { nowMs += ms; };

  const timers = new Map();
  let timerSeq = 0;
  const setTick = (cb, ms) => { const id = ++timerSeq; timers.set(id, { cb, ms }); return id; };
  const clearTick = (id) => { timers.delete(id); };

  // 内存 localStorage 替身
  const mem = new Map();
  const storage = {
    getItem: (k) => (mem.has(k) ? mem.get(k) : null),
    setItem: (k, v) => mem.set(k, String(v)),
    removeItem: (k) => mem.delete(k),
  };

  const sched = createScheduler({ now, setTick, clearTick, storage, tickMs: 1_000 });
  return { sched, now, advance, timers, setTick, storage, mem };
}

test("register: creates a task with defaults and computes nextRunAt = now + interval", () => {
  const { sched, now } = makeEnv();
  const t0 = now();
  const snap = sched.register({ id: "a", intervalMs: 5_000, run: () => {} });
  assert.equal(snap.intervalMs, 5_000);
  assert.equal(snap.enabled, true);
  assert.equal(snap.nextRunAt, t0 + 5_000, "nextRunAt scheduled one interval ahead");
  assert.equal(sched.getTasks().length, 1);
});

test("register: dedupes by id (re-register does not create a second task, updates run/label)", () => {
  const { sched, advance } = makeEnv();
  let calls1 = 0, calls2 = 0;
  sched.register({ id: "dup", intervalMs: 1_000, run: () => calls1++ });
  sched.register({ id: "dup", intervalMs: 1_000, run: () => calls2++ });
  assert.equal(sched.getTasks().length, 1, "same id registered only once");
  // 到期触发时跑的是后注册的 run（合并覆盖）。
  advance(5_000);
  sched.tick();
  assert.equal(calls1, 0);
  assert.equal(calls2, 1);
});

test("setEnabled: disabled tasks are not ticked; re-enabled reschedules from now", () => {
  const { sched, advance } = makeEnv();
  let runs = 0;
  sched.register({ id: "x", intervalMs: 1_000, run: () => runs++ });

  sched.setEnabled("x", false);
  advance(100_000); // far past the interval
  const fired = sched.tick();
  assert.equal(runs, 0, "disabled task must not run");
  assert.deepEqual(fired, []);

  sched.setEnabled("x", true);
  advance(500); // not yet due (rescheduled from the re-enable moment)
  assert.deepEqual(sched.tick(), []);
  advance(600); // now due
  assert.deepEqual(sched.tick(), ["x"]);
  assert.equal(runs, 1);
});

test("setIntervalMs: changes interval and reschedules next run", () => {
  const { sched, now, advance } = makeEnv();
  let runs = 0;
  sched.register({ id: "y", intervalMs: 1_000, run: () => runs++ });
  sched.setIntervalMs("y", 10_000);

  advance(5_000); // halfway to the new 10s interval
  assert.deepEqual(sched.tick(), [], "shorter elapsed time must not fire after lengthening interval");
  advance(6_000); // now 11s since reschedule -> due
  assert.deepEqual(sched.tick(), ["y"]);
  assert.equal(runs, 1);
});

test("tick: fires only due tasks, not future ones", () => {
  const { sched, advance } = makeEnv();
  const ran = [];
  sched.register({ id: "fast", intervalMs: 1_000, run: () => ran.push("fast") });
  sched.register({ id: "slow", intervalMs: 10_000, run: () => ran.push("slow") });

  advance(1_500); // fast due, slow not
  const fired = sched.tick();
  assert.deepEqual(fired, ["fast"]);
  assert.deepEqual(ran, ["fast"]);

  advance(500); // 仍在 fast 的下一个 1s 周期内，slow(10s) 也远未到
  assert.deepEqual(sched.tick(), [], "no task fires early");
});

test("tick: async run() is awaited and next run is rescheduled afterwards", async () => {
  const { sched, advance } = makeEnv();
  let done = 0;
  sched.register({
    id: "async",
    intervalMs: 1_000,
    run: async () => { await Promise.resolve(); done++; },
  });
  advance(2_000);
  sched.tick();
  // tick() 不 await run()；这里把 invoke -> await run 的微任务链冲刷干净。
  for (let i = 0; i < 5; i++) await Promise.resolve();
  assert.equal(done, 1);
  // after the run, nextRunAt moved forward — a tick at the same clock does not refire
  advance(500);
  assert.deepEqual(sched.tick(), [], "no immediate re-fire after reschedule");
});

test("heartbeat task is driven by the single main tick — no per-task setInterval", () => {
  const { sched, advance, timers } = makeEnv();
  let hb = 0;
  // 心跳任务自己不调用 setInterval；它只是登记一个 run。
  sched.register({ id: "clawdchatHeartbeat", intervalMs: 7_200_000, run: () => hb++ });
  assert.equal(timers.size, 0, "registering a task does NOT open a timer");

  sched.start();
  assert.equal(timers.size, 1, "start() opens exactly ONE main tick");
  advance(7_200_000 + 1);
  sched.tick();
  assert.equal(hb, 1, "heartbeat fires through the unified tick");
  sched.stop();
  assert.equal(timers.size, 0, "stop() clears the single main tick");
});

test("persistence: enable/interval overrides survive into a fresh scheduler on the same storage", () => {
  const env = makeEnv();
  env.sched.register({ id: "persist", intervalMs: 1_000, enabled: true, run: () => {} });
  env.sched.setEnabled("persist", false);
  env.sched.setIntervalMs("persist", 99_000);

  // 新调度器实例，共用同一份内存存储 —— 应回放覆盖项。
  const s2 = createScheduler({ now: env.now, setTick: env.setTick, clearTick: env.clearTick, storage: env.storage });
  s2.register({ id: "persist", intervalMs: 1_000, enabled: true, run: () => {} });
  const snap = s2.getTask("persist");
  assert.equal(snap.enabled, false, "disabled override restored");
  assert.equal(snap.intervalMs, 99_000, "interval override restored");
});

test("formatInterval: renders localized durations from presets", () => {
  const fakeT = (k) => k;
  assert.equal(formatInterval(15_000, fakeT), "15 scheduler.unitSecond");
  assert.equal(formatInterval(300_000, fakeT), "5 scheduler.unitMinute");
  assert.equal(formatInterval(7_200_000, fakeT), "2 scheduler.unitHour");
  assert.equal(formatInterval(86_400_000, fakeT), "1 scheduler.unitDay");
  // presets are sane positive numbers
  assert.ok(INTERVAL_PRESETS_MS.every((ms) => Number.isFinite(ms) && ms > 0));
});
