// renderer/modules/quant-picker.js — let the user pick a GGUF quantization
// (Q4_K_M / Q5_K_M / Q8_0 / FP8 …) before a model is downloaded.
"use strict";

import { t } from "./i18n.js";

// Known quant schemes in display order. `rec` marks the balanced recommendation.
const QUANT_META = [
  { key: "Q8_0", note: "quantPicker.q8_0" },
  { key: "Q6_K", note: "quantPicker.q6_k" },
  { key: "Q5_K_M", note: "quantPicker.q5_k_m" },
  { key: "Q5_K_S", note: "quantPicker.q5_k_s" },
  { key: "Q4_K_M", note: "quantPicker.q4_k_m", rec: true },
  { key: "Q4_K_S", note: "quantPicker.q4_k_s" },
  { key: "IQ4_NL", note: "quantPicker.iq4_nl" },
  { key: "IQ4_XS", note: "quantPicker.iq4_xs" },
  { key: "Q3_K_L", note: "quantPicker.q3_k_l" },
  { key: "Q3_K_M", note: "quantPicker.q3_k_m" },
  { key: "Q3_K_S", note: "quantPicker.q3_k_s" },
  { key: "IQ3_M", note: "quantPicker.iq3_m" },
  { key: "Q2_K", note: "quantPicker.q2_k" },
  { key: "IQ2_M", note: "quantPicker.iq2_m" },
  { key: "IQ2_XS", note: "quantPicker.iq2_xs" },
  { key: "IQ2_S", note: "quantPicker.iq2_s" },
  { key: "IQ2_XXS", note: "quantPicker.iq2_xxs" },
  { key: "FP8", note: "quantPicker.fp8" },
  { key: "FP16", note: "quantPicker.fp16" },
  { key: "BF16", note: "quantPicker.bf16" },
];

const META_BY_KEY = new Map(QUANT_META.map((m) => [m.key, m]));
const ORDER_BY_KEY = new Map(QUANT_META.map((m, i) => [m.key, i]));
// Longest keys first so "Q4_K_M" wins over a bare "Q4".
const ALL_KEYS = QUANT_META.map((m) => m.key).sort((a, b) => b.length - a.length);

