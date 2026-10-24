// renderer/modules/engine-picker.js — 安装模型时让用户自己选引擎。
//
// 真实问题：installItem() 里写死 `api.installEngine(engines[0])`，一个模型
// 支持 llama.cpp / mnn / transformers 多个引擎时，用户没有任何选择余地——
// 装出来的永远是目录里的第一个，而不是他机器上跑得动或他想要的那个。
// 注释里写着"multi-engine models can pick a different engine from the detail
// panel"，但详情面板上的按钮只装引擎、不下权重，根本不是同一件事。
//
// 这里补上：多引擎时弹出选择，并记住上次选择（可按模型分别记，也可全局记）。
"use strict";

const KEY_PREFIX = "kevrai:engine-choice:";
const KEY_GLOBAL = "kevrai:engine-choice-global";

/**
 * 决定该用哪个引擎。
 *
 * 优先级：显式传入 > 该模型上次的记忆 > 全局默认 > 目录里的第一个。
 * 记忆值如果已经不在候选里（模型更新后引擎列表变了）就忽略，不能拿一个
 * 不存在的引擎去安装。
 *
 * @param {string[]} engines 候选引擎 id
 * @param {{modelId?:string, remembered?:Record<string,string>, globalDefault?:string}} opts
 */
export function resolveEngine(engines, opts = {}) {
  const list = (engines || []).filter((x) => typeof x === "string" && x);
  if (!list.length) return null;

  const remembered = opts.remembered || {};
  const mid = opts.modelId || "";
  const candidates = [];
  if (mid && remembered[mid]) candidates.push(remembered[mid]);
  if (opts.globalDefault) candidates.push(opts.globalDefault);

  for (const c of candidates) {
    if (list.includes(c)) return { engine: c, remembered: true };
  }
  return { engine: list[0], remembered: false };
}

/** 读取全部"按模型记忆"的引擎选择。localStorage 不可用/损坏时返回空对象。 */
export function readRemembered() {
  try {
    const out = {};
    for (let i = 0; i < localStorage.length; i++) {
      const k = localStorage.key(i);
      if (!k || !k.startsWith(KEY_PREFIX)) continue;
      const v = localStorage.getItem(k);
      if (v) out[k.slice(KEY_PREFIX.length)] = v;
    }
    return out;
  } catch (_) {
    return {};
  }
}

/** 记住某个模型的引擎选择。 */
export function rememberEngine(modelId, engine) {
  if (!modelId || !engine) return;
  try { localStorage.setItem(KEY_PREFIX + modelId, engine); } catch (_) {}
}

/** 读取全局默认引擎。 */
export function readGlobalDefault() {
  try { return localStorage.getItem(KEY_GLOBAL) || ""; } catch (_) { return ""; }
}

/** 设置（或传入空串清除）全局默认引擎。 */
export function setGlobalDefault(engine) {
  try {
    if (engine) localStorage.setItem(KEY_GLOBAL, engine);
    else localStorage.removeItem(KEY_GLOBAL);
  } catch (_) {}
}

function esc(s) {
  return String(s == null ? "" : s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}

/**
 * 弹出引擎选择面板。
 *
 * @returns {Promise<null|{engine:string, remember:boolean, global:boolean}>}
 *   null 表示用户取消——调用方必须据此中止安装，不能退回用默认引擎继续装。
 */
export function pickEngine({ engines = [], modelId = "", modelName = "", t = (k) => k } = {}) {
  const list = (engines || []).filter((x) => typeof x === "string" && x);
  if (list.length <= 1) {
    return Promise.resolve(list.length === 1 ? { engine: list[0], remember: false, global: false } : null);
  }

  return new Promise((resolve) => {
    const overlay = document.createElement("div");
    overlay.className = "overlay";
    overlay.setAttribute("role", "dialog");
    overlay.setAttribute("aria-modal", "true");

    const remembered = readRemembered();
    const g = readGlobalDefault();
    const current = resolveEngine(list, { modelId, remembered, globalDefault: g });

    const rows = list.map((e) => `
      <label class="engine-pick-row" data-engine="${esc(e)}">
        <input type="radio" name="kevrai-engine-pick" value="${esc(e)}"
               ${e === current.engine ? "checked" : ""} />
        <span class="ep-name">${esc(e)}</span>
      </label>`).join("");

    overlay.innerHTML = `
      <div class="overlay-card" role="document">
        <header>
          <h2>${esc(t("enginePick.title"))}</h2>
          <button class="ghost icon" type="button" data-act="close" aria-label="${esc(t("app.close"))}">×</button>
        </header>
        <p class="hint">${esc(t("enginePick.hint"))}${modelName ? ` — ${esc(modelName)}` : ""}</p>
        <div class="engine-pick-list">${rows}</div>
        <label class="engine-pick-flag">
          <input type="checkbox" data-flag="remember" checked />
          <span>${esc(t("enginePick.rememberForModel"))}</span>
        </label>
        <label class="engine-pick-flag">
          <input type="checkbox" data-flag="global" />
          <span>${esc(t("enginePick.useAsDefault"))}</span>
        </label>
        <div class="sidebar-manager-foot">
          <span class="spacer"></span>
          <button class="secondary" type="button" data-act="cancel">${esc(t("app.cancel"))}</button>
          <button class="primary" type="button" data-act="ok">${esc(t("app.confirm"))}</button>
        </div>
      </div>`;

    const close = (val) => {
      overlay.remove();
      document.removeEventListener("keydown", onKey);
      resolve(val);
    };
    const onKey = (e) => { if (e.key === "Escape") close(null); };
    document.addEventListener("keydown", onKey);

    overlay.addEventListener("click", (e) => {
      if (e.target === overlay) { close(null); return; }
      if (e.target.closest("[data-act=cancel]") || e.target.closest("[data-act=close]")) { close(null); return; }
      if (e.target.closest("[data-act=ok]")) {
        const box = overlay.querySelector('input[name="kevrai-engine-pick"]:checked');
        const engine = box ? box.value : current.engine;
        const remember = !!overlay.querySelector('[data-flag=remember]')?.checked;
        const isGlobal = !!overlay.querySelector('[data-flag=global]')?.checked;
        close({ engine, remember, global: isGlobal });
      }
    });

    document.body.appendChild(overlay);
  });
}

/** 把 pickEngine 的结果写回本地存储。 */
export function applyEngineChoice(result, modelId) {
  if (!result) return;
  if (result.global) setGlobalDefault(result.engine);
  else if (result.remember) rememberEngine(modelId, result.engine);
}
