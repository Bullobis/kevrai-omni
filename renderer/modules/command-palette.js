// renderer/modules/command-palette.js
// Cherry-Studio-style command palette (Ctrl/Cmd+K): fuzzy jump to any pane or
// run a global action. It reuses existing buttons/tabs by .click()-ing them, so
// it needs no changes to the app's wiring. Also registers a few global
// shortcuts (Ctrl+Digit, Ctrl+, Ctrl+D).
"use strict";

const clickTab = (tab) => {
  const el = document.querySelector(`.sidebar .pane-tab[data-tab="${tab}"]`);
  if (el) el.click();
};
const clickAction = (action) => {
  const el = document.querySelector(`[data-action="${action}"]`);
  if (el) el.click();
};

const COMMANDS = [
  { group: "导航", icon: "◇", label: "模型市场", keywords: "market model moxing shichang", run: () => clickTab("market") },
  { group: "导航", icon: "⚡", label: "硬件推荐", keywords: "hardware gpu yingjian tui jian", run: () => clickTab("hardware") },
  { group: "导航", icon: "⟁", label: "AI 引擎", keywords: "engines yinqing", run: () => clickTab("engines") },
  { group: "导航", icon: "⬢", label: "MNN 引擎", keywords: "mnn", run: () => clickTab("mnn") },
  { group: "导航", icon: "🤖", label: "AI Agent", keywords: "agent assistant zhushou", run: () => clickTab("agent") },
  { group: "导航", icon: "🎥", label: "LTX-2.5 视频", keywords: "ltx video shipin", run: () => clickTab("ltx") },
  { group: "导航", icon: "⛁", label: "本地模型", keywords: "local bendi", run: () => clickTab("local") },
  { group: "导航", icon: "▤", label: "GGUF 仓库", keywords: "gguf cangku", run: () => clickTab("gguf") },
  { group: "导航", icon: "⌛", label: "待官方开源", keywords: "pending kaiyuan", run: () => clickTab("pending") },
  { group: "导航", icon: "⚙", label: "环境管理", keywords: "environments huanjing", run: () => clickTab("environments") },
  { group: "动作", icon: "⟳", label: "刷新", keywords: "refresh shuaxin reload", run: () => clickAction("refresh") },
  { group: "动作", icon: "⚙", label: "检测 GPU", keywords: "detect gpu jiance", run: () => clickAction("detect-gpu") },
  { group: "动作", icon: "↻", label: "检查更新", keywords: "update gengxin", run: () => clickAction("check-updates") },
  { group: "动作", icon: "⬇", label: "下载面板", keywords: "downloads xiazai", run: () => clickAction("open-downloads") },
  { group: "动作", icon: "⚙", label: "设置", keywords: "settings shezhi", run: () => clickAction("open-settings") },
];

// Simple fuzzy: every query token appears as a contiguous substring of the
// haystack (label + keywords), case-insensitive. Good enough and predictable.
function matches(cmd, q) {
  const hay = (cmd.label + " " + cmd.group + " " + cmd.keywords).toLowerCase();
  return q.toLowerCase().split(/\s+/).filter(Boolean).every((t) => hay.includes(t));
}

export function initCommandPalette() {
  if (document.getElementById("cmd-palette")) return;

  const root = document.createElement("div");
  root.id = "cmd-palette";
  root.className = "cmd-overlay";
  root.hidden = true;
  root.innerHTML = `
    <div class="cmd-card" role="dialog" aria-modal="true" aria-label="命令面板">
      <div class="cmd-input-row">
        <span class="cmd-search-ico">⌘</span>
        <input class="cmd-input" type="text" autocomplete="off" spellcheck="false"
               placeholder="搜索页面或动作…" aria-label="命令搜索" />
      </div>
      <div class="cmd-list" role="listbox"></div>
      <div class="cmd-foot">
        <span><kbd>↑↓</kbd> 选择</span>
        <span><kbd>Enter</kbd> 执行</span>
        <span><kbd>Esc</kbd> 关闭</span>
      </div>
    </div>`;
  document.body.appendChild(root);

  const input = root.querySelector(".cmd-input");
  const listEl = root.querySelector(".cmd-list");
  let selected = 0;
  let visible = [];

  const renderList = () => {
    const q = input.value.trim();
    visible = COMMANDS.filter((c) => (q ? matches(c, q) : true));
    selected = 0;
    if (!visible.length) {
      listEl.innerHTML = `<div class="cmd-empty">没有匹配的命令</div>`;
      return;
    }
    listEl.innerHTML = visible
      .map(
        (c, i) => `
        <button class="cmd-item${i === 0 ? " active" : ""}" role="option"
                aria-selected="${i === 0}" data-i="${i}">
          <span class="cmd-ico">${c.icon}</span>
          <span class="cmd-label">${c.label}</span>
          <span class="cmd-group">${c.group}</span>
        </button>`,
      )
      .join("");
    listEl.querySelectorAll(".cmd-item").forEach((b) => {
      b.addEventListener("mousedown", (e) => {
        e.preventDefault();
        selected = Number(b.dataset.i);
        execute();
      });
      b.addEventListener("mouseenter", () => setSelected(Number(b.dataset.i)));
    });
  };

  const setSelected = (i) => {
    selected = i;
    listEl.querySelectorAll(".cmd-item").forEach((b, j) => {
      const on = j === i;
      b.classList.toggle("active", on);
      b.setAttribute("aria-selected", on ? "true" : "false");
      if (on) b.scrollIntoView({ block: "nearest" });
    });
  };

  const execute = () => {
    const cmd = visible[selected];
    close();
    if (cmd) cmd.run();
  };

  const open = () => {
    root.hidden = false;
    input.value = "";
    renderList();
    // Focus after the element is shown.
    requestAnimationFrame(() => input.focus());
    document.addEventListener("keydown", onPanelKey, true);
  };

  const close = () => {
    root.hidden = true;
    document.removeEventListener("keydown", onPanelKey, true);
  };

  const onPanelKey = (e) => {
    if (e.key === "Escape") {
      e.preventDefault();
      close();
    } else if (e.key === "ArrowDown") {
      e.preventDefault();
      if (visible.length) setSelected((selected + 1) % visible.length);
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      if (visible.length) setSelected((selected - 1 + visible.length) % visible.length);
    } else if (e.key === "Enter") {
      e.preventDefault();
      execute();
    }
  };

  input.addEventListener("input", renderList);
  root.addEventListener("mousedown", (e) => {
    if (e.target === root) close();
  });

  document.addEventListener("keydown", (e) => {
    const mod = e.ctrlKey || e.metaKey;
    // Ctrl/Cmd+K toggles the palette (ignore when typing in a form field? no —
    // palette should open from anywhere).
    if (mod && (e.key === "k" || e.key === "K")) {
      e.preventDefault();
      root.hidden ? open() : close();
      return;
    }
    if (root.hidden && mod && !e.shiftKey && !e.altKey) {
      // Ctrl+1..0 → first ten panes in tab order.
      const digits = ["1", "2", "3", "4", "5", "6", "7", "8", "9", "0"];
      const di = digits.indexOf(e.key);
      const tabs = ["market", "hardware", "engines", "mnn", "agent", "ltx", "local", "gguf", "pending", "environments"];
      if (di !== -1) {
        e.preventDefault();
        clickTab(tabs[di]);
        return;
      }
      if (e.key === ",") {
        e.preventDefault();
        clickAction("open-settings");
      } else if (e.key === "d" || e.key === "D") {
        e.preventDefault();
        clickAction("open-downloads");
      }
    }
  });
}
