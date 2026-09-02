"use strict";
/**
 * Kevrai Omni — preload (hardened context bridge).
 *
 * Rules:
 *   - Expose ONLY the audit-approved API on `window.kevrai`. No `ipcRenderer`
 *     and no Node primitives leak into the renderer.
 *   - Validate inputs at the bridge: type checks, length caps, enum checks.
 *   - Wrap every `invoke` in a Promise that *normalizes* errors to plain
 *     `Error` (string-only) so renderer cannot introspect internals.
 *
 * Renderer talks only to `window.kevrai`. contextIsolation is on, sandbox is
 * on, so the worst the renderer can do is call these names with bad args.
 */

const { contextBridge, ipcRenderer } = require("electron");

// ---------------------------------------------------------------------------
// Internal helpers (not exported)
// ---------------------------------------------------------------------------

const ERR_PRELUED = "kevrai:";

function exposeErr(name, e) {
  // Strip out stack traces and chans; renderer never sees raw ipcError.
  const msg = (e && e.message) ? String(e.message) : String(e);
  return new Error(`${ERR_PRELUED}${name}: ${msg}`);
}

function assertString(v, name, max = 256) {
  if (typeof v !== "string" || v.length === 0 || v.length > max) {
    throw new Error(`${name}: must be a non-empty string ≤${max}`);
  }
}
function assertEnum(v, list, name) {
  if (typeof v !== "string" || !list.includes(v)) {
    throw new Error(`${name}: must be one of ${list.join("|")}`);
  }
}
function assertObject(v, name) {
  if (!v || typeof v !== "object" || Array.isArray(v)) {
    throw new Error(`${name}: must be an object`);
  }
}
function assertOptionalString(v, name, max = 256) {
  if (v == null) return;
  if (typeof v !== "string" || v.length > max) {
    throw new Error(`${name}: must be string ≤${max}`);
  }
}

// Wraps `invoke(channel, ...args)` so renderer cannot pick arbitrary channels.
function invoke(channel, ...args) {
  return ipcRenderer.invoke(channel, ...args).catch((e) => {
    throw exposeErr(channel, e);
  });
}

// Wrap `ipcRenderer.on` and only forward specific, allowlisted channels with
// a safely-shaped payload. Return an unsubscribe function (renderer-clean).
function listen(channel, cb) {
  if (typeof cb !== "function") return () => {};
  const handler = (_event, payload) => {
    try { cb(payload); } catch (_) {}
  };
  ipcRenderer.on(channel, handler);
  return () => ipcRenderer.removeListener(channel, handler);
}

// ---------------------------------------------------------------------------
// Public API surface — mirror of electron/main.js handler list.
// ---------------------------------------------------------------------------

