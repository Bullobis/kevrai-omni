// renderer/__tests__/agent-quickphrases-dom.test.js — DOM 行为：
// Agent 面板渲染出折叠的快捷短语区；展开后 chips 可见；点击 chip 把短语
// 填入 #agent-input、聚焦并启用发送按钮（不自动发送）。
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { setupDom } from "./helpers/dom.js";
import { registerDict } from "../modules/i18n.js";
import {
  QUICK_PHRASE_GROUPS, findPhraseById, allPhrases, composePhraseValue,
} from "../modules/agent-quickphrases.js";

// 注入真实 zh-CN 字典，使 t("agent.quickPhrases") 解析为中文按钮文案。
const zhDict = JSON.parse(
  readFileSync(new URL("../locales/zh-CN.json", import.meta.url), "utf8"),
);
registerDict("zh-CN", zhDict);

const { window, document } = setupDom('<div id="agent-root"></div>');

// initAgent() 启动时会拉取的状态接口；给最小可解包负载即可（全部 try/catch 兜底）。
window.kevrai = {
  agentStatus: async () => ({ body: { llm_ready: false, mode: "rule_based", tool_count: 0 } }),
  agentBrain: async () => ({ body: { backend: "rule", models: [] } }),
  agentSessions: async () => ({ body: { sessions: [] } }),
  agentSkills: async () => ({ body: { skills: [], count: 0, active_count: 0, active_tool_count: 0 } }),
  agentApprovalPolicy: async () => ({ body: { policy: "auto" } }),
};

const mod = await import(`../modules/agent.js?dom=${Date.now()}`);
await mod.initAgent();

const input = document.getElementById("agent-input");
const sendBtn = document.getElementById("agent-send-btn");
const toggle = document.getElementById("agent-qp-toggle");
const body = document.getElementById("agent-qp-body");

test("渲染出折叠的快捷短语按钮，默认收起且文案走 i18n", () => {
  assert.ok(toggle, "toggle button rendered");
  assert.ok(body, "chips body rendered");
  assert.equal(body.hidden, true, "chips collapsed by default");
  assert.match(toggle.textContent, /快捷短语/);
});

test("展开后渲染出与数据一致的 chips，data-qp 全部可解析", () => {
  toggle.click();
  assert.equal(body.hidden, false, "chips expand on toggle");
  assert.equal(toggle.getAttribute("aria-expanded"), "true");
  const chips = body.querySelectorAll("[data-qp]");
  assert.equal(chips.length, allPhrases().length);
  for (const chip of chips) {
    assert.ok(findPhraseById(chip.dataset.qp), `unresolvable chip: ${chip.dataset.qp}`);
  }
  // 每个分组都至少出现一个 chip。
  const renderedGroups = new Set(
    [...body.querySelectorAll(".agent-qp-chip")].map((c) => findPhraseById(c.dataset.qp).group.id),
  );
  for (const g of QUICK_PHRASE_GROUPS) assert.ok(renderedGroups.has(g.id));
});

test("点击 chip：短语填入输入框、聚焦、启用发送按钮，但不发送", () => {
  input.value = "";
  sendBtn.disabled = true;
  const hit = findPhraseById("media.tts");
  const chip = body.querySelector('[data-qp="media.tts"]');
  chip.click();
  assert.equal(input.value, hit.phrase.text, "chip text fills the input");
  assert.equal(sendBtn.disabled, false, "send button enabled after fill");
  assert.equal(document.activeElement, input, "input receives focus");
});

test("再点另一条 chip：换行叠加，不清空用户已填内容", () => {
  const first = findPhraseById("media.tts").phrase.text;
  const second = findPhraseById("models.list").phrase.text;
  body.querySelector('[data-qp="models.list"]').click();
  assert.equal(input.value, composePhraseValue(first, second),
    "second phrase appends on a new line");
});

test("再次点击折叠按钮可收起 chips（不长期占屏）", () => {
  toggle.click();
  assert.equal(body.hidden, true);
  assert.equal(toggle.getAttribute("aria-expanded"), "false");
});
