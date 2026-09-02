// renderer/modules/models.js — sidebar search, model grid (virtual), detail panel.
"use strict";
import { api } from "./api.js";
import { toast } from "./toast.js";
import { state, setState } from "./state.js";
import { VirtualGrid } from "./virtual-grid.js";
import { highlight as highlightText } from "./search.js";

const $  = (s) => document.querySelector(s);
const $$ = (s) => Array.from(document.querySelectorAll(s));

let vgrid = null;

export function getVgrid() { return vgrid; }

export function initModels() {
  vgrid = new VirtualGrid($("#models-grid"), {
    itemHeight: 168,
    renderItem: renderCard,
    onItemClick: onCardClick,
  });
}

export function renderModelGrid() {
  // Initial render: catalog models only (local models live in the Local pane).
  const items = (state.models || []).slice();
  $("#models-count").textContent = `${items.length} 条`;
  vgrid.setItems(items);
}

function renderCard(m) {
  const root = document.createElement("div");
  root.className = "card model-card";
  root.setAttribute("tabindex", "0");
  root.setAttribute("role", "button");
  root.setAttribute("aria-label", `选择模型 ${m.name}`);
  root.dataset.id = m.id || "";
  const hw = m.hardware || {};
  const engineList = Array.isArray(m.engine) ? m.engine : (m.engine ? [m.engine] : []);
  const highlights = m._highlights || [];
  const nameHl = highlights.filter((h) => h.field === "name");
  const descHl = highlights.filter((h) => h.field === "description");
  const nameHtml = nameHl.length
    ? highlightText(m.name || m.id || "未命名", nameHl)
    : escapeHtml(m.name || m.id || "未命名");
  const descHtml = descHl.length
    ? highlightText(m.description || "", descHl)
    : escapeHtml(m.description || "");
  const scoreTag = (m._score && m._score > 0)
    ? `<span class="pill score" title="相关度 ${m._score}">·</span>` : "";
  root.innerHTML = `
    <div class="card-head">
      <div class="card-title">${nameHtml}</div>
      <div class="card-pills">
        ${m.size_gb ? `<span class="pill">${(+m.size_gb).toFixed(1)} GB</span>` : ""}
        ${m.license ? `<span class="pill">${escapeHtml(m.license)}</span>` : ""}
        ${m.trending ? `<span class="pill warn">🔥 trending</span>` : ""}
        ${engineList.includes("mnn") ? `<span class="pill ok">MNN 可选</span>` : ""}
        ${scoreTag}
      </div>
    </div>
    <div class="card-body">
      <p class="card-desc">${descHtml}</p>
    </div>
    <div class="card-foot">
      <span class="mut">${escapeHtml(m.category || "")}${hw.vram_gb ? ` · 建议 ${escapeHtml(hw.vram_gb)}GB 显存` : ""}</span>
      <button class="primary small" data-action="install" aria-label="开始安装">安装</button>
    </div>
  `;
  return root;
}

function onCardClick(idx, item, e) {
  // Detail selection is always triggered; install button does its own thing.
  if (e.target.closest("[data-action=install]")) {
    e.stopPropagation();
    installItem(item).catch(() => {});
    return;
  }
  setState({ selectedId: item.id || null });
  showDetail(item);
}

async function installItem(item) {
  const engines = Array.isArray(item.engine) ? item.engine
                : (item.engine ? [item.engine] : []);
  if (!engines.length) {
    toast("该模型暂未指定引擎", { kind: "warn" });
    return;
  }
  // 安装首选引擎；支持多引擎的模型（如 llama.cpp + MNN）可在详情页选择其他引擎。
  try {
    await api.installEngine(engines[0]);
    toast(`正在安装引擎 ${engines[0]}${engines.length > 1 ? `（另可选 ${engines.slice(1).join("/")}）` : ""}`, { kind: "ok" });
  } catch (_) { /* toast shown */ }
}

export function wireModelGrid() {
  // Search / filter / sort / keyboard wiring is owned by modules/search.js
  // (v2.4.0 super search). This remains as a hook for any grid-specific
  // global listeners that do not conflict with the search controller.
}

export function populateCategoryFilter() {
  const sel = $("#cat-filter");
  sel.replaceChildren();
  const all = document.createElement("option");
  all.value = ""; all.textContent = "全部分类";
  sel.appendChild(all);
  for (const c of (state.categories || [])) {
    const o = document.createElement("option");
    o.value = c.id; o.textContent = c.label || c.id;
    sel.appendChild(o);
  }
}

// ---------------------------------------------------------------------------
// Detail panel (right column)
// ---------------------------------------------------------------------------

const detailHost = () => document.querySelector("#detail-panel");

// Reject a promise after `ms` so the UI never hangs on a dead network call.
function withTimeout(promise, ms) {
  return Promise.race([
    promise,
    new Promise((_, rej) => setTimeout(() => rej(new Error("timeout")), ms)),
  ]);
}

