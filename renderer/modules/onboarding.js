// renderer/modules/onboarding.js — first-run 3-step guide (v2.4.1).
// 初衷：小白用户只看得懂模板、看不懂工作流 —— 首次启动直接告诉他
// 「装引擎 → 下模型 → 输提示词」三步，然后让开。
"use strict";

import { recordFocus, restoreFocus, trapFocus, registerEsc } from "./focus-return.js";

const FLAG = "kevrai.onboarded.v1";
// Idempotent: deferred to idle at startup; re-invocation must not re-trap focus
// or stack duplicate close handlers.
let onboardWired = false;

export function wireOnboarding() {
  if (onboardWired) return;
  onboardWired = true;
  const overlay = document.getElementById("onboarding-overlay");
  if (!overlay) return;
  try {
    if (localStorage.getItem(FLAG)) { overlay.remove(); return; }
  } catch (_) { /* storage unavailable → show once per launch */ }

  // a11y — 记录打开前焦点，trap Tab 序，Esc 关闭。
  const savedTrigger = recordFocus();
  overlay.removeAttribute("hidden");
  trapFocus(overlay);
  const close = () => {
    try { localStorage.setItem(FLAG, "1"); } catch (_) {}
    overlay.remove();
    restoreFocus(savedTrigger);
  };
  overlay.querySelectorAll("[data-action=close-onboarding]").forEach((b) =>
    b.addEventListener("click", close));
  overlay.addEventListener("click", (e) => { if (e.target === overlay) close(); });
  registerEsc({
    order: 50,
    root: overlay,
    isOpen: () => overlay.isConnected && !overlay.hasAttribute("hidden"),
    close,
  });
}
