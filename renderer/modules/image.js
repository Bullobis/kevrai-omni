// renderer/modules/image.js — ComfyUI 图片生成（SD1.5 / SDXL 文生图）面板。
//
// 交互参考 Local Dream（手机端本地 SD）：先选模型家族（SD1.5 快 / SDXL 细节好），
// 自动带出合适的默认步数、CFG 与分辨率预设；小白只填提示词即可出图，
// 高级参数（采样器/调度器/种子/批量/负向）折叠在"高级"里。
//
// 依赖 ComfyUI 后端（默认本机 8188，可用环境变量 KEVRAI_COMFYUI_URL 指定）。
// 后端把模板参数组装成标准 SD 工作流，submit 后 wait 取回图片 data URL。
"use strict";
import { api } from "./api.js";
import { toast } from "./toast.js";
import { t } from "./i18n.js";

const $ = (s, r = document) => r.querySelector(s);

let imageInited = false;
let templates = [];

// 常用采样器 / 调度器（ComfyUI 内置；后端不做硬枚举，非法值由 ComfyUI 报错透传）。
const SAMPLERS = ["euler", "euler_ancestral", "dpmpp_2m", "dpmpp_2m_sde", "dpmpp_3m_sde", "lcm", "ddim"];
const SCHEDULERS = ["normal", "karras", "exponential", "simple", "beta"];

