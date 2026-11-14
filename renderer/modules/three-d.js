// renderer/modules/three-d.js — 3D 资产生成面板（TripoSR / TRELLIS / Hunyuan3D）。
//
// 后端 /api/3d/* 早已就绪，但侧栏一直没有入口。本面板把它接到界面：
//   1. 先 capabilities 探测本机 torch/diffusers/cuda 是否齐备，不齐给引导卡；
//   2. 选模型 → 选模式（triposr 只支持图生 3D，切到文生时明确禁用并提示）；
//   3. 提交后轮询任务进度，完成后列出 GLB 输出，可打开输出目录。
//
// GLB 内嵌预览需要 three.js 打包管线，首版不做模型查看器，先保证能跑通、
// 产物落盘可被外部查看器打开。
"use strict";
import { api } from "./api.js";
import { toast } from "./toast.js";
import { t } from "./i18n.js";

const $ = (s, r = document) => r.querySelector(s);

let inited = false;
let pollTimer = null;

// 常用模型预设（id 即 HuggingFace repo id；后端 from_pretrained 直接加载）。
// 全部选公开可下（非 gated）的仓库，避免用户卡在 HF 登录。
// vramMin 为公开文档/后端 MODEL_LICENSES 标注的最低显存（GB），用于本机适配提示。
const MODELS = [
  { id: "stabilityai/TripoSR", label: "TripoSR（MIT，最快，仅图生）", t23d: false, vramMin: 6, license: "MIT", commercial: true, note: "纯 MIT，无地区限制；仅顶点色无 PBR。" },
  { id: "JeffreyXiang/TRELLIS-image-large", label: "TRELLIS（MIT，PBR 网格，仅图生）", t23d: false, vramMin: 16, license: "MIT", commercial: true, note: "MIT；只支持单图输入，不支持文生 3D。" },
  { id: "tencent/Hunyuan3D-2", label: "Hunyuan3D 2（腾讯社区许可，质量高）", t23d: true, vramMin: 10, license: "腾讯社区许可", commercial: "conditional", note: "非 MIT：有月活上限，且商用排除欧盟/英国/韩国；文生 3D 实为串联文生图。" },
  { id: "kevrai/preview-3d", label: "轻量预览（离线，非 AI，秒出 GLB）", t23d: true, vramMin: 0, license: "built-in", commercial: true, note: "无需显卡/权重，确定性生成可打开的网格预览。" },
  { id: "openai/shap-e", label: "Shap-E（MIT，文生 3D 轻量基线）", t23d: true, vramMin: 6, license: "MIT", commercial: true, note: "Diffusers 原生 ShapEPipeline；速度快，质量中等。" },
  { id: "TencentARC/InstantMesh", label: "InstantMesh（Apache-2.0，单图前馈）", t23d: false, vramMin: 8, license: "Apache-2.0", commercial: true, note: "需按官方仓库安装 InstantMesh 代码；单图重建。" },
  { id: "VAST-AI/MIDI-3D", label: "MIDI-3D（Apache-2.0，多视图）", t23d: false, vramMin: 8, license: "Apache-2.0", commercial: true, note: "官方主路径是多张参考图；单图仅作粗略预览。" },
];

// 本机最佳显存（GB），init 时探测一次。
let localVram = null;

function body(res) { return (res && res.body) ? res.body : res; }
import { escapeHtml } from "./net.js";

function currentModel() {
  const sel = $("#threed-model");
  const id = sel ? sel.value : MODELS[0].id;
  return MODELS.find((m) => m.id === id) || MODELS[0];
}

function syncModeAvailability() {
  const m = currentModel();
  const t2 = $("#threed-mode-t23d");
  if (!t2) return;
  t2.disabled = !m.t23d;
  if (t2.disabled && t2.checked) {
    $("#threed-mode-i23d").checked = true;
  }
  syncModeFields();
  updateFit();
}

function syncModeFields() {
  const mode = currentMode();
  const imageField = $("#threed-image-field");
  const promptField = $("#threed-prompt-field");
  if (imageField) imageField.hidden = mode !== "i23d";
  if (promptField) promptField.hidden = mode !== "t23d";
}

// 本机显存 vs 模型需求：显示「跑得动哪档」与许可/商用提示。
function updateFit() {
  const bar = $("#threed-fit-bar");
  if (!bar) return;
  const m = currentModel();
  let verdict, color, detail;
  if (localVram === null) {
    verdict = t("threed.fitUnknown"); color = "var(--mut,#6b7280)"; detail = "";
  } else if (localVram <= 0) {
    verdict = t("threed.fitNoGpu"); color = "var(--warn,#b45309)"; detail = "";
  } else if (localVram >= m.vramMin) {
    verdict = t("threed.fitOk", { gb: localVram, need: m.vramMin });
    color = "var(--ok,#15803d)"; detail = "";
  } else if (localVram >= m.vramMin * 0.7) {
    verdict = t("threed.fitTight", { gb: localVram, need: m.vramMin });
    color = "var(--warn,#b45309)"; detail = t("threed.fitTightHint");
  } else {
    verdict = t("threed.fitShort", { gb: localVram, need: m.vramMin });
    color = "var(--danger,#b91c1c)"; detail = t("threed.fitShortHint");
  }
  const comm = m.commercial === true
    ? `<span class="pill pill-ok">${escapeHtml(t("threed.licenseOk"))}</span>`
    : m.commercial === "conditional"
      ? `<span class="pill pill-warn">${escapeHtml(t("threed.licenseCond"))}</span>`
      : "";
  bar.innerHTML =
    `<div class="fit-line" style="color:${color}">● ${escapeHtml(verdict)}</div>` +
    `<div class="fit-detail muted">${escapeHtml(m.note)} ${detail}</div>` +
    `<div class="fit-lic"><span class="pill">${escapeHtml(m.license)}</span> ${comm}</div>`;
}

