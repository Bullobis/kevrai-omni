// renderer/__tests__/agent-approval.test.js — pure helpers for the agent
// tool-call approval gate: edited-params parsing + pending-card HTML.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { setupDom } from "./helpers/dom.js";
import { registerDict } from "../modules/i18n.js";
import { parseEditedParams, approvalCardHtml } from "../modules/agent-approval.js";

// 加载真实 zh-CN 字典，使模块里的 t() 返回真实文案（而非 key）。
const _here = dirname(fileURLToPath(import.meta.url));
registerDict("zh-CN", JSON.parse(readFileSync(join(_here, "..", "locales", "zh-CN.json"), "utf8")));

// ---------------------------------------------------------------------------
// parseEditedParams
// ---------------------------------------------------------------------------
test("parseEditedParams: blank field -> null (approve with original params)", () => {
  assert.equal(parseEditedParams(""), null);
  assert.equal(parseEditedParams("   "), null);
  assert.equal(parseEditedParams(null), null);
});

test("parseEditedParams: a valid JSON object is returned as-is", () => {
  assert.deepEqual(parseEditedParams('{"query":"bge","limit":5}'), { query: "bge", limit: 5 });
});

test("parseEditedParams: invalid JSON throws a readable error", () => {
  assert.throws(() => parseEditedParams("{not json"), /JSON/);
});

test("parseEditedParams: arrays / scalars are rejected (params must be an object)", () => {
  assert.throws(() => parseEditedParams("[1,2,3]"), /JSON 对象/);
  assert.throws(() => parseEditedParams('"just a string"'), /JSON 对象/);
  assert.throws(() => parseEditedParams("42"), /JSON 对象/);
});

// ---------------------------------------------------------------------------
// approvalCardHtml (rendered into a jsdom container)
// ---------------------------------------------------------------------------
test("approvalCardHtml: shows tool name, editable JSON params and both buttons", () => {
  setupDom(`<div id="agent-approvals"></div>`);
  const container = document.getElementById("agent-approvals");
  container.innerHTML = approvalCardHtml({
    id: "abc123",
    tool_name: "download_model",
    tool_params: { model_id: "bge-m3", quant: "q4" },
  });

  const card = container.querySelector(".agent-approval-card");
  assert.ok(card, "renders a card");
  assert.equal(card.dataset.id, "abc123");
  assert.match(card.querySelector(".agent-approval-tool").textContent, /download_model/);

  const ta = card.querySelector("textarea.agent-approval-params");
  assert.ok(ta, "has an editable params textarea");
  assert.match(ta.value, /"model_id": "bge-m3"/);

  assert.ok(card.querySelector("[data-approval-approve]"), "approve button present");
  assert.ok(card.querySelector("[data-approval-reject]"), "reject button present");
});

test("approvalCardHtml: empty params render as an empty-object JSON body", () => {
  setupDom(`<div id="host"></div>`);
  const html = approvalCardHtml({ id: "x", tool_name: "check_hardware", tool_params: {} });
  assert.match(html, /\{\s*\}/);
});
