// renderer/modules/mcp-servers.js — Settings panel for remote MCP servers.
//
// Lets the user add remote MCP (Model Context Protocol) servers over the
// streamable-HTTP and SSE transports, enable/disable them, inspect the tools
// they expose, and call a tool as a smoke test. The Agent's MCP skill then
// reaches the same servers. Pure helpers are exported for unit tests.
"use strict";

import { api } from "./api.js";
import { toast } from "./toast.js";
import { t } from "./i18n.js";
import { unwrap } from "./net.js";

const $ = (s, r = document) => r.querySelector(s);

/** HTML-escape a string for safe insertion into server/tool rendering. */
export function escapeHtml(s) {
  return String(s == null ? "" : s)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

/** Render the discovered tools of one server as an HTML block (pure). */
export function mcpToolsHtml(tools) {
  if (!tools || !tools.length) {
    return `<p class="mut tiny">${t("mcp.noTools")}</p>`;
  }
  const rows = tools.map((tool) => {
    const required = (tool.inputSchema?.required || []);
    const props = Object.keys(tool.inputSchema?.properties || {})
      .map((p) => {
        const req = required.includes(p) ? " *" : "";
        return `<code>${escapeHtml(p)}${req}</code>`;
      })
      .join(", ");
    return (
      `<div class="mcp-tool">` +
      `<div class="mcp-tool-name">🔧 ${escapeHtml(tool.name)}</div>` +
      (tool.description
        ? `<div class="mut tiny">${escapeHtml(tool.description)}</div>`
        : "") +
      (props
        ? `<div class="mcp-tool-params tiny">${t("mcp.params")}: ${props}</div>`
        : "") +
      `</div>`
    );
  });
  return `<div class="mcp-tool-list">${rows.join("")}</div>`;
}

let _wired = false;

export async function initMcpPanel() {
  const list = $("#mcp-server-list");
  if (!list) return;
  if (!_wired) {
    $("#mcp-add-btn")?.addEventListener("click", addServer);
    _wired = true;
  }
  await refresh();
}

async function addServer() {
  const name = $("#mcp-add-name").value.trim();
  const transport = $("#mcp-add-transport").value;
  const url = $("#mcp-add-url").value.trim();
  if (!name || !url) {
    toast(t("mcp.needFields"), { kind: "err" });
    return;
  }
  try {
    await api.mcpAddServer({ name, transport, url, enabled: true });
    $("#mcp-add-name").value = "";
    $("#mcp-add-url").value = "";
    toast(t("mcp.added"), { kind: "ok" });
    await refresh();
  } catch (_) { /* api layer toasted */ }
}

async function refresh() {
  const list = $("#mcp-server-list");
  let servers = [];
  try {
    servers = unwrap(await api.mcpListServers()).servers || [];
  } catch (_) { return; }
  list.replaceChildren();
  if (!servers.length) {
    const p = document.createElement("p");
    p.className = "mut tiny";
    p.textContent = t("mcp.empty");
    list.appendChild(p);
    return;
  }
  for (const server of servers) list.appendChild(buildRow(server));
}

function buildRow(server) {
  const row = document.createElement("div");
  row.className = "mcp-server-row";
  row.dataset.id = server.id;

  const head = document.createElement("div");
  head.className = "mcp-server-head";
  const title = document.createElement("span");
  title.className = "mcp-server-title";
  title.textContent = `🔌 ${server.name} · ${server.transport}`;
  head.appendChild(title);

  const enableLabel = document.createElement("label");
  enableLabel.className = "field inline";
  const box = document.createElement("input");
  box.type = "checkbox";
  box.checked = !!server.enabled;
  box.addEventListener("change", async () => {
    try {
      await api.mcpSetEnabled(server.id, box.checked);
      toast(t(box.checked ? "mcp.enabled" : "mcp.disabled"),
            { kind: "ok" });
    } catch (_) { box.checked = !box.checked; }
  });
  const enText = document.createElement("span");
  enText.className = "tiny";
  enText.textContent = t("mcp.enabled");
  enableLabel.append(box, enText);
  head.appendChild(enableLabel);
  row.appendChild(head);

  const urlLine = document.createElement("div");
  urlLine.className = "mut tiny mcp-server-url";
  urlLine.textContent = server.url;
  row.appendChild(urlLine);

  const actions = document.createElement("div");
  actions.className = "mcp-server-actions";
  const toolsBtn = document.createElement("button");
  toolsBtn.type = "button";
  toolsBtn.className = "btn btn-sm ghost";
  toolsBtn.textContent = t("mcp.inspect");
  const callBtn = document.createElement("button");
  callBtn.type = "button";
  callBtn.className = "btn btn-sm ghost";
  callBtn.textContent = t("mcp.testCall");
  const delBtn = document.createElement("button");
  delBtn.type = "button";
  delBtn.className = "btn btn-sm ghost danger";
  delBtn.textContent = t("mcp.remove");
  actions.append(toolsBtn, callBtn, delBtn);
  row.appendChild(actions);

  const detail = document.createElement("div");
  detail.className = "mcp-server-detail";
  detail.hidden = true;
  row.appendChild(detail);

  toolsBtn.addEventListener("click", async () => {
    if (!detail.hidden) { detail.hidden = true; return; }
    detail.hidden = false;
    detail.textContent = t("mcp.connecting");
    try {
      const tools = unwrap(await api.mcpListTools(server.id)).tools || [];
      detail.innerHTML = mcpToolsHtml(tools);
    } catch (err) {
      detail.textContent = t("mcp.connectFailed", { err: err.message || err });
    }
  });

  callBtn.addEventListener("click", async () => {
    detail.hidden = false;
    detail.textContent = t("mcp.connecting");
    let tools = [];
    try {
      tools = unwrap(await api.mcpListTools(server.id)).tools || [];
    } catch (err) {
      detail.textContent = t("mcp.connectFailed", { err: err.message || err });
      return;
    }
    const first = tools[0];
    if (!first) {
      detail.innerHTML = mcpToolsHtml([]);
      return;
    }
    const args = sampleArgs(first);
    try {
      const res = unwrap(await api.mcpCallTool(
        server.id, first.name, args));
      detail.innerHTML =
        `<div class="mcp-call-result">` +
        `<div class="tiny mut">${t("mcp.called", { tool: first.name })}</div>` +
        `<pre>${escapeHtml(res.text || JSON.stringify(res.structured))}</pre>` +
        `</div>`;
    } catch (err) {
      detail.textContent = t("mcp.callFailed", { err: err.message || err });
    }
  });

  delBtn.addEventListener("click", async () => {
    try {
      await api.mcpRemoveServer(server.id);
      toast(t("mcp.removed"), { kind: "ok" });
      await refresh();
    } catch (_) { /* toasted */ }
  });

  return row;
}

/** Build placeholder arguments from a tool's declared parameter schema. */
export function sampleArgs(tool) {
  const out = {};
  const props = tool.inputSchema?.properties || {};
  for (const [name, spec] of Object.entries(props)) {
    const type = spec.type;
    if (type === "number" || type === "integer") out[name] = 0;
    else if (type === "boolean") out[name] = false;
    else if (Array.isArray(type)) out[name] = "";
    else out[name] = spec.example ?? "";
  }
  return out;
}
