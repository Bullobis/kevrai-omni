// renderer/modules/piper.js — 本地 TTS 工作台：Piper / Kokoro / Chatterbox。
"use strict";
import { api } from "./api.js";
import { toast } from "./toast.js";
import { t } from "./i18n.js";

const $ = (s, r = document) => r.querySelector(s);

let piperInited = false;
let lastJob = null;
let cap = { installed: false, voices: [], kokoro: {}, chatterbox: {} };
const objectUrls = [];

const SPEED_OPTIONS = [
  { value: "0.85", labelKey: "piper.fast" },
  { value: "1.0", labelKey: "piper.normal" },
  { value: "1.2", labelKey: "piper.slow" },
];

const ENGINES = [
  { id: "piper", label: "Piper" },
  { id: "kokoro", label: "Kokoro" },
  { id: "chatterbox", label: "Chatterbox" },
];

import { escapeHtml } from "./net.js";

function isInstalled(engine) {
  if (engine === "piper") return Boolean(cap.installed);
  return Boolean(cap[engine] && cap[engine].installed);
}

function currentEngine() {
  return $("#piper-engine")?.value || "piper";
}

function voicesFor(engine) {
  if (engine === "piper") return cap.voices || [];
  if (engine === "kokoro") return (cap.kokoro && cap.kokoro.voices) || [];
  if (engine === "chatterbox") {
    return ((cap.chatterbox && cap.chatterbox.cloned_voices) || []).map((v) => ({
      id: v.id || `chatterbox:${v.name}`,
      label: v.name,
    }));
  }
  return [];
}

function refreshVoices() {
  const engine = currentEngine();
  const sel = $("#piper-voice");
  const voices = voicesFor(engine);
  if (sel) {
    sel.innerHTML = voices.length
      ? voices.map((v) => `<option value="${escapeHtml(v.id)}">${escapeHtml(v.label)}</option>`).join("")
      : `<option value="">${escapeHtml(t("piper.cloneEmpty"))}</option>`;
  }
  const hint = $("#piper-engine-hint");
  if (hint) {
    const name = ENGINES.find((e) => e.id === engine)?.label || engine;
    hint.textContent = isInstalled(engine)
      ? t("piper.engineReadyShort", { name })
      : t("piper.engineMissingShort", { name });
  }
  const status = $("#piper-engine-status");
  if (status) {
    const name = ENGINES.find((e) => e.id === engine)?.label || engine;
    status.textContent = isInstalled(engine)
      ? t("piper.engineReadyShort", { name })
      : t("piper.engineMissingShort", { name });
  }
  const install = $("#piper-engine-install");
  if (install) install.hidden = isInstalled(engine);
  const cloneCard = $("#chatterbox-clone-card");
  if (cloneCard) cloneCard.hidden = engine !== "chatterbox";
  const cloneSel = $("#chatterbox-clone");
  if (cloneSel) {
    const clones = (cap.chatterbox && cap.chatterbox.cloned_voices) || [];
    cloneSel.innerHTML = clones.length
      ? clones.map((v) => `<option value="${escapeHtml(v.id || `chatterbox:${v.name}`)}">${escapeHtml(v.name)}</option>`).join("")
      : `<option value="">${escapeHtml(t("piper.cloneEmpty"))}</option>`;
  }
  const missingCard = $("#piper-missing-card");
  if (missingCard) missingCard.hidden = Boolean(cap.installed);
  // Kokoro/Chatterbox 的语速参数尚未透传，只有 Piper 显示语速。
  const speedRow = $("#piper-speed")?.closest(".form-row");
  if (speedRow) speedRow.hidden = engine !== "piper";
}

async function refresh() {
  const root = $("#pane-piper");
  if (!root) return;
  try {
    const r = await api.piperCapabilities();
    const body = (r && r.body) ? r.body : r;
    if (body && typeof body === "object") cap = body;
  } catch (_) { /* bridge error toasted */ }
  root.classList.toggle("engine-missing", !cap.installed);
  const sel = $("#piper-engine");
  if (sel) {
    const prev = sel.value || "piper";
    sel.innerHTML = ENGINES.map((e) => {
      const suffix = isInstalled(e.id) ? "" : ` · ${t("piper.engineMissing")}`;
      return `<option value="${e.id}">${escapeHtml(e.label + suffix)}</option>`;
    }).join("");
    sel.value = ENGINES.some((e) => e.id === prev) ? prev : "piper";
  }
  refreshVoices();
}

async function run() {
  const text = $("#piper-text").value.trim();
  if (!text) {
    toast(t("piper.enterFirst"), { kind: "warn" });
    return;
  }
  const engine = currentEngine();
  if (!isInstalled(engine)) {
    toast(t("piper.engineMissingShort", { name: engine }), { kind: "warn" });
    return;
  }
  const voice = $("#piper-voice").value;
  if (!voice) {
    toast(t("piper.cloneEmpty"), { kind: "warn" });
    return;
  }
  const btn = $("#piper-run");
  btn.disabled = true;
  try {
    const job = await api.piperSynthesize({
      voice_id: voice,
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
      <span>${escapeHtml(job.voice || job.engine)}</span>
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

async function installEngine(engine) {
  const btn = $("#piper-engine-install");
  btn.disabled = true;
  const old = btn.textContent;
  btn.textContent = t("piper.installing");
  try {
    await api.installEngine(engine === "kokoro" ? "kokoro-engine" : engine);
    toast(t("piper.engineInstalled"), { kind: "ok" });
    await refresh();
  } catch (e) {
    toast(e.message, { kind: "err" });
  } finally {
    btn.disabled = false;
    btn.textContent = old;
  }
}

async function uploadClone() {
  const name = $("#chatterbox-name").value.trim();
  const file = $("#chatterbox-ref").files?.[0];
  if (!name || !file) {
    toast(t("piper.cloneNeedName"), { kind: "warn" });
    return;
  }
  try {
    const data = new Uint8Array(await file.arrayBuffer());
    await api.piperCreateClone({
      name,
      file: { filename: file.name, contentType: file.type || "audio/wav", data },
    });
    toast(t("piper.cloneSaved"), { kind: "ok" });
    $("#chatterbox-name").value = "";
    $("#chatterbox-ref").value = "";
    await refresh();
  } catch (e) {
    toast(t("piper.cloneFailed", { err: e.message }), { kind: "err" });
  }
}

async function deleteClone() {
  const value = $("#chatterbox-clone").value;
  if (!value) return;
  const name = value.replace(/^chatterbox:/, "");
  try {
    await api.piperDeleteClone(name);
    toast(t("piper.cloneDeleted"), { kind: "ok" });
    await refresh();
  } catch (e) {
    toast(t("piper.cloneFailed", { err: e.message }), { kind: "err" });
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
  $("#piper-install").addEventListener("click", () => installEngine("piper"));
  $("#piper-engine").addEventListener("change", refreshVoices);
  $("#piper-engine-install").addEventListener("click", () => installEngine(currentEngine()));
  $("#chatterbox-upload").addEventListener("click", uploadClone);
  $("#chatterbox-delete").addEventListener("click", deleteClone);
  await refresh();
  window.addEventListener("kevrai:view-change", (e) => {
    if (e.detail && e.detail.view === "piper") refresh().catch(() => {});
  });
  window.addEventListener("beforeunload", () => {
    objectUrls.forEach((u) => URL.revokeObjectURL(u));
  });
}

export { refresh as refreshPiper };
