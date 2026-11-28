// renderer/__tests__/agent-execution-dom.test.js — 执行方式必须可见、可理解、可选择。
// 验证：状态条常驻、三档选项都在、完整 Shell 必须勾选风险确认、保存后状态更新。
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { setupDom } from "./helpers/dom.js";
import { registerDict } from "../modules/i18n.js";

const zhDict = JSON.parse(
  readFileSync(new URL("../locales/zh-CN.json", import.meta.url), "utf8"),
);
registerDict("zh-CN", zhDict);

const { window, document } = setupDom('<div id="agent-root"></div>');

const modes = [
  { id: "none", label: "不执行命令", description: "只读写文件。" },
  { id: "test_runner", label: "测试运行器", description: "白名单测试，不是沙箱。" },
  { id: "approval_shell", label: "完整 Shell（无隔离）", description: "任意命令，无隔离。" },
];
let saved = [];
const approvalSaved = [];

window.kevrai = {
  agentStatus: async () => ({ body: { llm_ready: false, mode: "rule_based", tool_count: 0 } }),
  agentBrain: async () => ({ body: { backend: "rule", models: [] } }),
  agentSessions: async () => ({ body: { sessions: [] } }),
  agentSkills: async () => ({ body: { skills: [], count: 0, active_count: 0, active_tool_count: 0 } }),
  agentApprovalPolicy: async () => ({ body: { policy: "auto" } }),
  agentSetApprovalPolicy: async (_sessionId, policy) => {
    approvalSaved.push(policy);
    return { body: { policy } };
  },
  agentExecutionMode: async () => ({
    body: { mode: "none", explicit: false, root: "/tmp/workspace", modes },
  }),
  agentApprovals: async () => ({ body: { requests: [] } }),
  agentWsInfo: async () => { throw new Error("stream unavailable"); },
  agentChat: async () => ({
    body: {
      answer: "已改好，运行测试通过。",
      tools_used: ["edit_lines"],
      steps: [{
        thought: "修改入口文件第 2-3 行",
        action_tool: "edit_lines",
        action_params: { path: "python/app/agent/workspace.py", start_line: 2, end_line: 3, new_text: "new" },
        observation: {
          ok: true, path: "python/app/agent/workspace.py", operation: "replace",
          start_line: 2, end_line: 3, additions: 2, deletions: 1,
          diff: "--- before\n+++ after\n@@ -1,2 +1,3 @@\n-old\n+new\n+extra",
        },
        observation_ok: true,
      }],
    },
  }),
  agentSetExecutionMode: async (mode, ack) => {
    saved.push([mode, ack]);
    return { body: { mode, explicit: true, root: "/tmp/workspace", modes } };
  },
};

const mod = await import(`../modules/agent.js?execution=${Date.now()}`);
await mod.initAgent();
await new Promise((r) => setTimeout(r, 0));

const bar = document.getElementById("agent-execution-bar");
const btn = document.getElementById("agent-execution-btn");
const dialog = document.getElementById("agent-execution-dialog");

test("工具审批是明确的三档选择，默认自动确认", () => {
  const select = document.getElementById("agent-approval-select");
  assert.ok(select, "approval select rendered");
  assert.deepEqual(
    [...select.options].map((o) => o.value),
    ["confirm", "auto", "no_confirm"],
  );
  assert.equal(select.value, "auto");
});

test("执行方式状态条常驻，未选择时明确提示默认不执行命令", () => {
  assert.ok(bar && btn, "execution bar rendered");
  assert.equal(bar.hidden, false);
  assert.match(document.getElementById("agent-execution-current").textContent, /未选择/);
  assert.match(document.getElementById("agent-execution-note").textContent, /只能读写文件/);
});

test("打开选择面板：三种方式全部可见，不是藏在高级设置里", () => {
  btn.click();
  assert.equal(dialog.hidden, false);
  const options = dialog.querySelectorAll("input[name=agent-exec-mode]");
  assert.equal(options.length, 3);
  assert.deepEqual(
    [...options].map((o) => o.value),
    ["none", "test_runner", "approval_shell"],
  );
});

test("完整 Shell 必须显式勾选风险确认，否则不保存", async () => {
  dialog.querySelector('input[value="approval_shell"]').click();
  assert.equal(document.getElementById("agent-execution-risk").hidden, false);
  saved = [];
  document.getElementById("agent-execution-save").click();
  await new Promise((r) => setTimeout(r, 0));
  assert.equal(saved.length, 0, "risk acknowledgement is mandatory");
});

test("勾选风险后可保存完整 Shell，并把真实代价写回状态条", async () => {
  document.getElementById("agent-execution-risk-ack").checked = true;
  saved = [];
  document.getElementById("agent-execution-save").click();
  await new Promise((r) => setTimeout(r, 0));
  assert.deepEqual(saved, [["approval_shell", true]]);
  assert.equal(bar.dataset.mode, "approval_shell");
  assert.match(document.getElementById("agent-execution-current").textContent, /完整 Shell/);
  assert.match(document.getElementById("agent-execution-note").textContent, /无隔离/);
});

test("编辑文件的思考卡显示 +N -N，并可展开完整 diff", async () => {
  const input = document.getElementById("agent-input");
  const send = document.getElementById("agent-send-btn");
  input.value = "改一下 workspace.py";
  input.dispatchEvent(new window.Event("input"));
  send.click();
  await new Promise((r) => setTimeout(r, 250));

  const card = document.querySelector(".agent-edit-card");
  assert.ok(card, "edit card rendered");
  assert.match(card.textContent, /已编辑文件 python\/app\/agent\/workspace\.py · 第 2-3 行/);
  assert.match(card.textContent, /\+2/);
  assert.match(card.textContent, /-1/);
  const details = card.querySelector(".agent-edit-details");
  assert.ok(details, "diff is expandable");
  const diff = card.querySelector(".agent-edit-diff");
  assert.match(diff.textContent, /\+new/);
  assert.match(diff.textContent, /-old/);
});


test("切换工具审批三档会用新策略并处理高风险确认", async () => {
  const select = document.getElementById("agent-approval-select");
  window.confirm = () => true;
  select.value = "confirm";
  select.dispatchEvent(new window.Event("change"));
  await new Promise((r) => setTimeout(r, 20));
  assert.equal(approvalSaved.at(-1), "confirm");

  select.value = "no_confirm";
  select.dispatchEvent(new window.Event("change"));
  await new Promise((r) => setTimeout(r, 20));
  assert.equal(approvalSaved.at(-1), "no_confirm");
});
