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
];

// 本机最佳显存（GB），init 时探测一次。
let localVram = null;

function body(res) { return (res && res.body) ? res.body : res; }
function escapeHtml(s) {
  return String(s == null ? "" : s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;")
    .replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

function currentModel() {
  const sel = $("#3d-model");
  const id = sel ? sel.value : MODELS[0].id;
  return MODELS.find((m) => m.id === id) || MODELS[0];
}

function syncModeAvailability() {
  const m = currentModel();
  const t2 = $("#3d-mode-t23d");
  if (!t2) return;
  t2.disabled = !m.t23d;
  if (t2.disabled && t2.checked) {
    $("#3d-mode-i23d").checked = true;
  }
  updateFit();
}

// 本机显存 vs 模型需求：显示「跑得动哪档」与许可/商用提示。
function updateFit() {
  const bar = $("#3d-fit-bar");
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
  const status = $("#3d-engine-status");
  const missingCard = $("#3d-missing-card");
  const form = $("#3d-form");
  try {
    const cap = body(await api.threeDCapabilities());
    const ok = !!(cap && cap.available);
    if (status) status.textContent = ok
      ? t("threed.engineReady")
      : t("threed.engineMissing", { miss: (cap && cap.missing || []).join(", ") });
    if (missingCard) {
      missingCard.style.display = ok ? "none" : "";
      const hint = $("#3d-install-hint");
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
  const sel = $("#3d-model");
  if (!sel) return;
  sel.innerHTML = MODELS.map((m) =>
    `<option value="${escapeHtml(m.id)}">${escapeHtml(m.label)}</option>`).join("");
  syncModeAvailability();
}

function currentMode() {
  return $("#3d-mode-t23d") && $("#3d-mode-t23d").checked ? "t23d" : "i23d";
}

async function generate() {
  const mode = currentMode();
  const model = currentModel();
  const prompt = ($("#3d-prompt").value || "").trim();
  const imagePath = ($("#3d-image-path").value || "").trim();

  if (mode === "t23d" && !prompt) {
    toast(t("threed.promptFirst"), { kind: "warn" });
    return;
  }
  if (mode === "i23d" && !imagePath) {
    toast(t("threed.imageFirst"), { kind: "warn" });
    return;
  }

  const btn = $("#3d-run");
  btn.disabled = true;
  btn.textContent = t("threed.working");
  try {
    const res = body(await api.threeDGenerate({
      mode,
      model_id: model.id,
      prompt: prompt || "a object",
      image_path: imagePath,
      preset: $("#3d-preset").value || "fast",
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
  const out = $("#3d-result");
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
    const btn = $("#3d-run");
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

  $("#3d-model").addEventListener("change", () => { syncModeAvailability(); });
  $("#3d-run").addEventListener("click", generate);

  window.addEventListener("kevrai:view-change", (e) => {
    if (e.detail && e.detail.view === "3d") {
      refreshCaps().catch(() => {});
    }
  });
}
