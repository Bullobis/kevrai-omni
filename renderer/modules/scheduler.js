// renderer/modules/scheduler.js — 统一周期性任务调度。
//
// 设计目标：
//   - 把原先散落在各处的 setInterval（sidecar 健康检查、虾聊心跳……）收进一个
//     「注册中心 + 单一主 tick」。主 tick 按各任务的 nextRunAt 到期触发，任务本身
//     不再各自 setInterval，避免定时器数量随任务线性增长。
//   - 核心为纯函数 + 可注入时钟/定时器，便于 Node 单元测试（虚拟时钟，不真打网络）。
//   - 任务开关与周期持久化到 localStorage；启动时回放用户覆盖项。
//   - 凭证类任务（如虾聊心跳）的 run() 由调用方注入；本模块不持有任何密钥，
//     心跳实际经 sidecar（~/.clawdchat/credentials.json，权限 600）执行。
//
// 不纳入本调度的：LTX / MNN 等「视图绑定」的高频轮询——它们随视图显隐启停
// （R4 view-visibility），与这里用户可感知的长周期计划任务性质不同，保持原样。
"use strict";

const STORAGE_KEY = "kevrai-scheduler-v1";
//: 主 tick 粒度。1s 足以分辨最短任务周期（sidecar 15s），又不至于空转太频繁。
export const DEFAULT_TICK_MS = 1_000;

const hasLocalStorage = (() => {
  try { return typeof localStorage !== "undefined"; } catch (_) { return false; }
})();

// ── 持久化 ──────────────────────────────────────────────────────────────────
// 覆盖项形状：{ [taskId]: { enabled?: boolean, intervalMs?: number } }。
// 未被用户改过的任务不落盘，使用其注册时的默认值。
function readOverrides(store) {
  try {
    if (!store) return {};
    const raw = store.getItem(STORAGE_KEY);
    if (!raw) return {};
    const obj = JSON.parse(raw);
    return obj && typeof obj === "object" ? obj : {};
  } catch (_) { return {}; }
}

function writeOverrides(store, overrides) {
  try {
    if (!store) return;
    store.setItem(STORAGE_KEY, JSON.stringify(overrides));
  } catch (_) { /* quota / private mode — 调度仍可运行，仅不持久化 */ }
}

function finiteNumber(v, fallback) {
  return Number.isFinite(v) && v > 0 ? v : fallback;
}

/**
 * 构造一个调度器实例。
 * @param {object}   opts
 * @param {()=>number} opts.now        当前时间戳（ms）。测试可注入假时钟。
 * @param {Function} opts.setTick      启动主 tick 的定时器（默认 globalThis.setInterval）。
 * @param {Function} opts.clearTick    停止主 tick（默认 globalThis.clearInterval）。
 * @param {Storage}  opts.storage     持久化介质（默认 localStorage；测试可注入内存对象）。
 * @param {number}   opts.tickMs       主 tick 粒度（默认 DEFAULT_TICK_MS）。
 */
