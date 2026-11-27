// renderer/modules/mnn.js — MNN 引擎页：安装引擎 / 模型市场 / 下载 / 加载 / 真实对话。
"use strict";
import { api } from "./api.js";
import { toast } from "./toast.js";
import { isViewVisible, onViewState } from "./view-visibility.js";
import { t } from "./i18n.js";

const $ = (s, r) => (r || document).querySelector(s);

function esc(s) {
  return String(s == null ? "" : s).replace(/[&<>"']/g, (m) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[m]));
}

let _pollTimer = null;
let _lastDoneKey = "";
let _chatHistory = [];
let _lastCvtSrc = "";
let _lastCvtArch = "";
// R4 — 记住最近一次 renderMnnPage 的 root，视图切回时无需重新 render 即可
// 恢复下载轮询（renderMnnPage 本身也会在切回时跑，这里只为防御时序）。
let _lastRoot = null;

export async function renderMnnPage(root) {
  _lastRoot = root;
  // 每次 render 先停掉上一份下载轮询，避免反复切页叠加定时器。
  if (_pollTimer) { clearInterval(_pollTimer); _pollTimer = null; }
  root.innerHTML = `
    <div class="hw-toolbar">
      <div>
        <h2 class="hw-title">${t("mnn.title")}</h2>
        <p class="hint">${t("mnn.hint")}</p>
      </div>
      <div class="hw-toolbar-actions">
        <button class="secondary" id="mnn-refresh">${t("mnn.refresh")}</button>
      </div>
    </div>
    <div id="mnn-engine-state"></div>
    <div id="mnn-dl-progress" class="mnn-dl-card" hidden>
      <div class="mnn-dl-head">
        <span id="mnn-dl-name" class="sub"></span>
        <button class="ghost small" id="mnn-dl-cancel">${t("mnn.cancel")}</button>
      </div>
      <div class="hw-score-bar"><div id="mnn-dl-fill" class="hw-score-fill" style="width:0%"></div></div>
      <div id="mnn-dl-info" class="mut tiny"></div>
    </div>
    <div id="mnn-chat-card" class="mnn-chat-card" hidden></div>
    <h3 class="section">${t("mnn.downloadedTitle")}</h3>
    <div id="mnn-local" class="list"></div>
    <h3 class="section">${t("mnn.convertTitle")}</h3>
    <div id="mnn-convert" class="mnn-convert-card"></div>
    <h3 class="section">${t("mnn.marketTitle")} <span id="mnn-market-count" class="mut tiny"></span></h3>
    <div id="mnn-market" class="list"></div>
  `;

  $("#mnn-refresh", root).addEventListener("click", () => refreshAll(root));
  $("#mnn-dl-cancel", root).addEventListener("click", async () => {
    try { await api.mnnDownloadCancel(); toast(t("mnn.cancelRequested"), { kind: "ok" }); }
    catch (_) {}
  });

  await refreshAll(root);
  _ensureDownloadPoll(root);
}

// R4 — 下载轮询生命周期：视图可见时跑，隐藏时 clearInterval 彻底停掉。
// 定时器回调里再做一次可见性判断作为防御（切换事件与回调之间的竞态窗口）。
function _ensureDownloadPoll(root) {
  if (_pollTimer) return;
  _pollTimer = setInterval(() => {
    if (!isViewVisible("mnn")) return;
    pollDownload(root);
  }, 1500);
}

function _pausePolling() {
  if (_pollTimer) { clearInterval(_pollTimer); _pollTimer = null; }
  // 转换轮询一并暂停；切回时 renderMnnPage → renderConvert 会按侧端状态
  // 决定是否重启（仍在跑则 startConvertPoll，已完成则直接展示结果）。
  if (_convertPollTimer) { clearInterval(_convertPollTimer); _convertPollTimer = null; }
}

function _resumePolling() {
  if (!_lastRoot) return;
  _ensureDownloadPoll(_lastRoot);
  // 切回立即补拉一次下载状态，不用等 1.5s。
  pollDownload(_lastRoot);
}

onViewState("mnn", { onShow: _resumePolling, onHide: _pausePolling });

async function refreshAll(root) {
  await Promise.all([
    renderEngineState(root),
    renderLocal(root),
    renderConvert(root),
    renderMarket(root),
    pollDownload(root),
  ]);
}

// ---------------------------------------------------------------------------
// 引擎安装状态
// ---------------------------------------------------------------------------

async function renderEngineState(root) {
  const el = $("#mnn-engine-state", root);
  let st;
  try {
    const r = await api.mnnStatus();
    st = r?.body || r || {};
  } catch (e) {
    el.innerHTML = `<p class="hint">${t("mnn.statusFailed", { err: esc(e?.message || e) })}</p>`;
    return;
  }
  _renderEngineStateInner(root, el, st);
}

function _renderEngineStateInner(root, el, st) {
  const installed = !!st.engine_available;
  const rawVersion = String(st.engine_version || "").trim();
  const showVersion = rawVersion && !/^(unknown|n\/a|none|null|-)$/i.test(rawVersion);
  el.innerHTML = installed ? `
    <div class="mnn-state-card ok">
      <div class="grow">
        <div class="name">${t("mnn.ready")} ${showVersion ? `<span class="pill ok">v${esc(rawVersion)}</span>` : ""}</div>
        <div class="sub">${st.loaded
          ? t("mnn.loadedChat", { model: `<b>${esc(st.model_name)}</b>`, n: st.chat_count })
          : t("mnn.notLoaded")}</div>
      </div>
      ${st.loaded ? `<button class="danger small" data-action="mnn-unload">${t("mnn.unload")}</button>` : ""}
    </div>
  ` : `
    <div class="mnn-state-card warn">
      <div class="grow">
        <div class="name">${t("mnn.notInstalled")}</div>
        <div class="sub">${t("mnn.installHint")}</div>
      </div>
      <button class="primary" data-action="mnn-install">${t("mnn.install")}</button>
    </div>
  `;

  const installBtn = el.querySelector("[data-action=mnn-install]");
  if (installBtn) installBtn.addEventListener("click", async () => {
    installBtn.disabled = true;
    installBtn.textContent = t("mnn.installing");
    try {
      // MNN 是引擎而不是普通环境包：必须走 engine 安装路径，装到
      // <data_root>/engines/pip-mnn，冻结版 sidecar 才能导入到它。
      // 走 envInstallPip 时冻结版 pip-runner 会把包装进临时/用户目录，
      // 进程退出即丢失，界面会一直显示“MNN 引擎未安装”。
      await api.envInstallEngine({ id: "mnn" });
      toast(t("mnn.installDone"), { kind: "ok" });
      await renderEngineState(root);
    } catch (_) { /* toast shown */ }
    installBtn.disabled = false;
    installBtn.textContent = t("mnn.install");
  });

  const unloadBtn = el.querySelector("[data-action=mnn-unload]");
  if (unloadBtn) unloadBtn.addEventListener("click", async () => {
    try {
      await api.mnnUnload();
      _chatHistory = [];
      toast(t("mnn.unloadedModel"), { kind: "ok" });
      await refreshAll(root);
    } catch (_) {}
  });

  renderChatCard(root, st);
}

// ---------------------------------------------------------------------------
// 对话测试窗（真实推理）
// ---------------------------------------------------------------------------

function renderChatCard(root, st) {
  const card = $("#mnn-chat-card", root);
  if (!card) return;
  if (!st || !st.loaded) {
    card.hidden = true;
    delete card.dataset.mnnBuilt;
    return;
  }
  card.hidden = false;
  // status 轮询每 1.5s 调一次这里。之前每次都重写 innerHTML，会把用户正在
  // 输入的文本、已显示的对话和在途的“思考中”整块抹掉——输入框看起来像
  // 自己清空了，点击发送也偶发无请求。同一个模型只建一次 DOM；换模型或
  // 卸载后才重建。
  const modelKey = String(st.model_name || "");
  if (card.dataset.mnnBuilt === "1" && card.dataset.mnnModel === modelKey) {
    return;
  }
  card.dataset.mnnBuilt = "1";
  card.dataset.mnnModel = modelKey;
  card.innerHTML = `
    <div class="mnn-chat-head">
      <b>${t("mnn.chatWith", { model: esc(st.model_name) })}</b>
      <span class="mut tiny" id="mnn-chat-stat"></span>
    </div>
    <div class="mnn-chat-log" id="mnn-chat-log"></div>
    <div class="mnn-chat-input-row">
      <input id="mnn-chat-input" type="text" maxlength="4000"
             placeholder="${t("mnn.chatPlaceholder")}" autocomplete="off" />
      <button class="primary" id="mnn-chat-send">${t("mnn.send")}</button>
    </div>
  `;
  const log = $("#mnn-chat-log", card);
  _chatHistory.forEach((m) => appendChatMsg(log, m.role, m.content));
  if (!_chatHistory.length) {
    log.innerHTML = `<div class="mut tiny" style="padding:8px 2px">${t("mnn.tryHint")}</div>`;
  }
  const input = $("#mnn-chat-input", card);
  const send = async () => {
    const text = (input.value || "").trim();
    if (!text) return;
    input.value = "";
    input.disabled = true;
    appendChatMsg(log, "user", text);
    const thinking = appendChatMsg(log, "assistant", t("mnn.thinking"));
    try {
      const r = await api.mnnChat({ prompt: text, history: _chatHistory.slice(-20), max_new_tokens: 512 });
      const b = r?.body || r || {};
      thinking.textContent = b.text || t("mnn.emptyReply");
      const stat = $("#mnn-chat-stat", card);
      if (stat) stat.textContent = t("mnn.rate", { s: b.elapsed_s || "?", cps: b.speed_cps || "?" });
      _chatHistory.push({ role: "user", content: text });
      _chatHistory.push({ role: "assistant", content: b.text || "" });
      // 下载轮询只刷新下载状态，不会刷新引擎卡片里的对话计数。这里主动
      // 重渲染一次；renderChatCard 对同一模型已做“只建一次”保护，不会
      // 抹掉刚显示的对话。失败也不能把已经成功的回复改成报错。
      try { await renderEngineState(root); } catch (_) { /* 状态刷新失败不影响本轮回复 */ }
    } catch (e) {
      thinking.textContent = t("mnn.chatError", { err: e?.message || e });
    }
    input.disabled = false;
    input.focus();
  };
  $("#mnn-chat-send", card).addEventListener("click", send);
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter") { e.preventDefault(); send(); }
  });
  input.focus();
}

