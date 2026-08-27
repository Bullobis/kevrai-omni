// renderer/modules/environments.js — in-app environment / dependency page.
// Detects what is installed locally, prompts the user to install missing deps,
// and shows the multi-source mirror picker for downloads.
"use strict";
import { api } from "./api.js";
import { toast } from "./toast.js";

const KNOWN_PIP_MIRRORS = [
  { id: "pypi-official",   label: "PyPI 官方",          url: "https://pypi.org/simple/" },
  { id: "aliyun",          label: "阿里云 PyPI 镜像",   url: "https://mirrors.aliyun.com/pypi/simple/" },
  { id: "tsinghua",        label: "清华 PyPI 镜像",     url: "https://pypi.tuna.tsinghua.edu.cn/simple/" },
  { id: "huaweicloud",     label: "华为云 PyPI 镜像",   url: "https://mirrors.huaweicloud.com/repository/pypi/simple/" },
  { id: "tencent",         label: "腾讯云 PyPI 镜像",   url: "https://mirrors.cloud.tencent.com/pypi/simple/" },
];

const KNOWN_MODEL_MIRRORS = [
  { id: "hf-official",  label: "HuggingFace 官方",  url: "https://huggingface.co" },
  { id: "hf-mirror",    label: "HF-Mirror (CN)",    url: "https://hf-mirror.com" },
  { id: "hf-mirror-us", label: "HF-Mirror (US)",    url: "https://hf-mirror.us" },
  { id: "hf-cn",        label: "HF-CN-Mirror",      url: "https://hf-cn-mirror.com" },
  { id: "modelscope",   label: "ModelScope (CN)",   url: "https://www.modelscope.cn" },
  { id: "aliyun",       label: "阿里云 OSS",        url: "https://oss.aliyun.com" },
];

function el(tag, props = {}, ...children) {
  const e = document.createElement(tag);
  for (const k in props) {
    if (k === "class") e.className = props[k];
    else if (k === "style") e.style.cssText = props[k];
    else if (k.startsWith("on") && typeof props[k] === "function") e.addEventListener(k.slice(2), props[k]);
    else if (k === "html") e.innerHTML = props[k];
    else e.setAttribute(k, props[k]);
  }
  for (const c of children) {
    if (c == null) continue;
    e.appendChild(typeof c === "string" ? document.createTextNode(c) : c);
  }
  return e;
}

function fmtBytes(n) {
  if (!n) return "0 B";
  const u = ["B", "KB", "MB", "GB", "TB"];
  let i = 0;
  while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
  return `${n.toFixed(1)} ${u[i]}`;
}

let state = {
  status: null,
  selectedPipMirrors: new Set(["aliyun", "tsinghua"]),
  selectedModelMirrors: new Set(["hf-official", "hf-mirror"]),
  speedResults: null,
  speedTestingFor: null,
};

export async function renderEnvironmentsPage(root) {
  root.innerHTML = "";
  const page = el("section", { class: "page page-env" });
  page.appendChild(el("h1", {}, "环境管理 / Environments"));
  page.appendChild(el("p", { class: "page-sub" },
    "在这里检测、安装、更新 Python 依赖、推理引擎、模型。所有依赖都可以在软件里下载，无需手动配置环境。"));

  // Mirror manager
  const mirrorCard = el("div", { class: "card" });
  mirrorCard.appendChild(el("h2", {}, "下载源 / Download Sources"));
  mirrorCard.appendChild(el("p", { class: "muted" },
    "勾选想要使用的镜像。下载时会自动测速，挑最快可达的源。"));

  const pipMirrorGroup = el("div", { class: "mirror-group" });
  pipMirrorGroup.appendChild(el("h3", {}, "Python pip 镜像"));
  for (const m of KNOWN_PIP_MIRRORS) {
    pipMirrorGroup.appendChild(makeMirrorRow(m, "pip", state.selectedPipMirrors));
  }
  mirrorCard.appendChild(pipMirrorGroup);

  const modelMirrorGroup = el("div", { class: "mirror-group" });
  modelMirrorGroup.appendChild(el("h3", {}, "模型/引擎 镜像"));
  for (const m of KNOWN_MODEL_MIRRORS) {
    modelMirrorGroup.appendChild(makeMirrorRow(m, "model", state.selectedModelMirrors));
  }
  mirrorCard.appendChild(modelMirrorGroup);

  // Speed test button
  const testBtn = el("button", { class: "btn", onclick: () => testAllSources() },
    "测速全部镜像");
  mirrorCard.appendChild(testBtn);

  const speedBox = el("div", { class: "speed-results" });
  mirrorCard.appendChild(speedBox);

  page.appendChild(mirrorCard);

  // Status card
  const statusCard = el("div", { class: "card" });
  statusCard.appendChild(el("h2", {}, "系统状态 / System Status"));
  const refreshBtn = el("button", { class: "btn", onclick: () => loadStatus(statusCard) }, "刷新检测");
  statusCard.appendChild(refreshBtn);
  page.appendChild(statusCard);

  // Engines card
  const engCard = el("div", { class: "card" });
  engCard.appendChild(el("h2", {}, "推理引擎 / Engines"));
  page.appendChild(engCard);

  root.appendChild(page);

  await loadStatus(statusCard);
  await loadEngines(engCard);
}