export function createScheduler({
  now = () => Date.now(),
  setTick = (typeof globalThis !== "undefined" && globalThis.setInterval) || undefined,
  clearTick = (typeof globalThis !== "undefined" && globalThis.clearInterval) || undefined,
  storage = null,
  tickMs = DEFAULT_TICK_MS,
} = {}) {
  /** @type {Map<string, object>} */
  const tasks = new Map();
  const store = storage !== null ? storage : (hasLocalStorage ? localStorage : null);
  let timerId = null;
  let overrides = readOverrides(store);

  function persist(id, patch) {
    const cur = overrides[id] || {};
    overrides[id] = { ...cur, ...patch };
    writeOverrides(store, overrides);
  }

  // 计算某任务的下一次运行时刻：从「现在」往后推一个周期。
  // 已到期任务（例如刚改短周期）会在下一个 tick 立即触发，而不是再等满一个周期。
  function reschedule(task, fromNow) {
    task.nextRunAt = task.enabled ? fromNow + task.intervalMs : Infinity;
  }

  /**
   * 注册一个任务。同 id 重复注册为幂等合并：保留已持久化的开关/周期，
   * 仅更新 label 与 run。返回任务记录（快照字段）。
   * @param {{id:string, label?:string, intervalMs:number, enabled?:boolean, run:()=>any}} def
   */
  function register(def) {
    if (!def || typeof def.id !== "string" || !def.id) {
      throw new Error("scheduler.register: task needs a non-empty string id");
    }
    const ov = overrides[def.id] || {};
    const existing = tasks.get(def.id);
    if (existing) {
      // 去重：不重复建任务。仅刷新可变说明与执行体，沿用已生效的周期/开关。
      if (def.label != null) existing.label = def.label;
      if (typeof def.run === "function") existing.run = def.run;
      return snapshot(existing);
    }
    const task = {
      id: def.id,
      label: def.label || def.id,
      intervalMs: finiteNumber(ov.intervalMs, finiteNumber(def.intervalMs, DEFAULT_TICK_MS)),
      enabled: typeof ov.enabled === "boolean" ? ov.enabled : (def.enabled !== false),
      run: typeof def.run === "function" ? def.run : () => {},
      lastRunAt: 0,
      lastOk: null,
    };
    tasks.set(def.id, task);
    reschedule(task, now());
    return snapshot(task);
  }

  function unregister(id) {
    tasks.delete(id);
  }

  /** 启停某任务。停用后不再触发；重新启用时从现在起算下一个周期。 */
  function setEnabled(id, enabled) {
    const task = tasks.get(id);
    if (!task) return false;
    task.enabled = !!enabled;
    persist(id, { enabled: task.enabled });
    reschedule(task, now());
    return true;
  }

  /** 修改某任务周期（ms），立即重排下次运行。 */
  function setIntervalMs(id, ms) {
    const task = tasks.get(id);
    if (!task) return false;
    task.intervalMs = finiteNumber(ms, task.intervalMs);
    persist(id, { intervalMs: task.intervalMs });
    reschedule(task, now());
    return true;
  }

  /** 立即跑一次某任务（不等到期），跑完按周期重排。返回 run() 的结果。 */
  function runNow(id) {
    const task = tasks.get(id);
    if (!task) return undefined;
    return invoke(task);
  }

  async function invoke(task) {
    task.lastRunAt = now();
    try {
      const r = task.run();
      if (r && typeof r.then === "function") {
        await r;
        task.lastOk = true;
      } else {
        task.lastOk = true;
      }
    } catch (_) {
      task.lastOk = false;
    } finally {
      reschedule(task, now());
    }
  }

  /** 主 tick：触发所有已到期且启用的任务。可传入 nowTs 便于测试。 */
  function tick(nowTs) {
    const t = Number.isFinite(nowTs) ? nowTs : now();
    const due = [];
    for (const task of tasks.values()) {
      if (task.enabled && task.nextRunAt <= t) due.push(task);
    }
    // 逐个触发：异步 run 不阻塞后续到期任务的调度计算。
    for (const task of due) invoke(task);
    return due.map((task) => task.id);
  }

  function start() {
    if (timerId != null) return timerId;
    if (typeof setTick !== "function") return null;
    timerId = setTick(() => { try { tick(); } catch (_) {} }, tickMs);
    return timerId;
  }

  function stop() {
    if (timerId != null && typeof clearTick === "function") clearTick(timerId);
    timerId = null;
  }

  /** 供 UI 渲染的任务快照（按 id 排序）。 */
  function getTasks() {
    return [...tasks.values()].sort((a, b) => a.id.localeCompare(b.id)).map(snapshot);
  }

  function getTask(id) {
    const task = tasks.get(id);
    return task ? snapshot(task) : null;
  }

  return {
    register, unregister, setEnabled, setIntervalMs, runNow,
    tick, start, stop, getTasks, getTask,
    // 仅供测试/诊断
    _tasks: tasks,
  };
}

function snapshot(task) {
  return {
    id: task.id,
    label: task.label,
    intervalMs: task.intervalMs,
    enabled: task.enabled,
    nextRunAt: task.nextRunAt,
    lastRunAt: task.lastRunAt,
    lastOk: task.lastOk,
  };
}

// ── 模块级单例（renderer 运行时使用）─────────────────────────────────────────
// 测试应直接用 createScheduler() 造隔离实例；这里的单例只挂真实定时器/存储。
let singleton = null;
export function scheduler() {
  if (!singleton) singleton = createScheduler();
  return singleton;
}

// 常用周期预设（ms），供设置中心下拉使用。
export const INTERVAL_PRESETS_MS = [
  15_000,        // 15 秒（sidecar 健康检查）
  30_000,        // 30 秒
  60_000,        // 1 分钟
  300_000,       // 5 分钟
  900_000,       // 15 分钟
  1_800_000,     // 30 分钟
  3_600_000,     // 1 小时
  7_200_000,     // 2 小时（虾聊心跳默认）
  21_600_000,    // 6 小时
  86_400_000,    // 1 天
];

/** 把毫秒格式化为本地化的简短周期文本（单位词由 i18n 提供）。 */
export function formatInterval(ms, t) {
  const fn = typeof t === "function" ? t : (k) => k;
  const sec = Math.round(ms / 1000);
  if (sec < 60) return `${sec} ${fn("scheduler.unitSecond")}`;
  const min = Math.round(sec / 60);
  if (min < 60) return `${min} ${fn("scheduler.unitMinute")}`;
  const hr = Math.round(min / 60);
  if (hr < 24) return `${hr} ${fn("scheduler.unitHour")}`;
  const day = Math.round(hr / 24);
  return `${day} ${fn("scheduler.unitDay")}`;
}
