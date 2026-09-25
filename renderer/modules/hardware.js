// renderer/modules/hardware.js — 硬件快照 + 智能模型推荐页。
"use strict";
import { api } from "./api.js";
import { toast } from "./toast.js";
import { t } from "./i18n.js";

const $ = (s, r) => (r || document).querySelector(s);

function esc(s) {
  return String(s == null ? "" : s).replace(/[&<>"']/g, (m) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[m]));
}

const FIT_LABEL = {
  perfect: "hardware.fitPerfect",
  good: "hardware.fitGood",
  tight: "hardware.fitTight",
  no: "hardware.fitNo",
};
const FIT_CLASS = {
  perfect: "ok",
  good: "warn",
  tight: "warn",
  no: "err",
};

let _lastRecs = [];

export async function renderHardwarePage(root) {
  root.innerHTML = `
    <div class="hw-toolbar">
      <div>
        <h2 class="hw-title">${t("hardware.title")}</h2>
        <p class="hint">${t("hardware.hint")}</p>
      </div>
      <div class="hw-toolbar-actions">
        <select id="hw-cat" aria-label="${t("hardware.catFilter")}">
          <option value="">${t("market.allCategories")}</option>
        </select>
        <button class="secondary" id="hw-refresh">${t("hardware.rescan")}</button>
      </div>
    </div>
    <div id="hw-snapshot" class="hw-snapshot"><p class="mut">${t("hardware.detecting")}</p></div>
    <h3 class="section" id="hw-rec-title">${t("hardware.recTitle")}</h3>
    <div id="hw-recs" class="list"><p class="mut">${t("hardware.analyzing")}</p></div>
  `;

  $("#hw-refresh", root).addEventListener("click", () => load(root, true));
  $("#hw-cat", root).addEventListener("change", () => renderRecs(root));

  await load(root, false);
}

async function load(root, refresh) {
  const snapEl = $("#hw-snapshot", root);
  const recEl = $("#hw-recs", root);
  try {
    const r = await api.recommend({ limit: 20, refresh: refresh ? 1 : 0 });
    const body = r?.body || r || {};
    _lastRecs = body.recommendations || [];
    const hw = body.hardware || {};
    renderSnapshot(snapEl, hw);
    // 分类下拉
    const sel = $("#hw-cat", root);
    if (sel && sel.options.length <= 1) {
      const cats = new Set(_lastRecs.map((m) => m.category).filter(Boolean));
      for (const c of cats) {
        const o = document.createElement("option");
        o.value = c; o.textContent = c;
        sel.appendChild(o);
      }
    }
    renderRecs(root);
  } catch (e) {
    snapEl.innerHTML = `<p class="hint">${t("hardware.detectFailed", { err: esc(e?.message || e) })}</p>`;
    recEl.innerHTML = "";
  }
}

function renderSnapshot(el, hw) {
  const cpu = hw.cpu || {};
  const gpus = hw.gpus || [];
  const disk = hw.disk || {};
  const score = Number(hw.score || 0);
  const scoreLabel =
    score >= 80 ? t("hardware.gradeServer") : score >= 55 ? t("hardware.gradeHighend") : score >= 35 ? t("hardware.gradeMainstream") : t("hardware.gradeLight");

  const gpuRows = gpus.map((g) => `
    <div class="hw-kv">
      <span class="hw-k">${esc(g.vendor === "cpu" ? t("hardware.cpu") : t("hardware.gpu"))}</span>
      <span class="hw-v">${esc(g.name || t("hardware.unknown"))}
        ${g.vram_mb ? `<span class="pill">${(g.vram_mb / 1024).toFixed(0)} GB ${t("hardware.vram")}</span>` : ""}</span>
    </div>`).join("");

  el.innerHTML = `
    <div class="hw-grid">
      <div class="hw-card">
        <div class="hw-card-head"><span class="hw-ico">🧠</span> ${t("hardware.cpu")}</div>
        <div class="hw-kv"><span class="hw-k">${t("hardware.model")}</span><span class="hw-v">${esc(cpu.name || t("hardware.unknown"))}</span></div>
        <div class="hw-kv"><span class="hw-k">${t("hardware.physCores")}</span><span class="hw-v">${esc(cpu.physical_cores || "?")} ${t("hardware.coresUnit")}</span></div>
        <div class="hw-kv"><span class="hw-k">${t("hardware.isa")}</span><span class="hw-v">${
          cpu.avx512 ? t("hardware.avx512") : cpu.avx2 ? "AVX2" : t("hardware.basic")
        }</span></div>
      </div>
      <div class="hw-card">
        <div class="hw-card-head"><span class="hw-ico">💽</span> ${t("hardware.memStorage")}</div>
        <div class="hw-kv"><span class="hw-k">${t("hardware.ram")}</span><span class="hw-v">${esc(hw.ram_total_gb || "?")} GB</span></div>
        <div class="hw-kv"><span class="hw-k">${t("hardware.diskFree")}</span><span class="hw-v">${esc(disk.free_gb || "?")} / ${esc(disk.total_gb || "?")} GB</span></div>
        <div class="hw-kv"><span class="hw-k">${t("hardware.platform")}</span><span class="hw-v">${esc(hw.platform || "")}</span></div>
      </div>
      <div class="hw-card">
        <div class="hw-card-head"><span class="hw-ico">🎮</span> ${t("hardware.graphics")}</div>
        ${gpuRows || `<div class="hw-kv"><span class="hw-v mut">${t("hardware.noDgpu")}</span></div>`}
      </div>
      <div class="hw-card">
        <div class="hw-card-head"><span class="hw-ico">🌐</span> ${t("hardware.bandwidth")}</div>
        <div class="hw-kv"><span class="hw-k">下行测速</span>
          <span class="hw-v">${hw.bandwidth_mbps ? `${esc(hw.bandwidth_mbps)} Mbps` : "未测出"}
            <span class="pill ${hw.bandwidth_tier === "fast" ? "ok" : hw.bandwidth_tier === "medium" ? "warn" : ""}">${
              { fast: "高速", medium: "中等", slow: "较慢", unknown: "未知" }[hw.bandwidth_tier] || ""
            }</span></span></div>
        <div class="hw-kv"><span class="hw-k">建议</span><span class="hw-v">${
          hw.bandwidth_tier === "slow" ? "优先选择小体积模型" : "可下载大体积模型"
        }</span></div>
      </div>
      <div class="hw-card hw-score-card">
        <div class="hw-card-head"><span class="hw-ico">📊</span> 综合评分（LLM 推理导向）</div>
        <div class="hw-score-row">
          <span class="hw-score-num">${score}</span>
          <span class="hw-score-label">/ 100 · ${esc(scoreLabel)}</span>
        </div>
        <div class="hw-score-bar" role="img" aria-label="综合评分 ${score} 分">
          <div class="hw-score-fill" style="width:${Math.max(2, Math.min(100, score))}%"></div>
        </div>
      </div>
    </div>
  `;
}

function renderRecs(root) {
  const el = $("#hw-recs", root);
  const cat = ($("#hw-cat", root) || {}).value || "";
  const items = _lastRecs.filter((m) => !cat || m.category === cat);
  if (!items.length) {
    el.innerHTML = `<p class="hint">当前筛选下没有匹配的推荐。试试切回全部分类，或升级硬件后再来 😄</p>`;
    return;
  }
  el.innerHTML = items.map((m) => {
    const rec = m.recommendation || {};
    const need = rec.need || {};
    const reasons = (rec.reasons || []).map((r) => `<div class="hw-reason">· ${esc(r)}</div>`).join("");
    return `
    <div class="row hw-rec-row">
      <div class="grow">
        <div class="name">${esc(m.name || m.id)}
          ${m.trending ? '<span class="pill warn">🔥 新热</span>' : ""}
          <span class="pill">${esc(m.category || "")}</span>
        </div>
        <div class="sub">${esc(m.description || "")}</div>
        <div class="sub mut">官方建议：显存 ${esc(need.vram_gb || "?")}GB · 内存 ${esc(need.ram_gb || "?")}GB · 磁盘 ${esc(need.disk_gb || "?")}GB</div>
        ${reasons}
        ${rec.disk_note ? `<div class="hw-reason">💾 ${esc(rec.disk_note)}</div>` : ""}
        ${(m.hardware || {}).notes ? `<div class="hw-reason">💡 ${esc(m.hardware.notes)}</div>` : ""}
      </div>
      <div class="hw-rec-side">
        <span class="pill ${FIT_CLASS[rec.fit] || ""}">${esc(FIT_LABEL[rec.fit] ? t(FIT_LABEL[rec.fit]) : (rec.fit || ""))}</span>
        <span class="mut tiny">${esc((m.engine || []).join(" / "))}</span>
        <button class="primary small" data-action="hw-install" data-id="${esc(m.id)}">${t("market.install")}</button>
      </div>
    </div>`;
  }).join("");

  el.querySelectorAll("[data-action=hw-install]").forEach((b) => {
    b.addEventListener("click", async () => {
      const id = b.dataset.id;
      const item = _lastRecs.find((m) => m.id === id);
      if (!item) return;
      const engines = Array.isArray(item.engine) ? item.engine : (item.engine ? [item.engine] : []);
      if (!engines.length) { toast("该模型暂未指定引擎", { kind: "warn" }); return; }
      try {
        await api.installEngine(engines[0]);
        toast(`正在安装引擎 ${engines[0]}`, { kind: "ok" });
      } catch (_) { /* toast shown */ }
    });
  });
}
