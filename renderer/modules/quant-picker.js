// renderer/modules/quant-picker.js — let the user pick a GGUF quantization
// (Q4_K_M / Q5_K_M / Q8_0 / FP8 …) before a model is downloaded.
"use strict";

// Known quant schemes in display order. `rec` marks the balanced recommendation.
const QUANT_META = [
  { key: "Q8_0", note: "高质量，体积较大" },
  { key: "Q6_K", note: "接近原版质量" },
  { key: "Q5_K_M", note: "高质量" },
  { key: "Q5_K_S", note: "高质量，体积稍小" },
  { key: "Q4_K_M", note: "质量与体积均衡", rec: true },
  { key: "Q4_K_S", note: "均衡，体积更小" },
  { key: "IQ4_NL", note: "新量化，省内存" },
  { key: "IQ4_XS", note: "省内存" },
  { key: "Q3_K_L", note: "省内存" },
  { key: "Q3_K_M", note: "省内存" },
  { key: "Q3_K_S", note: "省内存，质量下降" },
  { key: "IQ3_M", note: "省内存" },
  { key: "Q2_K", note: "极限省内存，质量损失明显" },
  { key: "IQ2_M", note: "极限省内存" },
  { key: "IQ2_XS", note: "极限省内存" },
  { key: "IQ2_S", note: "极限省内存" },
  { key: "IQ2_XXS", note: "极限省内存" },
  { key: "FP8", note: "8-bit 浮点，需较新硬件" },
  { key: "FP16", note: "原始精度，需大显存" },
  { key: "BF16", note: "原始精度，需大显存" },
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

// Open the picker. Resolves to one of:
//   null                      → user cancelled
//   { files, repo }           → chosen shard paths + gguf repo
//   { files: [] }             → no quantizable files found (caller may default)
//   { error }                 → enumeration failed
export async function pickQuantization(item, api) {
  let resp;
  const ggufRepo = item.gguf_repo || "";
  try {
    if (item.remote || !ggufRepo) {
      // 远程 hub 模型没有 curated id 映射，直接列该仓库文件。
      resp = await api.hubFiles({
        hub: "hf", repo: item.repo, revision: item.revision || "",
      });
    } else {
      resp = await api.modelGgufFiles(item.id);
    }
  } catch (e) {
    return { error: String((e && e.message) || e) };
  }
  const body = (resp && resp.body) || resp;
  const files = body && Array.isArray(body.files) ? body.files : [];
  const groups = groupFiles(files);
  if (!groups.length) return { files: [] };

  return new Promise((resolve) => {
    const overlay = document.createElement("div");
    overlay.className = "overlay";
    overlay.setAttribute("role", "dialog");
    overlay.setAttribute("aria-modal", "true");
    overlay.innerHTML = `
      <div class="overlay-card quant-card">
        <header>
          <h2>选择量化版本 · ${esc(item.name || item.id)}</h2>
          <button class="ghost icon quant-x" type="button" aria-label="关闭">×</button>
        </header>
        <div class="quant-list">
          ${groups
            .map((g, i) => {
              const meta = META_BY_KEY.get(g.key);
              return `<button type="button" class="quant-opt${meta && meta.rec ? " is-rec" : ""}" data-i="${i}">
                <span class="quant-radio" aria-hidden="true"></span>
                <span class="quant-main">
                  <span class="quant-label">${esc(g.key)}${
                    meta && meta.rec
                      ? '<span class="quant-badge">推荐</span>'
                      : ""
                  }</span>
                  <span class="quant-note">${esc((meta && meta.note) || "")}</span>
                </span>
                <span class="quant-size">${(g.size / 1e9).toFixed(1)} GB</span>
              </button>`;
            })
            .join("")}
        </div>
        <footer class="quant-foot">
          <span class="quant-count mut">共 ${groups.length} 个版本</span>
          <span class="quant-spacer"></span>
          <button class="secondary quant-cancel" type="button">取消</button>
          <button class="primary quant-ok" type="button" disabled>下载选中版本</button>
        </footer>
      </div>`;

    let selected = null;
    const done = (val) => {
      overlay.remove();
      resolve(val);
    };
    overlay.addEventListener("click", (e) => {
      const opt = e.target.closest(".quant-opt");
      if (opt) {
        selected = parseInt(opt.dataset.i, 10);
        overlay.querySelectorAll(".quant-opt").forEach((o) =>
          o.classList.toggle("is-sel", o === opt),
        );
        overlay.querySelector(".quant-ok").disabled = false;
        return;
      }
      if (e.target.closest(".quant-cancel, .quant-x")) {
        done(null);
        return;
      }
      if (e.target.closest(".quant-ok") && selected != null) {
        const g = groups[selected];
        done({ files: g.files, repo: body.repo || ggufRepo || item.gguf_repo || item.repo });
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
