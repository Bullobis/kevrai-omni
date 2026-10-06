// renderer/modules/version-select.js — 安装前选择模型版本/量化。
//
// 流程：点卡片「安装」→ 若该模型在远程 hub（hf / modelscope）且有 repo，
// 先拉仓库文件树（复用 GET /api/hub/model/files，后端已封装 HF/ModelScope
// 两个上游），筛出 .gguf 并按量化档位排序，弹模态让用户选定具体文件；
// 用户确认后由调用方带选定文件名走原下载（POST /api/hub/download）。
//
// 回退原则（硬约束）：
//   · 拉文件树失败 / 超时 / 空列表 → 面板内给「使用默认版本」选项；
//   · 用户取消（Esc / × / 点遮罩）→ resolve(null)，调用方中止安装，什么都不做；
//   · 非远程 hub 模型（curated / MNN / 引擎本身）→ supportsVersionSelect=false，
//     调用方直接走原有安装逻辑。
//
// 纯函数（parseVersions / extractQuant / formatSize / sortVersions /
// isRecommended / supportsVersionSelect）不依赖网络与 DOM，供单元测试直接驱动。
"use strict";
import { api } from "./api.js";
import { unwrap, escapeHtml } from "./net.js";
import { t } from "./i18n.js";
import {
  recordFocus, restoreFocus, trapFocus, registerEsc,
} from "./focus-return.js";
import { overlayOpen, overlayClose } from "./overlay-fx.js";

// ── 纯函数：量化识别 / 解析 / 排序 / 格式化 ──────────────────────────────────

// llama.cpp 常见量化档位，按「体积小 → 质量高」排序，用于默认次序。
// 未列入表内的量化（如 IQ4_XS、自定义后缀）排到已知档位之后。
const QUANT_ORDER = [
  "Q2_K", "Q3_K_S", "Q3_K_M", "Q3_K_L",
  "Q4_K_S", "Q4_K_M", "Q5_K_S", "Q5_K_M",
  "Q6_K", "Q8_0", "F16", "F32",
];

// 平衡之选：显存/体积与质量的通常最佳折中。
const RECOMMENDED_QUANT = "Q4_K_M";

// 远程 hub 中支持文件树枚举的来源。
const SELECTABLE_HUBS = new Set(["hf", "modelscope"]);

// 从文件名里提取量化标签。覆盖 llama.cpp 常见写法：
//   xxx-Q4_K_M.gguf / xxx-Q5_K_M / xxx-Q6_K / xxx-Q8_0 / xxx-Q2_K /
//   xxx-F16.gguf / xxx-f16.gguf
// 找不到则返回 ""（调用方把它当未知档位排到末尾）。
const QUANT_RE = /\b((?:Q[2-8]_0|Q[2-8]_K(?:_[SML])?|IQ\d_[A-Z]+|F16|F32))\b/gi;

export function extractQuant(filename) {
  const name = String(filename || "");
  QUANT_RE.lastIndex = 0;
  const m = QUANT_RE.exec(name);
  if (!m) return "";
  return m[1].toUpperCase();
}

// 是否为推荐档位（平衡之选）。
export function isRecommended(quant) {
  return String(quant || "").toUpperCase() === RECOMMENDED_QUANT;
}

/**
 * 把后端文件树（{files: [{path,name,size,...}]} 里的 files 数组）解析成
 * 可选版本列表：只留 .gguf，剔除目录/配置/分词器等非权重文件。
 * 返回 [{ file: <下载用 path>, name, sizeBytes, quant }]。
 */
export function parseVersions(files) {
  if (!Array.isArray(files)) return [];
  const out = [];
  const seen = new Set();
  for (const f of files) {
    if (!f || typeof f !== "object") continue;
    const path = String(f.path || f.name || "");
    if (!path || seen.has(path)) continue;
    if (!/\.gguf$/i.test(path)) continue;
    seen.add(path);
    out.push({
      file: path,                       // hubDownload 按 path 建任务
      name: String(f.name || path.split("/").pop() || path),
      sizeBytes: Number(f.size) || 0,
      quant: extractQuant(path),
    });
  }
  return out;
}

