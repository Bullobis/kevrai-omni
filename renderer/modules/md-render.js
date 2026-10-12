// Lightweight, dependency-safe Markdown renderer for assistant messages.
//
// Security model:
//   * Fenced code blocks and inline code spans are extracted to placeholders
//     before any other processing and are HTML-escaped verbatim.
//   * All remaining text is HTML-escaped before inline formatting is applied,
//     so raw markup can never reach the output.
//   * Links only render for an allow-listed protocol (http/https/mailto);
//     anything else (e.g. javascript:, data:) is shown as inert text.
//
// The code-block "copy" button carries the source as base64 (no markup-
// significant characters) in a data attribute; the host wires the click.

export function escapeHtml(s) {
  return String(s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

const ALLOWED_LINK_PROTOCOLS = new Set(["http:", "https:", "mailto:"]);

function decodeEntities(s) {
  return String(s)
    .replace(/&amp;/g, "&")
    .replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">")
    .replace(/&quot;/g, '"')
    .replace(/&#39;/g, "'");
}

// Inline formatting. Input is raw text; output is trusted-safe HTML.
function renderInline(raw, tokens) {
  let text = raw;

  // 1. Extract inline code spans first (content shown verbatim, escaped).
  text = text.replace(/`+([^`\n]+?)`+/g, (_m, code) => {
    const id = tokens.length;
    tokens.push(`<code class="md-icode">${escapeHtml(code)}</code>`);
    return `\u0000T${id}\u0000`;
  });

  // 2. Escape everything else.
  text = escapeHtml(text);

  // 3. Links. The URL is escaped; decode to validate the real protocol.
  text = text.replace(
    /\[([^\]]*)\]\(([A-Za-z][A-Za-z0-9+.\-]*:[^\s)]+|\/[^\s)]*|[^)\s]+)(?:\s+&quot;[^&]*&quot;)?\)/g,
    (m, label, urlEsc) => {
      const url = decodeEntities(urlEsc).trim();
      let proto = "relative";
      if (/^[A-Za-z][A-Za-z0-9+.\-]*:/.test(url)) {
        proto = url.slice(0, url.indexOf(":") + 1).toLowerCase();
      }
      const safe = /\s/.test(url) === false
        && !/[<>"']/.test(url)
        && (proto === "relative" || ALLOWED_LINK_PROTOCOLS.has(proto));
      if (!safe) return m; // leave the escaped markdown text as-is
      return `<a href="${escapeHtml(url)}" target="_blank" rel="noopener noreferrer">${label}</a>`;
    }
  );

  // 4. Bold / strikethrough / italic (bold before italic to avoid ** clashes).
  text = text
    .replace(/\*\*([^*\n]+?)\*\*/g, "<strong>$1</strong>")
    .replace(/__([^_\n]+?)__/g, "<strong>$1</strong>")
    .replace(/~~([^~\n]+?)~~/g, "<del>$1</del>")
    .replace(/(^|[\s(])\*([^*\n]+?)\*/g, "$1<em>$2</em>")
    .replace(/(^|[\s(])_([^_\n]+?)_/g, "$1<em>$2</em>");

  // 5. Restore code tokens.
  text = text.replace(/\u0000T(\d+)\u0000/g, (_m, id) => tokens[Number(id)]);
  return text;
}

const FENCE_RE = /^```([\w+\-.#]*)\s*$/;

function isTableSep(s) {
  const t = s.trim();
  if (!t.includes("-")) return false;
  const stripped = t.replace(/\|/g, "").replace(/[\s:\-]/g, "");
  return stripped === "" && t.includes("|");
}

function splitRow(line) {
  let t = line.trim();
  if (t.startsWith("|")) t = t.slice(1);
  if (t.endsWith("|")) t = t.slice(0, -1);
  return t.split("|").map((c) => c.trim());
}

// Render a full Markdown document to HTML.
export function renderMarkdown(input) {
  const tokens = [];
  const lines = String(input).replace(/\r\n?/g, "\n").split("\n");
  const out = [];
  let i = 0;

  const inline = (s) => renderInline(s, tokens);

  while (i < lines.length) {
    const line = lines[i];

    // Fenced code block -----------------------------------------------------
    const fence = line.match(FENCE_RE);
    if (fence) {
      const lang = fence[1] || "";
      const buf = [];
      i += 1;
      while (i < lines.length && !/^```\s*$/.test(lines[i])) {
        buf.push(lines[i]);
        i += 1;
      }
      i += 1; // consume closing fence (or run off EOF)
      const code = buf.join("\n");
      const b64 = typeof btoa !== "undefined"
        ? btoa(unescape(encodeURIComponent(code)))
        : Buffer.from(code, "utf8").toString("base64");
      out.push(
        `<div class="md-codeblock">`
        + `<div class="md-codebar"><span class="md-codelang">${escapeHtml(lang || "text")}</span>`
        + `<button type="button" class="md-copycode" data-msg-action="copy-code" data-code="${b64}">⧉ 复制代码</button>`
        + `</div><pre class="md-pre"><code>${escapeHtml(code)}</code></pre></div>`
      );
      continue;
    }

    // Blank line ------------------------------------------------------------
    if (/^\s*$/.test(line)) { i += 1; continue; }

    // Heading ---------------------------------------------------------------
    const h = line.match(/^(#{1,6})\s+(.*)$/);
    if (h) {
      const level = h[1].length;
      out.push(`<h${level} class="md-h md-h${level}">${inline(h[2].trim())}</h${level}>`);
      i += 1;
      continue;
    }

    // Horizontal rule -------------------------------------------------------
    if (/^\s*(\*{3,}|-{3,}|_{3,})\s*$/.test(line)) {
      out.push("<hr class=\"md-hr\">");
      i += 1;
      continue;
    }

    // Table -----------------------------------------------------------------
    if (line.includes("|") && i + 1 < lines.length && isTableSep(lines[i + 1])) {
      const head = splitRow(line);
      i += 2;
      const rows = [];
      while (i < lines.length && lines[i].includes("|") && !/^\s*$/.test(lines[i])) {
        rows.push(splitRow(lines[i]));
        i += 1;
      }
      const thead = `<tr>${head.map((c) => `<th>${inline(c)}</th>`).join("")}</tr>`;
      const tbody = rows
        .map((r) => `<tr>${head.map((_, c) => `<td>${inline(r[c] ?? "")}</td>`).join("")}</tr>`)
        .join("");
      out.push(`<table class="md-table"><thead>${thead}</thead><tbody>${tbody}</tbody></table>`);
      continue;
    }

    // Blockquote ------------------------------------------------------------
    if (/^\s*>\s?/.test(line)) {
      const buf = [];
      while (i < lines.length && /^\s*>\s?/.test(lines[i])) {
        buf.push(lines[i].replace(/^\s*>\s?/, ""));
        i += 1;
      }
      out.push(`<blockquote class="md-quote">${inline(buf.join("\n")).replace(/\n/g, "<br>")}</blockquote>`);
      continue;
    }

    // Unordered list --------------------------------------------------------
    if (/^\s*[-*+]\s+/.test(line)) {
      const items = [];
      while (i < lines.length && /^\s*[-*+]\s+/.test(lines[i])) {
        items.push(lines[i].replace(/^\s*[-*+]\s+/, ""));
        i += 1;
      }
      out.push(`<ul class="md-ul">${items.map((t) => `<li>${inline(t)}</li>`).join("")}</ul>`);
      continue;
    }

    // Ordered list ----------------------------------------------------------
    if (/^\s*\d+[.)]\s+/.test(line)) {
      const items = [];
      while (i < lines.length && /^\s*\d+[.)]\s+/.test(lines[i])) {
        items.push(lines[i].replace(/^\s*\d+[.)]\s+/, ""));
        i += 1;
      }
      out.push(`<ol class="md-ol">${items.map((t) => `<li>${inline(t)}</li>`).join("")}</ol>`);
      continue;
    }

    // Paragraph -------------------------------------------------------------
    const buf = [];
    while (
      i < lines.length
      && !/^\s*$/.test(lines[i])
      && !FENCE_RE.test(lines[i])
      && !/^(#{1,6})\s+/.test(lines[i])
      && !/^\s*[-*+]\s+/.test(lines[i])
      && !/^\s*\d+[.)]\s+/.test(lines[i])
      && !/^\s*>\s?/.test(lines[i])
    ) {
      buf.push(lines[i]);
      i += 1;
    }
    out.push(`<p class="md-p">${inline(buf.join("\n")).replace(/\n/g, "<br>")}</p>`);
  }

  return out.join("\n");
}
