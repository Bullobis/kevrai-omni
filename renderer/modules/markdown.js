// renderer/modules/markdown.js
// 零依赖、XSS 安全的 Markdown 渲染器（覆盖常用子集：代码/标题/强调/链接/列表/引用/表格）。
//
// 安全模型（顺序不可调换）：
//   1) 先抽取围栏代码块与行内代码为占位符，代码内容只做 HTML 转义、绝不解析其中的标记；
//   2) 其余正文先整体 HTML 转义——任何原始标签 / <script> / 事件属性在此全部失效；
//   3) 在已转义文本上应用行内与块级标记；链接仅允许 http/https，并强制 noopener；
//   4) 最后回填代码占位符。
//
// 用法：renderMarkdown(text) -> 安全的 HTML 字符串（不含 <script>，可直接注入）。

const _ESC = {
  "&": "&amp;",
  "<": "&lt;",
  ">": "&gt;",
  '"': "&quot;",
  "'": "&#39;",
};

export function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => _ESC[c]);
}

// 仅允许 http/https（杜绝 javascript:、data: 等）；输入已转义。
function safeUrl(u) {
  const decoded = u.replace(/&amp;/g, "&");
  if (/^https?:\/\/[^\s"']+$/i.test(decoded)) return decoded;
  return "";
}

function inline(s) {
  // 行内链接 [text](http(s)://...)
  s = s.replace(/\[([^\]]+)\]\(([^)\s]+(?:\s+[^)]+)?)\)/g, (m, label, rest) => {
    const url = rest.trim().split(/\s+/)[0];
    const ok = safeUrl(url);
    if (!ok) return label; // 非法 URL 退化为纯文本
    return `<a class="md-link" href="${escapeHtml(ok)}" target="_blank" rel="noopener noreferrer">${label}</a>`;
  });
  // 自动链接 <http(s)://...>（尖括号已转义为 &lt; &gt;）
  s = s.replace(/&lt;(https?:\/\/[^\s&]+)&gt;/g, (m, u) => {
    const ok = safeUrl(u);
    return ok
      ? `<a class="md-link" href="${escapeHtml(ok)}" target="_blank" rel="noopener noreferrer">${escapeHtml(ok)}</a>`
      : m;
  });
  // 粗体
  s = s.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
  s = s.replace(/__([^_]+)__/g, "<strong>$1</strong>");
  // 删除线
  s = s.replace(/~~([^~]+)~~/g, "<del>$1</del>");
  // 斜体（放在粗体之后，避免吞掉 **）
  s = s.replace(/(^|[^*])\*([^*\n]+)\*/g, "$1<em>$2</em>");
  s = s.replace(/(^|[^_])_([^_\n]+)_/g, "$1<em>$2</em>");
  return s;
}

function renderTable(rows) {
  // rows: 原始（已转义）行数组，至少 2 行（表头 + 分隔）
  const split = (line) =>
    line.replace(/^\s*\|/, "").replace(/\|\s*$/, "").split("|").map((c) => c.trim());
  const header = split(rows[0]);
  const alignRow = split(rows[1]);
  const aligns = alignRow.map((c) => {
    const l = c.startsWith(":");
    const r = c.endsWith(":");
    if (l && r) return "center";
    if (r) return "right";
    if (l) return "left";
    return "";
  });
  const th = header
    .map((h, i) => `<th${aligns[i] ? ` style="text-align:${aligns[i]}"` : ""}>${inline(h)}</th>`)
    .join("");
  const body = rows.slice(2).map((line) => {
    const cells = split(line);
    const tds = header
      .map((_, i) => {
        const cell = cells[i] ?? "";
        return `<td${aligns[i] ? ` style="text-align:${aligns[i]}"` : ""}>${inline(cell)}</td>`;
      })
      .join("");
    return `<tr>${tds}</tr>`;
  });
  return `<div class="md-table-wrap"><table class="md-table"><thead><tr>${th}</tr></thead><tbody>${body.join(
    ""
  )}</tbody></table></div>`;
}

