// renderer/modules/models.js — sidebar search, model grid (virtual), detail panel.
"use strict";
import { api } from "./api.js";
import { toast } from "./toast.js";
import { state, setState } from "./state.js";
import { VirtualGrid } from "./virtual-grid.js";
import { highlight as highlightText } from "./search.js";
import { escapeHtml } from "./net.js";
import { toggleFavorite, isFavorite, bumpRecent } from "./favorites.js";
import {
  toggleCompare, isInCompare, registerModel,
} from "./compare.js";
import { t } from "./i18n.js";
import { pickQuantization } from "./quant-picker.js";
import { pickRevision, buildDownloadArgs } from "./revision-picker.js";
import { planInstall, planReasonKey, PLAN } from "./plan-install.js";

const $  = (s) => document.querySelector(s);
const $$ = (s) => Array.from(document.querySelectorAll(s));

let vgrid = null;

export function getVgrid() { return vgrid; }

export function initModels() {
  vgrid = new VirtualGrid($("#models-grid"), {
    itemHeight: 220,
    renderItem: renderCard,
    onItemClick: onCardClick,
  });
  buildGridSkeleton();
  // The detail column is contextual: it only takes horizontal space once a
  // model is opened, and can be dismissed without changing the selection ring.
  const close = $("#detail-close");
  if (close) {
    close.addEventListener("click", () => {
      setState({ selectedId: null });
      syncSelectedCards();
      setDetailOpen(false);
    });
  }
  window.addEventListener("kevrai:view-change", (e) => {
    const view = e.detail && e.detail.view;
    setDetailOpen(view === "market" && !!state.selectedId);
  });
}

function setDetailOpen(open) {
  const shell = document.querySelector(".app-shell");
  if (shell) shell.classList.toggle("has-detail", !!open);
}

// First-load skeleton: 8 placeholder cards that mirror the real grid layout.
function buildGridSkeleton() {
  const host = document.getElementById("models-skeleton");
  if (!host || host.childElementCount) return;
  const card = () => {
    const c = document.createElement("div");
    c.className = "skeleton-card";
    c.innerHTML =
      '<div class="sk-line sk-w55"></div>' +
      '<div class="sk-line sk-w45"></div>' +
      '<div class="sk-line sk-w90"></div>' +
      '<div class="sk-line sk-w70"></div>' +
      '<div class="sk-foot"><span class="sk-pill"></span><span class="sk-btn"></span></div>';
    return c;
  };
  for (let i = 0; i < 8; i++) host.appendChild(card());
}

// Reveal the real grid and retire the skeleton (called once data arrives).
export function hideGridSkeleton() {
  const grid = document.getElementById("models-grid");
  const sk = document.getElementById("models-skeleton");
  if (grid) grid.classList.remove("is-loading");
  if (sk) sk.hidden = true;
}

// Prepare the market shell once the catalog has loaded: update the item count
// and retire the initial HTML skeleton. This deliberately does NOT call
// vgrid.setItems() — runSearch() (called right after in loadAll) is the single
// entry point that mounts the virtual window. Calling setItems() here would
// mount ~12 cards and then immediately remount them again when runSearch's
// results arrive: a redundant layout+render on the startup critical path.
export function renderModelGrid() {
  // Initial render: catalog models only (local models live in the Local pane).
  const items = (state.models || []).slice();
  $("#models-count").textContent = t("market.count", { n: items.length });
  hideGridSkeleton();
}

// v2.9.0 — source logo badge. HF ships no logo API, so the 🤗 emoji stands in;
// 魔搭 has a real official PNG (verified HTTP 200, 128×128) which is used
// directly. Kept as a function so the markup stays in one place.
const MS_LOGO_URL =
  "https://img.alicdn.com/imgextra/i4/O1CN01fvt4it25rEZU4Gjso_!!6000000007579-2-tps-128-128.png";

function hubLogo(hub, displayName) {
  if (!hub || hub === "curated") return "";
  if (hub === "modelscope") {
    // 降级不用内联 onerror：CSP 的 script-src 'self' 会拦截内联事件处理器，
    // 导致图片加载失败时静默空白。改由 img 的 error 事件委托处理（见下方
    // wireLogoFallbacks），data-fallback 携带降级文案。
    return `<span class="hub-logo" title="${escapeHtml(displayName || t("models.msTitle"))}">`
      + `<img src="${MS_LOGO_URL}" alt="${t("models.msAlt")}" width="14" height="14" loading="lazy" `
      + `referrerpolicy="no-referrer" data-fallback="${t("models.msAlt")}" />`
      + `</span>`;
  }
  if (hub === "hf") {
    return `<span class="hub-logo" title="${escapeHtml(displayName || "HuggingFace")}">🤗</span>`;
  }
  return `<span class="hub-logo">${escapeHtml(displayName || hub)}</span>`;
}

/**
 * 把 logo 的加载失败降级为文字。
 *
 * 用捕获阶段的 error 事件委托，而不是内联 `onerror`：CSP 的
 * `script-src 'self'` 会拦掉内联事件处理器，届时图片失败既不会降级也不会
 * 报错（静默空白）。`error` 事件不冒泡，所以必须用 capture。
 *
 * 幂等：重复调用不会叠加监听器。
 */
