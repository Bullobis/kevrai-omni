// renderer/modules/dragdrop.js — drop handler for model file import.
"use strict";
import { api } from "./api.js";
import { toast } from "./toast.js";

let counter = 0;

export function wireDragDrop() {
  // Whole-window drop area; show overlay while dragging.
  const dragHost = document.body;

  // Counter correctness: only `dragenter` increments (once per entry). The old
  // code also incremented on every `dragover`, which fires dozens of times while
  // the pointer moves, so a single `dragleave` when leaving the window could
  // never bring the counter back to 0 — leaving the drop overlay stuck on.
  window.addEventListener("dragenter", (e) => {
    e.preventDefault();
    counter += 1;
    dragHost.classList.add("dragging");
  });
  // `dragover` must be canceled to allow a drop, but it does NOT touch the count.
  window.addEventListener("dragover", (e) => { e.preventDefault(); });
  window.addEventListener("dragleave", (e) => {
    e.preventDefault();
    counter = Math.max(0, counter - 1);
    if (counter === 0) dragHost.classList.remove("dragging");
  });

  window.addEventListener("drop", async (e) => {
    e.preventDefault();
    counter = 0;
    dragHost.classList.remove("dragging");
    const files = Array.from(e.dataTransfer?.files || []);
    if (!files.length) return;
    for (const f of files) {
      try {
        // Electron 32 removed File.path; prefer the preload-exposed webUtils
        // bridge, falling back to the legacy property on older runtimes.
        let p = "";
        try { if (window.kevrai && typeof window.kevrai.pathForFile === "function") {
          p = window.kevrai.pathForFile(f) || "";
        } } catch (_) { /* use fallback */ }
        p = p || f.path || "";
        if (!p) throw new Error("无法获取本地路径，请使用按钮导入");
        await api.importModel({ path: p, mode: "copy" });
        toast(`已导入 ${f.name}`, { kind: "ok" });
      } catch (err) {
        const msg = err && err.message ? err.message : String(err);
        toast(`${f.name}: ${msg}`, { kind: "err" });
      }
    }
    // Refresh grid + local list (signal main.js to reload).
    window.dispatchEvent(new CustomEvent("kevrai:models-changed"));
  });
}