// 字节数 → 人类可读（与 downloads.js 的 fmtBytes 同口径：1024 进制）。
// 0 / 非法输入返回 ""，不渲染 "0 B" 这类无信息文本。
export function formatSize(bytes) {
  const v = Number(bytes);
  if (!Number.isFinite(v) || v <= 0) return "";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let i = 0;
  let x = v;
  while (x >= 1024 && i < units.length - 1) { x /= 1024; i += 1; }
  return `${x.toFixed(x >= 100 || i === 0 ? 0 : 1)} ${units[i]}`;
}

// 排序：推荐档（Q4_K_M）恒在最前；其余按量化档位表升序；同档按体积升序；
// 未知量化排最后，按体积升序。返回新数组，不改输入。
export function sortVersions(list) {
  const rank = (q) => {
    const i = QUANT_ORDER.indexOf(String(q || "").toUpperCase());
    return i === -1 ? QUANT_ORDER.length : i;
  };
  return [...(Array.isArray(list) ? list : [])].sort((a, b) => {
    const ra = isRecommended(a.quant) ? 0 : 1;
    const rb = isRecommended(b.quant) ? 0 : 1;
    if (ra !== rb) return ra - rb;
    const qa = rank(a.quant), qb = rank(b.quant);
    if (qa !== qb) return qa - qb;
    return (Number(a.sizeBytes) || 0) - (Number(b.sizeBytes) || 0);
  });
}

// 该模型是否支持「安装前选版本」：远程 hub（hf/modelscope）且有合法 repo。
// curated 目录模型、MNN、引擎本身一律不支持，调用方走原有逻辑。
export function supportsVersionSelect(item) {
  if (!item || typeof item !== "object") return false;
  if (!SELECTABLE_HUBS.has(item.hub)) return false;
  const repo = String(item.repo || "");
  return repo.includes("/") && repo.length <= 200;
}

// ── 网络：拉文件树 ──────────────────────────────────────────────────────────

function withTimeout(promise, ms) {
  return Promise.race([
    promise,
    new Promise((_, rej) => setTimeout(() => rej(new Error("timeout")), ms)),
  ]);
}

/**
 * 拉取并解析一个远程仓库的可选 GGUF 版本。失败时抛错（调用方/面板自行回退）。
 */
export async function fetchVersions(hub, repo, { timeoutMs = 8000 } = {}) {
  const r = await withTimeout(api.hubFiles({ hub, repo }), timeoutMs);
  const body = unwrap(r, {});
  return sortVersions(parseVersions(body.files));
}

// ── 模态面板 ────────────────────────────────────────────────────────────────

// Esc 优先级栈位置：高于下载面板(70)? 不——选择面板是更上层的临时对话框，
// 取 80：Esc 先关它。
const ESC_ORDER = 80;

function rowHtml(v, checked) {
  const rec = isRecommended(v.quant);
  const size = formatSize(v.sizeBytes);
  return `<label class="ver-row" title="${escapeHtml(v.file)}">`
    + `<input type="radio" name="ver-choice" value="${escapeHtml(v.file)}"${checked ? " checked" : ""} />`
    + `<span class="ver-quant">${escapeHtml(v.quant || v.name)}</span>`
    + `<span class="mut">${escapeHtml(size)}</span>`
    + (rec ? `<span class="pill ok">${escapeHtml(t("version.recommended"))}</span>` : "")
    + `</label>`;
}

function defaultRowHtml(checked) {
  return `<label class="ver-row">`
    + `<input type="radio" name="ver-choice" value=""${checked ? " checked" : ""} />`
    + `<span class="ver-quant">${escapeHtml(t("version.useDefault"))}</span>`
    + `</label>`;
}

