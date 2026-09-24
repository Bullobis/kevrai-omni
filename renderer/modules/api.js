// renderer/modules/api.js — typed-ish wrapper around window.kevrai.
// Every method returns a normalized {status, body, ...} shape OR throws.
// Errors are caught and logged as toasts; the caller may also catch them.
"use strict";
import { toast } from "./toast.js";
import { httpBridge } from "./http-bridge.js";

function wrap(name, fn) {
  return async (...args) => {
    try {
      const r = await fn(...args);
      return r;
    } catch (e) {
      // Always show an error toast; never silent.
      const msg = (e && e.message) ? String(e.message) : String(e);
      toast(`${name} 失败：${msg}`, { kind: "err" });
      throw e;
    }
  };
}

const k = () => {
  // 优先使用 Electron preload 桥；preload 缺失时（拦截 / 加载失败 / 浏览器
  // 调试）自动降级为 HTTP 直连桥，避免整个界面因 window.kevrai 缺失而空白。
  if (window.kevrai) return window.kevrai;
  return httpBridge;
};

export const api = {
  health:        wrap("health",        () => k().health()),
  categories:    wrap("categories",    () => k().categories()),
  models:        wrap("models",        (f) => k().listModels(f)),
  modelDetail:   wrap("modelDetail",   (id) => k().getModelDetail(id)),
  modelGgufFiles: wrap("modelGgufFiles", (id) => k().modelGgufFiles(id)),
  ggufRepos:     wrap("ggufRepos",     () => k().ggufRepos()),
  engines:       wrap("engines",       () => k().listEngines()),
  installEngine: wrap("installEngine", (id) => k().installEngine(id)),
  uninstallEngine: wrap("uninstallEngine", (id) => k().uninstallEngine(id)),
  checkEngineUpdates: wrap("checkEngineUpdates", (opts) => k().checkEngineUpdates(opts)),
  updateEngine: wrap("updateEngine", (id) => k().updateEngine(id)),
  localModels:   wrap("localModels",   () => k().listLocalModels()),
  importModel:   wrap("importModel",   (opts) => k().importModel(opts)),
  detectGPU:     wrap("detectGPU",     () => k().detectGPU()),
  getSettings:   wrap("getSettings",   () => k().getSettings()),
  putSettings:   wrap("putSettings",   (s) => k().putSettings(s)),
  startDownload: wrap("startDownload", (opts) => k().startDownload(opts)),
  cancelDownload:wrap("cancelDownload",(tid) => k().cancelDownload(tid)),
  onDownloadProgress: (cb) => k().onDownloadProgress(cb),
  openPath:      wrap("openPath",      (p) => k().openPath(p)),
  showErrorDialog: wrap("showErrorDialog", (opts) => k().showErrorDialog(opts)),
  pickFolder:    wrap("pickFolder",    () => k().pickFolder()),
  pickFile:      wrap("pickFile",      () => k().pickFile()),
  openExternal:  wrap("openExternal",  (url) => k().openExternal(url)),
  checkUpdates:  wrap("checkUpdates",  () => k().checkUpdates()),
  downloadUpdate: wrap("downloadUpdate", () => k().downloadUpdate()),
  installUpdate:  wrap("installUpdate",  () => k().installUpdate()),
  onUpdateProgress:   (cb) => k().onUpdateProgress(cb),
  onUpdateDownloaded: (cb) => k().onUpdateDownloaded(cb),
  onUpdateError:      (cb) => k().onUpdateError(cb),
  getAppVersion: wrap("getAppVersion", () => k().getAppVersion()),
  // v2.2.0 — multi-source & environment management
  envStatus:     wrap("envStatus",     () => k().envStatus()),
  envInstallPip: wrap("envInstallPip", (opts) => k().envInstallPip(opts)),
  envUpgrade:    wrap("envUpgrade",    (opts) => k().envUpgrade(opts)),
  envInstallEngine: wrap("envInstallEngine", (opts) => k().envInstallEngine(opts)),
  measureSources:   wrap("measureSources",   (urls) => k().measureSources(urls)),
  // v2.8.1 — source registry / health / manual lock.
  getSourceRegistry: wrap("getSourceRegistry", () => k().getSourceRegistry()),
  getSourceHealth:   wrap("getSourceHealth",   () => k().getSourceHealth()),
  lockSource:        wrap("lockSource",        (id) => k().lockSource(id)),
  // v2.3.0 — hardware / recommendation / MNN runtime
  hardware:       wrap("hardware",       (opts) => k().hardware(opts || {})),
  recommend:      wrap("recommend",      (opts) => k().recommend(opts || {})),
  mnnModels:      wrap("mnnModels",      () => k().mnnModels()),
  mnnModelFiles:  wrap("mnnModelFiles",  (id) => k().mnnModelFiles(id)),
  mnnStatus:      wrap("mnnStatus",      () => k().mnnStatus()),
  mnnLoad:        wrap("mnnLoad",        (opts) => k().mnnLoad(opts)),
  mnnUnload:      wrap("mnnUnload",      () => k().mnnUnload()),
  mnnChat:        wrap("mnnChat",        (opts) => k().mnnChat(opts)),
  mnnDownload:    wrap("mnnDownload",    (id) => k().mnnDownload(id)),
  mnnDownloadCancel:    wrap("mnnDownloadCancel",    () => k().mnnDownloadCancel()),
  mnnDownloadStatus:    wrap("mnnDownloadStatus",    () => k().mnnDownloadStatus()),
  mnnLocal:       wrap("mnnLocal",       () => k().mnnLocal()),
  // llama.cpp local runtime (DIY GGUF)
  llmStart:       wrap("llmStart",       (opts) => k().llmStart(opts)),
  llmStop:        wrap("llmStop",        () => k().llmStop()),
  llmStatus:      wrap("llmStatus",      () => k().llmStatus()),
  // Model converter
  convertCapabilities: wrap("convertCapabilities", () => k().convertCapabilities()),
  convertStart:   wrap("convertStart",   (opts) => k().convertStart(opts)),
  convertTasks:   wrap("convertTasks",   () => k().convertTasks()),
  convertTask:    wrap("convertTask",    (id) => k().convertTask(id)),
  convertCancel:  wrap("convertCancel",  (id) => k().convertCancel(id)),
  // Drama Agent (AI 短剧生成)
  dramaOptions:      wrap("dramaOptions",      () => k().dramaOptions()),
  dramaStorycraft:   wrap("dramaStorycraft",   () => k().dramaStorycraft()),
  dramaBrainstorm:   wrap("dramaBrainstorm",   (opts) => k().dramaBrainstorm(opts)),
  dramaScript:       wrap("dramaScript",       (opts) => k().dramaScript(opts)),
  dramaStoryboard:   wrap("dramaStoryboard",   (opts) => k().dramaStoryboard(opts)),
  dramaRenderPlan:   wrap("dramaRenderPlan",   (opts) => k().dramaRenderPlan(opts)),
  // v2.7.0 — Kevrai Agent (通用 AI 助手)
  agentStatus:          wrap("agentStatus",          () => k().agentStatus()),
  agentChat:            wrap("agentChat",            (opts) => k().agentChat(opts)),
  agentSessions:        wrap("agentSessions",        (limit) => k().agentSessions(limit)),
  agentSessionMessages: wrap("agentSessionMessages", (id, limit) => k().agentSessionMessages(id, limit)),
  agentGetPreferences:  wrap("agentGetPreferences",  () => k().agentGetPreferences()),
  agentSetPreference:   wrap("agentSetPreference",   (key, value) => k().agentSetPreference(key, value)),
  // v2.8.0 — 可插拔技能库
  agentSkills:          wrap("agentSkills",          () => k().agentSkills()),
  agentToggleSkill:     wrap("agentToggleSkill",     (id, enabled) => k().agentToggleSkill(id, enabled)),
  agentResetSkills:     wrap("agentResetSkills",     () => k().agentResetSkills()),
  // v2.9.0 — skill hub（导入外部 Anthropic SKILL.md 技能）
  // 走 k() 守卫：老 preload 缺这些方法时抛可捕获的错误，而不是 TypeError。
  skillHubList:      wrap("skillHubList",      () => k().skillHubList()),
  skillHubImportDir: wrap("skillHubImportDir", (p) => k().skillHubImportDir(p)),
  skillHubImportZip: wrap("skillHubImportZip", (p) => k().skillHubImportZip(p)),
  skillHubImportGit: wrap("skillHubImportGit", (u) => k().skillHubImportGit(u)),
  skillHubRemove:    wrap("skillHubRemove",    (id) => k().skillHubRemove(id)),
  // v2.4.0 — super search
  search:            wrap("search",            (params) => k().search(params)),
  searchRecent:      wrap("searchRecent",      () => k().searchRecent()),
  searchClearRecent: wrap("searchClearRecent", () => k().searchClearRecent()),
  // v2.8.0 — dual-source hub (HF + ModelScope). Guarded so an old preload
  // (which lacks these) throws a catchable error rather than a TypeError.
  hubSources:   wrap("hubSources",   () => k().hubSources()),
  hubSearch:    wrap("hubSearch",    (params) => k().hubSearch(params)),
  hubModel:     wrap("hubModel",     (params) => k().hubModel(params)),
  hubFiles:     wrap("hubFiles",     (params) => k().hubFiles(params)),
  hubDownload:  wrap("hubDownload",  (params) => k().hubDownload(params)),
  hubJob:       wrap("hubJob",       (jobId) => k().hubJob(jobId)),
  // v2.4.0 — LTX-2.5 video generation
  ltxCapabilities:   wrap("ltxCapabilities",   () => k().ltxCapabilities()),
  ltxGenerate:       wrap("ltxGenerate",       (opts) => k().ltxGenerate(opts)),
  ltxTasks:          wrap("ltxTasks",          () => k().ltxTasks()),
  ltxTask:           wrap("ltxTask",           (id) => k().ltxTask(id)),
  ltxCancel:         wrap("ltxCancel",         (id) => k().ltxCancel(id)),
  ltxOutputs:        wrap("ltxOutputs",        () => k().ltxOutputs()),
};