export async function showDetail(item) {
  const host = detailHost();
  if (!host) return;
  host.innerHTML = renderSkeleton(item);

  let detail = item;
  try {
    // Detail API responds instantly since v2.3.0 (no sync GGUF enumeration).
    const r = await withTimeout(api.modelDetail(item.id || item.owner_repo), 4_000);
    if (r && r.body) detail = r.body;
    else if (r) detail = r;
  } catch (_) { /* keep lite detail */ }

  // Fast path: hand-curated gguf_repos already carry their file lists.
  let gguf = null;
  try {
    gguf = await withTimeout(api.ggufRepos(), 4_000);
  } catch (_) { /* fine */ }
  const ggufForModel = (gguf?.body?.repos || gguf?.repos || [])
    .find((r) => r.owner_repo === (detail.owner_repo || detail.repo));

  host.innerHTML = renderDetail(detail, ggufForModel);

  // v2.3.0 — lazy-load the model's own gguf_repo file list AFTER the panel
  // is already on screen (cold enumeration may take ~10s on first hit).
  if (!ggufForModel && detail.gguf_repo && detail.id) {
    _lazyLoadGgufFiles(host, detail);
  }

  // Per-engine install buttons
  host.querySelectorAll("[data-action=install-engine]").forEach((b) =>
    b.addEventListener("click", async () => {
      b.disabled = true;
      const id = b.dataset.id;
      try { await api.installEngine(id); toast(`正在安装引擎 ${id}`, { kind: "ok" }); }
      catch (_) {}
      b.disabled = false;
    })
  );
  host.querySelectorAll("[data-action=uninstall-engine]").forEach((b) =>
    b.addEventListener("click", async () => {
      b.disabled = true;
      const id = b.dataset.id;
      try { await api.uninstallEngine(id); toast(`已卸载 ${id}`, { kind: "ok" }); }
      catch (_) {}
      b.disabled = false;
    })
  );
  host.querySelectorAll("[data-action=open-external]").forEach((b) =>
    b.addEventListener("click", async () => {
      try { await api.openExternal(b.dataset.url); }
      catch (_) {}
    })
  );
  host.querySelectorAll("[data-action=mnn-download-repo]").forEach((b) =>
    b.addEventListener("click", async () => {
      b.disabled = true;
      try {
        await api.mnnDownload({ repo: b.dataset.repo });
        toast("开始下载 MNN 模型（仓库直下）", { kind: "ok" });
      } catch (_) { /* toast shown (409 已存在/进行中) */ }
      b.disabled = false;
    })
  );
  host.querySelectorAll("[data-action=import-local]").forEach(() => {}); // handled below
  host.querySelector("#btn-import-local-for-detail")?.addEventListener("click", async () => {
    const p = await api.pickFile();
    if (!p) return;
    try { await api.importModel({ path: p, mode: "copy" }); toast("已导入", { kind: "ok" }); }
    catch (_) {}
  });
}

function renderSkeleton(item) {
  return `
    <header class="panel-head"><h2>${escapeHtml(item.name || item.id || "模型详情")}</h2></header>
    <p class="mut">加载完整信息…</p>
  `;
}

// v2.3.0 — lazy GGUF file enumeration (panel already visible; fill in later).
async function _lazyLoadGgufFiles(host, detail) {
  const anchor = host.querySelector("#gguf-lazy");
  if (!anchor) return;
  try {
    const r = await api.modelGgufFiles(detail.id);
    const body = r?.body || r || {};
    const files = body.files || [];
    // Panel may have been re-rendered for another model meanwhile — bail out.
    if (!host.querySelector("#gguf-lazy")) return;
    if (!files.length) {
      anchor.outerHTML = "";
      return;
    }
    anchor.outerHTML = `
      <h3 class="section">所有量化版本（${files.length}）</h3>
      <details><summary>展开全部 .gguf 文件</summary>
        <ul class="gguf-list">
          ${files.map((f) => `<li>
            <span class="gguf-name">${escapeHtml(f.path || f.name || "")}</span>
            <span class="mut">${((f.size || 0) / 1e9).toFixed(2)} GB</span>
          </li>`).join("")}
        </ul>
      </details>`;
  } catch (_) {
    const a = host.querySelector("#gguf-lazy");
    if (a) a.outerHTML = `<p class="hint">量化版本列表加载失败（仓库镜像均不可达）。</p>`;
  }
}

