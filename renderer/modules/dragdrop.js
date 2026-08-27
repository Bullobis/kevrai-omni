// renderer/modules/dragdrop.js — drop handler for model file import.
"use strict";
import { api } from "./api.js";
import { toast } from "./toast.js";

let counter = 0;

export function wireDragDrop() {
  // Whole-window drop area; show overlay while dragging.
  const dragHost = document.body;

  ["dragenter", "dragover"].forEach((ev) => {
    window.addEventListener(ev, (e) => {
      e.preventDefault();
      counter += 1;
      dragHost.classList.add("dragging");
    });
  });
  ["dragleave", "drop"].forEach((ev) => {
    window.addEventListener(ev, (e) => {
      e.preventDefault();
      counter = Math.max(0, counter - 1);
      if (counter === 0) dragHost.classList.remove("dragging");
    });
  });

  window.addEventListener("drop", async (e) => {
    e.preventDefault();
    const files = Array.from(e.dataTransfer?.files || []);
    if (!files.length) return;
    for (const f of files) {
      try {
        const p = (f.path) || (f.webkitRelativePath ? "" : "");
        // Electron exposes the absolute path on the File object via `.path`.
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
