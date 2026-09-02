// renderer/modules/onboarding.js — first-run 3-step guide (v2.4.1).
// 初衷：小白用户只看得懂模板、看不懂工作流 —— 首次启动直接告诉他
// 「装引擎 → 下模型 → 输提示词」三步，然后让开。
"use strict";

const FLAG = "kevrai.onboarded.v1";

export function wireOnboarding() {
  const overlay = document.getElementById("onboarding-overlay");
  if (!overlay) return;
  try {
    if (localStorage.getItem(FLAG)) { overlay.remove(); return; }
  } catch (_) { /* storage unavailable → show once per launch */ }

  overlay.removeAttribute("hidden");
  const close = () => {
    try { localStorage.setItem(FLAG, "1"); } catch (_) {}
    overlay.remove();
  };
  overlay.querySelectorAll("[data-action=close-onboarding]").forEach((b) =>
    b.addEventListener("click", close));
  overlay.addEventListener("click", (e) => { if (e.target === overlay) close(); });
}
