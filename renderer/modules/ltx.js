// renderer/modules/ltx.js — LTX-2.5 video generation panel.
"use strict";
import { api } from "./api.js";
import { toast } from "./toast.js";

const $ = (s) => document.querySelector(s);

let cap = null;
let pollTimer = null;
let outputsTimer = null;

export async function initLtx() {
  document.getElementById("ltx-generate").addEventListener("click", generate);
  document.getElementById("ltx-cancel").addEventListener("click", cancelActive);
  document.getElementById("ltx-seed-random").addEventListener("click", () => {
    document.getElementById("ltx-seed").value = -1;
  });
  const pickImg = document.getElementById("ltx-pick-image");
  if (pickImg) pickImg.addEventListener("click", async () => {
    try {
      const p = await api.pickFile();
      if (p) document.getElementById("ltx-image").value = p;
    } catch (_) {}
  });
  document.getElementById("ltx-preset").addEventListener("change", applyPreset);
  document.getElementById("ltx-mode").addEventListener("change", toggleMode);
  document.getElementById("ltx-refresh-outputs").addEventListener("click", loadOutputs);
  document.getElementById("ltx-open-folder").addEventListener("click", openOutputsFolder);

  await loadCapabilities();
  await loadOutputs();
  startPolling();
}

async function loadCapabilities() {
  try {
    const r = await api.ltxCapabilities();
    cap = r?.body || r;
    renderCapabilities();
  } catch (e) {
    setStatus("无法加载 LTX-2.5 能力信息：" + (e.message || e), "error");
  }
}

function renderCapabilities() {
  const engineBadge = document.getElementById("ltx-engine-badge");
  if (cap.engine_ready) {
    engineBadge.textContent = cap.cuda_available ? "引擎就绪 · CUDA" : "引擎就绪 · CPU";
    engineBadge.className = "ltx-badge ok";
  } else {
    engineBadge.textContent = "引擎未安装";
    engineBadge.className = "ltx-badge warn";
  }
  // Populate presets
  const sel = document.getElementById("ltx-preset");
  sel.innerHTML = cap.presets.map((p) =>
    `<option value="${p.id}">${p.label}</option>`).join("");
  sel.value = "balanced";
  applyPreset();
  // Install hint
  const hint = document.getElementById("ltx-install-hint");
  if (!cap.engine_ready) {
    hint.hidden = false;
    hint.querySelector("code").textContent = cap.install_hint || "";
  } else {
    hint.hidden = true;
  }
  // Outputs dir
  const dirEl = document.getElementById("ltx-outputs-dir");
  if (dirEl) dirEl.textContent = cap.outputs_dir || "";
}

function applyPreset() {
  if (!cap) return;
  const id = document.getElementById("ltx-preset").value;
  const p = cap.presets.find((x) => x.id === id);
  if (!p) return;
  document.getElementById("ltx-width").value = p.width;
  document.getElementById("ltx-height").value = p.height;
  document.getElementById("ltx-frames").value = p.num_frames;
  document.getElementById("ltx-steps").value = p.num_inference_steps;
  document.getElementById("ltx-cfg").value = p.guidance_scale;
}

function toggleMode() {
  const mode = document.getElementById("ltx-mode").value;
  const imgRow = document.getElementById("ltx-image-row");
  const strengthRow = document.getElementById("ltx-strength-row");
  imgRow.hidden = mode !== "i2v";
  strengthRow.hidden = mode !== "i2v";
}

function collectParams() {
  return {
    mode: document.getElementById("ltx-mode").value,
    prompt: document.getElementById("ltx-prompt").value.trim(),
    negative_prompt: document.getElementById("ltx-negative").value.trim(),
    preset: document.getElementById("ltx-preset").value,
    width: parseInt(document.getElementById("ltx-width").value, 10),
    height: parseInt(document.getElementById("ltx-height").value, 10),
    num_frames: parseInt(document.getElementById("ltx-frames").value, 10),
    num_inference_steps: parseInt(document.getElementById("ltx-steps").value, 10),
    guidance_scale: parseFloat(document.getElementById("ltx-cfg").value),
    seed: parseInt(document.getElementById("ltx-seed").value, 10),
    image_path: document.getElementById("ltx-image").value.trim(),
    strength: parseFloat(document.getElementById("ltx-strength").value),
    fps: parseInt(document.getElementById("ltx-fps").value, 10),
    output_format: document.getElementById("ltx-format").value,
    enable_vae_slicing: document.getElementById("ltx-vae-slice").checked,
    enable_model_cpu_offload: document.getElementById("ltx-cpu-offload").checked,
  };
}

async function generate() {
  if (cap && !cap.engine_ready) {
    toast("请先安装 LTX-2.5 推理引擎（见上方提示）", { kind: "error" });
    return;
  }
  const params = collectParams();
  if (!params.prompt) {
    toast("请输入提示词", { kind: "error" });
    document.getElementById("ltx-prompt").focus();
    return;
  }
  const btn = document.getElementById("ltx-generate");
  btn.disabled = true;
  setStatus("提交生成任务…", "info");
  try {
    const r = await api.ltxGenerate(params);
    const task = r?.body?.task || r?.task;
    if (task) {
      setStatus(`任务已提交：${task.id}`, "info");
      startPolling();
    }
  } catch (e) {
    const detail = e?.response?.data?.detail || e.message || String(e);
    setStatus("生成失败：" + detail, "error");
    toast("生成失败：" + detail, { kind: "error" });
  } finally {
    btn.disabled = false;
  }
}

