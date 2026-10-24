// renderer/modules/plan-install.js — decide WHAT to download for a catalog
// item, before any network call happens.
//
// Why this module exists
// ----------------------
// The install flow used to be one long `installItem()` with the download step
// buried inside two nested `if`s. That hid a class of bug where **the user
// clicked 安装 and nothing was ever downloaded** — no error, no toast, no task
// in the downloads panel. Two concrete cases:
//
//   1. A model with no `gguf_repo` (165 of 259 catalog entries) fell through to
//      a branch that only installed the engine and then toasted "正在安装引擎".
//      The model weights were never requested.
//   2. A GGUF model whose files could not be matched to a known quantization
//      produced `files: []`, which is truthy, so the caller's `if (!pick) return`
//      did not catch it and the whole flow returned silently.
//
// Pulling the decision out into a pure function makes both cases testable and,
// more importantly, makes "download nothing" an explicit, labelled outcome
// instead of an invisible fall-through.

"use strict";

export const HUB_HF = "hf";

/** Result kinds, so callers can branch without string matching. */
export const PLAN = {
  /** Download whole repo, let the backend decide which files. */
  AUTO: "auto",
  /** Download these specific shard paths at this revision. */
  FILES: "files",
  /** Nothing can be downloaded; `reason` says why, in one line. */
  NOTHING: "nothing",
};

/**
 * Decide the download plan for one catalog item.
 *
 * @param {object} item          catalog model entry
 * @param {object} [opts]
 * @param {string} [opts.revision]   chosen GGUF revision (default "main")
 * @param {string[]} [opts.files]    chosen shard paths, if the user picked a quant
 * @param {string} [opts.repo]       repo override from the quant/revision picker
 * @returns {{kind:string, args?:object, reason?:string}}
 */
export function planInstall(item, opts = {}) {
  if (!item || typeof item !== "object") {
    return { kind: PLAN.NOTHING, reason: "no-item" };
  }

  const ggufRepo = item.gguf_repo || "";
  const repo = (opts.repo || "").trim() || ggufRepo || (item.repo || "").trim();

  // No repo of any kind → genuinely nothing to download (e.g. pending models
  // whose weights have not been published yet).
  if (!repo) {
    return { kind: PLAN.NOTHING, reason: "no-repo" };
  }

  // GGUF repos: honour an explicit shard selection when the picker produced
  // one. An empty selection is NOT a cancel — it means "we could not recognise
  // any quantization", and the right answer is to let the backend auto-pick
  // rather than silently downloading nothing.
  if (ggufRepo) {
    const files = Array.isArray(opts.files) ? opts.files.filter(Boolean) : [];
    if (files.length) {
      return {
        kind: PLAN.FILES,
        args: {
          hub: HUB_HF,
          repo,
          files,
          revision: opts.revision || "main",
          auto_pick: false,
        },
      };
    }
    return {
      kind: PLAN.AUTO,
      args: { hub: HUB_HF, repo, revision: opts.revision || "main", auto_pick: true },
      reason: "quant-unrecognised",
    };
  }

  // Non-GGUF repo: download the whole thing and let the backend pick files.
  // THIS is the branch that used to download nothing at all.
  return {
    kind: PLAN.AUTO,
    args: { hub: HUB_HF, repo, revision: opts.revision || "main", auto_pick: true },
  };
}

/** Human-readable, i18n-keyed reason for a NOTHING plan. */
export function planReasonKey(reason) {
  switch (reason) {
    case "no-item": return "toast.installNothingNoItem";
    case "no-repo": return "toast.installNothingNoRepo";
    default: return "toast.installNothingGeneric";
  }
}

/**
 * Should the flow stop because the engine could not be installed?
 * Previously engine failures were swallowed and the download proceeded,
 * leaving the user with weights they could not run.
 */
export function shouldAbortOnEngineFailure(repoPlanned) {
  // Only fatal when we are about to download weights that need that engine.
  return !!repoPlanned;
}
