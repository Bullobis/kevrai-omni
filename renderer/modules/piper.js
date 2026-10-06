// renderer/modules/piper.js — Piper lightweight neural TTS workbench.
"use strict";
import { api } from "./api.js";
import { toast } from "./toast.js";
import { t } from "./i18n.js";

const $ = (s, r = document) => r.querySelector(s);

let piperInited = false;
let lastJob = null;
const objectUrls = [];

const SPEED_OPTIONS = [
  { value: "0.85", labelKey: "piper.fast" },
  { value: "1.0", labelKey: "piper.normal" },
  { value: "1.2", labelKey: "piper.slow" },
];

function escapeHtml(s) {
  return String(s == null ? "" : s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;")
    .replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

async function refresh() {
  const root = $("#pane-piper");
  if (!root) return;
  let cap = { installed: false, voices: [] };
  try {
    const r = await api.piperCapabilities();
    cap = (r && r.body) ? r.body : r;
  } catch (_) { /* bridge error toasted */ }
  root.classList.toggle("engine-missing", !cap.installed);
  const status = $("#piper-engine-status", root);
  if (status) {
    status.textContent = cap.installed
      ? t("piper.engineReady", { ver: cap.version || "" })
      : t("piper.engineMissing");
  }
  const sel = $("#piper-voice");
  if (sel && (cap.voices || []).length) {
    sel.innerHTML = cap.voices.map(
      (v) => `<option value="${escapeHtml(v.id)}">${escapeHtml(v.label)}</option>`
    ).join("");
  }
}

async function run() {
  const text = $("#piper-text").value.trim();
  if (!text) {
    toast(t("piper.enterFirst"), { kind: "warn" });
    return;
  }
  const btn = $("#piper-run");
  btn.disabled = true;
  try {
    const job = await api.piperSynthesize({
      voice_id: $("#piper-voice").value,
      text,
      length_scale: Number($("#piper-speed").value),
    });
    lastJob = (job && job.body) ? job.body : job;
    await renderResult(lastJob);
  } catch (e) {
    toast(t("piper.failed", { err: e.message }), { kind: "err" });
  } finally {
    btn.disabled = false;
  }
}

async function renderResult(job) {
  const out = $("#piper-result");
  let src = "";
  try {
    const bytes = await api.piperStream({ job_id: job.job_id });
    const blob = new Blob([bytes], { type: "audio/wav" });
    const url = URL.createObjectURL(blob);
    objectUrls.push(url);
    src = url;
  } catch (_) { /* player omitted on stream failure */ }
  out.innerHTML = `<div class="piper-stem">
    <div class="piper-stem-h">
      <span>${escapeHtml(job.voice)}</span>
      <span class="muted">${job.duration_s}s · ${(job.size_bytes / 1e6).toFixed(2)}MB</span>
    </div>
    ${src ? `<audio controls src="${src}"></audio>` : ""}
  </div>
  <div class="piper-actions"><button id="piper-open-dir">${t("piper.openDir")}</button></div>`;
  const od = $("#piper-open-dir");
  if (od) od.addEventListener("click", async () => {
    try {
      await window.kevrai.openPath(job.path.replace(/[^/]+$/, ""));
    } catch (e) { toast(e.message, { kind: "err" }); }
  });
}

async function installEngine() {
  const btn = $("#piper-install");
  btn.disabled = true;
  try {
    await api.installEngine("piper");
    toast(t("piper.engineInstalled"), { kind: "ok" });
    await refresh();
  } catch (e) {
    toast(e.message, { kind: "err" });
  } finally {
    btn.disabled = false;
  }
}

export async function initPiper() {
  if (piperInited) return;
  piperInited = true;
  const speed = $("#piper-speed");
  speed.innerHTML = SPEED_OPTIONS.map(
    (o) => `<option value="${o.value}">${t(o.labelKey)}</option>`
  ).join("");
  $("#piper-run").addEventListener("click", run);
  $("#piper-install").addEventListener("click", installEngine);
  await refresh();
  window.addEventListener("kevrai:view-change", (e) => {
    if (e.detail && e.detail.view === "piper") refresh().catch(() => {});
  });
  window.addEventListener("beforeunload", () => {
    objectUrls.forEach((u) => URL.revokeObjectURL(u));
  });
}
