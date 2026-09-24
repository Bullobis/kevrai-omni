// renderer/modules/markdown.js — 轻量、安全的 Markdown → HTML 渲染器（无第三方依赖）
//
// 为什么手写：CSP script-src 'self' 不允许从 CDN 加载 marked；Agent 返回内容是
// 不可信输入，安全策略是「先整体 HTML 转义，再在转义后的文本上做 Markdown 匹配」，
// 代码块/行内代码先抽取为占位符保护，链接 URL 走协议白名单（拒绝 javascript:/data:）。
"use strict";

import { escapeHtml } from "./net.js";

const STASH_OPEN = "\u0000B";
const STASH_CLOSE = "\u0000";

/** 判断 URL 是否为安全目标（http/https 或相对路径），返回原始 URL 或空串。 */
function safeUrl(u) {
  const url = String(u || "").trim();
  if (/^(https?:|\/|\.|\?|#)/i.test(url)) return url;
  return "";
}

/** 行内 Markdown：链接 / 粗体 / 斜体。输入已 HTML 转义，代码已抽为占位符。 */
function renderInline(s) {
  let out = String(s);
  // 链接 [text](url)
  out = out.replace(/\[([^\]]+)\]\(([^)\s]+)\)/g, (m, text, url) => {
    const u = safeUrl(url);
    if (!u) return text;
    return `<a class="md-link" href="${u}" target="_blank" rel="noopener noreferrer">${text}</a>`;
  });
  // 粗体 **text**
  out = out.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
  // 斜体 *text*（避开 **）
  out = out.replace(/(^|[^*])\*([^*\n]+)\*/g, "$1<em>$2</em>");
  return out;
}

/**
 * renderMarkdown(src) → 安全 HTML 字符串。
 * 支持：围栏代码块、行内代码、标题、粗体、斜体、链接、有序/无序列表、引用、换行。
 */
export function renderMarkdown(src) {
  const stashed = [];
  const stash = (html) => {
    const key = `${STASH_OPEN}${stashed.length}${STASH_CLOSE}`;
    stashed.push(html);
    return key;
  };

  // 1) 整体转义（用户内容里的 HTML 在此失效）。
  let work = escapeHtml(String(src ?? ""));

  // 2) 围栏代码块（先于行内代码，保护内部内容不被任何规则处理）。
  work = work.replace(/```([a-zA-Z0-9_+\-.]*)\n?([\s\S]*?)```/g, (m, lang, code) => {
    const inner = code.replace(/\n$/, "");
    const langAttr = lang ? ` data-lang="${escapeHtml(lang)}"` : "";
    return stash(`<pre class="md-code"${langAttr}><code>${inner}</code></pre>`);
  });

  // 3) 行内代码。
  work = work.replace(/`([^`\n]+)`/g, (m, code) => `<code class="md-icode">${code}</code>`);

  // 4) 按行处理块级结构。
  const lines = work.split("\n");
  const out = [];
  let list = null; // {type:'ul'|'ol', items:string[]}
  const flushList = () => {
    if (!list) return;
    const items = list.items.map((x) => `<li>${x}</li>`).join("");
    out.push(`<${list.type}>${items}</${list.type}>`);
    list = null;
  };

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    let m;
    // 标题
    if ((m = /^(#{1,4})\s+(.*)$/.exec(line))) {
      flushList();
      const lvl = m[1].length;
      out.push(`<h${lvl} class="md-h md-h${lvl}">${renderInline(m[2])}</h${lvl}>`);
      continue;
    }
    // 引用（支持连续多行）
    if ((m = /^&gt;\s?(.*)$/.exec(line))) {
      flushList();
      const quote = [m[1]];
      while (i + 1 < lines.length && /^&gt;/.test(lines[i + 1])) {
        i++;
        quote.push(/^&gt;\s?(.*)$/.exec(lines[i])[1]);
      }
      out.push(`<blockquote class="md-quote">${quote.map(renderInline).join("<br>")}</blockquote>`);
      continue;
    }
    // 无序列表
    if ((m = /^\s*[-*]\s+(.*)$/.exec(line))) {
      if (!list || list.type !== "ul") {
        flushList();
        list = { type: "ul", items: [] };
      }
      list.items.push(renderInline(m[1]));
      continue;
    }
    // 有序列表
    if ((m = /^\s*\d+\.\s+(.*)$/.exec(line))) {
      if (!list || list.type !== "ol") {
        flushList();
        list = { type: "ol", items: [] };
      }
      list.items.push(renderInline(m[1]));
      continue;
    }
    // 空行
    if (!line.trim()) {
      flushList();
      continue;
    }
    // 普通段落
    flushList();
    out.push(`<p class="md-p">${renderInline(line)}</p>`);
  }
  flushList();

  // 5) 还原代码块占位符。
  return out
    .join("\n")
    .replace(new RegExp(`${STASH_OPEN}(\\d+)${STASH_CLOSE}`, "g"), (m, n) => stashed[Number(n)]);
}
