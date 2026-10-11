// renderer/modules/translate.js — on-device dual-column translation workbench.
"use strict";
import { api } from "./api.js";
import { toast } from "./toast.js";
import { t } from "./i18n.js";

const $ = (s, r = document) => r.querySelector(s);

let translateInited = false;
let busy = false;

// Language list shared with the backend prompt mapping. The source side offers
// an "auto" detect option; the target side must pick an explicit language.
const LANGS = [
  { code: "zh", labelKey: "translate.lang.zh" },
  { code: "en", labelKey: "translate.lang.en" },
  { code: "ja", labelKey: "translate.lang.ja" },
  { code: "ko", labelKey: "translate.lang.ko" },
  { code: "fr", labelKey: "translate.lang.fr" },
  { code: "de", labelKey: "translate.lang.de" },
  { code: "es", labelKey: "translate.lang.es" },
  { code: "ru", labelKey: "translate.lang.ru" },
];

function fillSelect(sel, { includeAuto, preferred }) {
  if (!sel) return;
  const opts = [];
  if (includeAuto) {
    opts.push(`<option value="auto">${t("translate.lang.auto")}</option>`);
  }
  for (const l of LANGS) {
    opts.push(`<option value="${l.code}">${t(l.labelKey)}</option>`);
  }
  sel.innerHTML = opts.join("");
  if (preferred) sel.value = preferred;
}

function setStatus(msg) {
  const el = $("#translate-status");
  if (el) el.textContent = msg || "";
}

async function run() {
  const src = $("#translate-source");
  const out = $("#translate-target");
  const text = src.value.trim();
  if (!text) {
    toast(t("translate.enterFirst"), { kind: "warn" });
    return;
  }
  const btn = $("#translate-run");
  busy = true;
  btn.disabled = true;
  setStatus(t("translate.translating"));
  try {
    const r = await api.translate({
      text,
      source_lang: $("#translate-src-lang").value,
      target_lang: $("#translate-tgt-lang").value,
    });
    const body = (r && r.body) ? r.body : r;
    out.value = (body && body.translation) || "";
    setStatus(t("translate.done", { model: (body && body.model) || "" }));
  } catch (e) {
    // Error already toasted by api.js; surface a short note in the panel.
    setStatus(e && e.message ? e.message : t("translate.failed"));
  } finally {
    busy = false;
    btn.disabled = false;
  }
}

function swap() {
  const srcSel = $("#translate-src-lang");
  const tgtSel = $("#translate-tgt-lang");
  const srcBox = $("#translate-source");
  const tgtBox = $("#translate-target");
  if (!srcSel || !tgtSel) return;
  const oldSrc = srcSel.value;
  const oldTgt = tgtSel.value;
  // Swap languages, but never leave the source on "auto" after a swap (we now
  // know what the output language is).
  srcSel.value = oldTgt;
  tgtSel.value = oldSrc === "auto" ? "zh" : oldSrc;
  // Move the produced translation back into the source box for a round-trip.
  if (srcBox && tgtBox && tgtBox.value) {
    srcBox.value = tgtBox.value;
    tgtBox.value = "";
  }
}

async function copyResult() {
  const out = $("#translate-target");
  const text = out ? out.value.trim() : "";
  if (!text) {
    toast(t("translate.nothingToCopy"), { kind: "warn" });
    return;
  }
  try {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      await navigator.clipboard.writeText(text);
    } else {
      const ta = document.createElement("textarea");
      ta.value = text;
      document.body.appendChild(ta);
      ta.select();
      document.execCommand("copy");
      ta.remove();
    }
    toast(t("translate.copied"), { kind: "ok" });
  } catch (e) {
    toast(t("translate.copyFailed", { err: e.message }), { kind: "err" });
  }
}

export async function initTranslate() {
  if (translateInited) return;
  translateInited = true;
  fillSelect($("#translate-src-lang"), { includeAuto: true, preferred: "auto" });
  fillSelect($("#translate-tgt-lang"), { includeAuto: false, preferred: "en" });
  $("#translate-run").addEventListener("click", run);
  $("#translate-swap").addEventListener("click", swap);
  $("#translate-copy").addEventListener("click", copyResult);
  // Enter (without shift) in the source box triggers translation.
  const src = $("#translate-source");
  if (src) {
    src.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && !e.shiftKey && !busy) {
        e.preventDefault();
        run();
      }
    });
  }
}