function makeMirrorRow(mirror, kind, selectedSet) {
  const id = `${kind}-${mirror.id}`;
  const cb = el("input", { type: "checkbox", id });
  cb.checked = selectedSet.has(mirror.id);
  cb.addEventListener("change", () => {
    if (cb.checked) selectedSet.add(mirror.id);
    else selectedSet.delete(mirror.id);
  });
  return el("label", { class: "mirror-row", for: id },
    cb, el("span", { class: "mirror-label" }, mirror.label),
    el("span", { class: "mirror-url" }, mirror.url));
}

async function testAllSources() {
  const all = [
    ...[...state.selectedPipMirrors].map(id => KNOWN_PIP_MIRRORS.find(m => m.id === id)?.url).filter(Boolean),
    ...[...state.selectedModelMirrors].map(id => KNOWN_MODEL_MIRRORS.find(m => m.id === id)?.url).filter(Boolean),
  ];
  if (!all.length) { toast("请先勾选至少一个镜像", { kind: "warn" }); return; }
  toast(`测速 ${all.length} 个镜像…`);
  const res = await api.measureSources(all);
  state.speedResults = res.ranking || [];
  renderSpeedResults();
}

function renderSpeedResults() {
  const box = document.querySelector(".speed-results");
  if (!box) return;
  box.innerHTML = "";
  if (!state.speedResults || !state.speedResults.length) {
    box.appendChild(el("p", { class: "muted" }, "尚未测速。"));
    return;
  }
  const ul = el("ol", { class: "speed-list" });
  for (const r of state.speedResults) {
    const ok = r.ok ? "ok" : "fail";
    const speed = r.speed_mbps ? `${r.speed_mbps} MB/s` : "—";
    const lat = r.latency_ms ? `${Math.round(r.latency_ms)} ms` : "—";
    ul.appendChild(el("li", { class: `speed-row ${ok}` },
      el("span", { class: "rank" }, `#${state.speedResults.indexOf(r) + 1}`),
      el("span", { class: "url" }, r.url),
      el("span", { class: "metric" }, `${speed} / ${lat}`),
      el("span", { class: "status" }, r.status || "err")));
  }
  box.appendChild(ul);
}