export function wireLogoFallbacks(root = document) {
  if (root.__logoFallbackWired) return;
  root.__logoFallbackWired = true;
  root.addEventListener("error", (e) => {
    const el = e.target;
    if (!el || el.tagName !== "IMG") return;
    if (el.dataset && el.dataset.fallback) {
      el.replaceWith(document.createTextNode(el.dataset.fallback));
      return;
    }
    // 无降级文案的头像之类，直接隐藏，避免出现碎图标。
    if (el.classList && el.classList.contains("owner-avatar")) {
      el.style.display = "none";
    }
  }, true);
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

// lucide-style star outline; filled variant uses fill=currentColor.
const STAR_POLYGON =
  '<polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"/>';
function starSvg(active) {
  return active
    ? `<svg viewBox="0 0 24 24" fill="currentColor" stroke="none" aria-hidden="true">${STAR_POLYGON}</svg>`
    : `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"`
      + ` stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${STAR_POLYGON}</svg>`;
}

// Build the favorite star button for a card. renderCard runs on every virtual
// window re-layout, so isFavorite() is read fresh here to stay in sync.
function favButtonHtml(m) {
  const active = m.id ? isFavorite(m.id) : false;
  const name = m.name || m.id || "";
  const verb = active ? t("market.favRemoveShort") : t("market.favAddShort");
  const label = `${verb} ${name}`.trim();
  return `<button type="button" class="fav-btn${active ? " active" : ""}" data-action="fav"`
    + ` aria-label="${escapeHtml(label)}" title="${escapeHtml(verb)}">${starSvg(active)}</button>`;
}

// Repaint an already-mounted favorite button after a toggle (avoids a full
// grid re-render on every star click).
function paintFavButton(btn, active, name) {
  if (!btn) return;
  btn.classList.toggle("active", active);
  const verb = active ? t("market.favRemoveShort") : t("market.favAddShort");
  btn.setAttribute("aria-label", `${verb} ${name || ""}`.trim());
  btn.title = verb;
  btn.innerHTML = starSvg(active);
  btn.closest(".model-card")?.classList.toggle("is-favorite", active);
}

// lucide "columns" (side-by-side panes) — reads as "compare side by side".
const COLUMNS_SVG =
  '<rect width="18" height="18" x="3" y="3" rx="2"/><path d="M12 3v18"/>';
function columnsSvg(active) {
  return `<svg viewBox="0 0 24 24" fill="${active ? "currentColor" : "none"}" stroke="currentColor" stroke-width="2"`
    + ` stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${COLUMNS_SVG}</svg>`;
}

// Build the add-to-compare button for a card, sitting right beside the star.
// isInCompare() is read fresh so virtual-window re-renders stay in sync.
function compareButtonHtml(m) {
  const active = m.id ? isInCompare(m.id) : false;
  const name = m.name || m.id || "";
  const verb = active ? t("compare.remove") : t("compare.add");
  const label = `${verb} ${name}`.trim();
  return `<button type="button" class="compare-btn${active ? " active" : ""}" data-action="compare"`
    + ` aria-label="${escapeHtml(label)}" title="${escapeHtml(t("compare.title"))}">${columnsSvg(active)}</button>`;
}

// Repaint an already-mounted compare button after a toggle.
function paintCompareButton(btn, active, name) {
  if (!btn) return;
  btn.classList.toggle("active", active);
  const verb = active ? t("compare.remove") : t("compare.add");
  btn.setAttribute("aria-label", `${verb} ${name || ""}`.trim());
  btn.innerHTML = columnsSvg(active);
}

function renderCard(m) {
  const root = document.createElement("div");
  // Register the full model object so the compare modal can resolve it even when
  // the card scrolls out of the virtual window later.
  registerModel(m);
  root.className = "card model-card";
  root.setAttribute("tabindex", "0");
  root.setAttribute("role", "button");
  root.setAttribute("aria-label", t("market.selectModel", { name: escapeHtml(m.name || "") }));
  root.dataset.id = m.id || "";
  // Highlight the card whose detail panel is currently open. The grid re-renders
  // its window on scroll, so reading state here keeps the class in sync; clicks
  // also call syncSelectedCards() to update already-mounted cards immediately.
  if (state.selectedId && m.id && m.id === state.selectedId) root.classList.add("selected");
  if (m.id && isFavorite(m.id)) root.classList.add("is-favorite");
  const hw = m.hardware || {};
  const engineList = Array.isArray(m.engine) ? m.engine : (m.engine ? [m.engine] : []);
  const highlights = m._highlights || [];
  const nameHl = highlights.filter((h) => h.field === "name");
  const descHl = highlights.filter((h) => h.field === "description");
  const nameHtml = nameHl.length
    ? highlightText(m.name || m.id || t("market.unnamed"), nameHl)
    : escapeHtml(m.name || m.id || t("market.unnamed"));
  const descHtml = descHl.length
    ? highlightText(m.description || "", descHl)
    : escapeHtml(m.description || "");
  const isRemote = m.hub && m.hub !== "curated";
  const hubBadge = isRemote
    ? `<span class="pill hub-${escapeHtml(m.hub)}">${hubLogo(m.hub, m.hub_display)}${escapeHtml(m.hub_display || m.hub)}</span>` : "";
  // "Size unknown" is not a useful badge on every remote card; it reads as
  // noise. Show the real size when the catalog has one, otherwise omit it.
  const sizePill = m.size_gb ? `<span class="pill">${(+m.size_gb).toFixed(1)} GB</span>` : "";
  const typePill = m.category
    ? `<span class="pill" title="${t("market.typeLabel")}">${escapeHtml(categoryLabel(m.category))}</span>` : "";
  const capTask = (m.capability && m.capability.tasks && m.capability.tasks[0])
    ? `<span class="pill cap-task" title="${t("market.capabilityTitle")}">${escapeHtml(capTaskName(m.capability.tasks[0]))}</span>` : "";
  const licensePill = m.license
    ? `<span class="pill license" title="${escapeHtml(m.license)}">${escapeHtml(m.license)}</span>` : "";
  const trendBadge = m.trending
    ? `<span class="card-trend" title="${t("market.trendingTitle")}">🔥</span>` : "";
  const heatParts = [];
  if (m.downloads > 0) heatParts.push(`⬇ ${fmtCount(m.downloads)}`);
  if (m.likes > 0) heatParts.push(`♥ ${fmtCount(m.likes)}`);
  const heatText = heatParts.join("  ");
  const favBtn = favButtonHtml(m);
  const compareBtn = compareButtonHtml(m);
  const owner = escapeHtml(m.owner || m.category || "");
  const bodyHtml = isRemote && !m.description
    ? `<p class="card-desc card-repo">${escapeHtml(m.repo || "")}</p>`
    : `<p class="card-desc">${descHtml}</p>`;
  const statsHtml = heatText ? `<span class="card-stats">${heatText}</span>` : "";
  const installBtn = `<button class="primary small install-btn" data-action="install" aria-label="${t("market.installAria")}">${t("market.install")}</button>`;

  root.innerHTML = `
    <div class="card-head">
      <div class="card-head-main">
        <div class="card-title-row">
          <div class="card-title">${nameHtml}</div>
        </div>
        <div class="card-meta">
          ${trendBadge}
          ${sizePill}
          ${hubBadge}
          ${typePill}
          ${capTask}
          ${licensePill}
        </div>
      </div>
      <div class="card-actions">${favBtn}${compareBtn}</div>
    </div>
    <div class="card-body">${bodyHtml}</div>
    <div class="card-foot">
      <div class="card-foot-left">
        <span class="card-owner">${owner}</span>
        ${statsHtml}
      </div>
      ${installBtn}
    </div>
  `;
  // Keep sub-line labels consistent with the detail view without turning the
  // card into a wall of pills. VRAM (when known) is surfaced as a quiet hint.
  if (hw.vram_gb) {
    const meta = root.querySelector(".card-meta");
    if (meta) meta.insertAdjacentHTML("beforeend",
      `<span class="pill vram" title="${t("market.suggestedVram", { n: escapeHtml(hw.vram_gb) })}">VRAM ${escapeHtml(hw.vram_gb)} GB</span>`);
  }
  if (engineList.includes("mnn")) {
    const meta = root.querySelector(".card-meta");
    if (meta) meta.insertAdjacentHTML("beforeend",
      `<span class="pill ok">${t("market.mnnOptional")}</span>`);
  }
  return root;
}

// Human labels for the English category ids (the ids themselves are kept in
// data/state — only the display text is localized).
const CATEGORY_LABELS = {
  llm: "category_llm", tts: "category_tts", video: "category_video", image: "category_image",
  superres: "category_superres", audio: "category_audio", embedding: "category_embedding",
  "3d": "category_3d", vision: "category_vision",
  pending: "category_pending", other: "category_other",
};

const CAP_TASK_LABELS = {
  "text-to-image": "capTaskTextToImage", "image-to-image": "capTaskImageToImage",
  "text-to-video": "capTaskTextToVideo", "image-to-video": "capTaskImageToVideo",
  "video-to-video": "capTaskVideoToVideo", "audio-to-video": "capTaskAudioToVideo",
  "text-to-audio": "capTaskTextToAudio", "audio-to-audio": "capTaskAudioToAudio",
  "music-generation": "capTaskMusic", "sound-effects": "capTaskSoundEffects",
  "text-to-speech": "capTaskTts", "voice-clone": "capTaskVoiceClone",
  "asr": "capTaskAsr", "source-separation": "capTaskSeparation",
  "diarization": "capTaskDiarization", "text-to-3d": "capTaskTextTo3d",
  "image-to-3d": "capTaskImageTo3d", "upscale": "capTaskUpscale",
  "segmentation": "capTaskSegmentation", "detection": "capTaskDetection",
  "ocr": "capTaskOcr", "image-classification": "capTaskImageClassification",
  "embedding": "capTaskEmbedding", "text-generation": "capTaskTextGeneration",
  "image-understanding": "capTaskImageUnderstanding", "audio-understanding": "capTaskAudioUnderstanding",
  "video-understanding": "capTaskVideoUnderstanding", "image-captioning": "capTaskImageCaptioning",
  "text-detection": "capTaskTextDetection", "text-recognition": "capTaskTextRecognition",
  "controlnet": "capTaskControlnet", "voice-activity-detection": "capTaskVad",
};
const CAP_INPUT_LABELS = { text: "capInText", image: "capInImage", audio: "capInAudio", video: "capInVideo" };
const CAP_ROLE_LABELS = { first_frame: "capRoleFirstFrame", character: "capRoleCharacter",
  reference: "capRoleReference", control: "capRoleControl", source: "capRoleSource", driving: "capRoleDriving" };
const CAP_OUTPUT_LABELS = { image: "capOutImage", video: "capOutVideo", audio: "capOutAudio",
  "3d": "capOut3d", text: "capOutText", mask: "capOutMask", labels: "capOutLabels", embedding: "capOutEmbedding" };
const CAP_CONTROL_LABELS = { negative_prompt: "capCtrlNegative", duration: "capCtrlDuration",
  resolution: "capCtrlResolution", fps: "capCtrlFps", steps: "capCtrlSteps", cfg: "capCtrlCfg",
  seed: "capCtrlSeed", batch: "capCtrlBatch", format: "capCtrlFormat", lyrics: "capCtrlLyrics",
  voice: "capCtrlVoice", speed: "capCtrlSpeed", language: "capCtrlLanguage", motion: "capCtrlMotion",
  audio: "capCtrlAudio", video: "capCtrlVideo", image: "capCtrlImage", text_prompt: "capCtrlTextPrompt",
  labels: "capCtrlLabels" };

function capTaskName(id) {
  const key = CAP_TASK_LABELS[id];
  return key ? t(`market.${key}`) : String(id || "").replace(/-/g, " ");
}

function capInputName(type, info) {
  let name = t(`market.${CAP_INPUT_LABELS[type] || "capInText"}`);
  if (info && info.role && CAP_ROLE_LABELS[info.role]) {
    name += ` · ${t(`market.${CAP_ROLE_LABELS[info.role]}`)}`;
  }
  if (info && info.required === false) name += ` (${t("market.capOptional")})`;
  return name;
}

function capOutputName(type, info) {
  let name = t(`market.${CAP_OUTPUT_LABELS[type] || "capOutText"}`);
  if (info && info.audio) name += ` + ${t("market.capOutAudio")}`;
  if (info && Array.isArray(info.format)) name += ` · ${info.format.join(" / ")}`;
  else if (info && info.format) name += ` · ${info.format}`;
  return name;
}

function renderCapability(m) {
  const cap = m && m.capability;
  if (!cap || !Array.isArray(cap.tasks) || cap.tasks.length === 0) return "";
  const tasks = cap.tasks.map((id) => `<span class="pill cap-task">${escapeHtml(capTaskName(id))}</span>`).join("");
  const inputs = Object.entries(cap.inputs || {}).map(([type, info]) =>
    `<span class="pill">${escapeHtml(capInputName(type, info))}</span>`).join("");
  const outputs = Object.entries(cap.outputs || {}).map(([type, info]) =>
    `<span class="pill">${escapeHtml(capOutputName(type, info))}</span>`).join("");
  const controls = (cap.controls || []).filter((c) => CAP_CONTROL_LABELS[c])
    .map((c) => `<span class="pill quiet">${escapeHtml(t(`market.${CAP_CONTROL_LABELS[c]}`))}</span>`).join("");
  const confidenceKey = cap.confidence === "inferred" ? "capConfidenceInferred" : "capConfidenceVerified";
  const rawSource = String(cap.source || "");
  let sourceText;
  if (rawSource.startsWith("huggingface:")) {
    const repo = rawSource.slice("huggingface:".length).split(" ")[0];
    sourceText = t("market.capSourceHuggingFace", { repo: escapeHtml(repo) });
  } else if (rawSource === "catalog-tags") {
    sourceText = t("market.capSourceCatalog");
  } else {
    sourceText = t("market.capSourceCurated");
  }
  return `
    <h3 class="section">${t("market.capabilityTitle")}</h3>
    <div class="capability-block">
      <div class="capability-row"><span class="capability-key">${t("market.capTasks")}</span><div class="capability-pills">${tasks}</div></div>
      <div class="capability-row"><span class="capability-key">${t("market.capInputs")}</span><div class="capability-pills">${inputs || `<span class="muted">${t("market.capNone")}</span>`}</div></div>
      <div class="capability-row"><span class="capability-key">${t("market.capOutputs")}</span><div class="capability-pills">${outputs}</div></div>
      ${controls ? `<div class="capability-row"><span class="capability-key">${t("market.capControls")}</span><div class="capability-pills">${controls}</div></div>` : ""}
      <p class="hint" title="${escapeHtml(rawSource)}">${t("market.capSource", { source: sourceText, level: t(`market.${confidenceKey}`) })}</p>
    </div>`;
}

export function categoryLabel(id) {
  const key = CATEGORY_LABELS[id];
  return key ? t(`market.${key}`) : (id || "");
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
  // Favorite star: never open the detail panel, just toggle + repaint the button.
  const favBtn = e.target.closest && e.target.closest(".fav-btn");
  if (favBtn) {
    e.stopPropagation();
    if (!item.id) return;
    const nowFav = toggleFavorite(item.id);
    paintFavButton(favBtn, nowFav, item.name || item.id);
    return;
  }
  // Compare button: never open the detail panel, just toggle + repaint + toast.
  const cmpBtn = e.target.closest && e.target.closest(".compare-btn");
  if (cmpBtn) {
    e.stopPropagation();
    if (!item.id) return;
    const nowIn = toggleCompare(item);
    paintCompareButton(cmpBtn, nowIn, item.name || item.id);
    try { toast(t(nowIn ? "compare.added" : "compare.removed"), { kind: nowIn ? "ok" : "info" }); } catch (_) {}
    return;
  }
  setState({ selectedId: item.id || null });
  if (item.id) bumpRecent(item.id);
  syncSelectedCards();
  showDetail(item);
}

// Toggle the .selected ring/left-bar on the currently mounted cards after a
// selection change (off-screen cards get it on their next window render).
function syncSelectedCards() {
  $$("#models-grid .vgrid-item").forEach((n) => {
    n.classList.toggle("selected",
      !!state.selectedId && n.dataset.id === state.selectedId);
  });
}

// v4.11.1 — 引擎路由信息：异步查询后端，结果插到详情面板顶部。
// 失败时显示"未知"，绝不猜一个引擎出来。
async function _renderEngineRoute(host, detail) {
  const slot = host.querySelector("#engine-route-slot");
  if (!slot) return;
  // The catalog `engine` list is upstream capability order. Availability has
  // already resolved which engines this build can actually run in-process;
  // those must come first or MusicGen/MiniMax H3 details say "chat"/"ComfyUI"
  // while the model is really using the local music/Diffusers runtime.
  const nativeEngines = ((detail.availability && detail.availability.native) || [])
    .map((n) => n.engine).filter(Boolean);
  const catalogEngines = Array.isArray(detail.engine) ? detail.engine
                : (detail.engine ? [detail.engine] : []);
  const engines = [...new Set([...nativeEngines, ...catalogEngines])];
  try {
    const { fetchEngineRoute, renderRouteRow } = await import("./engine-route.js");
    const res = await fetchEngineRoute(api, engines, {
      modelId: detail.id || "", category: detail.category || "",
    });
    slot.innerHTML = renderRouteRow(res, t);
  } catch (_) {
    slot.innerHTML = `<div class="engine-route-row warn">${t("engineRoute.unknown")}</div>`;
  }
}

async function installItem(item) {
  const engines = Array.isArray(item.engine) ? item.engine
                : (item.engine ? [item.engine] : []);
  // import_only 模型（没有匹配到引擎）不再被挡回：跳过引擎安装，
  // 直接走 planInstall 自动挑文件下载，让所有模型都能"下载"而不只是导入。
  let chosenEngine = engines[0] || "";
  if (engines.length > 1) {
    const { pickEngine, applyEngineChoice } = await import("./engine-picker.js");
    const res = await pickEngine({
      engines,
      modelId: item.id || "",
      modelName: item.name || "",
      t,
    });
    if (!res) return;   // 取消 = 中止安装，不能退回默认引擎偷偷装
    applyEngineChoice(res, item.id || "");
    chosenEngine = res.engine;
  }

  // Make sure the preferred engine is present; multi-engine models can pick a
  // different engine from the detail panel. import_only 模型无引擎则跳过。
  if (chosenEngine) {
    try {
      await api.installEngine(chosenEngine);
    } catch (_) { /* toast shown by api */ }
  }

  // GGUF models ship several revisions (branches/tags) and quantizations.
  // First pick a revision (defaulting to main, skipped when there is no
  // choice), then pick a quantization off that revision, then download the
  // chosen shard(s) at the chosen revision.
  let revision = "main";
  let pickedFiles = null;
  let pickedRepo = "";

  if (item.gguf_repo) {
    const rev = await pickRevision(item, api);
    if (rev === null) return;  // user cancelled revision picker → abort install
    revision = (rev && rev.revision) || "main";

    const pick = await pickQuantization(item, api, revision);
    if (!pick) return;  // user cancelled
    if (pick.error) { toast(pick.error, { kind: "warn" }); return; }
    // 空数组 ≠ 取消：它表示"没识别出任何量化档"。以前这里 `if (pick.files…)`
    // 直接静默 return，用户点了安装却什么都没下。现在交给后端自动挑文件。
    pickedFiles = (pick && pick.files) || null;
    pickedRepo = (pick && pick.repo) || (rev && rev.repo) || "";
  }

  // v4.11.0 — 下载决策集中到 planInstall：它把"能下什么"变成显式的
  // 三种结果（指定分片 / 自动挑文件 / 真的无东西可下），而不是散落在
  // if 分支里。真实 bug：165 个没有 gguf_repo 的模型走到这里时旧代码
  // 只装引擎就 return，权重从未下载。
  const plan = planInstall(item, {
    revision,
    files: pickedFiles || undefined,
    repo: pickedRepo || undefined,
  });

  if (plan.kind === PLAN.NOTHING) {
    toast(t(planReasonKey(plan.reason)), { kind: "warn" });
    return;
  }

  try {
    const res = await api.hubDownload(plan.args);
    const body = (res && res.body) || res || {};
    if (body.auto_files_picked) {
      toast(t("toast.downloadStartedAuto", { file: item.name || item.id }),
            { kind: "ok" });
    } else {
      toast(t("toast.downloadStarted", { file: item.name || item.id }),
            { kind: "ok" });
    }
  } catch (e) {
    toast(String((e && e.message) || e), { kind: "err" });
  }
}

export function populateCategoryFilter() {
  const sel = $("#cat-filter");
  sel.replaceChildren();
  const all = document.createElement("option");
  all.value = ""; all.textContent = t("market.allCategories");
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
// The timer must be cleared when the wrapped promise settles first, otherwise
// every fast detail lookup leaves a live timeout behind (and keeps a rejected
// promise alive until it fires).
function withTimeout(promise, ms) {
  let timer;
  const timeout = new Promise((_, rej) => {
    timer = setTimeout(() => rej(new Error("timeout")), ms);
  });
  return Promise.race([
    promise.finally(() => clearTimeout(timer)),
    timeout,
  ]);
}

// Monotonic token for the detail panel. A remote detail request can take up to
// 6s; clicking model B while A is still in flight used to let A's response
// overwrite B's panel (wrong repo/engine, and install buttons acting on a
// stale model). Every await re-checks that this call is still the latest.
let _detailSeq = 0;

export async function showDetail(item) {
  const host = detailHost();
  if (!host) return;
  setDetailOpen(true);
  // #detail-panel starts life with the empty-state classes. Leaving them on
  // after injecting content centers every row and makes the spec sheet look
  // like a placeholder; strip them before the first paint.
  host.classList.remove("detail-empty", "empty-state");
  host.classList.add("detail-content");
  const seq = ++_detailSeq;
  host.innerHTML = renderSkeleton(item);

  const isRemote = !!(item.hub && item.hub !== "curated" && item.repo);
  let detail = item;

  if (isRemote && window.kevrai && typeof window.kevrai.hubModel === "function") {
    try {
      const remote = await fetchHubDetail(item);
      if (seq !== _detailSeq) return;
      if (remote) detail = { ...item, ...remote };
    } catch (_) { /* keep lite detail; banner below explains */ }
  }
  if (seq !== _detailSeq) return;

  try {
    // Curated / cached path: the local detail API responds instantly.
    if (!isRemote) {
      const r = await withTimeout(api.modelDetail(item.id || item.owner_repo), 4_000);
      if (seq !== _detailSeq) return;
      if (r && r.body) detail = r.body;
      else if (r) detail = r;
    }
  } catch (_) { /* keep lite detail */ }

  // Fast path: hand-curated gguf_repos already carry their file lists.
  let gguf = null;
  try {
    gguf = await withTimeout(api.ggufRepos(), 4_000);
  } catch (_) { /* fine */ }
  if (seq !== _detailSeq) return;
  const ggufForModel = (gguf?.body?.repos || gguf?.repos || [])
    .find((r) => r.owner_repo === (detail.owner_repo || detail.repo));

  host.innerHTML = renderDetail(detail, ggufForModel);

  // v4.11.1 — 显示"这个模型实际会用哪个引擎、走哪个端点"。
  // 以前前端自己猜（装的时候写死 engines[0]），而 settings 里的
  // default_engine_id 全仓库无人读取。现在后端是唯一决策点，这里只负责
  // 如实显示结果——包括"该引擎没有应用内加载端点"。
  _renderEngineRoute(host, detail);

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
      try { await api.installEngine(id); toast(t("toast.installingEngine", { id }), { kind: "ok" }); }
      catch (_) {}
      b.disabled = false;
    })
  );
  host.querySelectorAll("[data-action=uninstall-engine]").forEach((b) =>
    b.addEventListener("click", async () => {
      b.disabled = true;
      const id = b.dataset.id;
      try { await api.uninstallEngine(id); toast(t("toast.uninstalledEngine", { id }), { kind: "ok" }); }
      catch (_) {}
      b.disabled = false;
    })
  );
  host.querySelectorAll("[data-action=configure-external]").forEach((b) =>
    b.addEventListener("click", () => {
      const model = b.dataset.model || "";
      document.querySelector('.sidebar [data-tab="engines"]')?.click();
      // Card is mounted asynchronously on the engines page; fill once it exists.
      const fill = (tries = 12) => {
        const input = document.querySelector("#external-runtime-model");
        if (input) {
          input.value = model;
          input.focus();
          input.scrollIntoView({ block: "nearest", behavior: "smooth" });
        } else if (tries > 0) {
          setTimeout(() => fill(tries - 1), 120);
        }
      };
      fill();
    })
  );
  host.querySelectorAll("[data-action=open-comfyui-status]").forEach((b) =>
    b.addEventListener("click", () => {
      document.querySelector('.sidebar [data-tab="engines"]')?.click();
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
        toast(t("toast.mnnRepoStart"), { kind: "ok" });
      } catch (_) { /* toast shown (409 已存在/进行中) */ }
      b.disabled = false;
    })
  );
  host.querySelectorAll("[data-action=import-local]").forEach(() => {}); // handled below
  host.querySelector("#btn-import-local-for-detail")?.addEventListener("click", async () => {
    const p = await api.pickFile();
    if (!p) return;
    try { await api.importModel({ path: p, mode: "copy" }); toast(t("toast.imported"), { kind: "ok" }); }
    catch (_) {}
  });
}