function esc(s) {
  return String(s == null ? "" : s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}

// Extract a quant key from a single file path. Handles both flat bartowski names
// and unsloth directory/shard layouts ("IQ4_NL/model-IQ4_NL-00001-of-00003.gguf").
export function quantKeyOf(path) {
  const full = String(path || "").replace(/\\/g, "/");
  const base = full.split("/").pop()
    .replace(/-\d{5}-of-\d{5}\.gguf$/i, ".gguf").toUpperCase();
  for (const k of ALL_KEYS) {
    if (base.includes(k)) return k;
  }
  const upper = full.toUpperCase();
  for (const k of ALL_KEYS) {
    if (upper.includes(`/${k}/`)) return k;
  }
  return null;
}

// Group a flat file list by quantization, summing shard sizes.
export function groupFiles(files) {
  const groups = new Map();
  for (const f of files) {
    const path = f.path || f.name;
    const key = quantKeyOf(path);
    if (!key) continue;
    if (!groups.has(key)) groups.set(key, { key, files: [], size: 0 });
    const g = groups.get(key);
    g.files.push(path);
    g.size += Number(f.size || 0);
  }
  return [...groups.values()].sort(
    (a, b) =>
      (ORDER_BY_KEY.has(a.key) ? ORDER_BY_KEY.get(a.key) : 99) -
      (ORDER_BY_KEY.has(b.key) ? ORDER_BY_KEY.get(b.key) : 99),
  );
}

// Classify whether a quantization of `needGB` can run on this machine.
//   hw: the object returned by GET /api/hardware
// Returns { level, label } where level is "ok" (fits on GPU), "warn" (runs via
// multi-GPU / partial offload / CPU, slower) or "no" (does not fit in RAM).
export function classifyFit(needGB, hw) {
  if (!hw) return { level: "", label: "" };
  const vram = Number(hw.gpu_best_vram_gb || 0);   // largest single GPU
  const totalVram = Number(hw.gpu_total_vram_gb || 0);
  const ram = Number(hw.ram_total_gb || 0);
  const need = Number(needGB) || 0;
  // Reserve headroom for the KV cache, context window and OS.
  if (vram > 0 && need <= Math.max(0, vram - 1.5))
    return { level: "ok", label: t("quantPicker.fitOk") };
  if (vram > 0 && totalVram > vram && need <= Math.max(0, totalVram - 1.5))
    return { level: "warn", label: t("quantPicker.fitMultiGpu") };
  if (need <= ram * 0.7)
    return { level: "warn", label: vram > 0 ? t("quantPicker.fitPartialOffload") : t("quantPicker.fitCpuSlow") };
  if (need <= ram * 0.9) return { level: "warn", label: t("quantPicker.fitRamTight") };
  return { level: "no", label: t("quantPicker.fitRamNo") };
}

// Pick the option the user is most likely to want: a recommended quant that
// still fits, otherwise the best-fitting one. Never auto-pick a "no" option.
export function defaultIndex(groups, fits) {
  const isRec = (i) => {
    const m = META_BY_KEY.get(groups[i].key);
    return !!(m && m.rec);
  };
  const rec = groups.findIndex((_, i) => isRec(i) && fits[i].level !== "no");
  if (rec >= 0) return rec;
  for (const lvl of ["ok", "warn"]) {
    const i = fits.findIndex((f) => f.level === lvl);
    if (i >= 0) return i;
  }
  const anyRec = groups.findIndex((_, i) => isRec(i));
  return anyRec >= 0 ? anyRec : 0;
}

// Open the picker. Resolves to one of:
//   null                      → user cancelled
//   { files, repo }           → chosen shard paths + gguf repo
//   { files: [] }             → no quantizable files found (caller may default)
//   { error }                 → enumeration failed
export async function pickQuantization(item, api, revision) {
  let resp;
  try {
    resp = await api.modelGgufFiles(item.id, revision || "");
  } catch (e) {
    return { error: String((e && e.message) || e) };
  }
  const body = (resp && resp.body) || resp;
  const files = body && Array.isArray(body.files) ? body.files : [];
  const groups = groupFiles(files);
  if (!groups.length) return { files: [] };

  // Best-effort hardware snapshot for the green/yellow/red fit indicator.
  let hw = null;
  try {
    const hr = await api.hardware();
    const hb = (hr && hr.body) || hr;
    hw = hb && hb.hardware ? hb.hardware : hb;
  } catch (e) {
    hw = null; // without hardware info the picker still works, just uncolored
  }
  const fits = groups.map((g) => classifyFit(g.size / 1e9, hw));

  return new Promise((resolve) => {
    const overlay = document.createElement("div");
    overlay.className = "overlay";
    overlay.setAttribute("role", "dialog");
    overlay.setAttribute("aria-modal", "true");
    overlay.innerHTML = `
      <div class="overlay-card quant-card">
        <header>
          <h2>${t("quantPicker.choose", { name: esc(item.name || item.id) })}</h2>
          <button class="ghost icon quant-x" type="button" aria-label="${t("quantPicker.close")}">×</button>
        </header>
        <div class="quant-list">
          ${groups
            .map((g, i) => {
              const meta = META_BY_KEY.get(g.key);
              const fit = fits[i];
              const fitCls = fit.level ? ` fit-${fit.level}` : "";
              return `<button type="button" class="quant-opt${meta && meta.rec ? " is-rec" : ""}${fitCls}" data-i="${i}">
                <span class="quant-radio" aria-hidden="true"></span>
                <span class="quant-main">
                  <span class="quant-label">${esc(g.key)}${
                    meta && meta.rec
                      ? `<span class="quant-badge">${t("quantPicker.recommend")}</span>`
                      : ""
                  }${
                    fit.label
                      ? `<span class="quant-fit ${fit.level ? "lvl-" + fit.level : ""}">${esc(fit.label)}</span>`
                      : ""
                  }</span>
                  <span class="quant-note">${esc((meta && meta.note) ? t(meta.note) : "")}</span>
                </span>
                <span class="quant-size">${(g.size / 1e9).toFixed(1)} GB</span>
              </button>`;
            })
            .join("")}
        </div>
        <footer class="quant-foot">
          <span class="quant-count mut">${t("quantPicker.count", { n: groups.length })}</span>
          <span class="quant-spacer"></span>
          <button class="secondary quant-cancel" type="button">${t("quantPicker.cancel")}</button>
          <button class="primary quant-ok" type="button" disabled>${t("quantPicker.download")}</button>
        </footer>
      </div>`;

    let selected = defaultIndex(groups, fits);
    const applySel = () => {
      const opts = overlay.querySelectorAll(".quant-opt");
      opts.forEach((o) =>
        o.classList.toggle("is-sel", parseInt(o.dataset.i, 10) === selected),
      );
      overlay.querySelector(".quant-ok").disabled = false;
    };
    applySel();
    const done = (val) => {
      overlay.remove();
      resolve(val);
    };
    overlay.addEventListener("click", (e) => {
      const opt = e.target.closest(".quant-opt");
      if (opt) {
        selected = parseInt(opt.dataset.i, 10);
        applySel();
        return;
      }
      if (e.target.closest(".quant-cancel, .quant-x")) {
        done(null);
        return;
      }
      if (e.target.closest(".quant-ok") && selected != null) {
        const g = groups[selected];
        done({ files: g.files, repo: body.repo });
      }
    });
    // Click on the dim backdrop acts as cancel.
    overlay.addEventListener("click", (e) => {
      if (e.target === overlay) done(null);
    });
    const onKey = (e) => {
      if (e.key === "Escape") {
        document.removeEventListener("keydown", onKey);
        done(null);
      }
    };
    document.addEventListener("keydown", onKey);
    document.body.appendChild(overlay);
  });
}