async function cancelActive() {
  const r = await api.ltxTasks();
  const body = r?.body || r;
  const active = body?.active;
  if (!active) { toast("当前没有运行中的任务", { kind: "info" }); return; }
  try {
    await api.ltxCancel(active.id);
    toast("已请求取消", { kind: "info" });
  } catch (e) {
    toast("取消失败：" + (e.message || e), { kind: "error" });
  }
}

function startPolling() {
  if (pollTimer) return;
  pollTimer = setInterval(pollTasks, 1000);
  pollTasks();
}

async function pollTasks() {
  try {
    const r = await api.ltxTasks();
    const body = r?.body || r;
    renderActive(body?.active);
    renderTaskList(body?.tasks || []);
    const active = body?.active;
    if (!active || ["done", "failed", "cancelled"].includes(active.state)) {
      if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
      if (active?.state === "done") {
        setStatus("生成完成 ✓", "ok");
        toast("视频生成完成", { kind: "ok" });
        loadOutputs();
      } else if (active?.state === "failed") {
        setStatus("生成失败：" + (active.error || ""), "error");
      } else if (active?.state === "cancelled") {
        setStatus("已取消", "warn");
      }
    }
  } catch (e) {
    // sidecar may be briefly unavailable; keep polling
  }
}

function renderActive(active) {
  const box = document.getElementById("ltx-active");
  const progress = document.getElementById("ltx-progress");
  const cancelBtn = document.getElementById("ltx-cancel");
  if (!active) {
    box.hidden = true;
    progress.hidden = true;
    cancelBtn.hidden = true;
    return;
  }
  box.hidden = false;
  cancelBtn.hidden = ["done", "failed", "cancelled"].includes(active.state);
  const pct = Math.round((active.progress || 0) * 100);
  document.getElementById("ltx-active-id").textContent = active.id;
  document.getElementById("ltx-active-state").textContent = stateLabel(active.state);
  document.getElementById("ltx-active-step").textContent =
    `${active.step || 0}/${active.total_steps || "?"}`;
  document.getElementById("ltx-active-elapsed").textContent =
    `${active.elapsed_s?.toFixed?.(1) ?? active.elapsed_s ?? 0}s`;
  if (["running", "loading", "saving"].includes(active.state)) {
    progress.hidden = false;
    progress.querySelector(".bar").style.width = pct + "%";
    progress.querySelector(".pct").textContent = pct + "%";
  } else {
    progress.hidden = true;
  }
}

function renderTaskList(tasks) {
  const host = document.getElementById("ltx-task-list");
  if (!tasks.length) { host.innerHTML = `<p class="mut tiny">暂无任务</p>`; return; }
  host.innerHTML = tasks.slice(0, 10).map((t) => `
    <div class="ltx-task-row state-${t.state}">
      <span class="ltx-task-id">${t.id}</span>
      <span class="ltx-task-state">${stateLabel(t.state)}</span>
      <span class="ltx-task-meta">${t.width}×${t.height} · ${t.num_frames}f · ${t.preset}</span>
      <span class="ltx-task-time">${(t.elapsed_s || 0).toFixed(1)}s</span>
    </div>`).join("");
}

async function loadOutputs() {
  try {
    const r = await api.ltxOutputs();
    const body = r?.body || r;
    renderOutputs(body?.outputs || []);
  } catch (e) {
    const host = document.getElementById("ltx-gallery");
    if (host) host.innerHTML = `<p class="mut tiny">暂无生成视频</p>`;
  }
}

function renderOutputs(outputs) {
  const host = document.getElementById("ltx-gallery");
  if (!outputs.length) {
    host.innerHTML = `<p class="mut tiny">暂无生成视频，完成一次生成后将显示在这里</p>`;
    return;
  }
  host.innerHTML = outputs.map((o) => {
    const isVideo = /\.(mp4|webm)$/i.test(o.name);
    const url = "file:///" + o.path.replace(/\\/g, "/");
    return `<div class="ltx-output-card">
      ${isVideo
        ? `<video src="${url}" controls preload="metadata" class="ltx-video"></video>`
        : `<img src="${url}" class="ltx-thumb" alt="${o.name}">`}
      <div class="ltx-output-meta">
        <span class="ltx-output-name" title="${o.path}">${o.name}</span>
        <span class="ltx-output-size">${formatBytes(o.size_bytes)}</span>
      </div>
    </div>`;
  }).join("");
}

async function openOutputsFolder() {
  if (!cap?.outputs_dir) return;
  try {
    await api.openPath(cap.outputs_dir);
  } catch (e) {
    toast("无法打开文件夹：" + (e.message || e), { kind: "error" });
  }
}

function setStatus(msg, kind) {
  const el = document.getElementById("ltx-status");
  el.textContent = msg;
  el.className = "ltx-status " + (kind || "");
}

function stateLabel(s) {
  return ({
    queued: "排队中", loading: "加载模型", running: "生成中", saving: "保存中",
    done: "完成", failed: "失败", cancelled: "已取消",
  })[s] || s;
}

function formatBytes(n) {
  if (!n) return "0 B";
  const u = ["B", "KB", "MB", "GB"];
  let i = 0;
  while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
  return n.toFixed(1) + " " + u[i];
}