function appendChatMsg(log, role, text) {
  if (log.dataset.cleared !== "1") { log.innerHTML = ""; log.dataset.cleared = "1"; }
  const div = document.createElement("div");
  div.className = `mnn-msg ${role}`;
  div.textContent = text;
  log.appendChild(div);
  log.scrollTop = log.scrollHeight;
  return div;
}

// ---------------------------------------------------------------------------
// 已下载模型
// ---------------------------------------------------------------------------

async function renderLocal(root) {
  const el = $("#mnn-local", root);
  let data;
  try {
    const r = await api.mnnLocal();
    data = r?.body || r || {};
  } catch (e) {
    el.innerHTML = `<p class="hint">${t("mnn.readFailed", { err: esc(e?.message || e) })}</p>`;
    return;
  }
  const models = data.models || [];
  if (!models.length) {
    el.innerHTML = `<p class="hint">${t("mnn.noDownloaded")}</p>`;
    return;
  }
  let st = {};
  try { const r = await api.mnnStatus(); st = r?.body || r || {}; } catch (_) {}
  el.innerHTML = models.map((m) => `
    <div class="row">
      <div class="grow">
        <div class="name">${esc(m.id)}
          ${st.loaded && st.model_dir === m.dir ? `<span class="pill ok">${t("mnn.loaded")}</span>` : ""}</div>
        <div class="sub" title="${esc(m.dir)}">${esc(m.dir)} · ${esc(m.size_gb)} GB</div>
      </div>
      ${st.loaded && st.model_dir === m.dir ? "" : `<button class="primary small" data-action="mnn-load" data-dir="${esc(m.dir)}" data-name="${esc(m.id)}">${t("mnn.load")}</button>`}
    </div>
  `).join("");

  el.querySelectorAll("[data-action=mnn-load]").forEach((b) => {
    b.addEventListener("click", async () => {
      b.disabled = true;
      b.textContent = t("mnn.loading");
      try {
        await api.mnnLoad({ model_dir: b.dataset.dir, model_name: b.dataset.name });
        toast(t("mnn.loadedModel", { name: b.dataset.name }), { kind: "ok" });
        _chatHistory = [];
        await refreshAll(root);
      } catch (_) { /* toast shown */ }
    });
  });
}

