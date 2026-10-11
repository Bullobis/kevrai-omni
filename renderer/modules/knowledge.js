// renderer/modules/knowledge.js — Local knowledge base (RAG) panel.
//
// Pure helpers at the top are exported for unit testing (node:test, no DOM);
// initKnowledge() wires the DOM once the pane becomes visible.
"use strict";
import { api } from "./api.js";
import { toast } from "./toast.js";
import { t } from "./i18n.js";
import { attachTokenCounter } from "./token-estimate.js";

const $ = (s, r = document) => r.querySelector(s);

export const KB_ALLOWED_EXT = [".txt", ".md", ".markdown", ".json", ".csv", ".pdf", ".docx", ".pptx", ".png", ".jpg", ".jpeg", ".bmp"];

/** Image extensions recognised by the OCR path (uploaded base64). */
export const KB_IMAGE_EXT = [".png", ".jpg", ".jpeg", ".bmp"];

/** True when the filename is an image for OCR. */
export function isImageFile(filename) {
  const f = String(filename || "").toLowerCase();
  return KB_IMAGE_EXT.some((ext) => f.endsWith(ext));
}

/** True when the filename is a PDF (sent as base64, not plain text). */
export function isPdfFile(filename) {
  return String(filename || "").toLowerCase().endsWith(".pdf");
}

/** True when the filename is a .docx (sent as base64, not plain text). */
export function isDocxFile(filename) {
  return String(filename || "").toLowerCase().endsWith(".docx");
}

/** True when the filename is a .pptx (sent as base64, not plain text). */
export function isPptxFile(filename) {
  return String(filename || "").toLowerCase().endsWith(".pptx");
}

/** True for binary formats uploaded base64-encoded (PDF/DOCX/PPTX/images). */
export function isBinaryDocFile(filename) {
  return isPdfFile(filename) || isDocxFile(filename) || isPptxFile(filename) || isImageFile(filename);
}

/** Read a File as a base64 data string (data:...;base64,<payload>). */
function readFileAsDataUrl(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result || ""));
    reader.onerror = () => reject(reader.error || new Error("read failed"));
    reader.readAsDataURL(file);
  });
}

let knowledgeInited = false;
let currentKbId = null;

