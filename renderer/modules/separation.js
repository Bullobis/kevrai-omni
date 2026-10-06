// renderer/modules/separation.js — Demucs music source-separation panel.
"use strict";
import { api } from "./api.js";
import { toast } from "./toast.js";
import { t } from "./i18n.js";

const $ = (s, r = document) => r.querySelector(s);

let sepInited = false;
let pickedFile = null;
let lastManifest = null;
const objectUrls = [];

const MODEL_LABELS = {
  htdemucs: "HTDemucs · 4 轨（推荐）",
  htdemucs_ft: "HTDemucs FT · 4 轨（最高质量，较慢）",
  htdemucs_6s: "HTDemucs 6S · 6 轨（含钢琴/吉他）",
};

function escapeHtml(s) {
  return String(s == null ? "" : s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;")
    .replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

async function refresh() {
  const root = $("#pane-separation");
  if (!root) return;
  let cap = { installed: false, models: [] };
  try {
    const r = await api.separationCapabilities();
    cap = (r && r.body) ? r.body : r;
  } catch (_) { /* bridge error already toasted */ }
  root.classList.toggle("engine-missing", !cap.installed);
  const status = $("#sep-engine-status", root);
  if (status) {
    status.textContent = cap.installed
      ? t("separation.engineReady", { ver: cap.version || "" })
      : t("separation.engineMissing");
  }
  const sel = $("#sep-model");
  if (sel && (cap.models || []).length) {
    sel.innerHTML = cap.models.map(
      (m) => `<option value="${escapeHtml(m)}">${escapeHtml(MODEL_LABELS[m] || m)}</option>`
    ).join("");
  }
}

async function run() {
  if (!pickedFile) {
    toast(t("separation.pickFirst"), { kind: "warn" });
    return;
  }
  const btn = $("#sep-run");
  btn.disabled = true;
  try {
    const buf = await pickedFile.arrayBuffer();
    const manifest = await api.separation({
      model: $("#sep-model").value,
      shifts: Number($("#sep-shifts").value),
      file: {
        filename: pickedFile.name,
        contentType: pickedFile.type || "audio/wav",
        data: new Uint8Array(buf),
      },
    });
    lastManifest = (manifest && manifest.body) ? manifest.body : manifest;
    await renderStems(lastManifest);
  } catch (e) {
    toast(t("separation.failed", { err: e.message }), { kind: "err" });
  } finally {
    btn.disabled = false;
  }
}

async function renderStems(manifest) {
  const out = $("#sep-result");
  const rows = [];
  for (const stem of manifest.stems) {
    let src = "";
    try {
      const bytes = await api.separationStream({
        job_id: manifest.job_id, stem: stem.file,
      });
      const blob = new Blob([bytes], { type: "audio/wav" });
      const url = URL.createObjectURL(blob);
      objectUrls.push(url);
      src = url;
    } catch (_) { /* player omitted if stream fails */ }
    rows.push(`<div class="sep-stem">
      <div class="sep-stem-h">
        <span>${escapeHtml(stem.name)}</span>
        <span class="muted">${stem.duration_s}s · ${(stem.size_bytes / 1e6).toFixed(1)}MB</span>
      </div>
      ${src ? `<audio controls preload="none" src="${src}"></audio>` : ""}
    </div>`);
  }
  out.innerHTML =
    `<div class="sep-meta muted">${t("separation.outputDir")}: ${escapeHtml(manifest.output_dir)}</div>` +
    rows.join("") +
    `<div class="sep-actions"><button id="sep-open-dir">${t("separation.openDir")}</button></div>`;
  const od = $("#sep-open-dir");
  if (od) od.addEventListener("click", async () => {
    try {
      await window.kevrai.openPath(manifest.output_dir);
    } catch (e) { toast(e.message, { kind: "err" }); }
  });
}

async function installEngine() {
  const btn = $("#sep-install");
  btn.disabled = true;
  try {
    await api.installEngine("demucs");
    toast(t("separation.engineInstalled"), { kind: "ok" });
    await refresh();
  } catch (e) {
    toast(e.message, { kind: "err" });
  } finally {
    btn.disabled = false;
  }
}

export async function initSeparation() {
  if (sepInited) return;
  sepInited = true;
  const fileInput = $("#sep-file");
  fileInput.addEventListener("change", () => {
    pickedFile = fileInput.files && fileInput.files[0] ? fileInput.files[0] : null;
    const name = $("#sep-file-name");
    if (name) name.textContent = pickedFile ? pickedFile.name : "";
  });
  $("#sep-run").addEventListener("click", run);
  $("#sep-install").addEventListener("click", installEngine);
  await refresh();
  window.addEventListener("kevrai:view-change", (e) => {
    if (e.detail && e.detail.view === "separation") refresh().catch(() => {});
  });
  window.addEventListener("beforeunload", () => {
    objectUrls.forEach((u) => URL.revokeObjectURL(u));
  });
}