// ---------------------------------------------------------------------------
// 模型格式转换（HF → MNN）
// ---------------------------------------------------------------------------

let _convertPollTimer = null;


let _lastCvtKind = localStorage.getItem("kevrai_cvt_kind") || "hf-to-mnn-llm";
let _lastCvtTask = "";

function _cvtDstFor(kind, src) {
  const base = src.replace(/[\\/]+$/, "");
  const name = base.split(/[\\/]/).pop() || "model";
  switch (kind) {
    case "hf-to-mnn-llm": return `${name}-mnn`;
    case "hf-to-gguf": return `${name}-gguf`;
    case "hf-to-onnx": return `${name}-onnx`;
    case "hf-to-mlx": return `${name}-mlx`;
    case "onnx-to-mnn": return `${name}.mnn`;
    case "torch-to-mnn": return `${name}.mnn`;
    default: return `${name}-out`;
  }
}

function _cvtHint(kind) {
  switch (kind) {
    case "hf-to-mnn-llm":
      return t("mnn.descMllm");
    case "hf-to-gguf":
      return t("mnn.descGguf");
    case "hf-to-onnx":
      return t("mnn.descOnnx");
    case "hf-to-mlx":
      return t("mnn.descMlx");
    case "onnx-to-mnn":
      return t("mnn.descConvert");
    case "torch-to-mnn":
      return t("mnn.descConvertTs");
    default:
      return "";
  }
}