/**
 * 弹出版本选择面板。
 * @param {object} model  至少 { name, hub, repo }
 * @returns {Promise<null|{file: string|null}>}
 *   null          —— 用户取消（Esc/×/遮罩），调用方应中止安装；
 *   {file: path}   —— 用户选定具体 .gguf 文件；
 *   {file: null}   —— 用户在失败回退里选了「使用默认版本」。
 */
export function openVersionSelector(model) {
  return new Promise((resolve) => {
    const name = String((model && (model.name || model.id || model.repo)) || "");
    const el = document.createElement("div");
    el.className = "overlay ver-overlay";
    el.setAttribute("role", "dialog");
    el.setAttribute("aria-modal", "true");
    el.setAttribute("aria-label", t("version.title"));
    el.innerHTML = `
      <div class="overlay-card" role="document">
        <header class="panel-head">
          <h2 id="ver-title">${escapeHtml(name)} · ${escapeHtml(t("version.title"))}</h2>
          <button class="ghost" data-action="ver-close" aria-label="${escapeHtml(t("version.cancel"))}">×</button>
        </header>
        <div id="ver-body" class="list" aria-live="polite">
          <div class="hint">${escapeHtml(t("version.loading"))}</div>
        </div>
        <footer class="ver-foot">
          <button class="secondary" data-action="ver-cancel">${escapeHtml(t("version.cancel"))}</button>
          <button class="primary" data-action="ver-confirm" disabled>${escapeHtml(t("version.confirm"))}</button>
        </footer>
      </div>
    `;
    document.body.appendChild(el);

    const trigger = recordFocus();
    overlayOpen(el);
    trapFocus(el);

    let settled = false;
    let detachTrap = null;
    let unregisterEsc = null;

    const confirmBtn = el.querySelector("[data-action=ver-confirm]");
    const bodyBox = el.querySelector("#ver-body");

    const finish = (val) => {
      if (settled) return;
      settled = true;
      try { unregisterEsc?.(); } catch (_) {}
      try { detachTrap?.(); } catch (_) {}
      restoreFocus(trigger);
      overlayClose(el, { done: () => el.remove() });
      resolve(val);
    };

    const enableConfirm = (on) => { confirmBtn.disabled = !on; };

    const renderList = (versions) => {
      if (!versions || !versions.length) {
        // 空列表 = 该仓库没有可选项 → 给默认版本回退。
        bodyBox.innerHTML = `<p class="hint">${escapeHtml(t("version.empty"))}</p>`
          + defaultRowHtml(true);
        enableConfirm(true);
      } else {
        bodyBox.innerHTML = versions.map((v, i) => rowHtml(v, i === 0)).join("");
        enableConfirm(true);
      }
    };

    const renderError = () => {
      bodyBox.innerHTML = `<p class="hint">${escapeHtml(t("version.loadFailed"))}</p>`
        + defaultRowHtml(true);
      enableConfirm(true);
    };

    confirmBtn.addEventListener("click", () => {
      const sel = el.querySelector("input[name=ver-choice]:checked");
      if (!sel) return;
      finish({ file: sel.value || null });
    });
    el.addEventListener("click", (e) => {
      if (e.target === el) finish(null);                       // 点遮罩 = 取消
      if (e.target.closest("[data-action=ver-close], [data-action=ver-cancel]")) {
        finish(null);
      }
    });
    unregisterEsc = registerEsc({
      order: ESC_ORDER,
      root: el,
      isOpen: () => !el.hasAttribute("hidden"),
      close: () => finish(null),
    });
    detachTrap = trapFocus(el);

    // 拉文件树：面板已可见，加载态在面板内呈现，失败就地回退。
    fetchVersions(model.hub, model.repo)
      .then((versions) => { if (!settled) renderList(versions); })
      .catch(() => { if (!settled) renderError(); });
  });
}
