// renderer/modules/asr.js — Faster Whisper speech recognition panel.
"use strict";
import { api } from "./api.js";
import { toast } from "./toast.js";
import { t } from "./i18n.js";

const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));

let asrInited = false;
let pickedFile = null;

const LANGUAGES = [
  ["", "自动检测"], ["en", "English"], ["zh", "中文"], ["ja", "日本語"],
  ["ko", "한국어"], ["fr", "Français"], ["de", "Deutsch"], ["es", "Español"],
  ["ru", "Русский"], ["pt", "Português"], ["it", "Italiano"], ["ar", "العربية"],
  ["hi", "हिन्दी"], ["th", "ไทย"], ["vi", "Tiếng Việt"], ["id", "Indonesia"],
];

function escapeHtml(s) {
  return String(s == null ? "" : s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;")
    .replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

function fmtTime(v) {
  const ms = Math.round((v - Math.floor(v)) * 1000);
  const h = Math.floor(v / 3600);
  const m = Math.floor((v % 3600) / 60);
  const s = Math.floor(v % 60);
  return `${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}:` +
    `${String(s).padStart(2, "0")}.${String(ms).padStart(3, "0")}`;
}

async function refresh() {
  const root = $("#pane-asr");
  if (!root) return;
  let cap = { installed: false };
  try {
    cap = await api.asrCapabilities();
  } catch (_) { /* bridge error already toasted */ }
  root.classList.toggle("engine-missing", !cap.installed);
  const status = $("#asr-engine-status", root);
  if (status) {
    status.textContent = cap.installed
      ? t("asr.engineReady", { ver: cap.version || "" })
      : t("asr.engineMissing");
    status.dataset.installed = cap.installed ? "1" : "0";
  }
}

async function populateModels() {
  const sel = $("#asr-model");
  if (!sel) return;
  try {
    const all = await api.models({ category: "audio" });
    const items = (all.items || all).filter(
      (m) => (m.engine || []).includes("faster-whisper")
    );
    if (items.length) {
      sel.innerHTML = items.map(
        (m) => `<option value="${escapeHtml(m.repo)}">${escapeHtml(m.name)}</option>`
      ).join("");
    }
  } catch (_) { /* keep static options */ }
}

function renderResult(res, format) {
  const out = $("#asr-result");
  if (!out) return;
  if (format === "srt" || format === "vtt") {
    out.innerHTML = `<pre class="asr-pre">${escapeHtml(res.body)}</pre>`;
    return;
  }
  if (format === "text") {
    out.innerHTML = `<p class="asr-text">${escapeHtml(res.body)}</p>`;
    return;
  }
  const data = res.body;
  const text = data.text || "";
  const meta = data.language
    ? `<div class="asr-meta"><span>${escapeHtml(data.language)}</span>` +
      `<span>${Number(data.duration || 0).toFixed(1)}s</span></div>`
    : "";
  const segs = (data.segments || []);
  const rows = segs.length
    ? `<table class="asr-table"><tbody>${segs.map((s) =>
        `<tr><td class="asr-td-time">${fmtTime(s.start)}</td>` +
        `<td>${escapeHtml(s.text)}</td></tr>`).join("")}</tbody></table>`
    : "";
  out.innerHTML = meta + `<p class="asr-text">${escapeHtml(text)}</p>` + rows;
}

async function transcribe() {
  const root = $("#pane-asr");
  if (!pickedFile) {
    toast(t("asr.pickFirst"), { kind: "warn" });
    return;
  }
  const model = $("#asr-model").value;
  const language = $("#asr-language").value;
  const task = $("#asr-task").value;
  const format = $("#asr-format").value;
  const btn = $("#asr-run");
  btn.disabled = true;
  try {
    const buf = await pickedFile.arrayBuffer();
    const res = await api.asr({
      model,
      task,
      language: language || null,
      response_format: format,
      word_timestamps: true,
      file: {
        filename: pickedFile.name,
        contentType: pickedFile.type || "audio/wav",
        data: new Uint8Array(buf),
      },
    });
    renderResult(res, format);
  } catch (e) {
    toast(t("asr.failed", { err: e.message }), { kind: "err" });
  } finally {
    btn.disabled = false;
  }
}

async function installEngine() {
  const btn = $("#asr-install");
  btn.disabled = true;
  try {
    await api.installEngine("faster-whisper");
    toast(t("asr.engineInstalled"), { kind: "ok" });
    await refresh();
  } catch (e) {
    toast(e.message, { kind: "err" });
  } finally {
    btn.disabled = false;
  }
}

export async function initAsr() {
  if (asrInited) return;
  asrInited = true;
  const fileInput = $("#asr-file");
  fileInput.addEventListener("change", () => {
    pickedFile = fileInput.files && fileInput.files[0] ? fileInput.files[0] : null;
    const name = $("#asr-file-name");
    if (name) name.textContent = pickedFile ? pickedFile.name : "";
  });
  $("#asr-run").addEventListener("click", transcribe);
  $("#asr-install").addEventListener("click", installEngine);
  const lang = $("#asr-language");
  lang.innerHTML = LANGUAGES.map(
    ([v, l]) => `<option value="${v}">${l}</option>`
  ).join("");
  await populateModels();
  await refresh();
}
