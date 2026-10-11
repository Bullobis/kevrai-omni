// renderer/__tests__/knowledge-dom.test.js — DOM behavior for the knowledge
// panel: brain dropdown is populated, and the chosen brain repo is sent on ask.
import { test } from "node:test";
import assert from "node:assert/strict";
import { setupDom } from "./helpers/dom.js";

const html = `
  <section id="pane-knowledge">
    <div id="kb-engine-status"></div>
    <button id="kb-create-btn" type="button"></button>
    <button id="kb-doc-add-btn" type="button"></button>
    <button id="kb-search-btn" type="button"></button>
    <button id="kb-ask-btn" type="button"></button>
    <select id="kb-list"></select>
    <select id="kb-brain"><option value=""></option></select>
    <input id="kb-question" type="text" />
    <div id="kb-ask-result"></div>
    <div id="kb-search-results"></div>
    <div id="kb-doc-list"></div>
  </section>`;
const { window, document } = setupDom(html);

let sentAsk = null;
window.kevrai = {
  knowledgeCapabilities: async () => ({ body: { engine_installed: true } }),
  knowledgeList: async () => ({
    body: { items: [{ id: "kb1", name: "Notes", doc_count: 1, chunk_count: 2 }] },
  }),
  knowledgeDocs: async () => ({ body: { items: [] } }),
  agentBrain: async () => ({
    body: {
      backend: "transformers",
      models: [
        { repo: "HuggingFaceTB/SmolLM2-135M-Instruct", label: "SmolLM2 135M" },
        { repo: "Qwen/Qwen3-0.6B", label: "Qwen3 0.6B" },
      ],
    },
  }),
  knowledgeAsk: async (payload) => {
    sentAsk = payload;
    return {
      body: { answer: "ANSWER: blue", results: [{ score: 0.9, index: 0 }] },
    };
  },
};

const mod = await import(`../modules/knowledge.js?dom=${Date.now()}`);
await mod.initKnowledge();

test("brain dropdown is populated from selectable models", () => {
  const sel = document.getElementById("kb-brain");
  assert.equal(sel.options.length, 3); // default + 2 brains
  assert.equal(sel.options[1].value, "HuggingFaceTB/SmolLM2-135M-Instruct");
});

test("ask sends the selected brain model", async () => {
  const list = document.getElementById("kb-list");
  list.value = "kb1";
  list.dispatchEvent(new window.Event("change"));
  document.getElementById("kb-question").value = "what colour is the sky?";
  const brain = document.getElementById("kb-brain");
  brain.value = "Qwen/Qwen3-0.6B";
  document.getElementById("kb-ask-btn").click();
  await new Promise((r) => setTimeout(r, 5));
  assert.ok(sentAsk);
  assert.equal(sentAsk.brain_model, "Qwen/Qwen3-0.6B");
  assert.equal(sentAsk.kb_id, "kb1");
});

test("default (no brain) sends an empty brain_model", async () => {
  sentAsk = null;
  document.getElementById("kb-brain").value = "";
  document.getElementById("kb-ask-btn").click();
  await new Promise((r) => setTimeout(r, 5));
  assert.equal(sentAsk.brain_model, "");
});
