// renderer/modules/agent-slash.js — slash-command popup for the Agent composer.
// Typing "/" at the start of the input (or after whitespace) opens a filterable
// menu; arrows move, Tab/Enter accept, Esc closes. The backend agent is the
// authority for executing commands; this list mirrors the command ids.
"use strict";

import { t } from "./i18n.js";

// [id, takesArg, descriptionKey] — description is an i18n key resolved via t()
// at render/filter time (dicts may not be loaded when this module is imported).
export const SLASH_COMMANDS = [
  ["help", false, "agentSlash.help"],
  ["search", true, "agentSlash.search"],
  ["recommend", false, "agentSlash.recommend"],
  ["hardware", false, "agentSlash.hardware"],
  ["install", true, "agentSlash.install"],
  ["engines", false, "agentSlash.engines"],
  ["mirror", false, "agentSlash.mirror"],
  ["drama", false, "agentSlash.drama"],
  ["prompt", true, "agentSlash.prompt"],
  ["image", true, "agentSlash.image"],
  ["vision", true, "agentSlash.vision"],
  ["music", true, "agentSlash.music"],
  ["transcribe", false, "agentSlash.transcribe"],
  ["separate", false, "agentSlash.separate"],
  ["tts", true, "agentSlash.tts"],
  ["translate", true, "agentSlash.translate"],
];

// If the caret is inside a "/token" word at a word boundary, return
// { begin, q } where begin is the index of "/" and q the typed filter; else null.
export function slashContext(value, caret) {
  const before = value.slice(0, caret);
  const m = /(^|[\s])\/([^\s/]*)$/.exec(before);
  if (!m) return null;
  const begin = before.length - m[2].length - 1; // index of "/"
  return { begin, q: m[2] };
}

export function filterCommands(q) {
  const k = (q || "").toLowerCase();
  if (!k) return SLASH_COMMANDS.slice();
  return SLASH_COMMANDS.filter(
    ([id, , desc]) => id.includes(k) || t(desc).toLowerCase().includes(k)
  );
}

function rowHtml([id, takesArg, desc], active) {
  return (
    `<div class="slash-item${active ? " active" : ""}" data-cmd="${id}" role="option" aria-selected="${active}">` +
    `<span class="slash-name">/${id}</span>` +
    `<span class="slash-desc">${t(desc)}${takesArg ? "" : ""}</span></div>`
  );
}

// Attach the popup to a textarea. Idempotent.
export function attachSlashMenu(input) {
  if (!input || input.dataset.slashMenu === "1") return;
  input.dataset.slashMenu = "1";

  const wrap =
    input.closest(".agent-input-area") || input.parentElement;
  if (wrap) wrap.style.position = wrap.style.position || "relative";

  const menu = document.createElement("div");
  menu.className = "slash-menu";
  menu.hidden = true;
  menu.setAttribute("role", "listbox");

  let active = 0;
  let ctx = null;
  let current = SLASH_COMMANDS.slice();

  function render() {
    if (!current.length) {
      menu.innerHTML = `<div class="slash-empty">${t("agentSlash.noMatch")}</div>`;
      return;
    }
    menu.innerHTML = current
      .map((c, i) => rowHtml(c, i === active))
      .join("");
  }
  function open() {
    menu.hidden = false;
    if (wrap && wrap.appendChild(menu) !== menu) wrap.appendChild(menu);
    if (menu.parentNode !== wrap) wrap.appendChild(menu);
  }
  function close() {
    menu.hidden = true;
    ctx = null;
  }
  function accept() {
    if (!current[active]) return;
    const id = current[active][0];
    const caret = input.selectionStart || input.value.length;
    const c = slashContext(input.value, caret);
    if (!c) return close();
    const insert = `/${id} `;
    input.value =
      input.value.slice(0, c.begin) + insert + input.value.slice(caret);
    const pos = c.begin + insert.length;
    input.setSelectionRange(pos, pos);
    input.dispatchEvent(new Event("input", { bubbles: true }));
    if (id === "help" || !current[active][1]) {
      // commands without args can run immediately on Enter handled by agent;
      // keep menu closed so the next Enter sends.
      close();
    } else {
      close();
    }
    input.focus();
  }

  input.addEventListener("input", () => {
    const caret = input.selectionStart || input.value.length;
    ctx = slashContext(input.value, caret);
    if (!ctx) return close();
    current = filterCommands(ctx.q);
    active = 0;
    render();
    open();
  });

  input.addEventListener("keydown", (e) => {
    if (menu.hidden) return;
    if (e.key === "ArrowDown") {
      e.preventDefault();
      active = (active + 1) % Math.max(current.length, 1);
      render();
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      active =
        (active - 1 + Math.max(current.length, 1)) % Math.max(current.length, 1);
      render();
    } else if (e.key === "Tab" || e.key === "Enter") {
      e.preventDefault();
      accept();
    } else if (e.key === "Escape") {
      e.preventDefault();
      close();
    }
  });

  menu.addEventListener("mousedown", (e) => {
    const item = e.target.closest ? e.target.closest(".slash-item") : null;
    if (!item) return;
    const id = item.dataset.cmd;
    active = Math.max(0, current.findIndex((c) => c[0] === id));
    e.preventDefault();
    accept();
  });

  window.addEventListener("kevrai:slash-close", close);
}
