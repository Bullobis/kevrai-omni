// renderer/modules/revision-picker.js — let the user choose a GGUF
// revision (a non-default branch or a release tag) BEFORE picking a
// quantization. Falls back to `main` on any failure, and aborts the whole
// install only when the user explicitly cancels this picker.
"use strict";

// HF serves the default branch as revision "main".
export const DEFAULT_REVISION = "main";

function esc(s) {
  return String(s == null ? "" : s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}

// Normalize the /api/models/{id}/revisions response, tolerating either the
// raw body or the { status, body } IPC envelope. Always returns a plain,
// defensive shape — never throws.
export function parseRevisions(raw) {
  const out = { repo: "", branches: [], tags: [] };
  const body = (raw && typeof raw === "object" && raw.body != null) ? raw.body : raw;
  if (!body || typeof body !== "object") return out;
  out.repo = typeof body.repo === "string" ? body.repo : "";
  out.branches = Array.isArray(body.branches)
    ? body.branches
        .filter((b) => b && typeof b.name === "string" && b.name)
        .map((b) => ({ name: b.name, ref: typeof b.ref === "string" ? b.ref : "" }))
    : [];
  out.tags = Array.isArray(body.tags)
    ? body.tags
        .filter((t) => t && typeof t.name === "string" && t.name)
        .map((t) => ({ name: t.name, ref: typeof t.ref === "string" ? t.ref : "" }))
    : [];
  return out;
}

// Does the repo offer any choice beyond the plain default branch?
// A picker is warranted when there is a branch other than `main`, or any tag.
export function hasRevisionChoices(parsed) {
  if (!parsed || typeof parsed !== "object") return false;
  const nonMainBranch = (parsed.branches || []).some(
    (b) => b.name && b.name !== DEFAULT_REVISION,
  );
  return nonMainBranch || (parsed.tags || []).length > 0;
}

// Build the ordered, de-duplicated option list for the picker. The default
// branch is always listed first (and pre-selected); non-main branches come
// next, then tags. `kind` is "branch" | "tag" for display.
export function groupRevisionOptions(parsed) {
  const opts = [];
  opts.push({ value: DEFAULT_REVISION, kind: "branch", isDefault: true });
  for (const b of (parsed && parsed.branches) || []) {
    if (!b.name || b.name === DEFAULT_REVISION) continue;
    opts.push({ value: b.name, kind: "branch" });
  }
  for (const t of (parsed && parsed.tags) || []) {
    if (!t.name) continue;
    opts.push({ value: t.name, kind: "tag" });
  }
  return opts;
}

// Build the hub-download payload for a chosen revision + quant shards.
// Kept as a pure helper so the revision→download contract is unit-testable.
export function buildDownloadArgs({ repo, files, revision }) {
  return {
    hub: "hf",
    repo,
    files,
    auto_pick: false,
    revision: revision || DEFAULT_REVISION,
  };
}

// Open the picker. Resolves to one of:
//   null                                  → user cancelled (abort install)
//   { revision: "main", skipped: true }   → no choice offered, used default
//   { revision: "main", fallback: true }   → revisions lookup failed, used default
//   { revision, repo }                     → user picked a branch/tag
export async function pickRevision(item, api) {
  let parsed;
  try {
    parsed = parseRevisions(await api.modelRevisions(item.id));
  } catch (_e) {
    // Lookup failed/timeout/empty → graceful fall back to the default branch.
    return { revision: DEFAULT_REVISION, fallback: true };
  }
  if (!hasRevisionChoices(parsed)) {
    // Only `main` (or empty catalog) → don't bother the user.
    return { revision: DEFAULT_REVISION, skipped: true, repo: parsed.repo };
  }

  const options = groupRevisionOptions(parsed);
  return new Promise((resolve) => {
    const overlay = document.createElement("div");
    overlay.className = "overlay";
    overlay.setAttribute("role", "dialog");
    overlay.setAttribute("aria-modal", "true");
    overlay.innerHTML = `
      <div class="overlay-card quant-card">
        <header>
          <h2>选择版本 · ${esc(item.name || item.id)}</h2>
          <button class="ghost icon quant-x" type="button" aria-label="关闭">×</button>
        </header>
        <div class="quant-list">
          ${options
            .map((o, i) => {
              const kindLabel = o.kind === "tag" ? "标签" : "分支";
              return `<button type="button" class="quant-opt${o.isDefault ? " is-sel" : ""}" data-i="${i}">
                <span class="quant-radio" aria-hidden="true"></span>
                <span class="quant-main">
                  <span class="quant-label">${esc(o.value)}${
                    o.isDefault ? '<span class="quant-badge">默认</span>' : ""
                  }</span>
                  <span class="quant-note">${o.kind === "tag" ? "发布标签" : "分支"}</span>
                </span>
                <span class="quant-size">${esc(kindLabel)}</span>
              </button>`;
            })
            .join("")}
        </div>
        <footer class="quant-foot">
          <span class="quant-count mut">共 ${options.length} 个版本</span>
          <span class="quant-spacer"></span>
          <button class="secondary quant-cancel" type="button">取消</button>
          <button class="primary quant-ok" type="button">继续</button>
        </footer>
      </div>`;

    let selected = 0; // main is pre-selected

    const done = (val) => {
      overlay.remove();
      document.removeEventListener("keydown", onKey);
      resolve(val);
    };
    overlay.addEventListener("click", (e) => {
      const opt = e.target.closest(".quant-opt");
      if (opt) {
        selected = parseInt(opt.dataset.i, 10);
        overlay.querySelectorAll(".quant-opt").forEach((o) =>
          o.classList.toggle("is-sel", o === opt),
        );
        return;
      }
      if (e.target.closest(".quant-cancel, .quant-x")) {
        done(null);
        return;
      }
      if (e.target.closest(".quant-ok")) {
        const chosen = options[selected];
        done({ revision: chosen.value, repo: parsed.repo });
      }
    });
    // Click on the dim backdrop acts as cancel.
    overlay.addEventListener("click", (e) => {
      if (e.target === overlay) done(null);
    });
    const onKey = (e) => {
      if (e.key === "Escape") done(null);
    };
    document.addEventListener("keydown", onKey);
    document.body.appendChild(overlay);
  });
}
