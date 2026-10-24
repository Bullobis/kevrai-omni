// renderer/modules/engine-route.js — 问后端"这个模型该用哪个引擎、走哪个端点"。
//
// 为什么需要它：引擎选择以前散落在前端各处（装的时候写死 engines[0]），
// 而 settings 里的 default_engine_id 根本没有任何代码读取。现在后端
// /api/engine/route 是唯一决策点，前端只负责把结果如实显示出来——
// 包括"这个引擎当前没有应用内加载端点"这种不好听但必须让用户知道的情况。
"use strict";

/** 把后端 runtime 值翻成人话。未知值如实说未知，不编造。 */
export function describeRuntime(runtime, route, t = (k) => k) {
  switch (runtime) {
    case "mnn": return t("engineRoute.runtimeMnn");
    case "llamacpp": return t("engineRoute.runtimeLlamacpp");
    case "other": return route ? t("engineRoute.runtimeOther") : t("engineRoute.noEndpoint");
    default: return t("engineRoute.unknown");
  }
}

/** 把 source 翻成人话：这个决定是怎么来的。 */
export function describeSource(source, t = (k) => k) {
  switch (source) {
    case "explicit": return t("engineRoute.srcExplicit");
    case "default": return t("engineRoute.srcDefault");
    case "catalog": return t("engineRoute.srcCatalog");
    case "none": return t("engineRoute.srcNone");
    default: return t("engineRoute.unknown");
  }
}

/**
 * 查询路由。
 * @returns {Promise<{ok:boolean, data?:object, error?:string}>}
 *   网络失败返回 {ok:false} —— 调用方据此显示"未知"，不能猜一个引擎出来。
 */
export async function fetchEngineRoute(api, candidates, { modelId = "" } = {}) {
  const list = (Array.isArray(candidates) ? candidates : [candidates])
    .filter((x) => typeof x === "string" && x.trim());
  try {
    // 走正式通道：api.engineRoute -> preload -> api:engine:route -> 后端。
    // 此前这里写的是 api.get(...)，而 api.js 从无 get 方法，必然抛
    // "api.get is not a function"，使模型页的引擎信息永远显示"未知"。
    const res = await api.engineRoute({ engine: list, model_id: modelId });
    const body = (res && res.body) || res || {};
    if (!body || typeof body !== "object") return { ok: false, error: "bad_response" };
    return { ok: true, data: body };
  } catch (e) {
    return { ok: false, error: String((e && e.message) || e) };
  }
}

function esc(s) {
  return String(s == null ? "" : s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}

/**
 * 渲染路由信息行。未知/失败时明确显示"未知"，绝不显示一个猜出来的引擎。
 */
export function renderRouteRow(result, t = (k) => k) {
  if (!result || !result.ok || !result.data) {
    return `<div class="engine-route-row warn">
      <span class="er-label">${esc(t("engineRoute.title"))}</span>
      <span class="er-value">${esc(t("engineRoute.unknown"))}</span>
    </div>`;
  }
  const d = result.data;
  const cls = d.runtime === "other" ? "warn" : "ok";
  const note = d.note ? `<span class="er-note">${esc(d.note)}</span>` : "";
  return `<div class="engine-route-row ${cls}">
    <span class="er-label">${esc(t("engineRoute.title"))}</span>
    <span class="er-value">${esc(d.engine || t("engineRoute.unknown"))}</span>
    <span class="er-src">${esc(describeSource(d.source, t))}</span>
    <span class="er-rt">${esc(describeRuntime(d.runtime, d.route, t))}</span>
    ${note}
  </div>`;
}
