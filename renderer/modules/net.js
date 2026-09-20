// renderer/modules/net.js — IPC 响应解包与公共文本工具
//
// 背景：preload 暴露的每个 IPC handler 都返回归一化后的 {status, body} 形状
// （见 electron/main.js 的 sidecarFetch）。api.js 的 wrap() 只做错误 toast，
// **不解包**，所以调用方必须自己取 body。
//
// 历史上这里踩过两次坑：
//   - d36699c：search.js 解包层级错误 → 模型市场网格始终为空
//   - 本次：agent.js / environments.js / bootstrap.js / downloads.js
//     共 11 处漏解包 → 环境页、首启页、Agent 面板整页失效
//
// 因此统一走 unwrap()，避免每个调用点各写一遍 `r?.body || r`。
"use strict";

/**
 * 取出 IPC 响应里的业务负载。
 *
 * 兼容三种形态，永不抛异常：
 *   - `{status, body}`  → 返回 body
 *   - 裸对象            → 原样返回（部分通道确实不包 body）
 *   - null/undefined    → 返回 `{}`，让调用方的 `?.` 与 `||` 兜底
 *
 * @param {unknown} r IPC 返回值
 * @param {object} [fallback={}] 空值时的替代值，可按需传 `[]`
 * @returns {any} 业务负载
 */
export function unwrap(r, fallback = {}) {
  if (r == null) return fallback;
  // 只认「自带 body 字段」这一种包裹形态；body 为 null 时也承认包裹已解。
  if (typeof r === "object" && !Array.isArray(r) && "body" in r) {
    const b = r.body;
    return b == null ? fallback : b;
  }
  return r;
}

/**
 * HTML 转义。此前在 8 个模块里各复制了一份（app/models/search/agent/mnn/
 * drama/engines/downloads），行为一致但易漂移，收敛到此处。
 */
export function escapeHtml(s) {
  return String(s == null ? "" : s).replace(/[&<>"']/g, (m) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  }[m]));
}

/**
 * 属性值转义：在 escapeHtml 基础上额外转义反引号，用于内联到 HTML 属性里。
 */
export function escapeAttr(s) {
  return escapeHtml(s).replace(/`/g, "&#96;");
}