async function renderConvert(root) {
  const el = $("#mnn-convert", root);
  if (!el) return;
  let caps;
  try {
    const r = await api.convertCapabilities();
    caps = r?.body || r || {};
  } catch (_) { caps = {}; }

  let active = null;
  let tasks = [];
  try {
    const r = await api.convertTasks();
    const b = r?.body || r || {};
    tasks = b.tasks || [];
    active = b.active || null;
  } catch (_) { /* keep empty */ }

  const running = active && ["pending", "preparing", "running"].includes(active.status);
  const kind = _lastCvtKind;

  const kinds = [
    ["hf-to-mnn-llm", t("mnn.cvtTypeMllm")],
    ["hf-to-gguf", "HF → GGUF（llama.cpp / ollama）"],
    ["hf-to-onnx", "HF → ONNX（onnxruntime）"],
    ["hf-to-mlx", "HF → MLX（Apple Silicon）"],
    ["onnx-to-mnn", "ONNX → MNN"],
    ["torch-to-mnn", "TorchScript → MNN"],
  ];

  const srcPh = kind.startsWith("hf-")
    ? t("mnn.cvtSrcHfDir")
    : (kind === "onnx-to-mnn" ? t("mnn.cvtSrcOnnx") : t("mnn.cvtSrcTs"));
  const hint = _cvtHint(kind);

  el.innerHTML = `
    <p class="hint">${hint}</p>
    <div class="mnn-convert-row">
      <label class="mut tiny" style="margin-right:6px">${t("mnn.cvtTypeLabel")}</label>
      <select id="mnn-cvt-kind">
        ${kinds.map(([v, label]) => `<option value="${v}" ${v === kind ? "selected" : ""}>${label}</option>`).join("")}
      </select>
    </div>
    <div class="mnn-convert-row">
      <input id="mnn-cvt-src" type="text" placeholder="${srcPh}"
             value="${esc(_lastCvtSrc || "")}" autocomplete="off" style="flex:1" />
      <button class="ghost small" id="mnn-cvt-pick-src" title="${t("mnn.cvtPickSrcTitle")}">${t("mnn.cvtPickSrc")}</button>
    </div>
    <div id="mnn-cvt-opts">
      ${kind === "hf-to-mnn-llm" ? `
      <div class="mnn-convert-row">
        <input id="mnn-cvt-arch" type="text" placeholder="${t("mnn.cvtArchPh")}"
               value="${esc(_lastCvtArch || "")}" autocomplete="off" style="flex:1" />
      </div>
      <div class="mnn-convert-row">
        <label class="mut tiny" style="margin-right:6px">${t("mnn.cvtQuantLabel")}</label>
        <select id="mnn-cvt-quant">
          <option value="4" selected>${t("mnn.cvtQ4")}</option>
          <option value="8">${t("mnn.cvtQ8")}</option>
          <option value="0">${t("mnn.cvtQ0")}</option>
        </select>
      </div>` : ""}
      ${kind === "hf-to-gguf" ? `
      <div class="mnn-convert-row">
        <label class="mut tiny" style="margin-right:6px">${t("mnn.cvtOutPrecLabel")}</label>
        <select id="mnn-cvt-outtype">
          <option value="f16" selected>${t("mnn.cvtF16")}</option>
          <option value="f32">f32</option>
          <option value="bf16">bf16</option>
        </select>
      </div>` : ""}
      ${kind === "hf-to-onnx" ? `
      <div class="mnn-convert-row">
        <input id="mnn-cvt-task" type="text" placeholder="${t("mnn.cvtTaskPh")}"
               value="${esc(_lastCvtTask || "")}" autocomplete="off" style="flex:1" />
      </div>` : ""}
      ${kind === "hf-to-mlx" ? `
      <div class="mnn-convert-row">
        <label class="mut tiny" style="margin-right:6px"><input type="checkbox" id="mnn-cvt-quantize" checked /> ${t("mnn.cvt4bit")}</label>
      </div>` : ""}
      ${(kind === "onnx-to-mnn" || kind === "torch-to-mnn") ? `
      <div class="mnn-convert-row">
        <label class="mut tiny" style="margin-right:6px">${t("mnn.cvtBitsLabel")}</label>
        <select id="mnn-cvt-wqb">
          <option value="">${t("mnn.cvtNoQuant")}</option>
          <option value="4">int4</option>
          <option value="8">int8</option>
        </select>
      </div>` : ""}
    </div>
    <div class="mnn-convert-row">
      <button class="primary small" id="mnn-cvt-start" ${running ? "disabled" : ""}>${running ? t("mnn.cvtConverting") : t("mnn.cvtStart")}</button>
      <button class="danger small" id="mnn-cvt-cancel" ${running ? "" : "hidden"}>${t("mnn.cancel")}</button>
      <span class="mut tiny" style="margin-left:8px">${t("mnn.cvtOutputPrefix")}${_cvtDstFor(kind, t("mnn.cvtOutputExample"))}</span>
    </div>
    <div id="mnn-cvt-progress" ${running ? "" : "hidden"}>
      <div class="hw-score-bar"><div id="mnn-cvt-fill" class="hw-score-fill" style="width:${Math.round((active?.progress || 0) * 100)}%"></div></div>
      <div class="mut tiny" id="mnn-cvt-status">${esc(running ? active.status : "")}</div>
    </div>
    <div id="mnn-cvt-log" class="mut tiny mnn-cvt-log"></div>
    ${tasks.length ? `<div class="mut tiny" style="margin-top:6px">${t("mnn.cvtRecentPrefix")}${tasks.slice(-3).map((t) => `${esc(t.kind)} → <b>${esc(t.status)}</b>`).join(" · ")}</div>` : ""}
  `;

  const srcInput = $("#mnn-cvt-src", el);
  const kindSel = $("#mnn-cvt-kind", el);
  kindSel.addEventListener("change", () => {
    _lastCvtKind = kindSel.value;
    localStorage.setItem("kevrai_cvt_kind", _lastCvtKind);
    renderConvert(root);
  });
  $("#mnn-cvt-pick-src", el).addEventListener("click", async () => {
    try {
      const p = await api.pickFolder();
      if (p) { srcInput.value = p; _lastCvtSrc = p; }
    } catch (_) {}
  });
  srcInput.addEventListener("change", () => { _lastCvtSrc = srcInput.value; });
  const archInput = $("#mnn-cvt-arch", el);
  if (archInput) archInput.addEventListener("change", () => { _lastCvtArch = archInput.value; });
  const taskInput = $("#mnn-cvt-task", el);
  if (taskInput) taskInput.addEventListener("change", () => { _lastCvtTask = taskInput.value; });

  $("#mnn-cvt-start", el).addEventListener("click", async () => {
    const src = (srcInput.value || "").trim();
    if (!src) { toast(t("mnn.cvtNeedSrc"), { kind: "err" }); return; }
    _lastCvtSrc = src;
    const dst = _cvtDstFor(kind, src);
    const payload = { kind, src, dst };
    if (kind === "hf-to-mnn-llm") {
      payload.arch = (archInput?.value || "").trim();
      _lastCvtArch = payload.arch;
      payload.quant_bit = Number($("#mnn-cvt-quant", el).value || 4);
    }
    if (kind === "hf-to-gguf") {
      payload.outtype = $("#mnn-cvt-outtype", el).value || "f16";
    }
    if (kind === "hf-to-onnx") {
      payload.task = (taskInput?.value || "").trim();
      _lastCvtTask = payload.task;
    }
    if (kind === "hf-to-mlx") {
      payload.quantize = !!$("#mnn-cvt-quantize", el).checked;
    }
    if (kind === "onnx-to-mnn" || kind === "torch-to-mnn") {
      const wqb = $("#mnn-cvt-wqb", el).value;
      if (wqb) payload.weight_quant_bits = Number(wqb);
    }
    const b = $("#mnn-cvt-start", el);
    b.disabled = true; b.textContent = t("mnn.cvtStarting");
    try {
      await api.convertStart(payload);
      toast(t("mnn.cvtStarted"), { kind: "ok" });
      await renderConvert(root);
      startConvertPoll(root);
    } catch (e) {
      b.disabled = false; b.textContent = t("mnn.cvtStart");
    }
  });

  const cancelBtn = $("#mnn-cvt-cancel", el);
  if (cancelBtn) cancelBtn.addEventListener("click", async () => {
    if (active) {
      try { await api.convertCancel(active.id); toast(t("mnn.cvtCancelRequested"), { kind: "ok" }); }
      catch (_) {}
    }
  });

  if (running) { startConvertPoll(root); renderConvertLog(el, active); }
}