const api = {
  // ----- Version / updates -----
  getAppVersion: () => invoke("kevrai:check-updates")
    .then((r) => r && r.currentVersion ? r.currentVersion : "")
    .catch(() => ""),
  checkUpdates:  () => invoke("kevrai:check-updates"),

  // ----- Settings -----
  getSettings:   () => invoke("kevrai:get-settings"),
  putSettings:   (s) => {
    assertObject(s, "settings");
    return invoke("kevrai:put-settings", s);
  },

  // ----- Detection -----
  detectGPU:     () => invoke("kevrai:detect-gpu"),

  // ----- Models / catalog (legacy surface kept for renderer/app.js compat) -----
  health:        () => invoke("api:health"),
  categories:    () => invoke("api:categories"),
  models:        (params) => {
    const p = (params == null || typeof params === "object") ? (params || {}) : {};
    assertObject(p, "params");
    const clean = {};
    if (p.category != null) { assertString(p.category, "category", 64); clean.category = p.category; }
    if (p.q       != null) { assertString(p.q, "q", 200); clean.q = p.q; }
    if (p.search   != null) { assertString(p.search, "search", 200); clean.q = p.search; }
    return invoke("api:models", clean);
  },
  listModels:    (filter) => {
    const f = (filter == null) ? {} : filter;
    if (f && typeof f !== "object") throw new Error("filter: must be an object");
    const clean = {};
    if (f.category != null) { assertString(f.category, "category", 64); clean.category = f.category; }
    if (f.search   != null) { assertString(f.search, "search", 200); clean.q = f.search; }
    return invoke("api:models", clean);
  },
  modelDetail:   (id) => { assertString(id, "id", 128); return invoke("api:model:detail", id); },
  modelGgufFiles:(id) => { assertString(id, "id", 128); return invoke("api:model:gguf-files", id); },
  getModelDetail:(id) => { assertString(id, "id", 128); return invoke("api:model:detail", id); },
  ggufRepos:     () => invoke("api:gguf-repos"),

  // ----- Engines -----
  engines:       () => invoke("api:engines"),
  listEngines:   () => invoke("api:engines"),
  installEngine: (id) => {
    assertString(id, "id", 128);
    return invoke("api:engines:install", id);
  },
  uninstallEngine: (id) => {
    assertString(id, "id", 128);
    return invoke("api:engines:uninstall", id);
  },
  // v2.4.1 — engine update detection / one-click update
  checkEngineUpdates: (opts) => invoke("api:engines:check-updates", opts || {}),
  updateEngine: (id) => {
    assertString(id, "id", 128);
    return invoke("api:engines:update", id);
  },

  // ----- Local models / import -----
  localModels:     () => invoke("api:models:local"),
  listLocalModels: () => invoke("api:models:local"),
  importModel:     (pathOrOpts) => {
    // Accept either legacy string-only path, or {path, mode} opts.
    if (typeof pathOrOpts === "string") {
      assertString(pathOrOpts, "path", 4096);
      return invoke("api:models:import", pathOrOpts);
    }
    assertObject(pathOrOpts, "opts");
    assertString(pathOrOpts.path, "opts.path", 4096);
    if (pathOrOpts.mode != null) {
      assertEnum(pathOrOpts.mode, ["copy", "symlink"], "opts.mode");
      return invoke("api:models:import", pathOrOpts.path);
    }
    return invoke("api:models:import", pathOrOpts.path);
  },
  progress:        () => invoke("api:progress"),

  // ----- v2.2.0: environment / dependency / engine management -----
  envStatus:        () => invoke("kevrai:env-status"),
  envInstallPip: (opts) => {
    assertObject(opts, "opts");
    assertString(opts.name, "opts.name", 128);
    if (opts.version != null) assertString(opts.version, "opts.version", 64);
    if (opts.mirrors != null && !Array.isArray(opts.mirrors)) {
      throw new Error("opts.mirrors must be an array of urls");
    }
    if (Array.isArray(opts.mirrors)) {
      for (const m of opts.mirrors) {
        if (typeof m !== "string" || !/^https:\/\//.test(m)) {
          throw new Error("opts.mirrors entries must be https:// urls");
        }
      }
    }
    return invoke("kevrai:env-install", opts);
  },
  envUpgrade: (opts) => {
    assertObject(opts, "opts");
    assertString(opts.name, "opts.name", 128);
    if (opts.mirrors != null && !Array.isArray(opts.mirrors)) {
      throw new Error("opts.mirrors must be an array of urls");
    }
    return invoke("kevrai:env-upgrade", opts);
  },
  envInstallEngine: (opts) => {
    assertObject(opts, "opts");
    assertString(opts.id, "opts.id", 128);
    return invoke("kevrai:env-install-engine", opts);
  },
  measureSources: (urls) => {
    if (!Array.isArray(urls)) throw new Error("urls must be an array of strings");
    const clean = [];
    for (const u of urls) {
      if (typeof u !== "string" || !/^https?:\/\//.test(u)) {
        throw new Error("each url must be an http(s) string");
      }
      clean.push(u.slice(0, 2048));
      if (clean.length >= 32) break;
    }
    return invoke("kevrai:measure-sources", { urls: clean });
  },

  // ----- v2.3.0: hardware detection / recommendations / MNN runtime -----
  hardware: (opts) => {
    const o = (opts == null || typeof opts === "object") ? (opts || {}) : {};
    return invoke("kevrai:hardware", o);
  },
  recommend: (opts) => {
    const o = (opts == null || typeof opts === "object") ? (opts || {}) : {};
    if (o.category != null) assertString(o.category, "category", 64);
    if (o.limit != null && !Number.isInteger(o.limit)) {
      throw new Error("limit: must be an integer");
    }
    return invoke("kevrai:recommend", o);
  },
  mnnModels: () => invoke("kevrai:mnn-models"),
  mnnModelFiles: (id) => {
    assertString(id, "id", 128);
    return invoke("kevrai:mnn-model-files", id);
  },
  mnnStatus: () => invoke("kevrai:mnn-status"),
  mnnLoad: (opts) => {
    assertObject(opts, "opts");
    assertString(opts.model_dir, "opts.model_dir", 4096);
    if (opts.model_name != null) assertString(opts.model_name, "opts.model_name", 200);
    return invoke("kevrai:mnn-load", opts);
  },
  mnnUnload: () => invoke("kevrai:mnn-unload"),
  mnnChat: (opts) => {
    assertObject(opts, "opts");
    assertString(opts.prompt, "opts.prompt", 32000);
    if (opts.history != null && !Array.isArray(opts.history)) {
      throw new Error("opts.history: must be an array");
    }
    return invoke("kevrai:mnn-chat", opts);
  },
  mnnDownload: (opts) => {
    // Accept { entry_id } / { repo } or bare-string entry_id (legacy).
    if (typeof opts === "string") {
      assertString(opts, "entry_id", 128);
      return invoke("kevrai:mnn-download", { entry_id: opts });
    }
    const payload = {};
    if (opts && opts.entry_id != null) { assertString(opts.entry_id, "entry_id", 128); payload.entry_id = opts.entry_id; }
    if (opts && opts.repo != null) { assertString(opts.repo, "repo", 256); payload.repo = opts.repo; }
    if (!payload.entry_id && !payload.repo) throw new Error("mnnDownload: entry_id or repo required");
    return invoke("kevrai:mnn-download", payload);
  },
  mnnDownloadCancel: () => invoke("kevrai:mnn-download-cancel"),
  mnnDownloadStatus: () => invoke("kevrai:mnn-download-status"),
  mnnLocal: () => invoke("kevrai:mnn-local"),

  // ----- Model converter -----
  convertCapabilities: () => invoke("kevrai:convert-capabilities"),
  convertStart: (opts) => {
    assertObject(opts, "opts");
    assertString(opts.kind, "opts.kind", 64);
    assertString(opts.src, "opts.src", 4096);
    assertString(opts.dst, "opts.dst", 4096);
    if (opts.arch != null) assertString(opts.arch, "opts.arch", 64);
    if (opts.quant_bit != null && !Number.isInteger(opts.quant_bit)) throw new Error("quant_bit: must be an integer");
    if (opts.lm_quant_bit != null && !Number.isInteger(opts.lm_quant_bit)) throw new Error("lm_quant_bit: must be an integer");
    if (opts.quant_block != null && !Number.isInteger(opts.quant_block)) throw new Error("quant_block: must be an integer");
    return invoke("kevrai:convert-start", opts);
  },
  convertTasks: () => invoke("kevrai:convert-tasks"),
  convertTask: (id) => {
    assertString(id, "id", 128);
    return invoke("kevrai:convert-task", id);
  },
  convertCancel: (id) => {
    assertString(id, "id", 128);
    return invoke("kevrai:convert-cancel", id);
  },

  // ----- Drama Agent (AI 短剧生成) -----
  dramaOptions: () => invoke("kevrai:drama-options"),
  dramaBrainstorm: (opts) => {
    assertObject(opts, "opts");
    assertString(opts.topic, "opts.topic", 1000);
    return invoke("kevrai:drama-brainstorm", opts);
  },
  dramaScript: (opts) => {
    assertObject(opts, "opts");
    assertString(opts.topic, "opts.topic", 1000);
    return invoke("kevrai:drama-script", opts);
  },
  dramaStoryboard: (opts) => {
    assertObject(opts, "opts");
    if (typeof opts.script !== "object" || opts.script === null) {
      throw new Error("opts.script: must be an object");
    }
    return invoke("kevrai:drama-storyboard", opts);
  },
  dramaRenderPlan: (opts) => {
    assertObject(opts, "opts");
    if (typeof opts.storyboard !== "object" || opts.storyboard === null) {
      throw new Error("opts.storyboard: must be an object");
    }
    return invoke("kevrai:drama-render-plan", opts);
  },

  // ----- v2.4.0: super search -----
  search: (params) => {
    const p = (params == null || typeof params !== "object") ? {} : params;
    const clean = {};
    if (p.q != null)            { assertString(p.q, "q", 200); clean.q = p.q; }
    if (p.category != null)     { assertString(p.category, "category", 64); clean.category = p.category; }
    if (p.engine != null)       { assertString(p.engine, "engine", 64); clean.engine = p.engine; }
    if (p.license != null)      { assertString(p.license, "license", 128); clean.license = p.license; }
    if (p.size_bucket != null)  { assertString(p.size_bucket, "size_bucket", 32); clean.size_bucket = p.size_bucket; }
    if (p.trending != null)     { clean.trending = p.trending ? 1 : 0; }
    if (p.sort != null)         { assertEnum(p.sort, ["relevance","name_asc","size_desc","size_asc","trending"], "sort"); clean.sort = p.sort; }
    if (p.page != null)         { clean.page = Math.max(1, Math.min(500, parseInt(p.page, 10) || 1)); }
    if (p.page_size != null)    { clean.page_size = Math.max(1, Math.min(200, parseInt(p.page_size, 10) || 50)); }
    return invoke("api:search", clean);
  },
  searchRecent: () => invoke("api:search:recent"),
  searchClearRecent: () => invoke("api:search:recent:clear"),

  // ----- v2.4.0: LTX-2.5 video generation -----
  ltxCapabilities: () => invoke("api:ltx:capabilities"),
  ltxGenerate: (opts) => {
    assertObject(opts, "opts");
    assertString(opts.prompt, "opts.prompt", 2000);
    assertEnum(opts.mode || "t2v", ["t2v", "i2v"], "opts.mode");
    if (opts.mode === "i2v") assertString(opts.image_path, "opts.image_path", 4096);
    const clean = {
      mode: opts.mode || "t2v",
      prompt: opts.prompt,
      negative_prompt: typeof opts.negative_prompt === "string" ? opts.negative_prompt.slice(0, 2000) : "",
      preset: opts.preset || "balanced",
      width: parseInt(opts.width, 10) || 768,
      height: parseInt(opts.height, 10) || 432,
      num_frames: parseInt(opts.num_frames, 10) || 97,
      num_inference_steps: parseInt(opts.num_inference_steps, 10) || 25,
      guidance_scale: parseFloat(opts.guidance_scale) || 3.0,
      seed: parseInt(opts.seed, 10) || -1,
      image_path: opts.image_path || "",
      strength: parseFloat(opts.strength) || 0.85,
      fps: parseInt(opts.fps, 10) || 24,
      output_format: opts.output_format || "mp4",
      enable_vae_slicing: opts.enable_vae_slicing !== false,
      enable_model_cpu_offload: !!opts.enable_model_cpu_offload,
    };
    return invoke("api:ltx:generate", clean);
  },
  ltxTasks: () => invoke("api:ltx:tasks"),
  ltxTask: (id) => { assertString(id, "id", 128); return invoke("api:ltx:task", id); },
  ltxCancel: (id) => { assertString(id, "id", 128); return invoke("api:ltx:cancel", id); },
  ltxOutputs: () => invoke("api:ltx:outputs"),

  // ----- Downloads -----
  startDownload: (opts) => {
    assertObject(opts, "opts");
    assertString(opts.url, "opts.url", 2048);
    assertString(opts.dest_filename, "opts.dest_filename", 256);
    if (opts.sha256 != null) assertString(opts.sha256, "opts.sha256", 64);
    if (opts.gated != null && typeof opts.gated !== "boolean") {
      throw new Error("opts.gated must be boolean");
    }
    return invoke("kevrai:start-download", opts);
  },
  cancelDownload: (taskId) => {
    assertString(taskId, "taskId", 128);
    return invoke("kevrai:cancel-download", { task_id: taskId });
  },
  onDownloadProgress: (cb) => listen("download:progress", cb),

  // ----- Dialogs / paths / errors / shell -----
  pickFolder:   () => invoke("dialog:pickFolder"),
  pickFile:     () => invoke("dialog:pickFile"),
  openPath:     (p) => {
    assertString(p, "path", 4096);
    return invoke("kevrai:open-path", { path: p });
  },
  showErrorDialog: (opts) => {
    assertObject(opts, "opts");
    assertString(opts.title, "opts.title", 200);
    assertString(opts.message, "opts.message", 4000);
    if (opts.detail != null) assertString(opts.detail, "opts.detail", 8000);
    return invoke("kevrai:show-error-dialog", opts);
  },
  openExternal: (url) => {
    // Very tight allowlist at the bridge too; main re-validates.
    assertString(url, "url", 2048);
    if (!/^https?:\/\//i.test(url)) throw new Error("url: only http(s) allowed");
    return invoke("shell:openExternal", url);
  },

  // ----- Logging helpers (small, safe) -----
  // Renderer cannot write to disk directly; use this to push events into main log.
  logEvent: (level, msg) => {
    assertEnum(level, ["info", "warn", "error"], "level");
    assertString(msg, "msg", 1000);
    return invoke("kevrai:log-event", { level, msg });
  },

  // ----- Subscriptions / events -----
  onHealth:        (cb) => listen("sidecar:health", cb),
  onSidecarDown:   (cb) => listen("sidecar:down", cb),
};

// Keep `progress` events flowing too (named in original preload).
api.onProgress = (cb) => listen("api:progress:event", cb);

contextBridge.exposeInMainWorld("kevrai", api);
