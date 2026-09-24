// renderer/modules/http-bridge.js
//
// 浏览器直连降级桥（HTTP fallback bridge）。
//
// 背景：渲染层默认只与 Electron preload 注入的 `window.kevrai` 通信。
// 一旦 preload 缺失（被安全软件拦截、preload 加载失败、或在普通浏览器中
// 调试 / 预览），原实现会直接抛 "preload bridge unavailable" 且整个界面空白。
//
// 本模块提供一个与 electron/preload.js 暴露面【同构】的对象：所有能通过
// sidecar HTTP 完成的方法，直接 fetch http://127.0.0.1:17890 并返回与
// sidecarFetch 完全一致的 { status, body } 形状；少数 Electron 主进程专属
// 能力（原生文件对话框、自动更新下载/安装、本地 spawn llama-server、
// Python 运行时引导、openPath 等）在浏览器中无对应实现，抛出明确的中文
// 错误，而不是静默失败。
//
// 安全：目标地址固定为本地 sidecar，且 index.html 的 CSP connect-src 已
// 显式允许 http://127.0.0.1:17890，因此该降级在 Electron 内同样可用。
"use strict";

const SIDECAR_BASE = "http://127.0.0.1:17890";

// 与 electron/main.js sidecarFetch 的成功/失败语义保持一致：
//   成功 → { status, body }（body 为解析后的 JSON 或原始文本）
//   非 2xx / 网络错误 → 抛出 Error
async function request(path, options = {}) {
  const method = options.method || "GET";
  let res;
  try {
    res = await fetch(SIDECAR_BASE + path, {
      method,
      headers: { "Content-Type": "application/json" },
      body: options.body ? JSON.stringify(options.body) : undefined,
    });
  } catch (e) {
    throw new Error(`无法连接本地 sidecar（${SIDECAR_BASE}）：${e && e.message ? e.message : e}`);
  }
  const text = await res.text();
  let body = null;
  if (text) {
    try { body = JSON.parse(text); } catch (_) { body = text; }
  }
  if (!res.ok) {
    throw new Error(`sidecar ${res.status}: ${text.slice(0, 500)}`);
  }
  return { status: res.status, body };
}

// 把一组键值编码为 query string（跳过 null/undefined/空串）。
function qs(params = {}) {
  const u = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value === null || value === undefined || value === "") continue;
    u.set(key, String(value));
  }
  const s = u.toString();
  return s ? "?" + s : "";
}

// 桌面专属能力在浏览器直连模式下不可用。
function desktopOnly(name) {
  throw new Error(`当前为浏览器直连模式，不支持桌面专属功能「${name}」，请在 Kevrai Omni 桌面应用中使用。`);
}

// 事件订阅在浏览器中无主进程推送源；返回一个标准的取消订阅函数以保证调用安全。
function noopSubscribe() { return () => {}; }

const enc = encodeURIComponent;

