// renderer/__tests__/knowledge-dom.test.js — DOM behavior for the knowledge
// panel: brain dropdown is populated, the chosen brain repo is sent on ask,
// the upload pin checkbox flows into the add-doc payload, and pinned documents
// render a badge with an unpin action.
import { test } from "node:test";
import assert from "node:assert/strict";
import { setupDom } from "./helpers/dom.js";

const html = `
  <section id="pane-knowledge">
    <div id="kb-engine-status"></div>
    <button id="kb-create-btn" type="button"></button>
    <input type="file" id="kb-doc-file" />
    <input type="checkbox" id="kb-doc-pin" />
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
let sentAdd = null;
let sentPin = null;
window.kevrai = {
  knowledgeCapabilities: async () => ({ body: { engine_installed: true } }),
  knowledgeList: async () => ({
    body: { items: [{ id: "kb1", name: "Notes", doc_count: 2, chunk_count: 3 }] },
  }),
  knowledgeDocs: async () => ({
    body: {
      items: [
        { id: "doc1", filename: "pinned.md", pinned: true, chunk_count: 0 },
        { id: "doc2", filename: "indexed.md", pinned: false, chunk_count: 2 },
      ],
    },
  }),
  knowledgeAddDoc: async (payload) => {
    sentAdd = payload;
    return { body: { id: "docNew", pinned: !!payload.pinned, chunk_count: payload.pinned ? 0 : 1 } };
  },
  knowledgeSetPin: async (payload) => {
    sentPin = payload;
    return { body: { ok: true } };
  },
  knowledgeDeleteDoc: async () => ({ body: { ok: true } }),
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

test("pinned document renders a badge and an unpin action", async () => {
  await new Promise((r) => setTimeout(r, 5));
  const list = document.getElementById("kb-doc-list");
  const htmlText = list.innerHTML;
  // Pinned row carries the pin badge + an unpin button.
  assert.ok(htmlText.includes("📌"));
  const unpin = list.querySelector("[data-unpin-doc]");
  assert.ok(unpin, "expected an unpin button on the pinned doc");
  assert.equal(unpin.getAttribute("data-unpin-doc"), "doc1");
  // The ordinary indexed doc has no unpin button.
  assert.ok(!htmlText.includes("data-unpin-doc=\"doc2\""));
});

test("unpin button posts pinned:false to the pin endpoint", async () => {
  const unpin = document.querySelector("[data-unpin-doc]");
  unpin.click();
  await new Promise((r) => setTimeout(r, 5));
  assert.ok(sentPin);
  assert.equal(sentPin.kb_id, "kb1");
  assert.equal(sentPin.doc_id, "doc1");
  assert.equal(sentPin.pinned, false);
});

test("checked pin checkbox sends pinned:true on add", async () => {
  const fileInput = document.getElementById("kb-doc-file");
  const file = new window.File(["note body text"], "note.md", { type: "text/plain" });
  file.text = async () => "note body text";
  Object.defineProperty(fileInput, "files", {
    value: [file],
    configurable: true,
  });
  document.getElementById("kb-doc-pin").checked = true;
  document.getElementById("kb-doc-add-btn").click();
  await new Promise((r) => setTimeout(r, 5));
  assert.ok(sentAdd, "expected add-doc to be called");
  assert.equal(sentAdd.kb_id, "kb1");
  assert.equal(sentAdd.filename, "note.md");
  assert.equal(sentAdd.pinned, true);
});

test("unchecked pin checkbox sends pinned:false on add", async () => {
  sentAdd = null;
  document.getElementById("kb-doc-pin").checked = false;
  document.getElementById("kb-doc-add-btn").click();
  await new Promise((r) => setTimeout(r, 5));
  assert.ok(sentAdd);
  assert.equal(sentAdd.pinned, false);
});