function renderConvertLog(el, t) {
  const logEl = $("#mnn-cvt-log", el);
  if (!logEl || !t) return;
  const lines = (t.log || []).slice(-12);
  logEl.innerHTML = lines.map((l) => `<div>${esc(l)}</div>`).join("");
  logEl.scrollTop = logEl.scrollHeight;
}

function startConvertPoll(root) {
  if (_convertPollTimer) return;
  _convertPollTimer = setInterval(async () => {
    try {
      const r = await api.convertTasks();
      const b = r?.body || r || {};
      const active = b.active || null;
      const el = $("#mnn-convert", root);
      if (!el) return;
      if (active && ["pending", "preparing", "running"].includes(active.status)) {
        const fill = $("#mnn-cvt-fill", el);
        if (fill) fill.style.width = `${Math.round((active.progress || 0) * 100)}%`;
        const st = $("#mnn-cvt-status", el);
        if (st) st.textContent = active.status;
        renderConvertLog(el, active);
        return;
      }
      // finished
      clearInterval(_convertPollTimer); _convertPollTimer = null;
      const box = $("#mnn-cvt-progress", el);
      if (box) box.hidden = true;
      const startBtn = $("#mnn-cvt-start", el);
      if (startBtn) { startBtn.disabled = false; startBtn.textContent = t("mnn.cvtStart"); }
      const cancelBtn = $("#mnn-cvt-cancel", el);
      if (cancelBtn) cancelBtn.hidden = true;
      if (active && active.status === "done") {
        toast(t("mnn.cvtDone"), { kind: "ok" });
      } else if (active && active.status === "failed") {
        toast(t("mnn.cvtFailed", { err: active.error || t("mnn.unknownError") }), { kind: "err" });
      }
      await refreshAll(root);
    } catch (_) {
      clearInterval(_convertPollTimer); _convertPollTimer = null;
    }
  }, 2000);
}

