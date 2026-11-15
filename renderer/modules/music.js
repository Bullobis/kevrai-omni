// renderer/modules/music.js — 音乐生成面板：MusicGen / ACE-Step，按模型能力显示时长与歌词。
"use strict";
import { api } from "./api.js";
import { toast } from "./toast.js";
import { t } from "./i18n.js";

const $ = (s, r = document) => r.querySelector(s);

let musicInited = false;
let musicModels = [];

import { escapeHtml } from "./net.js";

async function refresh() {
  const root = $("#pane-music");
  if (!root) return;
  let cap = { installed: false, models: [] };
  try {
    const r = await api.musicCapabilities();
    cap = (r && r.body) ? r.body : r;
  } catch (_) { /* bridge error already toasted */ }
  root.classList.remove("engine-loading");
  root.classList.toggle("engine-missing", !cap.installed);
  const status = $("#music-engine-status", root);
  if (status) {
    status.textContent = cap.installed
      ? t("music.engineReady", { ver: cap.version || "" })
      : t("music.engineMissing");
    status.dataset.installed = cap.installed ? "1" : "0";
  }
  return cap;
}

async function populateModels() {
  const sel = $("#music-model");
  if (!sel) return;
  // Prefer the capability-declared MusicGen models; fall back to catalog.
  let items = [];
  try {
    const r = await api.musicCapabilities();
    const cap = (r && r.body) ? r.body : r;
    items = (cap && cap.models) || [];
  } catch (_) { /* ignore */ }
  if (items.length) {
    musicModels = items;
    sel.innerHTML = items.map(
      (m) => `<option value="${escapeHtml(m.id)}">${escapeHtml(m.id)} · ${escapeHtml(m.license || "unknown")}</option>`
    ).join("");
    applyMusicModelCapability();
    return;
  }
  try {
    const r = await api.models({ category: "audio" });
    const all = (r && r.body) ? r.body : r;
    const rows = (all && all.items) ? all.items : (all || []);
    const music = rows.filter(
      (m) => (m.engine || []).includes("transformers") &&
             m.id.toLowerCase().includes("musicgen")
    );
    if (music.length) {
      sel.innerHTML = music.map(
        (m) => `<option value="${escapeHtml(m.id)}">${escapeHtml(m.name)}</option>`
      ).join("");
    }
  } catch (_) { /* keep static */ }
}

function currentMusicModel() {
  const id = $("#music-model")?.value || "";
  return musicModels.find((m) => m.id === id) || null;
}

function ensureDurationOptions(maxS) {
  const sel = $("#music-duration");
  if (!sel) return;
  const max = Math.max(5, Number(maxS) || 60);
  const values = [5, 10, 15, 30, 60, 120, 300, 600].filter((v) => v <= max);
  const current = Number(sel.value) || 15;
  sel.innerHTML = values.map((v) => `<option value="${v}">${v}</option>`).join("");
  sel.value = String(Math.min(current, max));
}

function applyMusicModelCapability() {
  const m = currentMusicModel();
  const lyricsRow = $("#music-lyrics-row");
  const hint = $("#music-model-capability");
  if (lyricsRow) lyricsRow.hidden = !(m && m.supports_lyrics);
  if (m) {
    ensureDurationOptions(m.duration_max_s);
    if (hint) {
      hint.textContent = `${m.license || "unknown"} · ${t("music.durationMax", { max: m.duration_max_s || 60 })}` +
        (m.commercial === "false" ? ` · ${t("music.nonCommercial")}` : "");
      hint.hidden = false;
    }
    const durHint = $("#music-duration-hint");
    if (durHint) durHint.textContent = t("music.durationMax", { max: m.duration_max_s || 60 });
  } else {
    if (hint) { hint.hidden = true; hint.textContent = ""; }
  }
}

function renderResult(res) {
  const out = $("#music-result");
  if (!out) return;
  const data = (res && res.body) ? res.body : res;
  const src = data.data_url || "";
  const audio = src
    ? `<audio controls src="${src}" class="music-audio"></audio>`
    : "";
  out.innerHTML =
    `<h3 class="mm-answer-title">${escapeHtml(t("music.result"))}</h3>` +
    audio +
    `<div class="music-meta muted">${escapeHtml(
      t("music.meta", {
        dur: Number(data.duration_s || 0).toFixed(0),
        elapsed: Number(data.elapsed_s || 0).toFixed(1),
      })
    )}</div>` +
    `<div class="form-actions">` +
    `<button id="music-open-dir" class="ghost">${escapeHtml(t("music.openDir"))}</button>` +
    (src ? `<a class="ghost music-download" href="${src}" download="${escapeHtml(data.file || "music.wav")}">${escapeHtml(t("music.download"))}</a>` : "") +
    `</div>`;
  const openBtn = $("#music-open-dir", out);
  if (openBtn) {
    openBtn.addEventListener("click", async () => {
      try {
        await api.openPath(data.audio_path || data.output_dir || "");
      } catch (e) {
        toast(e.message, { kind: "err" });
      }
    });
  }
}

async function generate() {
  const prompt = ($("#music-prompt").value || "").trim();
  if (!prompt) {
    toast(t("music.promptFirst"), { kind: "warn" });
    return;
  }
  const model = $("#music-model").value;
  const duration_s = Number($("#music-duration").value);
  const guidance_scale = Number($("#music-guidance").value);
  const modelInfo = currentMusicModel();
  if (modelInfo && duration_s > Number(modelInfo.duration_max_s || 60)) {
    toast(t("music.durationTooLong", { max: modelInfo.duration_max_s }), { kind: "warn" });
    return;
  }
  const lyrics = (modelInfo && modelInfo.supports_lyrics)
    ? ($("#music-lyrics").value || "").trim()
    : "";
  const btn = $("#music-run");
  btn.disabled = true;
  btn.textContent = t("music.generating");
  try {
    const res = await api.musicGenerate({
      model, prompt, lyrics, duration_s, guidance_scale, do_sample: true,
    });
    renderResult(res);
  } catch (e) {
    toast(e.message, { kind: "err" });
  } finally {
    btn.disabled = false;
    btn.textContent = t("music.run");
  }
}

async function installEngine() {
  const btn = $("#music-install");
  btn.disabled = true;
  try {
    await api.installEngine("transformers");
    toast(t("music.engineInstalled"), { kind: "ok" });
    await refresh();
    await populateModels();
  } catch (e) {
    toast(e.message, { kind: "err" });
  } finally {
    btn.disabled = false;
  }
}

export async function initMusic() {
  if (musicInited) return;
  musicInited = true;
  $("#music-run").addEventListener("click", generate);
  $("#music-model").addEventListener("change", applyMusicModelCapability);
  $("#music-install").addEventListener("click", installEngine);
  await refresh();
  await populateModels();
  window.addEventListener("kevrai:view-change", (e) => {
    if (e.detail && e.detail.view === "music") {
      refresh().catch(() => {});
    }
  });
}
