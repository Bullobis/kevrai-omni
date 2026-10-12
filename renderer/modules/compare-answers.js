// renderer/modules/compare-answers.js — multi-model answer comparison.
//
// Asks ONE question to several local "brain" models and renders their answers
// in side-by-side columns (Cherry Studio style compare). Models run
// sequentially on the backend (a low-RAM host may hold only one at a time).
"use strict";
import { api } from "./api.js";
import { toast } from "./toast.js";
import { t } from "./i18n.js";
import { escapeHtml, unwrap } from "./net.js";

/** The part of an "owner/name" repo id after the slash — compact column title. */
export function shortLabel(repo) {
  const s = String(repo || "");
  const i = s.lastIndexOf("/");
  return (i >= 0 ? s.slice(i + 1) : s) || s;
}

/** Render one checkbox per selectable brain model. */
export function modelCheckboxesHtml(models) {
  const list = Array.isArray(models) ? models : [];
  if (!list.length) {
    return `<p class="muted">${escapeHtml(t("compareAnswers.noModels"))}</p>`;
  }
  return list.map((m, i) => {
    const repo = escapeHtml(m.repo);
    const label = escapeHtml(m.label || shortLabel(m.repo));
    const checked = i === 0 ? " checked" : "";
    return (
      `<label class="ca-model"><input type="checkbox" ` +
      `value="${repo}"${checked}> <span title="${repo}">${label}</span></label>`
    );
  }).join("");
}

/** Render the answer columns for backend results. */
export function answerColumnsHtml(results) {
  const list = Array.isArray(results) ? results : [];
  if (!list.length) return "";
  return list.map((r) => {
    const repo = escapeHtml(r.repo || "");
    const head = escapeHtml(shortLabel(r.repo));
    let body;
    if (r.error) {
      body = `<p class="ca-error">${escapeHtml(r.error)}</p>`;
    } else {
      body = (
        `<p class="ca-answer">` +
        `${escapeHtml(r.answer || "—")}</p>`
      );
    }
    return (
      `<div class="ca-col"><div class="ca-col-head" title="${repo}">` +
      `${head}</div>${body}</div>`
    );
  }).join("");
}

function loadingColumnsHtml(repos) {
  return repos.map((r) => (
    `<div class="ca-col"><div class="ca-col-head" title="${escapeHtml(r)}">` +
    `${escapeHtml(shortLabel(r))}</div>` +
    `<p class="ca-loading">${escapeHtml(t("compareAnswers.running"))}</p></div>`
  )).join("");
}

let wired = false;

export async function initCompareAnswers() {
  const pane = document.getElementById("pane-compare-answers");
  if (!pane) return;

  const modelsBox = pane.querySelector("#ca-models");
  const question = pane.querySelector("#ca-question");
  const runBtn = pane.querySelector("#ca-run");
  const results = pane.querySelector("#ca-results");

  // Populate the model checkboxes from the brain catalog.
  try {
    const brain = unwrap(await api.agentBrain());
    modelsBox.innerHTML = modelCheckboxesHtml(brain.models || []);
  } catch (_) {
    modelsBox.innerHTML = modelCheckboxesHtml([]);
  }

  if (wired) return;
  wired = true;

  runBtn.addEventListener("click", async () => {
    const repos = [...modelsBox.querySelectorAll("input:checked")]
      .map((c) => c.value);
    const q = question.value.trim();
    if (!repos.length) {
      toast(t("compareAnswers.pickModel"), { kind: "warn" });
      return;
    }
    if (!q) {
      toast(t("compareAnswers.pickQuestion"), { kind: "warn" });
      return;
    }
    runBtn.disabled = true;
    results.innerHTML =
      `<div class="ca-grid">${loadingColumnsHtml(repos)}</div>`;
    try {
      const res = unwrap(await api.agentCompareAnswers(
        { repos, question: q, max_new_tokens: 512 }));
      results.innerHTML =
        `<div class="ca-grid">${answerColumnsHtml(res.results || [])}</div>`;
    } catch (err) {
      results.innerHTML = "";
      toast(t("compareAnswers.failed", { err: err.message || err }),
        { kind: "err" });
    } finally {
      runBtn.disabled = false;
    }
  });
}