async function detectLocalVram() {
  try {
    const res = body(await api.hardware({}));
    // /api/hardware 返回 {"hardware": {...}, "cached": bool}；硬件字段在 .hardware 里。
    const hw = (res && res.hardware) ? res.hardware : res;
    const v = hw && (hw.gpu_best_vram_gb ?? (hw.gpu && hw.gpu.best_vram_gb) ?? null);
    localVram = (typeof v === "number" && isFinite(v)) ? v : null;
  } catch (_) {
    localVram = null;
  }
}

async function refreshCaps() {
  const status = $("#threed-engine-status");
  const missingCard = $("#threed-missing-card");
  const form = $("#threed-form");
  try {
    const cap = body(await api.threeDCapabilities());
    const ok = !!(cap && cap.available);
    const missing = Array.isArray(cap && cap.missing)
      ? cap.missing.filter(Boolean).join(", ")
      : "";
    if (status) status.textContent = ok
      ? t("threed.engineReady")
      : (missing ? `${t("threed.notReady")} (${missing})` : t("threed.notReady"));
    if (missingCard) {
      missingCard.style.display = ok ? "none" : "";
      const hint = $("#threed-install-hint");
      if (hint && cap && cap.install_hint) hint.textContent = cap.install_hint;
    }
    if (form) form.style.display = ok ? "" : "none";
    return ok;
  } catch (e) {
    if (missingCard) missingCard.style.display = "";
    if (form) form.style.display = "none";
    return false;
  }
}

function populateModels() {
  const sel = $("#threed-model");
  if (!sel) return;
  sel.innerHTML = MODELS.map((m) =>
    `<option value="${escapeHtml(m.id)}">${escapeHtml(m.label)}</option>`).join("");
  syncModeAvailability();
}

function currentMode() {
  return $("#threed-mode-t23d") && $("#threed-mode-t23d").checked ? "t23d" : "i23d";
}

async function generate() {
  const mode = currentMode();
  const model = currentModel();
  const prompt = ($("#threed-prompt").value || "").trim();
  const imagePath = ($("#threed-image-path").value || "").trim();

  if (mode === "t23d" && !prompt) {
    toast(t("threed.promptFirst"), { kind: "warn" });
    return;
  }
  if (mode === "i23d" && !imagePath) {
    toast(t("threed.imageFirst"), { kind: "warn" });
    return;
  }

  const btn = $("#threed-run");
  btn.disabled = true;
  btn.textContent = t("threed.working");
  try {
    const res = body(await api.threeDGenerate({
      mode,
      model_id: model.id,
      prompt: prompt || "a object",
      image_path: imagePath,
      preset: $("#threed-preset").value || "fast",
      bake_texture: true,
      remove_background: true,
    }));
    const task = (res && res.task) || res;
    if (task && task.id) {
      startPolling(task.id);
    } else {
      toast(t("threed.submitFailed"), { kind: "err" });
      btn.disabled = false;
      btn.textContent = t("threed.run");
    }
  } catch (e) {
    toast(e.message, { kind: "err" });
    btn.disabled = false;
    btn.textContent = t("threed.run");
  }
}

function renderTask(task) {
  const out = $("#threed-result");
  if (!out || !task) return;
  const pct = Math.round((task.progress || 0) * 100);
  const state = task.state;
  let html = `<h3 class="mm-answer-title">${escapeHtml(t("threed.task"))} · ${escapeHtml(state)} (${pct}%)</h3>`;
  if (task.step) html += `<p class="muted">${escapeHtml(task.step)}</p>`;
  html += `<div class="adv-row"><div class="mm-progress"><div class="mm-progress-bar" style="width:${pct}%"></div></div></div>`;
  if (state === "done" || task.output_path) {
    html += `<p class="muted">${escapeHtml(t("threed.done", { path: task.output_path || "" }))}</p>`;
    if (task.mesh_faces) html += `<p class="muted">${escapeHtml(t("threed.mesh", { faces: task.mesh_faces, verts: task.mesh_vertices || 0 }))}</p>`;
  }
  if (state === "error") {
    html += `<p class="muted" style="color:var(--danger,#b91c1c)">${escapeHtml(task.error || "")}</p>`;
  }
  out.innerHTML = html;

  if (state === "done" || state === "error" || state === "cancelled") {
    stopPolling();
    const btn = $("#threed-run");
    if (btn) { btn.disabled = false; btn.textContent = t("threed.run"); }
  }
}

async function pollOnce(taskId) {
  try {
    const res = body(await api.threeDTask(taskId));
    renderTask(res && (res.task || res));
  } catch (_) { /* 轮询瞬时失败不中断 */ }
}

function startPolling(taskId) {
  stopPolling();
  pollTimer = setInterval(() => pollOnce(taskId), 2000);
  pollOnce(taskId);
}
function stopPolling() {
  if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
}

export async function initThreeD() {
  if (inited) return;
  inited = true;
  populateModels();
  await refreshCaps();
  await detectLocalVram();
  updateFit();

  $("#threed-model").addEventListener("change", () => { syncModeAvailability(); });
  ["threed-mode-i23d", "threed-mode-t23d"].forEach((id) => {
    const el = document.getElementById(id);
    if (el) el.addEventListener("change", syncModeFields);
  });
  $("#threed-run").addEventListener("click", generate);
  const retry = $("#threed-retry");
  if (retry) retry.addEventListener("click", () => refreshCaps().catch(() => {}));

  window.addEventListener("kevrai:view-change", (e) => {
    if (e.detail && e.detail.view === "3d") {
      refreshCaps().catch(() => {});
    }
  });
}