function renderSkeleton(item) {
  return `
    <header class="panel-head"><h2>${escapeHtml(item.name || item.id || t("market.detailFallbackTitle"))}</h2></header>
    <p class="mut">${t("market.loadingDetail")}</p>
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
      <h3 class="section">${t("market.quantVersions", { n: files.length })}</h3>
      <details><summary>${t("market.expandGguf")}</summary>
        <ul class="gguf-list">
          ${files.map((f) => `<li>
            <span class="gguf-name">${escapeHtml(f.path || f.name || "")}</span>
            <span class="mut">${((f.size || 0) / 1e9).toFixed(2)} GB</span>
          </li>`).join("")}
        </ul>
      </details>`;
  } catch (_) {
    const a = host.querySelector("#gguf-lazy");
    if (a) a.outerHTML = `<p class="hint">${t("market.quantLoadFailed")}</p>`;
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

// v4.18.0 — 外部 OpenAI 兼容服务模型（vLLM / SGLang / Ollama）给出
// 一个真正可点的入口：跳到引擎页并预填模型名，而不是只显示一句“需要外部”。
function renderExternalBridge(m, a) {
  // Only models whose best available path is external need this bridge.
  // A Diffusers-native model may also list vLLM/SGLang as alternates; offering
  // "configure external service" there would contradict “本地可运行”.
  if (!a || a.status !== "external") return "";
  const services = (a.external || []).map((x) => x.service);
  const model = m.repo || m.id || "";
  if (services.some((x) => x === "vLLM" || x === "SGLang" || x === "Ollama")) {
    return `<p class="external-bridge">
      <button class="secondary small" data-action="configure-external"
              data-model="${escapeHtml(model)}">${t("market.configureExternal")}</button>
      <span class="hint">${t("market.configureExternalHint")}</span>
    </p>`;
  }
  if (services.includes("ComfyUI")) {
    // ComfyUI needs a model-specific workflow. The image page only speaks
    // Stable Diffusion checkpoints; browsing a MiniMax music/3D model there
    // would be a false affordance. Point at the service status instead and
    // say plainly that a one-click workflow is not wired for this model yet.
    return `<p class="external-bridge">
      <button class="secondary small" data-action="open-comfyui-status"
              data-model="${escapeHtml(model)}">${t("market.comfyuiStatus")}</button>
      <span class="hint">${t("market.comfyuiStatusHint")}</span>
    </p>`;
  }
  return "";
}

function renderAvailability(a) {
  const nativeLabels = (a.native || []).map((x) => x.label).join(" / ");
  const externalLabels = (a.external || []).map((x) => x.service).join(" / ");
  const conversion = a.conversion
    ? `<span class="er-note">${escapeHtml(a.conversion.label)}</span>` : "";
  if (a.status === "native") {
    return `<p class="engine-route-row ok"><span class="er-label">${t("market.availabilityTitle")}</span><span class="er-value">${escapeHtml(t("market.availabilityNative", { list: nativeLabels }))}</span>${conversion}</p>`;
  }
  if (a.status === "external") {
    return `<p class="engine-route-row warn"><span class="er-label">${t("market.availabilityTitle")}</span><span class="er-value">${escapeHtml(t("market.availabilityExternal", { list: externalLabels }))}</span><span class="er-note">${escapeHtml(a.reason || "")}</span>${conversion}</p>`;
  }
  return `<p class="engine-route-row warn"><span class="er-label">${t("market.availabilityTitle")}</span><span class="er-value">${t("market.availabilityUnsupported")}</span><span class="er-note">${escapeHtml(a.reason || "")}</span>${conversion}</p>`;
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
  const repoLabel = m.hub === "modelscope" ? t("market.viewOnModelscope") : t("market.viewOnHf");
  const sizePill = m.size_gb ? `<span class="pill">${(+m.size_gb).toFixed(1)} GB</span>` : "";
  const ownerText = m.owner_full_name
    ? `${m.owner || ""}${m.owner ? " · " : ""}${m.owner_full_name}` : (m.owner || "");
  const heatBits = [];
  if (m.downloads > 0) heatBits.push(t("market.downloadsCount", { n: fmtCount(m.downloads) }));
  if (m.likes > 0) heatBits.push(t("market.likesCount", { n: fmtCount(m.likes) }));
  if (m.trending_score > 0) heatBits.push(t("market.heatScore", { n: m.trending_score }));
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
        ${m.is_hot ? `<span class="pill warn">${t("market.hot")}</span>` : ""}
        ${m.is_new ? `<span class="pill ok">${t("market.new")}</span>` : ""}
        ${engines.includes("mnn") ? `<span class="pill ok">${t("market.mnnOptional")}</span>` : ""}
        ${m.import_only ? `<span class="pill">${t("market.importOnly")}</span>` : ""}
        ${m.gated ? `<span class="pill warn">${t("market.gated")}</span>` : ""}
      </div>
    </header>
    ${(m.description || chineseNameOf(m))
      ? `<p class="card-desc">${escapeHtml(m.description || chineseNameOf(m))}</p>` : ""}
    ${m.gated ? `<p class="hint">${t("market.gatedHint")}</p>` : ""}
    ${m.availability ? renderAvailability(m.availability) : ""}
    ${renderExternalBridge(m, m.availability)}
    ${renderCapability(m)}
    <div id="engine-route-slot" class="engine-route-slot"></div>

    ${isRemote ? `
    <h3 class="section">${t("market.basicInfo")}</h3>
    <div class="detail-grid">
      ${detailRow(t("market.detailName"), m.name)}
      ${detailRow(t("market.detailRepo"), m.repo, { title: m.repo })}
      ${detailRow(t("market.detailOrg"), ownerText)}
      ${detailRow(t("market.detailUploader"), m.nickname)}
      ${detailRow(t("market.detailType"), categoryLabel(m.category))}
      ${detailRow(t("market.detailTask"), taskLabel(m.task))}
      ${detailRow(t("market.detailSize"), m.size_known === false ? t("market.unknown") : fmtBytes(m.size_bytes) || (m.size_gb ? `${m.size_gb} GB` : ""))}
      ${detailRow(t("market.detailLicense"), m.license)}
      ${detailRow(t("market.detailFramework"), m.library)}
      ${detailRow(t("market.detailFrameworks"), frameworks.join(" / "))}
      ${detailRow(t("market.detailArch"), architectures.join(" / "))}
      ${detailRow(t("market.detailVersion"), m.revision)}
    </div>

    <h3 class="section">${t("market.heatAndTime")}</h3>
    <div class="detail-grid">
      ${detailRow(t("market.detailDownloads"), m.downloads > 0 ? m.downloads.toLocaleString() : "")}
      ${detailRow(t("market.detailLikes"), m.likes > 0 ? m.likes.toLocaleString() : "")}
      ${detailRow(t("market.detailHeat"), m.trending_score > 0 ? String(m.trending_score) : "")}
      ${detailRow(t("market.detailCreated"), fmtDate(m.created_at))}
      ${detailRow(t("market.detailUpdated"), fmtDate(m.updated_at))}
      ${detailRow(t("market.detailMarks"), [m.is_hot ? t("market.markHot") : "", m.is_new ? t("market.markNew") : "", m.trending ? "trending" : ""].filter(Boolean).join(" / "))}
    </div>
    ` : ""}

    ${m.repo ? `<p>
      <button class="secondary small" data-action="open-external"
              data-url="${escapeHtml(repoUrl)}">
        ${repoLabel}
      </button>
      ${m.owner_url ? `<img class="owner-avatar" src="${escapeHtml(m.owner_url)}" alt="${escapeHtml(m.owner || "")}" `
        + `width="20" height="20" loading="lazy" referrerpolicy="no-referrer" />` : ""}
    </p>` : ""}

    ${engines.includes("mnn") && m.mnn_repo ? `<p>
      <button class="primary small" data-action="mnn-download-repo"
              data-repo="${escapeHtml(m.mnn_repo)}">${t("market.downloadMnn", { repo: escapeHtml(m.mnn_repo) })}</button>
      <span class="hint">${t("market.downloadMnnHint")}</span>
    </p>` : ""}

    ${hw.vram_gb ? `
    <h3 class="section">${t("market.recommendedConfig")}</h3>
    <div class="hw-need-grid">
      <div class="hw-need"><span class="hw-k">${t("market.vram")}</span><span class="hw-v">${escapeHtml(hw.vram_gb)} GB${hw.min_vram_gb ? `（${t("market.minVram", { n: escapeHtml(hw.min_vram_gb) })}）` : ""}</span></div>
      <div class="hw-need"><span class="hw-k">${t("market.ram")}</span><span class="hw-v">${escapeHtml(hw.ram_gb || "?")} GB</span></div>
      <div class="hw-need"><span class="hw-k">${t("market.disk")}</span><span class="hw-v">${escapeHtml(hw.disk_gb || m.size_gb || "?")} GB</span></div>
    </div>
    ${hw.notes ? `<p class="hint">💡 ${escapeHtml(hw.notes)}</p>` : ""}
    ${engines.includes("mnn") ? `<p class="hint">${t("market.mnnEngineHint")}</p>` : ""}
    ` : ""}

    <h3 class="section">${t("market.requiredEngines")}</h3>
    <div class="list compact">
      ${engines.length === 0
        ? `<div class="hint">${t("market.engineUnassigned")}</div>`
        : engines.map((e) => `
          <div class="row">
            <div class="grow">
              <div class="name">${escapeHtml(typeof e === "string" ? e : (e.id || e.name || ""))}${e === "mnn" ? ` <span class="pill ok">${t("market.edgeAccel")}</span>` : ""}</div>
              <div class="sub">${escapeHtml(typeof e === "string" ? "" : (e.description || ""))}</div>
            </div>
            <span class="pill ${e.installed ? "ok" : ""}">${e.installed ? t("market.installed") : t("market.notInstalled")}</span>
            <button class="primary" data-action="install-engine"
                    data-id="${escapeHtml(typeof e === "string" ? e : e.id)}">${e.installed ? t("market.reinstall") : t("market.install")}</button>
            ${e.installed ? `<button class="danger" data-action="uninstall-engine"
              data-id="${escapeHtml(typeof e === "string" ? e : e.id)}">${t("market.uninstall")}</button>` : ""}
          </div>`).join("")}
    </div>

    <h3 class="section">${t("market.importLocalCopy")}</h3>
    <p class="hint">${t("market.importLocalHint")}</p>
    <button class="secondary" id="btn-import-local-for-detail">${t("market.chooseFile")}</button>

    ${m.description && isRemote ? `
      <h3 class="section">${t("models.detailTitle")}</h3>
      <p class="detail-prose">${escapeHtml(m.description)}</p>
    ` : ""}

    ${ggufFiles.length > 0 ? `
      <h3 class="section">${t("market.quantVersions", { n: ggufFiles.length })}</h3>
      <details><summary>${t("market.expandGguf")}</summary>
        <ul class="gguf-list">
          ${ggufFiles.map((f) => `<li>
            <span class="gguf-name">${escapeHtml(f.path || f.name || "")}</span>
            <span class="mut">${((f.size || 0) / 1e9).toFixed(2)} GB</span>
          </li>`).join("")}
        </ul>
      </details>
    ` : (m.gguf_repo ? `<div id="gguf-lazy"><p class="mut tiny">${t("market.loadingQuant")}</p></div>` : "")}
  `;
}

// 魔搭 exposes a Chinese display name; surface it when there is no prose yet.
function chineseNameOf(m) {
  return (m.name_cn || "").trim();
}
