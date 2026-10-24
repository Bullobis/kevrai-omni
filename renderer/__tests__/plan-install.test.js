// renderer/__tests__/plan-install.test.js — 下载决策（点安装之后到底下什么）。
//
// 真实 bug 背景：目录里 259 个模型中有 165 个没有 gguf_repo。旧流程对它们
// 只装引擎就 return，权重从未被下载 —— 没有报错、没有 toast、下载面板里
// 也不会出现任务，用户以为装好了。
import { test } from "node:test";
import assert from "node:assert/strict";

import {
  planInstall,
  planReasonKey,
  shouldAbortOnEngineFailure,
  PLAN,
  HUB_HF,
} from "../modules/plan-install.js";

// ---------------------------------------------------------------------------
// GGUF 仓库
// ---------------------------------------------------------------------------

test("GGUF + 已选分片 → 按分片下载", () => {
  const p = planInstall(
    { id: "m", gguf_repo: "org/repo-GGUF", repo: "org/repo" },
    { files: ["repo-Q4_K_M.gguf"], revision: "main", repo: "org/repo-GGUF" },
  );
  assert.equal(p.kind, PLAN.FILES);
  assert.deepEqual(p.args.files, ["repo-Q4_K_M.gguf"]);
  assert.equal(p.args.hub, HUB_HF);
  assert.equal(p.args.auto_pick, false);
});

test("GGUF + 空分片数组 ≠ 取消，降级为自动挑文件", () => {
  // 这是原来静默失败的那一档：pick.files 为 [] 时旧代码 `if (pick.files…)`
  // 不成立，整个安装流程无声无息地结束。
  const p = planInstall({ id: "m", gguf_repo: "org/repo-GGUF" }, { files: [] });
  assert.equal(p.kind, PLAN.AUTO);
  assert.equal(p.args.auto_pick, true);
  assert.equal(p.reason, "quant-unrecognised");
});

test("GGUF + 未选 → 自动挑文件", () => {
  const p = planInstall({ id: "m", gguf_repo: "org/repo-GGUF" });
  assert.equal(p.kind, PLAN.AUTO);
  assert.equal(p.args.repo, "org/repo-GGUF");
});

test("分片数组里的空串被过滤掉", () => {
  const p = planInstall(
    { id: "m", gguf_repo: "org/r" },
    { files: ["", null, undefined, "a.gguf"] },
  );
  assert.equal(p.kind, PLAN.FILES);
  assert.deepEqual(p.args.files, ["a.gguf"]);
});

// ---------------------------------------------------------------------------
// 非 GGUF 仓库 —— 本次修复的核心
// ---------------------------------------------------------------------------

test("普通 HF 仓库 → 自动挑文件（旧行为：什么都不下）", () => {
  const p = planInstall({ id: "qwen3-omni", repo: "Qwen/Qwen3-Omni-30B-A3B-Instruct" });
  assert.equal(p.kind, PLAN.AUTO);
  assert.equal(p.args.repo, "Qwen/Qwen3-Omni-30B-A3B-Instruct");
  assert.equal(p.args.auto_pick, true);
  assert.equal(p.args.hub, HUB_HF);
});

test("非 GGUF 仓库带 revision 时透传", () => {
  const p = planInstall({ id: "m", repo: "org/repo" }, { revision: "v1.2" });
  assert.equal(p.args.revision, "v1.2");
});

// ---------------------------------------------------------------------------
// 真的没东西可下
// ---------------------------------------------------------------------------

test("无 repo → NOTHING，并给出原因", () => {
  const p = planInstall({ id: "pending-model" });
  assert.equal(p.kind, PLAN.NOTHING);
  assert.equal(p.reason, "no-repo");
});

test("空对象 → NOTHING", () => {
  assert.equal(planInstall(null).kind, PLAN.NOTHING);
  assert.equal(planInstall(undefined).reason, "no-item");
});

test("repo 覆盖生效（量化选择器返回的 repo 优先）", () => {
  const p = planInstall(
    { id: "m", gguf_repo: "org/a" },
    { repo: "org/override", files: ["x.gguf"] },
  );
  assert.equal(p.args.repo, "org/override");
});

// ---------------------------------------------------------------------------
// 原因 → i18n 键
// ---------------------------------------------------------------------------

test("每种原因都有对应的 i18n 键", () => {
  assert.equal(planReasonKey("no-item"), "toast.installNothingNoItem");
  assert.equal(planReasonKey("no-repo"), "toast.installNothingNoRepo");
  assert.equal(planReasonKey("unknown"), "toast.installNothingGeneric");
});

// ---------------------------------------------------------------------------
// 引擎失败是否该中断
// ---------------------------------------------------------------------------

test("准备下权重时引擎失败应该中断", () => {
  // 否则用户会拿到一堆跑不起来的权重
  assert.equal(shouldAbortOnEngineFailure(true), true);
});

test("本来就没权重可下时不必因引擎失败中断", () => {
  assert.equal(shouldAbortOnEngineFailure(false), false);
});
