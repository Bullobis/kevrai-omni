// renderer/modules/token-estimate.js — heuristic, model-free token estimate for
// the composer counters. CJK ideographs / kana / hangul / full-width forms
// tokenize close to 1 token/char; Latin text is ~4 visible chars/token
// (cl100k-style). This is an *estimate*; exact counts depend on the model's
// tokenizer and are shown with a leading "~".

const CJK_RE =
  /[⺀-⿏぀-ヿ㐀-䶿一-鿿豈-﫿가-ퟯ＀-￯　-〿]/;

// Ranges: CJK symbols(3000-303F), hiragana/katakana(3040-30FF), CJK ext A
// (3400-4DBF), CJK unified (4E00-9FFF), compat (F900-FAFF), hangul syllables
// (AC00-D7AF), half/full-width (FF00-FFEF).

export function estimateTokens(text) {
  if (!text) return 0;
  let cjk = 0;
  let other = 0;
  for (const ch of text) {
    if (CJK_RE.test(ch)) {
      cjk += 1;
    } else if (/\S/.test(ch)) {
      other += 1;
    }
  }
  const latin = other > 0 ? Math.ceil(other / 4) : 0;
  return cjk + latin;
}

export const TOKEN_PREF_KEY = "kevrai.showTokenEstimate";

export function tokenEstimateEnabled() {
  try {
    return localStorage.getItem(TOKEN_PREF_KEY) !== "0"; // default on
  } catch {
    return true;
  }
}

export function setTokenEstimateEnabled(on) {
  try {
    localStorage.setItem(TOKEN_PREF_KEY, on ? "1" : "0");
  } catch {
    /* storage unavailable; ignore */
  }
  try {
    window.dispatchEvent(new CustomEvent("kevrai:token-estimate-changed"));
  } catch {
    /* no window */
  }
}

export function formatTokenCount(n) {
  return `~${n}`;
}

// Attach a live counter near a (dynamically rendered) input. Safe to call more
// than once; returns the label element.
export function attachTokenCounter(inputEl) {
  if (!inputEl || inputEl.dataset.tokenCounter === "1") {
    return inputEl ? document.getElementById(inputEl.dataset.tokenLabelId) : null;
  }
  const out = document.createElement("span");
  out.className = "token-counter";
  out.setAttribute("aria-live", "off");
  const labelId = `tc-${Math.random().toString(36).slice(2, 9)}`;
  out.id = labelId;
  inputEl.dataset.tokenCounter = "1";
  inputEl.dataset.tokenLabelId = labelId;

  const place = () => {
    if (out.parentElement) return;
    if (inputEl.insertAdjacentElement) {
      inputEl.insertAdjacentElement("afterend", out);
    } else if (inputEl.parentNode) {
      inputEl.parentNode.appendChild(out);
    }
  };
  const update = () => {
    place();
    if (!tokenEstimateEnabled()) {
      out.style.display = "none";
      return;
    }
    out.style.display = "";
    out.textContent = `${formatTokenCount(estimateTokens(inputEl.value))} tokens`;
  };
  inputEl.addEventListener("input", update);
  window.addEventListener("kevrai:token-estimate-changed", update);
  update();
  return out;
}