export function escapeHtml(s) {
  return String(s == null ? "" : s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;")
    .replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

/**
 * Build HTML for the numbered source list attached to a grounded answer.
 * Each item: [n] doc name · match score · excerpt. Pure / testable.
 */
export function sourceCardsHtml(sources) {
  const list = Array.isArray(sources) ? sources : [];
  if (!list.length) return "";
  const rows = list.map((s) => {
    const pct = (typeof s.score === "number") ? ` · ${Math.round(s.score * 100)}%` : "";
    return `<li class="kb-src"><span class="kb-src-ref">[${s.ref}]</span>` +
      `<span class="kb-src-name">${escapeHtml(s.doc_name || "")}${pct}</span>` +
      `<span class="kb-src-excerpt">${escapeHtml(s.excerpt || "")}</span></li>`;
  }).join("");
  return `<div class="kb-sources"><h5 data-i18n="knowledge.sources">来源</h5><ul>${rows}</ul></div>`;
}

/** True when the filename has an extension this build can ingest. */
export function isAllowedFile(filename) {
  const name = String(filename || "").toLowerCase();
  return KB_ALLOWED_EXT.some((ext) => name.endsWith(ext));
}

/** Trim a KB name; return "" when it is blank (caller should refuse). */
export function sanitizeKbName(name) {
  return String(name || "").trim().slice(0, 120);
}

/** Format a cosine score (0..1) as a readable percentage. */
export function scorePct(score) {
  const n = Number(score);
  if (!Number.isFinite(n)) return "0%";
  return `${Math.round(Math.max(0, Math.min(1, n)) * 100)}%`;
}

/** Build the download filename for an exported KB: ``kb-<name>-<date>.json``. */
export function formatExportFilename(name, dateStr) {
  const slug = String(name || "kb").trim().replace(/[\\/:*?"<>|\s]+/g, "-")
    .replace(/-+$/, "").slice(0, 60) || "kb";
  const date = String(dateStr || "").replace(/[^0-9]/g, "").slice(0, 8) || "00000000";
  return `kb-${slug}-${date}.json`;
}

function currentKb() {
  return currentKbId;
}

async function refreshCapabilities() {
  const root = $("#pane-knowledge");
  if (!root) return;
  let cap = { engine_installed: true };
  try {
    const r = await api.knowledgeCapabilities();
    cap = (r && r.body) ? r.body : r;
  } catch (_) { /* bridge error already toasted */ }
  root.classList.toggle("engine-missing", !cap.engine_installed);
  const status = $("#kb-engine-status");
  if (status) {
    status.textContent = cap.engine_installed
      ? t("knowledge.engineReady")
      : t("knowledge.engineMissing");
    status.dataset.installed = cap.engine_installed ? "1" : "0";
  }
}

async function refreshKbList() {
  const sel = $("#kb-list");
  if (!sel) return;
  let items = [];
  try {
    const r = await api.knowledgeList();
    const body = (r && r.body) ? r.body : r;
    items = body.items || [];
  } catch (_) { items = []; }

  if (!items.length) {
    sel.innerHTML = `<option value="">${t("knowledge.empty")}</option>`;
  } else {
    sel.innerHTML = items.map((kb) =>
      `<option value="${escapeHtml(kb.id)}">${escapeHtml(kb.name)}` +
      ` (${kb.doc_count} docs / ${kb.chunk_count} chunks)</option>`
    ).join("");
  }
  // Keep a valid selection where possible.
  if (currentKbId && items.some((kb) => kb.id === currentKbId)) {
    sel.value = currentKbId;
  } else {
    currentKbId = items.length ? items[0].id : null;
    if (items.length) sel.value = currentKbId;
  }
  await refreshDocs();
}

async function refreshDocs() {
  const list = $("#kb-doc-list");
  if (!list) return;
  if (!currentKbId) {
    list.innerHTML = `<p class="muted">${t("knowledge.selectFirst")}</p>`;
    return;
  }
  let items = [];
  try {
    const r = await api.knowledgeDocs(currentKbId);
    const body = (r && r.body) ? r.body : r;
    items = body.items || [];
  } catch (_) { items = []; }

  if (!items.length) {
    list.innerHTML = `<p class="muted">${t("knowledge.noDocs")}</p>`;
    return;
  }
  list.innerHTML = items.map((d) => {
    const pinned = !!d.pinned;
    const pinBadge = pinned
      ? `<span class="kb-pin-badge" title="${escapeHtml(t("knowledge.pinned"))}">📌</span>`
      : "";
    const meta = pinned
      ? t("knowledge.pinnedDoc")
      : `${d.chunk_count} ${t("knowledge.chunks")}`;
    const unpinBtn = pinned
      ? `<button class="ghost tiny" data-unpin-doc="${escapeHtml(d.id)}">` +
        `${t("knowledge.unpin")}</button>`
      : "";
    return `<div class="kb-doc-row">` +
      `${pinBadge}<span class="kb-doc-name">${escapeHtml(d.filename)}</span>` +
      `<span class="muted tiny">${escapeHtml(meta)}</span>` +
      `${unpinBtn}` +
      `<button class="ghost tiny" data-del-doc="${escapeHtml(d.id)}" ` +
      `data-doc-name="${escapeHtml(d.filename)}">${t("knowledge.delete")}</button>` +
      `</div>`;
  }).join("");
  list.querySelectorAll("[data-unpin-doc]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const docId = btn.getAttribute("data-unpin-doc");
      try {
        await api.knowledgeSetPin({ kb_id: currentKbId, doc_id: docId, pinned: false });
        toast(t("knowledge.unpinned"), { kind: "ok" });
        await refreshKbList();
      } catch (e) {
        toast(e.message, { kind: "err" });
      }
    });
  });
  list.querySelectorAll("[data-del-doc]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const docId = btn.getAttribute("data-del-doc");
      try {
        await api.knowledgeDeleteDoc({ kb_id: currentKbId, doc_id: docId });
        toast(t("knowledge.docDeleted"), { kind: "ok" });
        await refreshKbList();
      } catch (e) {
        toast(e.message, { kind: "err" });
      }
    });
  });
}

