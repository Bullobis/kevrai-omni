// renderer/modules/api.js — typed-ish wrapper around window.kevrai.
// Every method returns a normalized {status, body, ...} shape OR throws.
// Errors are caught and logged as toasts; the caller may also catch them.
"use strict";
import { toast } from "./toast.js";

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
  if (!window.kevrai) {
    throw new Error("preload bridge unavailable (window.kevrai missing)");
  }
  return window.kevrai;
};

// Event subscriptions return an unsubscribe function. They are invoked
// synchronously during bootstrap wiring, so they must NOT throw when the
// bridge is unavailable (otherwise the rest of bootstrap — including nav,
// updates and the command palette — is never wired). Degrade to a no-op
// unsubscribe instead.
function safeOn(name) {
  return (cb) => {
    const b = window.kevrai;
    if (b && typeof b[name] === "function") return b[name](cb);
    return () => {};
  };
}

export const api = {
  health:        wrap("health",        () => k().health()),
  clawdchatHeartbeat: wrap("clawdchatHeartbeat", () => k().clawdchatHeartbeat()),
  categories:    wrap("categories",    () => k().categories()),
  models:        wrap("models",        (f) => k().listModels(f)),
  modelDetail:   wrap("modelDetail",   (id) => k().getModelDetail(id)),
  modelRevisions: wrap("modelRevisions", (id) => k().modelRevisions(id)),
  modelGgufFiles: wrap("modelGgufFiles", (id, revision) => k().modelGgufFiles(id, revision)),
  ggufRepos:     wrap("ggufRepos",     () => k().ggufRepos()),
  engines:       wrap("engines",       () => k().listEngines()),
  installEngine: wrap("installEngine", (id) => k().installEngine(id)),
  uninstallEngine: wrap("uninstallEngine", (id) => k().uninstallEngine(id)),
  asrCapabilities: wrap("asrCapabilities", () => k().asrCapabilities()),
  asr:            wrap("asr", (payload) => k().asr(payload)),
  embeddingsCapabilities: wrap("embeddingsCapabilities", () => k().embeddingsCapabilities()),
  embeddings:             wrap("embeddings", (payload) => k().embeddings(payload)),
  knowledgeCapabilities: wrap("knowledgeCapabilities", () => k().knowledgeCapabilities()),
  knowledgeList:     wrap("knowledgeList",     () => k().knowledgeList()),
  knowledgeCreate:   wrap("knowledgeCreate",   (payload) => k().knowledgeCreate(payload)),
  knowledgeGet:      wrap("knowledgeGet",      (kbId) => k().knowledgeGet(kbId)),
  knowledgeDelete:   wrap("knowledgeDelete",   (kbId) => k().knowledgeDelete(kbId)),
  knowledgeDocs:     wrap("knowledgeDocs",       (kbId) => k().knowledgeDocs(kbId)),
  knowledgeAddDoc:  wrap("knowledgeAddDoc",    (payload) => k().knowledgeAddDoc(payload)),
  knowledgeDeleteDoc: wrap("knowledgeDeleteDoc", (payload) => k().knowledgeDeleteDoc(payload)),
  knowledgeSetPin:  wrap("knowledgeSetPin",   (payload) => k().knowledgeSetPin(payload)),
  knowledgeSearch:  wrap("knowledgeSearch",    (payload) => k().knowledgeSearch(payload)),
  knowledgeAsk:      wrap("knowledgeAsk",      (payload) => k().knowledgeAsk(payload)),
  knowledgeExport:  wrap("knowledgeExport",   (kbId) => k().knowledgeExport(kbId)),
  knowledgeImport:  wrap("knowledgeImport",   (payload) => k().knowledgeImport(payload)),
  backupDiskGet:    wrap("backupDiskGet",     () => k().backupDiskGet()),
  backupDiskPut:    wrap("backupDiskPut",     (payload) => k().backupDiskPut(payload)),
  webdavConfigGet:  wrap("webdavConfigGet",   () => k().webdavConfigGet()),
  webdavConfigSet:  wrap("webdavConfigSet",   (payload) => k().webdavConfigSet(payload)),
  webdavTest:       wrap("webdavTest",        (payload) => k().webdavTest(payload)),
  webdavPush:       wrap("webdavPush",        () => k().webdavPush()),
  webdavPull:       wrap("webdavPull",        (payload) => k().webdavPull(payload)),
  translate:         wrap("translate",         (payload) => k().translate(payload)),
  separationCapabilities: wrap("separationCapabilities", () => k().separationCapabilities()),
  separation:             wrap("separation", (payload) => k().separation(payload)),
  separationStream:       wrap("separationStream", (params) => k().separationStream(params)),
  piperCapabilities:      wrap("piperCapabilities", () => k().piperCapabilities()),
  piperSynthesize:        wrap("piperSynthesize", (payload) => k().piperSynthesize(payload)),
  piperStream:            wrap("piperStream", (params) => k().piperStream(params)),
  multimodalCapabilities: wrap("multimodalCapabilities", () => k().multimodalCapabilities()),
  multimodalChat:         wrap("multimodalChat", (payload) => k().multimodalChat(payload)),
  // 图像生成。preload 与 IPC 早已具备（api:multimodal:generate-image），
  // 此前唯独缺这一层封装，导致 multimodal.js 调用时抛 "api.multimodalGenerateImage
  // is not a function" —— 功能在后端可用、在 UI 上必崩。
  multimodalGenerateImage: wrap("multimodalGenerateImage", (payload) => k().multimodalGenerateImage(payload)),
  // 引擎路由：后端 /api/engine/route 是"该用哪个引擎"的唯一决策点。
  engineRoute: wrap("engineRoute", (params) => k().engineRoute(params)),
  // ----- 通道补全：与 preload 一一对应 -----
  roles:              wrap("roles", (opts) => k().roles(opts)),
  rolesCapabilities:  wrap("rolesCapabilities", () => k().rolesCapabilities()),
  rolesFit:           wrap("rolesFit", (vram) => k().rolesFit(vram)),
  rolesPin:           wrap("rolesPin", (payload) => k().rolesPin(payload)),
  rolesUnpin:         wrap("rolesUnpin", (role) => k().rolesUnpin(role)),
  paramsCatalogue:    wrap("paramsCatalogue", () => k().paramsCatalogue()),
  paramsExplain:      wrap("paramsExplain", (p) => k().paramsExplain(p)),
  paramsScenario:     wrap("paramsScenario", (key) => k().paramsScenario(key)),
  paramsScenarioValues: wrap("paramsScenarioValues", (key) => k().paramsScenarioValues(key)),
  agentTools:         wrap("agentTools", () => k().agentTools()),
  paramsExplainAll:   wrap("paramsExplainAll", (p) => k().paramsExplainAll(p)),
  piperListClones:    wrap("piperListClones", () => k().piperListClones()),
  piperCreateClone:   wrap("piperCreateClone", (payload) => k().piperCreateClone(payload)),
  piperDeleteClone:   wrap("piperDeleteClone", (name) => k().piperDeleteClone(name)),
  threeDCapabilities: wrap("threeDCapabilities", () => k().threeDCapabilities()),
  threeDGenerate:     wrap("threeDGenerate", (payload) => k().threeDGenerate(payload)),
  threeDTasks:        wrap("threeDTasks", () => k().threeDTasks()),
  threeDTask:         wrap("threeDTask", (id) => k().threeDTask(id)),
  threeDCancel:       wrap("threeDCancel", (id) => k().threeDCancel(id)),
  threeDOutputs:      wrap("threeDOutputs", () => k().threeDOutputs()),
  comfyuiStatus:         wrap("comfyuiStatus", (u) => k().comfyuiStatus(u)),
  comfyuiMusicTemplates: wrap("comfyuiMusicTemplates", () => k().comfyuiMusicTemplates()),
  comfyuiMusicBuild:     wrap("comfyuiMusicBuild", (p) => k().comfyuiMusicBuild(p)),
  comfyuiMusicGenerate:  wrap("comfyuiMusicGenerate", (p) => k().comfyuiMusicGenerate(p)),
  comfyuiSubmit:         wrap("comfyuiSubmit", (p) => k().comfyuiSubmit(p)),
  comfyuiHistory:        wrap("comfyuiHistory", (p) => k().comfyuiHistory(p)),
  comfyuiQueue:          wrap("comfyuiQueue", (u) => k().comfyuiQueue(u)),
  comfyuiFree:           wrap("comfyuiFree", (u) => k().comfyuiFree(u)),
  comfyuiImageTemplates: wrap("comfyuiImageTemplates", () => k().comfyuiImageTemplates()),
  comfyuiImageBuild:     wrap("comfyuiImageBuild", (p) => k().comfyuiImageBuild(p)),
  comfyuiImageGenerate:  wrap("comfyuiImageGenerate", (p) => k().comfyuiImageGenerate(p)),
  comfyuiCheckpoints:    wrap("comfyuiCheckpoints", (u) => k().comfyuiCheckpoints(u)),
  separationSeparateLocal: wrap("separationSeparateLocal", (p) => k().separationSeparateLocal(p)),
  musicCapabilities: wrap("musicCapabilities", () => k().musicCapabilities()),
  musicGenerate:     wrap("musicGenerate", (payload) => k().musicGenerate(payload)),
  checkEngineUpdates: wrap("checkEngineUpdates", (opts) => k().checkEngineUpdates(opts)),
  updateEngine: wrap("updateEngine", (id) => k().updateEngine(id)),
  localModels:   wrap("localModels",   () => k().listLocalModels()),
  importModel:   wrap("importModel",   (opts) => k().importModel(opts)),
  detectGPU:     wrap("detectGPU",     () => k().detectGPU()),
  getSettings:   wrap("getSettings",   () => k().getSettings()),
  putSettings:   wrap("putSettings",   (s) => k().putSettings(s)),
  startDownload: wrap("startDownload", (opts) => k().startDownload(opts)),
  cancelDownload:wrap("cancelDownload",(tid) => k().cancelDownload(tid)),
  onDownloadProgress: safeOn("onDownloadProgress"),
  openPath:      wrap("openPath",      (p) => k().openPath(p)),
  showErrorDialog: wrap("showErrorDialog", (opts) => k().showErrorDialog(opts)),
  pickFolder:    wrap("pickFolder",    () => k().pickFolder()),
  pickFile:      wrap("pickFile",      () => k().pickFile()),
  openExternal:  wrap("openExternal",  (url) => k().openExternal(url)),
  checkUpdates:  wrap("checkUpdates",  () => k().checkUpdates()),
  downloadUpdate: wrap("downloadUpdate", () => k().downloadUpdate()),
  installUpdate:  wrap("installUpdate",  () => k().installUpdate()),
  workspaceList:   wrap("workspaceList",   () => k().workspaceList()),
  workspaceCreate: wrap("workspaceCreate", (name) => k().workspaceCreate(name)),
  workspaceRename: wrap("workspaceRename", (id, name) => k().workspaceRename(id, name)),
  workspaceRemove: wrap("workspaceRemove", (id) => k().workspaceRemove(id)),
  workspaceSwitch: wrap("workspaceSwitch", (id) => k().workspaceSwitch(id)),
  onUpdateProgress:   safeOn("onUpdateProgress"),
  onUpdateDownloaded: safeOn("onUpdateDownloaded"),
  onUpdateError:      safeOn("onUpdateError"),
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
  agentWsInfo:          wrap("agentWsInfo",          () => k().agentWsInfo()),
  agentSessions:        wrap("agentSessions",        (limit) => k().agentSessions(limit)),
  agentSessionMessages: wrap("agentSessionMessages", (id, limit) => k().agentSessionMessages(id, limit)),
  agentRegenerate:      wrap("agentRegenerate",      (id) => k().agentRegenerate(id)),
  agentDeleteMessage:   wrap("agentDeleteMessage",   (id, mid) => k().agentDeleteMessage(id, mid)),
  agentEditMessage:     wrap("agentEditMessage",     (id, mid, content) => k().agentEditMessage(id, mid, content)),
  agentGetPreferences:  wrap("agentGetPreferences",  () => k().agentGetPreferences()),
  agentSetPreference:   wrap("agentSetPreference",   (key, value) => k().agentSetPreference(key, value)),
  // v2.8.0 — 可插拔技能库
  agentSkills:          wrap("agentSkills",          () => k().agentSkills()),
  agentToggleSkill:     wrap("agentToggleSkill",     (id, enabled) => k().agentToggleSkill(id, enabled)),
  agentResetSkills:     wrap("agentResetSkills",     () => k().agentResetSkills()),
  // v3.23.0 — local brain selection + cancel
  agentBrain:           wrap("agentBrain",           () => k().agentBrain()),
  agentSetBrain:        wrap("agentSetBrain",        (backend) => k().agentSetBrain(backend)),
  agentCompareAnswers:  wrap("agentCompareAnswers",  (payload) => k().agentCompareAnswers(payload)),
  agentPersona:         wrap("agentPersona",         () => k().agentPersona()),
  agentSetPersona:      wrap("agentSetPersona",      (persona) => k().agentSetPersona(persona)),
  agentRenameSession:   wrap("agentRenameSession",   (id, title) => k().agentRenameSession(id, title)),
  agentExportSession:   wrap("agentExportSession",   (id) => k().agentExportSession(id)),
  mcpListServers:       wrap("mcpListServers",       () => k().mcpListServers()),
  mcpAddServer:         wrap("mcpAddServer",         (server) => k().mcpAddServer(server)),
  mcpRemoveServer:      wrap("mcpRemoveServer",      (id) => k().mcpRemoveServer(id)),
  mcpSetEnabled:        wrap("mcpSetEnabled",        (id, en) => k().mcpSetEnabled(id, en)),
  mcpListTools:         wrap("mcpListTools",         (id) => k().mcpListTools(id)),
  mcpCallTool:          wrap("mcpCallTool",          (id, tool, args) => k().mcpCallTool(id, tool, args)),
  agentCancel:          wrap("agentCancel",          (id) => k().agentCancel(id)),
  // Tool-call approval gate (confirm-before-run)
  agentApprovals:       wrap("agentApprovals",       () => k().agentApprovals()),
  agentApproveTool:     wrap("agentApproveTool",     (id, edited) => k().agentApproveTool(id, edited)),
  agentRejectTool:      wrap("agentRejectTool",      (id) => k().agentRejectTool(id)),
  agentApprovalPolicy:  wrap("agentApprovalPolicy",  (sid) => k().agentApprovalPolicy(sid)),
  agentSetApprovalPolicy: wrap("agentSetApprovalPolicy", (sid, policy) => k().agentSetApprovalPolicy(sid, policy)),
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