function escapeHtml(s) {
  return String(s == null ? "" : s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;")
    .replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

function body(res) {
  return (res && res.body) ? res.body : res;
}

/** 根据当前模板 id 取模板元数据。 */
function currentTemplate() {
  const sel = $("#image-template");
  const id = sel ? sel.value : "sd15-txt2img";
  return templates.find((x) => x.id === id) || templates[0] || null;
}

/** 把 "512x768" 解析成 [512, 768]；非法输入回落 [512,512]。纯函数，便于单测。 */
export function parseDimensions(s) {
  const parts = String(s || "512x512").split("x").map(Number);
  if (parts.length === 2 && parts.every((n) => Number.isFinite(n) && n > 0)) {
    return [parts[0], parts[1]];
  }
  return [512, 512];
}

/** 输入校验：返回 null 表示可用，否则返回 "prompt" | "ckpt"。纯函数。 */
export function validateInputs(prompt, ckpt) {
  if (!String(prompt || "").trim()) return "prompt";
  if (!String(ckpt || "").trim()) return "ckpt";
  return null;
}

/** 模板切换：回填默认步数/CFG/分辨率预设。 */
function applyTemplateDefaults() {
  const tmpl = currentTemplate();
  if (!tmpl) return;
  const resSel = $("#image-resolution");
  if (resSel && tmpl.resolutions) {
    resSel.innerHTML = tmpl.resolutions.map(([w, h]) =>
      `<option value="${w}x${h}">${w} × ${h}</option>`).join("");
    // 默认选中与模板默认宽高一致的那项
    const match = `${tmpl.width}x${tmpl.height}`;
    if (Array.from(resSel.options).some((o) => o.value === match)) resSel.value = match;
  }
  const steps = $("#image-steps");
  if (steps && !steps.dataset.userTouched) steps.value = tmpl.steps;
  const cfg = $("#image-cfg");
  if (cfg && !cfg.dataset.userTouched) cfg.value = tmpl.cfg;
}

async function refreshStatus() {
  const status = $("#image-engine-status");
  const missingCard = $("#image-missing-card");
  const form = $("#image-form");
  try {
    const s = body(await api.comfyuiStatus());
    const ok = !!(s && s.available);
    if (status) {
      status.textContent = ok
        ? t("image.engineReady", { url: s.base_url || "8188" })
        : t("image.engineMissing");
      status.dataset.installed = ok ? "1" : "0";
    }
    if (missingCard) missingCard.style.display = ok ? "none" : "";
    if (form) form.style.display = ok ? "" : "none";
    return ok;
  } catch (e) {
    if (missingCard) missingCard.style.display = "";
    if (form) form.style.display = "none";
    if (status) status.dataset.installed = "0";
    return false;
  }
}

async function populateTemplates() {
  const sel = $("#image-template");
  if (!sel) return;
  try {
    const r = body(await api.comfyuiImageTemplates());
    templates = (r && r.templates) || [];
  } catch (_) { templates = []; }
  sel.innerHTML = templates.map((x) =>
    `<option value="${escapeHtml(x.id)}">${escapeHtml(x.label)}</option>`).join("");
  applyTemplateDefaults();
}

async function populateCheckpoints() {
  const sel = $("#image-ckpt");
  const txt = $("#image-ckpt-text");
  if (!sel) return;
  let names = [];
  try {
    const r = body(await api.comfyuiCheckpoints());
    names = (r && r.checkpoints) || [];
  } catch (_) { names = []; }
  if (names.length) {
    sel.innerHTML = names.map((n) => `<option value="${escapeHtml(n)}">${escapeHtml(n)}</option>`).join("");
    sel.style.display = "";
    if (txt) txt.style.display = "none";
  } else {
    // 连不上或 ComfyUI 还没索引到模型：退回文本框让用户手填文件名。
    sel.style.display = "none";
    if (txt) txt.style.display = "";
  }
}

function fillSamplerDefaults() {
  const s = $("#image-sampler");
  const sch = $("#image-scheduler");
  if (s) s.innerHTML = SAMPLERS.map((x) => `<option value="${x}">${x}</option>`).join("");
  if (sch) sch.innerHTML = SCHEDULERS.map((x) => `<option value="${x}">${x}</option>`).join("");
}

function renderResult(res) {
  const out = $("#image-result");
  if (!out) return;
  const data = body(res);
  const images = (data && data.images) || [];
  if (!images.length) {
    out.innerHTML = `<p class="muted">${escapeHtml(t("image.noImage"))}</p>`;
    return;
  }
  const cards = images.map((im, i) => {
    if (im.error) {
      return `<figure class="img-card"><figcaption class="muted">${escapeHtml(im.filename)}: ${escapeHtml(im.error)}</figcaption></figure>`;
    }
    return `
      <figure class="img-card">
        <img src="${im.data_url}" alt="image ${i + 1}" />
        <figcaption>
          <span class="muted">${escapeHtml(im.filename || "")}</span>
          <a class="ghost img-download" href="${im.data_url}" download="${escapeHtml(im.filename || "kevrai.png")}">
            ${escapeHtml(t("image.download"))}
          </a>
        </figcaption>
      </figure>`;
  }).join("");
  const seed = data && data.params ? data.params.seed : "";
  out.innerHTML =
    `<h3 class="mm-answer-title">${escapeHtml(t("image.result"))}</h3>` +
    (seed ? `<p class="muted">${escapeHtml(t("image.seedInfo", { seed }))}</p>` : "") +
    `<div class="img-gallery">${cards}</div>`;
}

async function generate() {
  const prompt = ($("#image-prompt").value || "").trim();
  const ckpt = ($("#image-ckpt").value || $("#image-ckpt-text")?.value || "").trim();
  const err = validateInputs(prompt, ckpt);
  if (err === "prompt") {
    toast(t("image.promptFirst"), { kind: "warn" });
    return;
  }
  if (err === "ckpt") {
    toast(t("image.ckptFirst"), { kind: "warn" });
    return;
  }
  const tmpl = currentTemplate() || {};
  const [w, h] = parseDimensions($("#image-resolution").value);
  const btn = $("#image-run");
  btn.disabled = true;
  btn.textContent = t("image.generating");
  try {
    const params = {
      template_id: tmpl.id || "sd15-txt2img",
      ckpt_name: ckpt,
      prompt,
      negative_prompt: ($("#image-negative").value || "").trim() || null,
      steps: Number($("#image-steps").value),
      cfg: Number($("#image-cfg").value),
      sampler_name: $("#image-sampler").value,
      scheduler: $("#image-scheduler").value,
      width: w,
      height: h,
      batch_size: Number($("#image-batch").value),
      seed: Number($("#image-seed").value.trim() === "" ? -1 : $("#image-seed").value),
      filename_prefix: "kevrai",
    };
    // 优先走流式 WS（实时采样进度）；连不上时回退同步等待。
    try {
      await generateViaStream(params);
    } catch (streamErr) {
      const res = await api.comfyuiImageGenerate({ ...params, wait: true, include_images: true });
      renderResult(res);
    }
  } catch (e) {
    toast(e.message, { kind: "err" });
  } finally {
    btn.disabled = false;
    btn.textContent = t("image.run");
    hideProgress();
  }
}

// 流式文生图：连 sidecar /ws/comfyui/generate，逐帧显示采样进度。
function generateViaStream(params) {
  return api.agentWsInfo().then((info) => new Promise((resolve, reject) => {
    const url = `${info.baseUrl}/ws/comfyui/generate?token=${encodeURIComponent(info.token)}`;
    const ws = new WebSocket(url);
    let settled = false;
    ws.onopen = () => ws.send(JSON.stringify(params));
    ws.onmessage = (ev) => {
      let m;
      try { m = JSON.parse(ev.data); } catch { return; }
      if (m.type === "progress") {
        setProgress(m.step || 0, m.max || 1);
      } else if (m.type === "done") {
        settled = true;
        renderResult({ images: m.images || [], params: m.params || {} });
        try { ws.close(); } catch {}
        resolve();
      } else if (m.type === "error") {
        settled = true;
        try { ws.close(); } catch {}
        reject(new Error(m.message || "生成失败"));
      }
    };
    ws.onerror = () => { if (!settled) reject(new Error("stream error")); };
    ws.onclose = () => { if (!settled) reject(new Error("stream closed")); };
  }));
}

// 采样进度条（动态插入生成按钮下方）。
function setProgress(step, max) {
  const box = ensureProgressEl();
  const pct = Math.min(100, Math.round((step / Math.max(1, max)) * 100));
  box.innerHTML = `<div class="progress"><div class="progress-bar" style="width:${pct}%"></div></div>` +
    `<p class="muted" style="margin:.4rem 0 0">采样中 ${step}/${max}（${pct}%）</p>`;
}
function ensureProgressEl() {
  let el = $("#image-progress");
  if (el) return el;
  el = document.createElement("div");
  el.id = "image-progress";
  el.style.marginTop = ".5rem";
  const run = $("#image-run");
  if (run && run.parentNode) run.parentNode.insertBefore(el, run.nextSibling);
  return el;
}
function hideProgress() {
  const el = $("#image-progress");
  if (el) el.remove();
}

async function freeMemory() {
  const btn = $("#image-free");
  if (btn) btn.disabled = true;
  try {
    await api.comfyuiFree();
    toast(t("image.freed"), { kind: "ok" });
  } catch (e) {
    toast(e.message, { kind: "err" });
  } finally {
    if (btn) btn.disabled = false;
  }
}

export async function initImage() {
  if (imageInited) return;
  imageInited = true;

  fillSamplerDefaults();
  await populateTemplates();
  await populateCheckpoints();
  await refreshStatus();

  $("#image-template").addEventListener("change", applyTemplateDefaults);
  $("#image-steps").addEventListener("input", () => { $("#image-steps").dataset.userTouched = "1"; });
  $("#image-cfg").addEventListener("input", () => { $("#image-cfg").dataset.userTouched = "1"; });
  $("#image-run").addEventListener("click", generate);
  const freeBtn = $("#image-free");
  if (freeBtn) freeBtn.addEventListener("click", freeMemory);

  // 重切回本页时刷新连接状态与模型列表（ComfyUI 可能刚启动）。
  window.addEventListener("kevrai:view-change", (e) => {
    if (e.detail && e.detail.view === "image") {
      refreshStatus().then((ok) => { if (ok) populateCheckpoints(); }).catch(() => {});
    }
  });
}