export const httpBridge = {
  // ----- 版本 / 自动更新 -----
  // preload 的 getAppVersion 返回版本字符串；浏览器中从 /api/health 派生。
  getAppVersion: async () => {
    try {
      const r = await request("/api/health");
      return (r.body && r.body.version) ? r.body.version : "";
    } catch (_) { return ""; }
  },
  checkUpdates: () => desktopOnly("检查应用更新"),
  downloadUpdate: () => desktopOnly("下载应用更新"),
  installUpdate: () => desktopOnly("安装应用更新"),
  onUpdateProgress: noopSubscribe,
  onUpdateDownloaded: noopSubscribe,
  onUpdateError: noopSubscribe,

  // ----- 设置 -----
  getSettings: () => request("/api/settings"),
  putSettings: (s) => request("/api/settings", { method: "PUT", body: s }),

  // ----- 检测 -----
  detectGPU: () => request("/api/gpu"),

  // ----- 模型 / 目录 -----
  health: () => request("/api/health"),
  categories: () => request("/api/categories"),
  models: (params = {}) => {
    const p = params || {};
    return request("/api/models" + qs({ category: p.category, q: p.q }));
  },
  listModels: (filter = {}) => {
    const f = filter || {};
    return request("/api/models" + qs({ category: f.category, q: f.search }));
  },
  modelDetail: (id) => request(`/api/models/${enc(id)}`),
  getModelDetail: (id) => request(`/api/models/${enc(id)}`),
  modelGgufFiles: (id) => request(`/api/models/${enc(id)}/gguf-files`),
  ggufRepos: () => request("/api/gguf-repos"),

  // ----- 引擎 -----
  engines: () => request("/api/engines"),
  listEngines: () => request("/api/engines"),
  installEngine: (id) => request("/api/engines/install", { method: "POST", body: { engine_id: id } }),
  uninstallEngine: (id) => request("/api/engines/uninstall", { method: "POST", body: { engine_id: id } }),
  checkEngineUpdates: (opts = {}) =>
    request("/api/engines/check-updates", { method: "POST", body: { force: !!(opts && opts.force) } }),
  updateEngine: (id) => request("/api/engines/update", { method: "POST", body: { engine_id: id } }),

  // ----- 本地模型 / 导入 -----
  localModels: () => request("/api/models/local"),
  listLocalModels: () => request("/api/models/local"),
  importModel: (pathOrOpts) => {
    const p = typeof pathOrOpts === "string" ? pathOrOpts : (pathOrOpts && pathOrOpts.path);
    return request("/api/models/import", { method: "POST", body: { path: p } });
  },
  progress: () => request("/api/progress"),

  // ----- 环境 / 依赖 / 引擎管理 -----
  envStatus: () => request("/api/env/status"),
  envInstallPip: (opts) => request("/api/env/install", { method: "POST", body: opts }),
  envUpgrade: (opts) => request("/api/env/upgrade", { method: "POST", body: opts }),
  envInstallEngine: (opts) => request("/api/env/install-engine", { method: "POST", body: opts }),
  measureSources: (urls) => request("/api/sources/measure", { method: "POST", body: { urls } }),

  // ----- 来源注册 / 健康 / 锁定 -----
  getSourceRegistry: () => request("/api/sources/registry"),
  getSourceHealth: () => request("/api/sources/health"),
  lockSource: (sourceId) =>
    request("/api/sources/lock", { method: "POST", body: { source_id: sourceId } }),

  // ----- 硬件 / 推荐 -----
  hardware: (opts = {}) => request("/api/hardware" + qs({ refresh: opts.refresh ? 1 : undefined })),
  recommend: (opts = {}) => request("/api/recommend" + qs({
    limit: opts.limit, category: opts.category, refresh: opts.refresh ? 1 : undefined,
  })),

  // ----- MNN 运行时 -----
  mnnModels: () => request("/api/mnn/models"),
  mnnModelFiles: (id) => request(`/api/mnn/models/${enc(id)}/files`),
  mnnStatus: () => request("/api/mnn/status"),
  mnnLoad: (opts) => request("/api/mnn/load", { method: "POST", body: opts }),
  mnnUnload: () => request("/api/mnn/unload", { method: "POST" }),
  mnnChat: (opts) => request("/api/mnn/chat", { method: "POST", body: opts }),
  mnnDownload: (opts) => {
    const payload = typeof opts === "string" ? { entry_id: opts } : opts;
    return request("/api/mnn/download", { method: "POST", body: payload });
  },
  mnnDownloadCancel: () => request("/api/mnn/download/cancel", { method: "POST" }),
  mnnDownloadStatus: () => request("/api/mnn/download"),
  mnnLocal: () => request("/api/mnn/local"),

  // ----- llama.cpp 本地运行时（Electron 主进程直接 spawn，浏览器不支持）-----
  llmStart: () => desktopOnly("启动 llama-server"),
  llmStop: () => desktopOnly("停止 llama-server"),
  llmStatus: () => desktopOnly("llama-server 状态"),

  // ----- 模型转换 -----
  convertCapabilities: () => request("/api/convert/capabilities"),
  convertStart: (opts) => request("/api/convert/start", { method: "POST", body: opts }),
  convertTasks: () => request("/api/convert/tasks"),
  convertTask: (id) => request(`/api/convert/${enc(id)}`),
  convertCancel: (id) => request(`/api/convert/${enc(id)}/cancel`, { method: "POST" }),

  // ----- 短剧 Agent -----
  dramaOptions: () => request("/api/drama/options"),
  dramaStorycraft: () => request("/api/drama/storycraft"),
  dramaBrainstorm: (opts) => request("/api/drama/brainstorm", { method: "POST", body: opts }),
  dramaScript: (opts) => request("/api/drama/script", { method: "POST", body: opts }),
  dramaStoryboard: (opts) => request("/api/drama/storyboard", { method: "POST", body: opts }),
  dramaRenderPlan: (opts) => request("/api/drama/render-plan", { method: "POST", body: opts }),

  // ----- Kevrai Agent -----
  agentStatus: () => request("/api/agent/status"),
  agentChat: (opts) => request("/api/agent/chat", { method: "POST", body: opts }),
  agentSessions: (limit) => request("/api/agent/sessions" + qs({ limit })),
  agentSessionMessages: (id, limit) =>
    request(`/api/agent/sessions/${enc(id)}/messages` + qs({ limit })),
  agentGetPreferences: () => request("/api/agent/preferences"),
  agentSetPreference: (key, value) =>
    request("/api/agent/preferences", { method: "PUT", body: { key, value } }),

  // ----- 可插拔技能库 -----
  agentSkills: () => request("/api/agent/skills"),
  agentToggleSkill: (skillId, enabled) =>
    request(`/api/agent/skills/${enc(skillId)}`, { method: "POST", body: { enabled } }),
  agentResetSkills: () => request("/api/agent/skills/reset", { method: "POST", body: {} }),

  // ----- skill hub -----
  skillHubList: () => request("/api/agent/skill-hub"),
  skillHubImportDir: (dirPath) =>
    request("/api/agent/skill-hub/import", { method: "POST", body: { path: dirPath } }),
  skillHubImportZip: (zipPath) =>
    request("/api/agent/skill-hub/import-zip", { method: "POST", body: { path: zipPath } }),
  skillHubImportGit: (url) =>
    request("/api/agent/skill-hub/import-git", { method: "POST", body: { url } }),
  skillHubRemove: (skillId) =>
    request(`/api/agent/skill-hub/${enc(skillId)}`, { method: "DELETE" }),

  // ----- 超级搜索 -----
  search: (params = {}) => {
    const p = params || {};
    return request("/api/search" + qs({
      q: p.q,
      category: p.category,
      engine: p.engine,
      license: p.license,
      size_bucket: p.size_bucket,
      trending: p.trending ? 1 : undefined,
      sort: p.sort,
      page: p.page,
      page_size: p.page_size,
    }));
  },
  searchRecent: () => request("/api/search/recent"),
  searchClearRecent: () => request("/api/search/recent", { method: "DELETE" }),

  // ----- 双源 hub（HF + ModelScope + curated）-----
  hubSources: () => request("/api/hub/sources"),
  hubHealth: () => request("/api/hub/health", { method: "POST" }),
  hubSearch: (params = {}) => request("/api/hub/search" + qs(params)),
  hubModel: (params = {}) => request("/api/hub/model" + qs(params)),
  hubFiles: (params = {}) => request("/api/hub/model/files" + qs(params)),
  hubDownload: (params) => request("/api/hub/download", { method: "POST", body: params }),
  hubJob: (jobId) => request(`/api/hub/jobs/${enc(jobId)}`),

  // ----- LTX-2.5 视频 -----
  ltxCapabilities: () => request("/api/ltx/capabilities"),
  ltxGenerate: (opts) => request("/api/ltx/generate", { method: "POST", body: opts }),
  ltxTasks: () => request("/api/ltx/tasks"),
  ltxTask: (id) => request(`/api/ltx/tasks/${enc(id)}`),
  ltxCancel: (id) => request(`/api/ltx/tasks/${enc(id)}/cancel`, { method: "POST" }),
  ltxOutputs: () => request("/api/ltx/outputs"),

  // ----- 下载 -----
  startDownload: (opts) => request("/api/download/start", { method: "POST", body: opts }),
  cancelDownload: (taskId) =>
    request(`/api/download/${enc(taskId)}/cancel`, { method: "POST" }),
  onDownloadProgress: noopSubscribe,

  // ----- Python 运行时引导（Electron 专属）-----
  bootstrapStatus: () => desktopOnly("引导状态"),
  installPythonRuntime: () => desktopOnly("安装 Python 运行时"),
  installPythonDeps: () => desktopOnly("安装 Python 依赖"),
  bootstrapRetry: () => desktopOnly("重试引导"),
  onBootstrapProgress: noopSubscribe,

  // ----- 对话框 / 路径 / 外壳 -----
  pickFolder: () => desktopOnly("选择文件夹"),
  pickFile: () => desktopOnly("选择文件"),
  openPath: () => desktopOnly("在文件管理器中显示"),
  showErrorDialog: () => desktopOnly("错误对话框"),
  openExternal: (url) => {
    if (!/^https?:\/\//i.test(url)) throw new Error("url: only http(s) allowed");
    const w = window.open(url, "_blank", "noopener");
    return Promise.resolve(w ? true : false);
  },

  // ----- 日志 / 健康事件 -----
  logEvent: () => Promise.resolve({ status: 200, body: null }),
  onHealth: noopSubscribe,
  onSidecarDown: noopSubscribe,
  onProgress: noopSubscribe,
};
