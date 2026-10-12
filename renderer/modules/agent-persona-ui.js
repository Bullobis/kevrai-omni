// renderer/modules/agent-persona-ui.js — pure helpers for the persona selector.
// No DOM/i18n dependencies so the logic can be unit tested in isolation.

// Build <option> markup for the persona dropdown from API items [{id,label}].
export function personaOptionsHtml(items, esc) {
  const safe = esc || ((s) => String(s ?? "").replace(/[&<>"]/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c])));
  return (items || [])
    .map((p) => `<option value="${safe(p.id)}">${safe(p.label)}</option>`)
    .join("");
}