async function createKb() {
  const name = sanitizeKbName($("#kb-name").value);
  const model = ($("#kb-model").value || "").trim();
  if (!name) {
    toast(t("knowledge.nameRequired"), { kind: "warn" });
    return;
  }
  if (!model || model.indexOf("/") === -1) {
    toast(t("knowledge.modelRequired"), { kind: "warn" });
    return;
  }
  const chunkSize = parseInt($("#kb-chunk-size").value, 10) || 600;
  const overlap = parseInt($("#kb-overlap").value, 10) || 80;
  const btn = $("#kb-create-btn");
  btn.disabled = true;
  try {
    const r = await api.knowledgeCreate({
      name, model, chunk_size: chunkSize, overlap,
    });
    const kb = (r && r.body) ? r.body : r;
    currentKbId = kb.id;
    $("#kb-name").value = "";
    toast(t("knowledge.created"), { kind: "ok" });
    await refreshKbList();
  } catch (e) {
    toast(e.message, { kind: "err" });
  } finally {
    btn.disabled = false;
  }
}

async function addDocument() {
  if (!currentKbId) {
    toast(t("knowledge.selectFirst"), { kind: "warn" });
    return;
  }
  const input = $("#kb-doc-file");
  const file = input && input.files && input.files[0];
  if (!file) {
    toast(t("knowledge.chooseFile"), { kind: "warn" });
    return;
  }
  if (!isAllowedFile(file.name)) {
    toast(t("knowledge.badExt", { ext: file.name }), { kind: "warn" });
    return;
  }
  const btn = $("#kb-doc-add-btn");
  btn.disabled = true;
  try {
    const pinBox = $("#kb-doc-pin");
    const pinned = !!(pinBox && pinBox.checked);
    let payload;
    if (isBinaryDocFile(file.name)) {
      // PDF / DOCX are binary: read as base64 and let the sidecar extract text.
      const dataUrl = await readFileAsDataUrl(file);
      const b64 = dataUrl.slice(dataUrl.indexOf(",") + 1);
      payload = {
        kb_id: currentKbId,
        filename: file.name,
        content_b64: b64,
        encoding: "base64",
        pinned,
      };
    } else {
      const content = await file.text();
      payload = {
        kb_id: currentKbId,
        filename: file.name,
        content,
        pinned,
      };
    }
    await api.knowledgeAddDoc(payload);
    input.value = "";
    if (pinBox) pinBox.checked = false;
    toast(t("knowledge.docAdded"), { kind: "ok" });
    await refreshKbList();
  } catch (e) {
    toast(e.message, { kind: "err" });
  } finally {
    btn.disabled = false;
  }
}

function renderResults(payload) {
  const out = $("#kb-search-results");
  if (!out) return;
  const data = (payload && payload.body) ? payload.body : payload;
  const results = data.results || [];
  if (!results.length) {
    out.innerHTML = `<p class="muted">${t("knowledge.noResults")}</p>`;
    return;
  }
  out.innerHTML = results.map((r) =>
    `<div class="kb-result">` +
    `<div class="kb-result-h"><span class="kb-score">${scorePct(r.score)}</span>` +
    `${escapeHtml(r.doc_name)} · #${(r.index || 0) + 1}</div>` +
    `<pre class="kb-pre">${escapeHtml(r.text)}</pre></div>`
  ).join("");
}

async function runSearch() {
  if (!currentKbId) {
    toast(t("knowledge.selectFirst"), { kind: "warn" });
    return;
  }
  const query = ($("#kb-query").value || "").trim();
  if (!query) {
    toast(t("knowledge.enterQuery"), { kind: "warn" });
    return;
  }
  const btn = $("#kb-search-btn");
  btn.disabled = true;
  try {
    const payload = await api.knowledgeSearch({
      kb_id: currentKbId, query, top_k: 4,
    });
    renderResults(payload);
  } catch (e) {
    toast(e.message, { kind: "err" });
  } finally {
    btn.disabled = false;
  }
}