export function renderMarkdown(src) {
  const text = String(src ?? "");
  const store = [];
  const stash = (html) => {
    const key = "\u0000" + store.length + "\u0000";
    store.push(html);
    return key;
  };

  // 1) 抽取围栏代码块
  let work = text.replace(/```([^\n`]*)\n?([\s\S]*?)```/g, (m, lang, code) => {
    const l = lang.trim();
    const cls = l ? ` class="language-${escapeHtml(l)}"` : "";
    const body = escapeHtml(code.replace(/\n$/, ""));
    return stash(`<pre class="md-pre"><code${cls}>${body}</code></pre>`);
  });

  // 2) 整体转义（消灭一切原始标签/脚本）
  work = escapeHtml(work);

  // 3) 抽取行内代码（转义后反引号仍在；内容已是转义态，直接保护）
  work = work.replace(/`([^`\n]+)`/g, (m, code) => stash(`<code class="md-code">${code}</code>`));

  // 4) 块级解析
  const lines = work.split("\n");
  const out = [];
  let i = 0;
  while (i < lines.length) {
    let line = lines[i];

    // 代码块占位符独占一行
    if (/^\u0000\d+\u0000$/.test(line.trim())) {
      out.push(line.trim());
      i++;
      continue;
    }
    // 空行
    if (!line.trim()) {
      i++;
      continue;
    }
    // 水平线
    if (/^\s*(\*\*\*|---|___)\s*$/.test(line)) {
      out.push('<hr class="md-hr">');
      i++;
      continue;
    }
    // 标题
    const h = /^(#{1,6})\s+(.*)$/.exec(line);
    if (h) {
      const lvl = h[1].length;
      out.push(`<h${lvl} class="md-h md-h${lvl}">${inline(h[2])}</h${lvl}>`);
      i++;
      continue;
    }
    // 表格（当前行含 | 且下一行为分隔行）
    if (line.includes("|") && i + 1 < lines.length && /^\s*\|?[\s:|-]+\|[\s:|-]*$/.test(lines[i + 1]) && /-/.test(lines[i + 1])) {
      const trows = [lines[i], lines[i + 1]];
      i += 2;
      while (i < lines.length && lines[i].includes("|") && lines[i].trim()) {
        trows.push(lines[i]);
        i++;
      }
      out.push(renderTable(trows));
      continue;
    }
    // 引用块
    if (/^&gt;\s?/.test(line)) {
      const quote = [];
      while (i < lines.length && /^&gt;\s?/.test(lines[i])) {
        quote.push(lines[i].replace(/^&gt;\s?/, ""));
        i++;
      }
      out.push(`<blockquote class="md-quote">${inline(quote.join("<br>"))}</blockquote>`);
      continue;
    }
    // 无序列表
    if (/^\s*[-*+]\s+/.test(line)) {
      const items = [];
      while (i < lines.length && /^\s*[-*+]\s+/.test(lines[i])) {
        items.push(`<li>${inline(lines[i].replace(/^\s*[-*+]\s+/, ""))}</li>`);
        i++;
      }
      out.push(`<ul class="md-ul">${items.join("")}</ul>`);
      continue;
    }
    // 有序列表
    if (/^\s*\d+\.\s+/.test(line)) {
      const items = [];
      while (i < lines.length && /^\s*\d+\.\s+/.test(lines[i])) {
        items.push(`<li>${inline(lines[i].replace(/^\s*\d+\.\s+/, ""))}</li>`);
        i++;
      }
      out.push(`<ol class="md-ol">${items.join("")}</ol>`);
      continue;
    }
    // 段落：聚合到下一个空行/块级起点
    const para = [line];
    i++;
    while (
      i < lines.length &&
      lines[i].trim() &&
      !/^\u0000\d+\u0000$/.test(lines[i].trim()) &&
      !/^(#{1,6})\s+/.test(lines[i]) &&
      !/^\s*[-*+]\s+/.test(lines[i]) &&
      !/^\s*\d+\.\s+/.test(lines[i]) &&
      !/^&gt;/.test(lines[i]) &&
      !/^\s*(\*\*\*|---|___)\s*$/.test(lines[i])
    ) {
      para.push(lines[i]);
      i++;
    }
    out.push(`<p class="md-p">${inline(para.join("\n"))}</p>`);
  }

  let html = out.join("\n");

  // 5) 回填代码占位
  html = html.replace(/\u0000(\d+)\u0000/g, (m, n) => store[Number(n)]);

  return html;
}

// 浏览器非模块环境兜底（模块内通常直接 import { renderMarkdown } 使用）。
if (typeof window !== "undefined") {
  window.renderMarkdown = renderMarkdown;
}
