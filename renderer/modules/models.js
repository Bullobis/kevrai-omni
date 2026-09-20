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

// v2.9.0 — source logo badge. HF ships no logo API, so the 🤗 emoji stands in;
// 魔搭 has a real official PNG (verified HTTP 200, 128×128) which is used
// directly. Kept as a function so the markup stays in one place.
const MS_LOGO_URL =
  "https://img.alicdn.com/imgextra/i4/O1CN01fvt4it25rEZU4Gjso_!!6000000007579-2-tps-128-128.png";

function hubLogo(hub, displayName) {
  if (!hub || hub === "curated") return "";
  if (hub === "modelscope") {
    return `<span class="hub-logo" title="${escapeHtml(displayName || "魔搭 ModelScope")}">`
      + `<img src="${MS_LOGO_URL}" alt="魔搭" width="14" height="14" loading="lazy" `
      + `referrerpolicy="no-referrer" onerror="this.replaceWith(document.createTextNode('魔搭'))" />`
      + `</span>`;
  }
  if (hub === "hf") {
    return `<span class="hub-logo" title="${escapeHtml(displayName || "HuggingFace")}">🤗</span>`;
  }
  return `<span class="hub-logo">${escapeHtml(displayName || hub)}</span>`;
}

// v2.9.0 — compact heat readout: 12.3k / 1.2M rather than 12345678.
function fmtCount(n) {
  const v = Number(n) || 0;
  if (v <= 0) return "";
  if (v >= 1e9) return `${(v / 1e9).toFixed(1)}B`;
  if (v >= 1e6) return `${(v / 1e6).toFixed(1)}M`;
  if (v >= 1e3) return `${(v / 1e3).toFixed(1)}k`;
  return String(v);
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
  const isRemote = m.hub && m.hub !== "curated";
  // v2.8.0 — remote cards: show the hub badge and, when the size is unknown,
  // say so explicitly instead of rendering "0 B" / NaN (E28).
  const hubBadge = isRemote
    ? `<span class="pill hub-${escapeHtml(m.hub)}">${hubLogo(m.hub, m.hub_display)}${escapeHtml(m.hub_display || m.hub)}</span>` : "";
  const sizePill = m.size_known === false || (!m.size_gb && isRemote)
    ? `<span class="pill" title="上游未提供体积">大小未知</span>`
    : (m.size_gb ? `<span class="pill">${(+m.size_gb).toFixed(1)} GB</span>` : "");
  // v2.9.0 — type (category) + function (task) + heat, per the user's request
  // to surface everything the upstream actually provides.
  const typePill = m.category
    ? `<span class="pill" title="类型">${escapeHtml(categoryLabel(m.category))}</span>` : "";
  const taskPill = m.task
    ? `<span class="pill" title="功能">${escapeHtml(taskLabel(m.task))}</span>` : "";
  const heatParts = [];
  if (m.downloads > 0) heatParts.push(`⬇ ${fmtCount(m.downloads)}`);
  if (m.likes > 0) heatParts.push(`♥ ${fmtCount(m.likes)}`);
  if (m.trending_score > 0) heatParts.push(`🔥 ${fmtCount(m.trending_score)}`);
  const heatPill = heatParts.length
    ? `<span class="pill heat" title="下载 / 点赞 / 热度分">${heatParts.join(" · ")}</span>` : "";
  if (isRemote && !m.description) {
    // Cards for remote models have no upstream prose — show the repo instead of
    // leaving an empty paragraph, so the card never looks broken.
    root.innerHTML = `
    <div class="card-head">
      <div class="card-title">${nameHtml}</div>
      <div class="card-pills">
        ${sizePill}
        ${hubBadge}
        ${typePill}
        ${taskPill}
        ${m.license ? `<span class="pill">${escapeHtml(m.license)}</span>` : ""}
        ${m.trending ? `<span class="pill warn">🔥 trending</span>` : ""}
        ${engineList.includes("mnn") ? `<span class="pill ok">MNN 可选</span>` : ""}
        ${m.import_only ? `<span class="pill">仅下载/导入</span>` : ""}
        ${scoreTag}
      </div>
    </div>
    <div class="card-body">
      <p class="card-desc mut mono-repo">${escapeHtml(m.repo || "")}</p>
      ${heatPill ? `<p class="card-heat">${heatPill}</p>` : ""}
    </div>
    <div class="card-foot">
      <span class="mut">${escapeHtml(m.owner || "")}${hw.vram_gb ? ` · 建议 ${escapeHtml(hw.vram_gb)}GB 显存` : ""}</span>
      <button class="primary small" data-action="install" aria-label="开始安装">安装</button>
    </div>
  `;
    return root;
  }
  root.innerHTML = `
    <div class="card-head">
      <div class="card-title">${nameHtml}</div>
      <div class="card-pills">
        ${sizePill}
        ${hubBadge}
        ${typePill}
        ${taskPill}
        ${m.license ? `<span class="pill">${escapeHtml(m.license)}</span>` : ""}
        ${m.trending ? `<span class="pill warn">🔥 trending</span>` : ""}
        ${engineList.includes("mnn") ? `<span class="pill ok">MNN 可选</span>` : ""}
        ${m.import_only ? `<span class="pill">仅下载/导入</span>` : ""}
        ${scoreTag}
      </div>
    </div>
    <div class="card-body">
      <p class="card-desc">${descHtml}</p>
      ${heatPill ? `<p class="card-heat">${heatPill}</p>` : ""}
    </div>
    <div class="card-foot">
      <span class="mut">${escapeHtml(m.owner || m.category || "")}${hw.vram_gb ? ` · 建议 ${escapeHtml(hw.vram_gb)}GB 显存` : ""}</span>
      <button class="primary small" data-action="install" aria-label="开始安装">安装</button>
    </div>
  `;
  return root;
}

