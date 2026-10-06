// renderer/modules/multimodal.js — generic transformers image+text chat.
"use strict";
import { api } from "./api.js";
import { toast } from "./toast.js";
import { t } from "./i18n.js";

const $ = (s, r = document) => r.querySelector(s);

let mmInited = false;
let imageDataUrl = "";

function escapeHtml(s) {
  return String(s == null ? "" : s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;")
    .replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

async function refresh() {
  const root = $("#pane-multimodal");
  if (!root) return;
  let cap = { installed: false, models: [] };
  try {
    const r = await api.multimodalCapabilities();
    cap = (r && r.body) ? r.body : r;
  } catch (_) { /* bridge error toasted */ }
  root.classList.toggle("engine-missing", !cap.installed);
  const status = $("#mm-engine-status", root);
  if (status) {
    status.textContent = cap.installed
      ? t("multimodal.engineReady", { ver: cap.version || "" })
      : t("multimodal.engineMissing");
  }
  const sel = $("#mm-model");
  if (sel && (cap.models || []).length) {
    const current = sel.value;
    sel.innerHTML = cap.models.map(
      (m) => `<option value="${escapeHtml(m.id)}">${escapeHtml(m.id)}</option>`
    ).join("");
    if (current) sel.value = current;
  }
  const imgSel = $("#mm-img-model");
  if (imgSel) {
    const imgModels = (cap.models || []).filter((m) => m.image_generation);
    const current = imgSel.value;
    imgSel.innerHTML = imgModels.map(
      (m) => `<option value="${escapeHtml(m.id)}">${escapeHtml(m.id)}</option>`
    ).join("");
    if (current) imgSel.value = current;
    else if (imgModels.length) imgSel.value = imgModels[imgModels.length - 1].id;
  }
}

function setMode(mode) {
  const isImage = mode === "image";
  $("#mm-mode-understand").hidden = isImage;
  $("#mm-mode-image").hidden = !isImage;
  const bu = $("#mm-tab-understand");
  const bi = $("#mm-tab-image");
  bu.classList.toggle("active", !isImage);
  bi.classList.toggle("active", isImage);
  bu.setAttribute("aria-selected", String(!isImage));
  bi.setAttribute("aria-selected", String(isImage));
}

async function generateImage() {
  const prompt = $("#mm-img-prompt").value.trim();
  if (!prompt) {
    toast(t("multimodal.enterFirst"), { kind: "warn" });
    return;
  }
  const seedRaw = $("#mm-seed").value.trim();
  const btn = $("#mm-img-run");
  btn.disabled = true;
  const result = $("#mm-img-result");
  result.textContent = t("multimodal.thinking");
  try {
    const r = await api.multimodalGenerateImage({
      model: $("#mm-img-model").value,
      prompt,
      guidance_scale: Number($("#mm-guidance").value) || 5,
      seed: seedRaw === "" ? null : Number(seedRaw),
      num_images: Number($("#mm-img-count").value) || 1,
    });
    const body = (r && r.body) ? r.body : r;
    const items = body.images || [];
    if (!items.length) {
      result.textContent = t("multimodal.emptyAnswer");
      return;
    }
    result.innerHTML = items.map(
      (it) => `<figure class="mm-img-item">
        <img alt="${escapeHtml(it.file)}" src="${it.data_url}">
        <figcaption>${escapeHtml(it.file)}</figcaption>
      </figure>`
    ).join("");
  } catch (e) {
    result.textContent = "";
    toast(t("multimodal.failed", { err: e.message }), { kind: "err" });
  } finally {
    btn.disabled = false;
  }
}

function onPickFile(e) {
  const file = e.target.files && e.target.files[0];
  if (!file) return;
  const reader = new FileReader();
  reader.onload = () => {
    imageDataUrl = String(reader.result || "");
    const prev = $("#mm-preview");
    prev.innerHTML = `<img alt="" src="${imageDataUrl}">
      <button id="mm-clear-img" type="button">${t("multimodal.clearImage")}</button>`;
    $("#mm-clear-img").addEventListener("click", clearImage);
  };
  reader.readAsDataURL(file);
}

function clearImage() {
  imageDataUrl = "";
  const prev = $("#mm-preview");
  if (prev) prev.innerHTML = "";
  const picker = $("#mm-file");
  if (picker) picker.value = "";
}

async function ask() {
  const question = $("#mm-question").value.trim();
  if (!question && !imageDataUrl) {
    toast(t("multimodal.enterFirst"), { kind: "warn" });
    return;
  }
  const content = [];
  if (imageDataUrl) content.push({ type: "image_url", image_url: { url: imageDataUrl } });
  if (question) content.push({ type: "text", text: question });
  const messages = [{ role: "user", content }];

  const btn = $("#mm-run");
  btn.disabled = true;
  const answer = $("#mm-answer");
  answer.textContent = t("multimodal.thinking");
  try {
    const r = await api.multimodalChat({
      model: $("#mm-model").value,
      messages,
      max_tokens: Number($("#mm-max-tokens").value) || 512,
    });
    const body = (r && r.body) ? r.body : r;
    const text0 = body.choices && body.choices[0]
      ? body.choices[0].message.content : "";
    answer.textContent = text0 || t("multimodal.emptyAnswer");
  } catch (e) {
    answer.textContent = "";
    toast(t("multimodal.failed", { err: e.message }), { kind: "err" });
  } finally {
    btn.disabled = false;
  }
}

async function installEngine() {
  const btn = $("#mm-install");
  btn.disabled = true;
  try {
    await api.installEngine("transformers");
    toast(t("multimodal.engineInstalled"), { kind: "ok" });
    await refresh();
  } catch (e) {
    toast(e.message, { kind: "err" });
  } finally {
    btn.disabled = false;
  }
}

export async function initMultimodal() {
  if (mmInited) return;
  mmInited = true;
  $("#mm-file").addEventListener("change", onPickFile);
  $("#mm-pick-btn").addEventListener("click", () => $("#mm-file").click());
  $("#mm-run").addEventListener("click", ask);
  $("#mm-install").addEventListener("click", installEngine);
  $("#mm-tab-understand").addEventListener("click", () => setMode("understand"));
  $("#mm-tab-image").addEventListener("click", () => setMode("image"));
  $("#mm-img-run").addEventListener("click", generateImage);
  await refresh();
  window.addEventListener("kevrai:view-change", (e) => {
    if (e.detail && e.detail.view === "multimodal") refresh().catch(() => {});
  });
}