// ---------------------------------------------------------------------------
// 模型市场
// ---------------------------------------------------------------------------

async function renderMarket(root) {
  const el = $("#mnn-market", root);
  let data;
  try {
    const r = await api.mnnModels();
    data = r?.body || r || {};
  } catch (e) {
    el.innerHTML = `<p class="hint">${t("mnn.marketLoadFailed", { err: esc(e?.message || e) })}</p>`;
    return;
  }
  const models = data.models || [];
  const countEl = $("#mnn-market-count", root);
  if (countEl) countEl.textContent = t("mnn.marketCount", { n: models.length });
  el.innerHTML = models.map((m) => {
    const sizeGb = Number(m.size_gb) || 0;
    const ram = Number((m.hardware || {}).ram_gb) || 0;
    const sizeText = sizeGb > 0 ? t("mnn.approxGb", { gb: esc(m.size_gb) }) : t("mnn.sizeAtDownload");
    const ramText = ram > 0 ? t("mnn.suggestRam", { ram }) : t("mnn.ramAtDownload");
    return `
    <div class="row">
      <div class="grow">
        <div class="name">${esc(m.name)}
          ${m.trending ? '<span class="pill warn">🔥</span>' : ""}
          <span class="pill">${esc(m.quant || "int4")}</span>
          ${m.live ? `<span class="pill">${t("mnn.liveBadge")}</span>` : ""}
        </div>
        <div class="sub">${esc(m.description || "")}</div>
        <div class="sub mut">${esc(m.repo)} · ${sizeText} · ${ramText}</div>
        <div class="mnn-row-dl" data-mnn-row="${esc(m.dest_id || m.id)}" hidden>
          <div class="hw-score-bar"><div class="hw-score-fill" data-mnn-row-fill style="width:0%"></div></div>
          <div class="mut tiny" data-mnn-row-info></div>
        </div>
      </div>
      <div class="hw-rec-side">
        <span class="pill">${esc(m.category || "llm")}</span>
        <button class="primary small" data-action="mnn-download" data-id="${esc(m.id)}">${t("mnn.download")}</button>
      </div>
    </div>`;
  }).join("");

  el.querySelectorAll("[data-action=mnn-download]").forEach((b) => {
    b.addEventListener("click", async () => {
      const model = models.find((m) => m.id === b.dataset.id);
      if (!model) return;
      b.disabled = true;
      try {
        // Live catalog rows are downloaded by repo; static curated rows keep
        // their stable entry_id (and its friendlier dest folder).
        _lastDoneKey = "";
        await api.mnnDownload(model.live ? { repo: model.repo } : model.id);
        toast(t("mnn.startDownloadMnn"), { kind: "ok" });
        pollDownload(root);
      } catch (_) { /* toast shown (409 已存在/进行中) */ }
      b.disabled = false;
    });
  });
}

