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
  await refresh();
  window.addEventListener("kevrai:view-change", (e) => {
    if (e.detail && e.detail.view === "multimodal") refresh().catch(() => {});
  });
}