async function loadStatus(card) {
  card.innerHTML = "";
  card.appendChild(el("h2", {}, "系统状态 / System Status"));
  card.appendChild(el("p", { class: "muted" }, "正在检测…"));
  let s;
  try {
    s = await api.envStatus();
  } catch (e) {
    card.innerHTML = "";
    card.appendChild(el("h2", {}, "系统状态 / System Status"));
    card.appendChild(el("p", { class: "err" }, "检测失败：" + e.message));
    return;
  }
  state.status = s;
  card.innerHTML = "";
  card.appendChild(el("h2", {}, "系统状态 / System Status"));
  card.appendChild(el("button", { class: "btn", onclick: () => loadStatus(card) }, "刷新检测"));

  // Issues banner
  if (s.issues && s.issues.length) {
    const banner = el("div", { class: "issues-banner" });
    for (const i of s.issues) {
      banner.appendChild(el("p", { class: "issue" }, "⚠ " + i));
    }
    card.appendChild(banner);
  }

  // Python / Node / GPU rows
  const summary = el("div", { class: "status-grid" });
  summary.appendChild(makeKV("Python", s.python?.available ? `${s.python.version}  (pip ${s.python.pip_version || "?"})` : "❌ 不可用"));
  summary.appendChild(makeKV("Node.js", s.node?.available ? s.node.version : "未检测到（可选）"));
  summary.appendChild(makeKV("GPU", (s.gpus || []).map(g => `${g.vendor}/${g.name} ${g.vram_mb ? g.vram_mb + "MB" : ""}`).join("、") || "仅 CPU"));
  summary.appendChild(makeKV("磁盘剩余", fmtBytes(s.disk?.free_bytes || 0) + ` / ${fmtBytes(s.disk?.total_bytes || 0)}`));
  summary.appendChild(makeKV("Models 目录占用", fmtBytes(s.disk?.models_dir_bytes || 0)));
  summary.appendChild(makeKV("Engines 目录占用", fmtBytes(s.disk?.engines_dir_bytes || 0)));
  card.appendChild(summary);

  // pip packages
  const pkg = el("div", { class: "pkg-list" });
  pkg.appendChild(el("h3", {}, "Python 依赖"));
  const tbl = el("table", { class: "pkg-table" });
  const thead = el("thead", {}, el("tr", {},
    el("th", {}, "包"), el("th", {}, "已安装"), el("th", {}, "要求"), el("th", {}, "操作")));
  tbl.appendChild(thead);
  const tbody = el("tbody");
  for (const p of (s.pip_packages || []).slice(0, 50)) {
    const tr = el("tr", {});
    tr.appendChild(el("td", {}, p.name));
    tr.appendChild(el("td", { class: p.version ? "" : "missing" }, p.version || "未安装"));
    tr.appendChild(el("td", { class: "muted" }, p.required || ""));
    const btn = el("button", { class: "btn small" },
      p.version ? "升级" : "安装");
    btn.addEventListener("click", () => installOrUpgrade(p));
    tr.appendChild(el("td", {}, btn));
    tbody.appendChild(tr);
  }
  tbl.appendChild(tbody);
  pkg.appendChild(tbl);
  card.appendChild(pkg);
}

function makeKV(k, v) {
  return el("div", { class: "kv" },
    el("span", { class: "k" }, k),
    el("span", { class: "v" }, v));
}

async function installOrUpgrade(pkg) {
  const mirrors = [...state.selectedPipMirrors]
    .map(id => KNOWN_PIP_MIRRORS.find(m => m.id === id)?.url)
    .filter(Boolean);
  toast(`${pkg.version ? "升级" : "安装"} ${pkg.name}…`);
  try {
    if (pkg.version) {
      await api.envUpgrade({ name: pkg.name, mirrors });
    } else {
      await api.envInstallPip({ name: pkg.name, mirrors });
    }
    toast(`${pkg.name} 完成`);
    // refresh
    const card = document.querySelector(".page-env .card:nth-of-type(2)");
    if (card) loadStatus(card);
  } catch (e) {
    toast(`${pkg.name} 失败：${e.message}`, { kind: "err" });
  }
}

async function loadEngines(card) {
  card.innerHTML = "";
  card.appendChild(el("h2", {}, "推理引擎 / Engines"));
  let engines;
  try {
    engines = await api.engines();
  } catch (e) {
    card.appendChild(el("p", { class: "err" }, "加载失败：" + e.message));
    return;
  }
  const list = (engines.engines || engines || []);
  const grid = el("div", { class: "engine-grid" });
  for (const e of list) {
    const isInstalled = e.installed || e.state === "installed";
    const item = el("div", { class: `engine-item ${isInstalled ? "installed" : ""}` });
    item.appendChild(el("h4", {}, e.name || e.id));
    item.appendChild(el("p", { class: "muted small" },
      `${e.category || ""} · ${e.license || "?"} · v${e.version || "?"}`));
    if (e.sources && e.sources.length) {
      item.appendChild(el("p", { class: "muted xsmall" },
        `${e.sources.length} 个下载源`));
    }
    const btn = el("button", { class: "btn small" },
      isInstalled ? "重新安装" : "安装");
    btn.addEventListener("click", () => installEngine(e));
    item.appendChild(btn);
    grid.appendChild(item);
  }
  card.appendChild(grid);
}

async function installEngine(engine) {
  toast(`安装 ${engine.name || engine.id}（自动挑选最快源）…`);
  try {
    const res = await api.envInstallEngine({ id: engine.id });
    toast(`${engine.name} 安装完成（源: ${res.source_used}）`);
    const card = document.querySelector(".page-env .card:nth-of-type(3)");
    if (card) loadEngines(card);
  } catch (e) {
    toast(`${engine.name} 失败：${e.message}`, { kind: "err" });
  }
}

export function getSelectedPipMirrors() {
  return [...state.selectedPipMirrors]
    .map(id => KNOWN_PIP_MIRRORS.find(m => m.id === id)?.url)
    .filter(Boolean);
}
