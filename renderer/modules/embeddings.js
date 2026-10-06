// renderer/modules/embeddings.js — Sentence-transformers embeddings panel.
"use strict";
import { api } from "./api.js";
import { toast } from "./toast.js";
import { t } from "./i18n.js";

const $ = (s, r = document) => r.querySelector(s);

let embInited = false;

function escapeHtml(s) {
  return String(s == null ? "" : s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;")
    .replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

async function refresh() {
  const root = $("#pane-embeddings");
  if (!root) return;
  let cap = { installed: false };
  try {
    const r = await api.embeddingsCapabilities();
    cap = (r && r.body) ? r.body : r;
  } catch (_) { /* bridge error already toasted */ }
  root.classList.toggle("engine-missing", !cap.installed);
  const status = $("#emb-engine-status", root);
  if (status) {
    status.textContent = cap.installed
      ? t("embeddings.engineReady", { ver: cap.version || "" })
      : t("embeddings.engineMissing");
    status.dataset.installed = cap.installed ? "1" : "0";
  }
}

async function populateModels() {
  const sel = $("#emb-model");
  if (!sel) return;
  try {
    const r = await api.models({ category: "embedding" });
    const all = (r && r.body) ? r.body : r;
    const items = (all.items || all).filter(
      (m) => (m.engine || []).includes("sentence-transformers")
    );
    if (items.length) {
      sel.innerHTML = items.map(
        (m) => `<option value="${escapeHtml(m.repo)}">${escapeHtml(m.name)}</option>`
      ).join("");
    }
  } catch (_) { /* keep static options */ }
}

let lastPayload = null;

function renderResult(payload) {
  const out = $("#emb-result");
  if (!out) return;
  const data = (payload && payload.body) ? payload.body : payload;
  const items = data.data || [];
  const usage = data.usage || {};
  const cards = items.map((item) => {
    const vec = item.embedding || [];
    const head = vec.slice(0, 8).map((x) => Number(x).toFixed(4)).join(", ");
    return `<div class="emb-card">
      <div class="emb-card-h">#${item.index} · ${vec.length} ${t("embeddings.dims")}</div>
      <pre class="emb-pre">[${head}, …]</pre>
    </div>`;
  }).join("");
  out.innerHTML =
    `<div class="emb-meta"><span>${t("embeddings.count", { n: items.length })}</span>` +
    `<span>${t("embeddings.tokens", { n: usage.prompt_tokens || 0 })}</span></div>` +
    cards;
}

async function encode() {
  const model = $("#emb-model").value;
  const raw = $("#emb-input").value || "";
  const lines = raw.split("\n").map((s) => s.trim()).filter(Boolean);
  if (!lines.length) {
    toast(t("embeddings.enterFirst"), { kind: "warn" });
    return;
  }
  const btn = $("#emb-run");
  btn.disabled = true;
  try {
    const payload = await api.embeddings({
      model,
      input: lines.length === 1 ? lines[0] : lines,
      encoding_format: "float",
      normalize_embeddings: $("#emb-normalize").checked,
    });
    lastPayload = payload;
    renderResult(payload);
  } catch (e) {
    toast(t("embeddings.failed", { err: e.message }), { kind: "err" });
  } finally {
    btn.disabled = false;
  }
}

async function copyFull() {
  if (!lastPayload) {
    toast(t("embeddings.enterFirst"), { kind: "warn" });
    return;
  }
  const data = (lastPayload && lastPayload.body) ? lastPayload.body : lastPayload;
  try {
    await navigator.clipboard.writeText(JSON.stringify(data));
    toast(t("embeddings.copied"), { kind: "ok" });
  } catch (e) {
    toast(e.message, { kind: "err" });
  }
}

async function installEngine() {
  const btn = $("#emb-install");
  btn.disabled = true;
  try {
    await api.installEngine("sentence-transformers");
    toast(t("embeddings.engineInstalled"), { kind: "ok" });
    await refresh();
  } catch (e) {
    toast(e.message, { kind: "err" });
  } finally {
    btn.disabled = false;
  }
}

export async function initEmbeddings() {
  if (embInited) return;
  embInited = true;
  $("#emb-run").addEventListener("click", encode);
  $("#emb-copy").addEventListener("click", copyFull);
  $("#emb-install").addEventListener("click", installEngine);
  await refresh();
  await populateModels();
  window.addEventListener("kevrai:view-change", (e) => {
    if (e.detail && e.detail.view === "embeddings") {
      refresh().catch(() => {});
    }
  });
}