async function runAsk() {
  if (!currentKbId) {
    toast(t("knowledge.selectFirst"), { kind: "warn" });
    return;
  }
  const question = ($("#kb-question").value || "").trim();
  if (!question) {
    toast(t("knowledge.enterQuery"), { kind: "warn" });
    return;
  }
  const btn = $("#kb-ask-btn");
  btn.disabled = true;
  try {
    const brainRepo = ($("#kb-brain") && $("#kb-brain").value) || "";
    const payload = await api.knowledgeAsk({
      kb_id: currentKbId, question, top_k: 4, brain_model: brainRepo,
    });
    const data = (payload && payload.body) ? payload.body : payload;
    const out = $("#kb-ask-result");
    const answer = data.answer
      ? `<div class="kb-answer"><h4>${t("knowledge.answer")}</h4>` +
        `<p>${escapeHtml(data.answer)}</p></div>`
      : `<p class="muted">${escapeHtml(data.answer_note || "")}</p>`;
    const sources = sourceCardsHtml(data.sources || []);
    renderResults({ results: data.results || [] });
    if (out) out.innerHTML = answer + sources;
  } catch (e) {
    toast(e.message, { kind: "err" });
  } finally {
    btn.disabled = false;
  }
}

// Populate the answer-model dropdown from the selectable local brain models.
async function populateBrains() {
  const sel = $("#kb-brain");
  if (!sel) return;
  try {
    const r = await api.agentBrain();
    const data = (r && r.body) ? r.body : r;
    (data.models || []).forEach((m) => {
      const o = document.createElement("option");
      o.value = m.repo || "";
      o.textContent = m.label || m.repo;
      sel.appendChild(o);
    });
  } catch (_) { /* brain list is optional; retrieval-only still works */ }
}

async function exportKb() {
  if (!currentKbId) {
    toast(t("knowledge.selectFirst"), { kind: "warn" });
    return;
  }
  const btn = $("#kb-export-btn");
  btn.disabled = true;
  try {
    const r = await api.knowledgeExport(currentKbId);
    const bundle = (r && r.body) ? r.body : r;
    const name = (bundle.meta && bundle.meta.name) || "kb";
    const stamp = new Date().toISOString().slice(0, 10).replace(/-/g, "");
    const fname = formatExportFilename(name, stamp);
    const blob = new Blob([JSON.stringify(bundle, null, 2)],
      { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = fname;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
    toast(t("knowledge.exported", { name }), { kind: "ok" });
  } catch (e) {
    toast(e.message, { kind: "err" });
  } finally {
    btn.disabled = false;
  }
}

function importKb() {
  const input = $("#kb-import-file");
  if (!input) return;
  input.value = "";
  input.click();
}

async function onImportFilePicked(e) {
  const file = e.target && e.target.files && e.target.files[0];
  if (!file) return;
  const btn = $("#kb-import-btn");
  btn.disabled = true;
  try {
    const text = await file.text();
    let bundle;
    try {
      bundle = JSON.parse(text);
    } catch (_) {
      toast(t("knowledge.importBadJson"), { kind: "err" });
      return;
    }
    const r = await api.knowledgeImport({ bundle });
    const result = (r && r.body) ? r.body : r;
    currentKbId = result.kb_id;
    toast(t("knowledge.imported", { name: result.name, n: result.doc_count }),
      { kind: "ok" });
    await refreshKbList();
    // Select the freshly imported KB in the dropdown.
    const sel = $("#kb-list");
    if (sel && currentKbId) sel.value = currentKbId;
  } catch (err) {
    toast(err.message, { kind: "err" });
  } finally {
    btn.disabled = false;
  }
}

export async function initKnowledge() {
  if (knowledgeInited) return;
  knowledgeInited = true;
  $("#kb-create-btn").addEventListener("click", createKb);
  $("#kb-doc-add-btn").addEventListener("click", addDocument);
  $("#kb-search-btn").addEventListener("click", runSearch);
  $("#kb-ask-btn").addEventListener("click", runAsk);
  attachTokenCounter($("#kb-question"));
  const exportBtn = $("#kb-export-btn");
  if (exportBtn) exportBtn.addEventListener("click", exportKb);
  const importBtn = $("#kb-import-btn");
  if (importBtn) importBtn.addEventListener("click", importKb);
  const imp = $("#kb-import-file");
  if (imp) imp.addEventListener("change", onImportFilePicked);
  $("#kb-list").addEventListener("change", async (e) => {
    currentKbId = e.target.value || null;
    await refreshDocs();
  });
  await refreshCapabilities();
  await refreshKbList();
  populateBrains().catch(() => {});
  window.addEventListener("kevrai:view-change", (e) => {
    if (e.detail && e.detail.view === "knowledge") {
      refreshCapabilities().catch(() => {});
      refreshKbList().catch(() => {});
    }
  });
}

// Exposed for tests / debugging.
export { currentKb };