function renderDetail(m, gguf) {
  const engines = Array.isArray(m.engines) ? m.engines
                 : (Array.isArray(m.engine) ? m.engine
                 : (m.engine ? [m.engine] : []));
  const ggufFiles = Array.isArray(gguf?.files) ? gguf.files : [];
  const hw = m.hardware || {};
  return `
    <header class="panel-head">
      <h2 id="detail-title">${escapeHtml(m.name || m.id)}</h2>
      <div class="card-pills">
        ${m.size_gb ? `<span class="pill">${(+m.size_gb).toFixed(1)} GB</span>` : ""}
        ${m.license ? `<span class="pill">${escapeHtml(m.license)}</span>` : ""}
        ${m.category ? `<span class="pill">${escapeHtml(m.category)}</span>` : ""}
        ${engines.includes("mnn") ? `<span class="pill ok">MNN 可选</span>` : ""}
        ${m.gated ? `<span class="pill warn">gated 受控访问</span>` : ""}
      </div>
    </header>
    <p class="card-desc">${escapeHtml(m.description || "")}</p>
    ${m.gated ? `<p class="hint">ⓘ 该仓库为 gated（受控访问）：先在 HuggingFace 仓库页面接受许可协议，再到「设置」填入你的 HF Token，然后才能下载。</p>` : ""}

    ${m.repo ? `<p>
      <button class="secondary small" data-action="open-external"
              data-url="https://huggingface.co/${escapeHtml(m.repo)}">
        在 Hugging Face 查看
      </button>
    </p>` : ""}

    ${engines.includes("mnn") && m.mnn_repo ? `<p>
      <button class="primary small" data-action="mnn-download-repo"
              data-repo="${escapeHtml(m.mnn_repo)}">下载 MNN 版（${escapeHtml(m.mnn_repo)}）</button>
      <span class="hint">从仓库直下官方预转换 MNN 模型。</span>
    </p>` : ""}

    ${hw.vram_gb ? `
    <h3 class="section">官方建议配置</h3>
    <div class="hw-need-grid">
      <div class="hw-need"><span class="hw-k">显存</span><span class="hw-v">${escapeHtml(hw.vram_gb)} GB${hw.min_vram_gb ? `（最低 ${escapeHtml(hw.min_vram_gb)}GB）` : ""}</span></div>
      <div class="hw-need"><span class="hw-k">内存</span><span class="hw-v">${escapeHtml(hw.ram_gb || "?")} GB</span></div>
      <div class="hw-need"><span class="hw-k">磁盘</span><span class="hw-v">${escapeHtml(hw.disk_gb || m.size_gb || "?")} GB</span></div>
    </div>
    ${hw.notes ? `<p class="hint">💡 ${escapeHtml(hw.notes)}</p>` : ""}
    ${engines.includes("mnn") ? `<p class="hint">⬢ 该模型支持 MNN 引擎（端侧更快）：可到「MNN 引擎」页下载官方预转换版本，或用 llama.cpp 加载 GGUF —— 两种引擎由你选择。</p>` : ""}
    ` : ""}

    <h3 class="section">所需引擎</h3>
    <div class="list compact">
      ${engines.length === 0
        ? `<div class="hint">该模型未指派引擎，请联系上游维护者。</div>`
        : engines.map((e) => `
          <div class="row">
            <div class="grow">
              <div class="name">${escapeHtml(typeof e === "string" ? e : (e.id || e.name || ""))}${e === "mnn" ? ' <span class="pill ok">端侧加速</span>' : ""}</div>
              <div class="sub">${escapeHtml(typeof e === "string" ? "" : (e.description || ""))}</div>
            </div>
            <span class="pill ${e.installed ? "ok" : ""}">${e.installed ? "已安装" : "未安装"}</span>
            <button class="primary" data-action="install-engine"
                    data-id="${escapeHtml(typeof e === "string" ? e : e.id)}">${e.installed ? "重装" : "安装"}</button>
            ${e.installed ? `<button class="danger" data-action="uninstall-engine"
              data-id="${escapeHtml(typeof e === "string" ? e : e.id)}">卸载</button>` : ""}
          </div>`).join("")}
    </div>

    <h3 class="section">导入本地副本</h3>
    <p class="hint">将已有的 GGUF / safetensors 文件导入到本地库。</p>
    <button class="secondary" id="btn-import-local-for-detail">📥 选择文件</button>

    ${ggufFiles.length > 0 ? `
      <h3 class="section">所有量化版本（${ggufFiles.length}）</h3>
      <details><summary>展开全部 .gguf 文件</summary>
        <ul class="gguf-list">
          ${ggufFiles.map((f) => `<li>
            <span class="gguf-name">${escapeHtml(f.path || f.name || "")}</span>
            <span class="mut">${((f.size || 0) / 1e9).toFixed(2)} GB</span>
          </li>`).join("")}
        </ul>
      </details>
    ` : (m.gguf_repo ? `<div id="gguf-lazy"><p class="mut tiny">正在加载量化版本列表…</p></div>` : "")}
  `;
}

function escapeHtml(s) {
  return String(s || "").replace(/[&<>"']/g, (m) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[m]));
}