// Human labels for the English category ids (the ids themselves are kept in
// data/state — only the display text is localized).
const CATEGORY_LABELS = {
  llm: "文本生成", tts: "语音合成", video: "视频生成", image: "图片生成",
  superres: "超分辨率", audio: "音频理解", "3d": "3D 生成", vision: "多模态",
  pending: "待定", other: "其他",
};

export function categoryLabel(id) {
  return CATEGORY_LABELS[id] || id || "";
}

// Upstream task ids are English/kebab-case; show the readable form.
function taskLabel(task) {
  const t = String(task || "").trim();
  if (!t) return "";
  return t.replace(/-/g, " ");
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

// v2.8.0 — small LRU for remote detail (design §3.6). Keyed by hub:repo so
// clicking the same card repeatedly does not refetch. Cleared only on restart.
const DETAIL_CACHE_MAX = 50;
const remoteDetailCache = new Map();

function cacheGet(key) {
  if (!remoteDetailCache.has(key)) return null;
  const v = remoteDetailCache.get(key);
  remoteDetailCache.delete(key);
  remoteDetailCache.set(key, v);   // refresh LRU position
  return v;
}

function cacheSet(key, value) {
  remoteDetailCache.set(key, value);
  while (remoteDetailCache.size > DETAIL_CACHE_MAX) {
    remoteDetailCache.delete(remoteDetailCache.keys().next().value);
  }
}

// v2.8.0 — fetch hub detail for a remote card, falling back to the legacy
// local detail endpoint when the model is curated or the bridge is absent.
async function fetchHubDetail(item) {
  const key = `${item.hub}:${item.repo}`;
  const hit = cacheGet(key);
  if (hit) return hit;
  const r = await withTimeout(api.hubModel({ hub: item.hub, repo: item.repo }), 6_000);
  const body = (r && r.body) ? r.body : r;
  const model = body ? (body.model || body) : null;
  if (model) cacheSet(key, model);
  return model;
}

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

  const isRemote = !!(item.hub && item.hub !== "curated" && item.repo);
  let detail = item;

  if (isRemote && window.kevrai && typeof window.kevrai.hubModel === "function") {
    try {
      const remote = await fetchHubDetail(item);
      if (remote) detail = { ...item, ...remote };
    } catch (_) { /* keep lite detail; banner below explains */ }
  }

  try {
    // Curated / cached path: the local detail API responds instantly.
    if (!isRemote) {
      const r = await withTimeout(api.modelDetail(item.id || item.owner_repo), 4_000);
      if (r && r.body) detail = r.body;
      else if (r) detail = r;
    }
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

// v2.9.0 — a labelled key/value row for the detail panel. Returns "" when the
// value is empty so a sparse upstream record never renders blank labels.
function detailRow(label, value, opts = {}) {
  const v = (value === 0 || value === false) ? value : (value || "");
  if (v === "" || v === null || v === undefined) return "";
  let shown = String(v);
  if (opts.mono) shown = escapeHtml(shown);
  else shown = escapeHtml(shown);
  const title = opts.title ? ` title="${escapeHtml(opts.title)}"` : "";
  return `<div class="detail-row"><span class="detail-k">${escapeHtml(label)}</span>`
    + `<span class="detail-v"${title}>${shown}${opts.suffix ? escapeHtml(opts.suffix) : ""}</span></div>`;
}

function fmtBytes(n) {
  const v = Number(n) || 0;
  if (v <= 0) return "";
  const gb = v / (1024 ** 3);
  return `${gb.toFixed(2)} GB`;
}

function fmtDate(iso) {
  const s = String(iso || "").trim();
  if (!s) return "";
  return s.replace("T", " ").replace("Z", " UTC").slice(0, 23);
}

function renderDetail(m, gguf) {
  const engines = Array.isArray(m.engines) ? m.engines
                 : (Array.isArray(m.engine) ? m.engine
                 : (m.engine ? [m.engine] : []));
  const ggufFiles = Array.isArray(gguf?.files) ? gguf.files : [];
  const hw = m.hardware || {};
  const isRemote = !!(m.hub && m.hub !== "curated");
  // v2.8.0 — remote repos live on HF or ModelScope; link to the right host.
  const repoHost = m.hub === "modelscope" ? "https://modelscope.cn/models" : "https://huggingface.co";
  const repoLabel = m.hub === "modelscope" ? "在 魔搭 ModelScope 查看" : "在 Hugging Face 查看";
  const sizePill = m.size_known === false || (!m.size_gb && isRemote)
    ? `<span class="pill">大小未知</span>`
    : (m.size_gb ? `<span class="pill">${(+m.size_gb).toFixed(1)} GB</span>` : "");
  const ownerText = m.owner_full_name
    ? `${m.owner || ""}${m.owner ? " · " : ""}${m.owner_full_name}` : (m.owner || "");
  const heatBits = [];
  if (m.downloads > 0) heatBits.push(`${fmtCount(m.downloads)} 次下载`);
  if (m.likes > 0) heatBits.push(`${fmtCount(m.likes)} 点赞`);
  if (m.trending_score > 0) heatBits.push(`热度分 ${m.trending_score}`);
  const frameworks = Array.isArray(m.frameworks) ? m.frameworks : [];
  const architectures = Array.isArray(m.architectures) ? m.architectures : [];
  const repoUrl = m.repo ? `${repoHost}/${m.repo}` : "";
  return `
    <header class="panel-head">
      <h2 id="detail-title">${escapeHtml(m.name || m.id)}</h2>
      <div class="card-pills">
        ${sizePill}
        ${isRemote ? `<span class="pill hub-${escapeHtml(m.hub)}">${hubLogo(m.hub, m.hub_display)}${escapeHtml(m.hub_display || m.hub)}</span>` : ""}
        ${m.category ? `<span class="pill">${escapeHtml(categoryLabel(m.category))}</span>` : ""}
        ${m.task ? `<span class="pill">${escapeHtml(taskLabel(m.task))}</span>` : ""}
        ${m.license ? `<span class="pill">${escapeHtml(m.license)}</span>` : ""}
        ${m.is_hot ? `<span class="pill warn">🔥 热门</span>` : ""}
        ${m.is_new ? `<span class="pill ok">✨ 新品</span>` : ""}
        ${engines.includes("mnn") ? `<span class="pill ok">MNN 可选</span>` : ""}
        ${m.import_only ? `<span class="pill">仅下载/导入</span>` : ""}
        ${m.gated ? `<span class="pill warn">gated 受控访问</span>` : ""}
      </div>
    </header>
    ${(m.description || chineseNameOf(m))
      ? `<p class="card-desc">${escapeHtml(m.description || chineseNameOf(m))}</p>` : ""}
    ${m.gated ? `<p class="hint">ⓘ 该仓库为 gated（受控访问）：先在 HuggingFace 仓库页面接受许可协议，再到「设置」填入你的 HF Token，然后才能下载。</p>` : ""}

    ${isRemote ? `
    <h3 class="section">基本信息</h3>
    <div class="detail-grid">
      ${detailRow("名称", m.name)}
      ${detailRow("仓库全名", m.repo, { title: m.repo })}
      ${detailRow("公司 / 组织", ownerText)}
      ${detailRow("上传者", m.nickname)}
      ${detailRow("类型", categoryLabel(m.category))}
      ${detailRow("功能", taskLabel(m.task))}
      ${detailRow("体积", m.size_known === false ? "未知" : fmtBytes(m.size_bytes) || (m.size_gb ? `${m.size_gb} GB` : ""))}
      ${detailRow("许可", m.license)}
      ${detailRow("主框架", m.library)}
      ${detailRow("框架", frameworks.join(" / "))}
      ${detailRow("架构", architectures.join(" / "))}
      ${detailRow("版本", m.revision)}
    </div>

    <h3 class="section">热度与时间</h3>
    <div class="detail-grid">
      ${detailRow("下载量", m.downloads > 0 ? m.downloads.toLocaleString("zh-CN") : "")}
      ${detailRow("点赞", m.likes > 0 ? m.likes.toLocaleString("zh-CN") : "")}
      ${detailRow("热度分", m.trending_score > 0 ? String(m.trending_score) : "")}
      ${detailRow("创建时间", fmtDate(m.created_at))}
      ${detailRow("更新时间", fmtDate(m.updated_at))}
      ${detailRow("标记", [m.is_hot ? "热门" : "", m.is_new ? "新品" : "", m.trending ? "trending" : ""].filter(Boolean).join(" / "))}
    </div>
    ` : ""}

    ${m.repo ? `<p>
      <button class="secondary small" data-action="open-external"
              data-url="${escapeHtml(repoUrl)}">
        ${repoLabel}
      </button>
      ${m.owner_url ? `<img class="owner-avatar" src="${escapeHtml(m.owner_url)}" alt="${escapeHtml(m.owner || "")}" `
        + `width="20" height="20" loading="lazy" referrerpolicy="no-referrer" `
        + `onerror="this.style.display='none'" />` : ""}
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

    ${m.description && isRemote ? `
      <h3 class="section">详细介绍</h3>
      <p class="detail-prose">${escapeHtml(m.description)}</p>
    ` : ""}

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

// 魔搭 exposes a Chinese display name; surface it when there is no prose yet.
function chineseNameOf(m) {
  return (m.name_cn || "").trim();
}

function escapeHtml(s) {
  return String(s || "").replace(/[&<>"']/g, (m) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[m]));
}