// ---------------------------------------------------------------------------
// 下载进度轮询
// ---------------------------------------------------------------------------

async function pollDownload(root) {
  const box = $("#mnn-dl-progress", root);
  if (!box) return;
  let st;
  try {
    const r = await api.mnnDownloadStatus();
    st = r?.body || r || {};
  } catch (_) { return; }
  if (!st || (!st.active && st.status === "idle")) {
    box.hidden = true;
    root.querySelectorAll("[data-mnn-row]").forEach((row) => { row.hidden = true; });
    return;
  }
  box.hidden = false;
  $("#mnn-dl-name", root).textContent = t("mnn.downloadingN", { name: st.name || st.entry_id, done: st.files_done, total: st.files_total });
  // Before the sidecar has enumerated file sizes, bytes_total is 0; fall back
  // to completed-files ratio so the bar shows real movement instead of a
  // permanently empty 0% bar.
  const pct = st.bytes_total > 0
    ? Math.min(100, Math.round((st.bytes_done / st.bytes_total) * 100))
    : (st.files_total > 0 ? Math.min(100, Math.round((st.files_done / st.files_total) * 100)) : 0);
  $("#mnn-dl-fill", root).style.width = `${pct}%`;
  const info = `${(st.bytes_done / 1e9).toFixed(2)} / ${(st.bytes_total / 1e9).toFixed(2)} GB · ${pct}% · ${st.current_file || ""} ${st.error ? "· " + st.error : ""}`;
  $("#mnn-dl-info", root).textContent = info;
  // Mirror the same progress into the active model's own row, so the bar
  // follows the model the user clicked instead of living only at page bottom.
  root.querySelectorAll("[data-mnn-row]").forEach((row) => { row.hidden = true; });
  for (const row of root.querySelectorAll("[data-mnn-row]")) {
    if (row.dataset.mnnRow !== (st.entry_id || "")) continue;
    row.hidden = false;
    const fill = row.querySelector("[data-mnn-row-fill]");
    const label = row.querySelector("[data-mnn-row-info]");
    if (fill) fill.style.width = `${pct}%`;
    if (label) label.textContent = info;
    break;
  }
  if (!st.active && (st.status === "done" || st.status === "failed" || st.status === "cancelled")) {
    const visible = !box.hidden;
    // Terminal states must not call refreshAll(): refreshAll() runs
    // pollDownload() again, and a still-"done" status would recursively re-enter
    // this branch, firing the completion toast and rebuilding the page on every
    // poll tick. Refresh only the lists, after hiding the progress card.
    box.hidden = true;
    if (st.status === "done") {
      const doneKey = `${st.entry_id || ""}:${st.files_total || 0}:${st.bytes_total || 0}`;
      if (doneKey !== _lastDoneKey) {
        _lastDoneKey = doneKey;
        if (visible) toast(t("mnn.downloadMnnDone"), { kind: "ok" });
      }
      await Promise.all([renderLocal(root), renderMarket(root)]);
    } else if (st.status === "failed") {
      if (visible) toast(t("mnn.downloadFailed", { err: st.error || t("mnn.unknownError") }), { kind: "err" });
    } else if (st.status === "cancelled") {
      if (visible) toast(t("mnn.downloadCancelled"), { kind: "ok" });
      await Promise.all([renderLocal(root), renderMarket(root)]);
    }
  }
}
