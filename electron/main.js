"use strict";
/**
 * Kevrai Omni — Electron main process (hardened).
 *
 * Responsibilities:
 *   - Create the desktop window with strict CSP & locked-down webPreferences.
 *   - Spawn the Python sidecar (FastAPI) as a child process, env-pinned.
 *   - Expose a small, audited IPC surface to the renderer.
 *   - Orchestrate engine / model lifecycle via the sidecar HTTP API.
 *   - Single-instance lock, rotating file logger, graceful shutdown.
 *
 * Security posture (HARD):
 *   - contextIsolation:true, nodeIntegration:false, sandbox:true (locked)
 *   - webview tag disabled, webPreferences locked down
 *   - setWindowOpenHandler always denies new windows
 *   - will-navigate blocked
 *   - All inputs from renderer are re-validated in handlers
 *   - HTML CSP delivered as a response header (defense-in-depth; HTML also
 *     declares a matching meta CSP).
 */

const { app, BrowserWindow, ipcMain, shell, dialog, session, Menu } = require("electron");
const path = require("node:path");
const fs = require("node:fs");
const fsp = require("node:fs/promises");
const { spawn } = require("node:child_process");
const http = require("node:http");
const { URL } = require("node:url");

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const SIDECAR_PORT = 17890;
const SIDECAR_HOST = "127.0.0.1";
const SIDECAR_HEALTH_TIMEOUT_MS = 30_000;
const SIDECAR_HEALTH_INTERVAL_MS = 2_000;
const SHUTDOWN_TIMEOUT_MS = 5_000;
const ALLOW_DEFAULT = ["huggingface.co", "github.com"];
const SIDECAR_RESTART_MAX = 3;

// Packaged: <resources>/python/app/main.py (extraResources).
// Dev (running from repo): <repo>/python/app/main.py — process.resourcesPath
// points into node_modules/electron/dist which has no python dir, and spawn
// would fail with ENOENT on a non-existent cwd.
const SIDECAR_PY = app.isPackaged
  ? path.join(process.resourcesPath, "python", "app", "main.py")
  : path.join(__dirname, "..", "python", "app", "main.py");

// Strict CSP for the renderer. Same policy is set as a meta tag in HTML for
// defense-in-depth, but the real enforcement happens here on every response.
const RENDERER_CSP = [
  "default-src 'self'",
  // Renderer talks ONLY to the sidecar (HTTP + WS upgrade). No third-party.
  "connect-src 'self' http://127.0.0.1:17890 ws://127.0.0.1:17890",
  "img-src 'self' data:",
  "style-src 'self' 'unsafe-inline'",
  "script-src 'self'",
  "object-src 'none'",
  "base-uri 'self'",
  "form-action 'none'",
  "frame-ancestors 'none'",
].join("; ");

// ---------------------------------------------------------------------------
// Logger (rotating). Rotates at 5MB × 3 files, written via stream pipeline.
// ---------------------------------------------------------------------------

let LOG_DIR = null;
let LOG_FILE = null;

function userDataDir() {
  try { return app.getPath("userData"); } catch (_) { return path.join(require("node:os").homedir(), ".local", "share", "KevraiOmni"); }
}

function setupLogger() {
  LOG_DIR = path.join(userDataDir(), "logs");
  fs.mkdirSync(LOG_DIR, { recursive: true });
  LOG_FILE = path.join(LOG_DIR, "main.log");
  // Best-effort rotation on startup
  rotateLogsIfNeeded().catch(() => {});
}

async function rotateLogsIfNeeded() {
  try {
    const st = await fsp.stat(LOG_FILE).catch(() => null);
    if (!st) return;
    const FIVE_MB = 5 * 1024 * 1024;
    if (st.size < FIVE_MB) return;
    // main.log -> main.log.1 -> main.log.2 (drop main.log.2)
    for (let i = 2; i >= 1; i--) {
      const src = path.join(LOG_DIR, `main.log.${i}`);
      const dst = path.join(LOG_DIR, `main.log.${i + 1}`);
      try { await fsp.rename(src, dst); } catch (_) {}
    }
    await fsp.rename(LOG_FILE, path.join(LOG_DIR, "main.log.1"));
  } catch (_) { /* ignore */ }
}

function log(level, ...args) {
  const ts = new Date().toISOString();
  const line = `${ts} [${level}] ${args.map((a) => (typeof a === "string" ? a : JSON.stringify(a))).join(" ")}\n`;
  // stdout
  try { process.stdout.write(line); } catch (_) {}
  // file
  try {
    if (!LOG_FILE) return;
    fs.appendFile(LOG_FILE, line, (err) => {
      if (err) return;
      // opportunistic rotation
      fs.stat(LOG_FILE, (e, st) => {
        if (!e && st && st.size >= 5 * 1024 * 1024) rotateLogsIfNeeded().catch(() => {});
      });
    });
  } catch (_) {}
}

const logInfo  = (...a) => log("INFO",  ...a);
const logWarn  = (...a) => log("WARN",  ...a);
const logError = (...a) => log("ERROR", ...a);

// ---------------------------------------------------------------------------
// Settings (small JSON store under userData)
// ---------------------------------------------------------------------------

let SETTINGS_PATH = null;
const DEFAULT_SETTINGS = {
  theme: "system",            // "light" | "dark" | "system"
  hardwareAccel: "auto",      // "auto" | "nvidia" | "amd" | "cpu"
  telemetry: false,
  allowlistAdvanced: false,
  allowlist: [...ALLOW_DEFAULT],
  modelDir: "",
  engineDir: "",
  hfToken: "",                // v2.4.1 — HuggingFace token for gated repos
};

function loadSettingsSync() {
  try {
    const raw = fs.readFileSync(SETTINGS_PATH, "utf-8");
    const parsed = JSON.parse(raw);
    return { ...DEFAULT_SETTINGS, ...parsed };
  } catch (_) { return { ...DEFAULT_SETTINGS }; }
}

async function loadSettings() { return loadSettingsSync(); }

async function saveSettings(next) {
  // whitelist persisted keys
  const allowed = ["theme", "hardwareAccel", "telemetry",
                   "allowlistAdvanced", "allowlist", "modelDir", "engineDir"];
  const out = {};
  for (const k of allowed) if (k in next) out[k] = next[k];
  await fsp.writeFile(SETTINGS_PATH, JSON.stringify({ ...loadSettingsSync(), ...out }, null, 2),
                      { encoding: "utf-8", mode: 0o600 });
  return loadSettingsSync();
}

// ---------------------------------------------------------------------------
// Sidecar lifecycle
// ---------------------------------------------------------------------------

let sidecarProc = null;
let sidecarManualStop = false;
let sidecarRestartCount = 0;
let sidecarReady = false;

function sidecarEnv() {
  return {
    ...process.env,
    KEVRAI_PORT: String(SIDECAR_PORT),
    PYTHONUNBUFFERED: "1",
    PYTHONIOENCODING: "UTF-8",
    NODE_OPTIONS: "--max-old-space-size=2048", // belt-and-braces; python ignores but pinned per spec
    ELECTRON_RUN_AS_NODE: "",
  };
}

function startSidecar() {
  const py = process.env.KEVRAI_PYTHON || (process.platform === "win32" ? "python" : "python3");
  const cmd = [
    "-X", "utf8", "-u",
    "-m", "uvicorn", "app.main:app",
    "--host", SIDECAR_HOST, "--port", String(SIDECAR_PORT),
    "--log-level", "info",
  ];
  const cwd = path.dirname(path.dirname(SIDECAR_PY));
  logInfo("spawn sidecar:", py, cmd.join(" "), "cwd=", cwd);
  try {
    sidecarProc = spawn(py, cmd, {
      cwd,
      env: sidecarEnv(),
      stdio: ["ignore", "pipe", "pipe"],
      windowsHide: true,
    });
  } catch (e) {
    logError("spawn failed:", e.message);
    return false;
  }
  sidecarProc.stdout.on("data", (d) => logInfo("[sidecar]", d.toString().trimEnd()));
  sidecarProc.stderr.on("data", (d) => logWarn("[sidecar-stderr]", d.toString().trimEnd()));
  sidecarProc.on("error", (e) => logError("sidecar error event:", e.message));
  sidecarProc.on("exit", (code, signal) => {
    sidecarReady = false;
    logWarn("sidecar exited code=", code, "signal=", signal);
    if (sidecarManualStop) return;
    if (sidecarRestartCount >= SIDECAR_RESTART_MAX) {
      logError("sidecar restart budget exceeded; giving up.");
      notifyRenderer("sidecar:down", { reason: "restart-budget-exhausted", code });
      return;
    }
    const delay = Math.min(30_000, 1000 * Math.pow(2, sidecarRestartCount));
    sidecarRestartCount += 1;
    logInfo(`sidecar auto-restart in ${delay}ms (attempt ${sidecarRestartCount}/${SIDECAR_RESTART_MAX})`);
    setTimeout(() => {
      try { startSidecar(); } catch (_) {}
    }, delay);
  });
  return true;
}

function sidecarFetch(p, opts = {}) {
  const url = `http://${SIDECAR_HOST}:${SIDECAR_PORT}${p}`;
  return new Promise((resolve, reject) => {
    let u;
    try { u = new URL(url); }
    catch (e) { return reject(new Error(`bad sidecar url: ${e.message}`)); }
    const req = http.request({
      host: u.hostname, port: u.port, path: u.pathname + u.search,
      method: opts.method || "GET",
      headers: { "Content-Type": "application/json", ...(opts.headers || {}) },
      timeout: 30_000,
    }, (res) => {
      let buf = "";
      res.on("data", (c) => (buf += c));
      res.on("end", () => {
        if (res.statusCode >= 200 && res.statusCode < 300) {
          try { resolve({ status: res.statusCode, body: buf ? JSON.parse(buf) : null }); }
          catch (_) { resolve({ status: res.statusCode, body: buf }); }
        } else {
          reject(new Error(`sidecar ${res.statusCode}: ${buf}`));
        }
      });
    });
    req.on("timeout", () => req.destroy(new Error("sidecar timeout")));
    req.on("error", reject);
    if (opts.body) req.write(JSON.stringify(opts.body));
    req.end();
  });
}

async function waitForSidecar(timeoutMs = SIDECAR_HEALTH_TIMEOUT_MS) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    try {
      const r = await sidecarFetch("/api/health");
      if (r.status === 200 && r.body) {
        sidecarReady = true;
        return r.body;
      }
    } catch (_) { /* retry */ }
    await new Promise((r) => setTimeout(r, SIDECAR_HEALTH_INTERVAL_MS));
  }
  throw new Error("sidecar health timeout");
}

async function stopSidecar(graceMs = SHUTDOWN_TIMEOUT_MS) {
  sidecarManualStop = true;
  if (!sidecarProc || sidecarProc.killed) return;
  try {
    sidecarProc.kill("SIGTERM");
  } catch (_) {}
  const start = Date.now();
  while (Date.now() - start < graceMs) {
    if (!sidecarProc || sidecarProc.killed || sidecarProc.exitCode !== null) return;
    await new Promise((r) => setTimeout(r, 100));
  }
  try { sidecarProc.kill("SIGKILL"); } catch (_) {}
}

// ---------------------------------------------------------------------------
// Window
// ---------------------------------------------------------------------------

let mainWindow = null;

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1380,
    height: 900,
    minWidth: 1024,
    minHeight: 700,
    title: "Kevrai Omni",
    // PNG works from inside the asar archive on every platform (.ico does not).
    icon: path.join(__dirname, "..", "assets", "icons", "icon-256.png"),
    backgroundColor: "#0b1020",
    autoHideMenuBar: true,
    frame: true,
    titleBarStyle: "hiddenInset",
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      webviewTag: false,
      // Disable things we don't use:
      webgl: false,
      plugins: false,
      experimentalFeatures: false,
      allowRunningInsecureContent: false,
      // Content-type & navigation enforcement
      enableBlinkFeatures: "",
    },
  });

  // Strip default menu (about/quit etc.) for a cleaner attack surface.
  try { Menu.setApplicationMenu(null); } catch (_) {}

  mainWindow.loadFile(path.join(__dirname, "..", "renderer", "index.html"));

  // Hard-deny any attempt to open a new window.
  mainWindow.webContents.setWindowOpenHandler(() => ({ action: "deny" }));

  // Refuse navigations away from our local file. Anything else is denied.
  mainWindow.webContents.on("will-navigate", (e) => e.preventDefault());
  mainWindow.webContents.on("will-redirect", (e) => e.preventDefault());

  // Inject CSP via response header on all renderer responses.
  const ses = mainWindow.webContents.session;
  ses.webRequest.onHeadersReceived((details, cb) => {
    const csp = RENDERER_CSP;
    cb({
      responseHeaders: {
        ...details.responseHeaders,
        "Content-Security-Policy": [csp],
        "X-Content-Type-Options": ["nosniff"],
        "Referrer-Policy": ["no-referrer"],
      },
    });
  });

  mainWindow.on("closed", () => { mainWindow = null; });
}

// ---------------------------------------------------------------------------
// Helpers for IPC validation
// ---------------------------------------------------------------------------

function err(msg, code = "EINVAL") { const e = new Error(msg); e.code = code; return e; }

function isString(v, max = 4096) { return typeof v === "string" && v.length > 0 && v.length <= max; }
function isOneOf(v, list)       { return typeof v === "string" && list.includes(v); }
function isInt(v)               { return Number.isInteger(v); }

function assert(cond, msg) { if (!cond) throw err(msg); }
function assertArray(v, name) {
  if (!Array.isArray(v)) throw err(`${name || "value"}: must be an array`);
}
function assertString(v, name, maxLen) {
  if (typeof v !== "string") throw err(`${name || "value"}: must be a string`);
  if (maxLen && v.length > maxLen) throw err(`${name}: exceeds ${maxLen} chars`);
}

function safeHostname(url) { try { return new URL(url).hostname.toLowerCase(); }
  catch (_) { return ""; } }

function isHostAllowed(host, settings) {
  if (!host) return false;
  const list = (settings && Array.isArray(settings.allowlist) && settings.allowlist.length)
    ? settings.allowlist
    : ALLOW_DEFAULT;
  return list.some((suffix) => host === suffix || host.endsWith("." + suffix));
}

function safePathWithin(base, candidate) {
  if (!isString(candidate, 4096)) throw err("path: invalid");
  const norm = path.normalize(candidate);
  // Block obvious traversal patterns even after normalization.
  if (norm.includes("..")) throw err("path: traversal not allowed");
  const baseAbs = path.resolve(base);
  const abs = path.resolve(norm);
  if (!abs.startsWith(baseAbs + path.sep) && abs !== baseAbs) {
    throw err("path: outside allowed root");
  }
  return abs;
}

// ---------------------------------------------------------------------------
// IPC handlers — all validate inputs.
// ---------------------------------------------------------------------------

function registerIpc() {
  // Original / first-party surface (kept stable so renderer/app.js style wiring still works).
  ipcMain.handle("api:health",       async () => sidecarFetch("/api/health"));
  ipcMain.handle("api:categories",   async () => sidecarFetch("/api/categories"));
  ipcMain.handle("api:models", async (_e, params) => {
    const p = (params && typeof params === "object") ? params : {};
    const qs = new URLSearchParams();
    if (isString(p.category, 64)) qs.set("category", p.category);
    if (isString(p.q, 200)) qs.set("q", p.q);
    return sidecarFetch(`/api/models?${qs.toString()}`);
  });
  ipcMain.handle("api:model:detail", async (_e, id) => {
    assert(isString(id, 128), "id: invalid");
    return sidecarFetch(`/api/models/${encodeURIComponent(id)}`);
  });
  ipcMain.handle("api:model:gguf-files", async (_e, id) => {
    assert(isString(id, 128), "id: invalid");
    return sidecarFetch(`/api/models/${encodeURIComponent(id)}/gguf-files`);
  });
  ipcMain.handle("api:gguf-repos",    async () => sidecarFetch("/api/gguf-repos"));
  ipcMain.handle("api:engines",       async () => sidecarFetch("/api/engines"));
  ipcMain.handle("api:engines:install", async (_e, engine_id) => {
    assert(isString(engine_id, 128), "engine_id: invalid");
    return sidecarFetch("/api/engines/install", { method: "POST", body: { engine_id } });
  });
  // v2.4.1 — engine update detection / one-click update
  ipcMain.handle("api:engines:check-updates", async (_e, opts) => {
    const force = !!(opts && opts.force);
    return sidecarFetch("/api/engines/check-updates", { method: "POST", body: { force } });
  });
  ipcMain.handle("api:engines:update", async (_e, engine_id) => {
    assert(isString(engine_id, 128), "engine_id: invalid");
    return sidecarFetch("/api/engines/update", { method: "POST", body: { engine_id } });
  });
  ipcMain.handle("api:models:import", async (_e, p) => {
    assert(isString(p, 4096), "path: invalid");
    return sidecarFetch("/api/models/import", { method: "POST", body: { path: p } });
  });
  ipcMain.handle("api:models:local",  async () => sidecarFetch("/api/models/local"));
  ipcMain.handle("api:engines:uninstall", async (_e, engine_id) => {
    assert(isString(engine_id, 128), "engine_id: invalid");
    return sidecarFetch("/api/engines/uninstall", { method: "POST", body: { engine_id } });
  });
  ipcMain.handle("api:progress",      async () => sidecarFetch("/api/progress"));

  // Dialogs
  ipcMain.handle("dialog:pickFolder", async () => {
    assert(!!mainWindow, "no window");
    const r = await dialog.showOpenDialog(mainWindow, { properties: ["openDirectory"] });
    if (r.canceled || !r.filePaths.length) return null;
    return r.filePaths[0];
  });
  ipcMain.handle("dialog:pickFile", async () => {
    assert(!!mainWindow, "no window");
    const r = await dialog.showOpenDialog(mainWindow, { properties: ["openFile"], filters: [
      { name: "Model files", extensions: ["gguf", "safetensors", "bin", "pt", "onnx", "ggml"] },
      { name: "All files", extensions: ["*"] },
    ] });
    if (r.canceled || !r.filePaths.length) return null;
    return r.filePaths[0];
  });
  ipcMain.handle("shell:openExternal", async (_e, url) => {
    assert(isString(url, 2048), "url: invalid");
    let u;
    try { u = new URL(url); } catch (_) { throw err("url: not a valid URL"); }
    assert(u.protocol === "https:" || u.protocol === "http:", "url: only http(s) allowed");
    await shell.openExternal(u.toString());
  });

  // --- New handlers -------------------------------------------------------

  ipcMain.handle("kevrai:detect-gpu", async () => sidecarFetch("/api/gpu"));

  // v2.2.0 — environment / dependency / engine management IPC.
  ipcMain.handle("kevrai:env-status", async () => sidecarFetch("/api/env/status"));
  ipcMain.handle("kevrai:env-install", async (_e, opts) => {
    assert(opts && typeof opts === "object", "opts: invalid");
    assertString(opts.name, "opts.name", 128);
    return sidecarFetch("/api/env/install", { method: "POST", body: opts });
  });
  ipcMain.handle("kevrai:env-upgrade", async (_e, opts) => {
    assert(opts && typeof opts === "object", "opts: invalid");
    assertString(opts.name, "opts.name", 128);
    return sidecarFetch("/api/env/upgrade", { method: "POST", body: opts });
  });
  ipcMain.handle("kevrai:env-install-engine", async (_e, opts) => {
    assert(opts && typeof opts === "object", "opts: invalid");
    assertString(opts.id, "opts.id", 128);
    return sidecarFetch("/api/env/install-engine", { method: "POST", body: opts });
  });
  ipcMain.handle("kevrai:measure-sources", async (_e, body) => {
    assert(body && typeof body === "object", "body: invalid");
    assertArray(body.urls, "urls");
    return sidecarFetch("/api/sources/measure", { method: "POST", body });
  });

  // --- v2.3.0: hardware / recommendation / MNN runtime ----------------------
  ipcMain.handle("kevrai:hardware", async (_e, opts) => {
    const o = (opts && typeof opts === "object") ? opts : {};
    const qs = o.refresh ? "?refresh=1" : "";
    return sidecarFetch(`/api/hardware${qs}`);
  });
  ipcMain.handle("kevrai:recommend", async (_e, opts) => {
    const o = (opts && typeof opts === "object") ? opts : {};
    const qs = new URLSearchParams();
    if (isInt(o.limit) && o.limit > 0 && o.limit <= 50) qs.set("limit", String(o.limit));
    if (isString(o.category, 64)) qs.set("category", o.category);
    if (o.refresh) qs.set("refresh", "1");
    return sidecarFetch(`/api/recommend?${qs.toString()}`);
  });
  ipcMain.handle("kevrai:mnn-models", async () => sidecarFetch("/api/mnn/models"));
  ipcMain.handle("kevrai:mnn-model-files", async (_e, id) => {
    assert(isString(id, 128), "id: invalid");
    return sidecarFetch(`/api/mnn/models/${encodeURIComponent(id)}/files`);
  });
  ipcMain.handle("kevrai:mnn-status", async () => sidecarFetch("/api/mnn/status"));
  ipcMain.handle("kevrai:mnn-load", async (_e, opts) => {
    assert(opts && typeof opts === "object", "opts: invalid");
    assertString(opts.model_dir, "opts.model_dir", 4096);
    if (opts.model_name != null) assertString(opts.model_name, "opts.model_name", 200);
    return sidecarFetch("/api/mnn/load", { method: "POST", body: {
      model_dir: opts.model_dir,
      model_name: opts.model_name || "",
    } });
  });
  ipcMain.handle("kevrai:mnn-unload", async () =>
    sidecarFetch("/api/mnn/unload", { method: "POST" }));
  ipcMain.handle("kevrai:mnn-chat", async (_e, opts) => {
    assert(opts && typeof opts === "object", "opts: invalid");
    assertString(opts.prompt, "opts.prompt", 32000);
    if (!Array.isArray(opts.history || [])) throw err("opts.history: must be an array");
    const hist = (opts.history || []).slice(0, 40).map((h) => ({
      role: String((h && h.role) || "user").slice(0, 32),
      content: String((h && h.content) || "").slice(0, 8000),
    }));
    return sidecarFetch("/api/mnn/chat", { method: "POST", body: {
      prompt: opts.prompt,
      history: hist,
      max_new_tokens: Number.isInteger(opts.max_new_tokens) ? opts.max_new_tokens : 512,
    } });
  });
  ipcMain.handle("kevrai:mnn-download", async (_e, opts) => {
    // Accept object form ({ entry_id } / { repo }) or bare-string entry_id.
    // Previously the whole opts object was passed to assertString, so
    // object-form calls always threw EINVAL (BUG-06); now repo直下 also works.
    const body = {};
    if (opts && typeof opts === "object") {
      if (opts.entry_id != null) body.entry_id = opts.entry_id;
      if (opts.repo != null) body.repo = opts.repo;
    } else {
      body.entry_id = opts;
    }
    if (body.entry_id != null) assertString(body.entry_id, "entry_id", 128);
    if (body.repo != null) assertString(body.repo, "repo", 256);
    if (!body.entry_id && !body.repo) throw new Error("mnnDownload: entry_id or repo required");
    return sidecarFetch("/api/mnn/download", { method: "POST", body });
  });
  ipcMain.handle("kevrai:mnn-download-cancel", async () =>
    sidecarFetch("/api/mnn/download/cancel", { method: "POST" }));
  ipcMain.handle("kevrai:mnn-download-status", async () => sidecarFetch("/api/mnn/download"));
  ipcMain.handle("kevrai:mnn-local", async () => sidecarFetch("/api/mnn/local"));

  // Model converter — python/app/converter.py
  ipcMain.handle("kevrai:convert-capabilities", async () => sidecarFetch("/api/convert/capabilities"));
  ipcMain.handle("kevrai:convert-start", async (_e, opts) => {
    assert(opts && typeof opts === "object", "opts: invalid");
    assertString(opts.kind, "opts.kind", 64);
    assertString(opts.src, "opts.src", 4096);
    assertString(opts.dst, "opts.dst", 4096);
    const body = { kind: opts.kind, src: opts.src, dst: opts.dst };
    if (typeof opts.arch === "string" && opts.arch.length) body.arch = String(opts.arch).slice(0, 64);
    if (Number.isInteger(opts.quant_bit)) body.quant_bit = opts.quant_bit;
    if (Number.isInteger(opts.lm_quant_bit)) body.lm_quant_bit = opts.lm_quant_bit;
    if (Number.isInteger(opts.quant_block)) body.quant_block = opts.quant_block;
    if (Number.isInteger(opts.visual_quant_bit)) body.visual_quant_bit = opts.visual_quant_bit;
    if (typeof opts.outtype === "string" && opts.outtype.length) body.outtype = String(opts.outtype).slice(0, 16);
    if (typeof opts.task === "string" && opts.task.length) body.task = String(opts.task).slice(0, 128);
    if (typeof opts.quantize === "boolean") body.quantize = opts.quantize;
    if (Number.isInteger(opts.weight_quant_bits)) body.weight_quant_bits = opts.weight_quant_bits;
    if (Number.isInteger(opts.weight_quant_block)) body.weight_quant_block = opts.weight_quant_block;
    if (typeof opts.biz_code === "string" && opts.biz_code.length) body.biz_code = String(opts.biz_code).slice(0, 128);
    return sidecarFetch("/api/convert/start", { method: "POST", body });
  });
  ipcMain.handle("kevrai:convert-tasks", async () => sidecarFetch("/api/convert/tasks"));
  ipcMain.handle("kevrai:convert-task", async (_e, id) => {
    assertString(id, "id", 128);
    return sidecarFetch(`/api/convert/${encodeURIComponent(id)}`);
  });
  ipcMain.handle("kevrai:convert-cancel", async (_e, id) => {
    assertString(id, "id", 128);
    return sidecarFetch(`/api/convert/${encodeURIComponent(id)}/cancel`, { method: "POST" });
  });

  // Drama Agent (AI 短剧生成) — python/app/drama.py
  ipcMain.handle("kevrai:drama-options", async () => sidecarFetch("/api/drama/options"));
  ipcMain.handle("kevrai:drama-brainstorm", async (_e, opts) => {
    assert(opts && typeof opts === "object", "opts: invalid");
    assertString(opts.topic, "opts.topic", 1000);
    return sidecarFetch("/api/drama/brainstorm", { method: "POST", body: {
      topic: opts.topic,
    } });
  });
  ipcMain.handle("kevrai:drama-script", async (_e, opts) => {
    assert(opts && typeof opts === "object", "opts: invalid");
    assertString(opts.topic, "opts.topic", 1000);
    const body = { topic: opts.topic };
    if (typeof opts.angle === "string" && opts.angle.length > 0) {
      body.angle = String(opts.angle).slice(0, 500);
    }
    if (opts.answers != null) body.answers = opts.answers;
    return sidecarFetch("/api/drama/script", { method: "POST", body });
  });
  ipcMain.handle("kevrai:drama-storyboard", async (_e, opts) => {
    assert(opts && typeof opts === "object", "opts: invalid");
    assert(opts.script && typeof opts.script === "object", "opts.script: invalid");
    return sidecarFetch("/api/drama/storyboard", { method: "POST", body: {
      script: opts.script,
    } });
  });
  ipcMain.handle("kevrai:drama-render-plan", async (_e, opts) => {
    assert(opts && typeof opts === "object", "opts: invalid");
    assert(opts.storyboard && typeof opts.storyboard === "object", "opts.storyboard: invalid");
    const choices = (opts.model_choices && typeof opts.model_choices === "object")
      ? opts.model_choices : {};
    return sidecarFetch("/api/drama/render-plan", { method: "POST", body: {
      storyboard: opts.storyboard,
      model_choices: choices,
    } });
  });

  // v2.4.0 — super search
  ipcMain.handle("api:search", async (_e, params) => {
    const p = (params && typeof params === "object") ? params : {};
    const qs = new URLSearchParams();
    if (isString(p.q, 200)) qs.set("q", p.q);
    if (isString(p.category, 64)) qs.set("category", p.category);
    if (isString(p.engine, 64)) qs.set("engine", p.engine);
    if (isString(p.license, 128)) qs.set("license", p.license);
    if (isString(p.size_bucket, 32)) qs.set("size_bucket", p.size_bucket);
    if (p.trending) qs.set("trending", "1");
    if (isString(p.sort, 32)) qs.set("sort", p.sort);
    if (p.page) qs.set("page", String(p.page));
    if (p.page_size) qs.set("page_size", String(p.page_size));
    return sidecarFetch(`/api/search?${qs.toString()}`);
  });
  ipcMain.handle("api:search:recent", async () => sidecarFetch("/api/search/recent"));
  ipcMain.handle("api:search:recent:clear", async () =>
    sidecarFetch("/api/search/recent", { method: "DELETE" }));

  // v2.4.0 — LTX-2.5 video generation
  ipcMain.handle("api:ltx:capabilities", async () => sidecarFetch("/api/ltx/capabilities"));
  ipcMain.handle("api:ltx:generate", async (_e, opts) => {
    assert(opts && typeof opts === "object", "opts: invalid");
    assert(isString(opts.prompt, 2000), "prompt: invalid");
    const body = {
      mode: opts.mode || "t2v",
      prompt: opts.prompt,
      negative_prompt: String(opts.negative_prompt || "").slice(0, 2000),
      model_id: "Lightricks/LTX-2.5",
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
    return sidecarFetch("/api/ltx/generate", { method: "POST", body });
  });
  ipcMain.handle("api:ltx:tasks", async () => sidecarFetch("/api/ltx/tasks"));
  ipcMain.handle("api:ltx:task", async (_e, id) => {
    assert(isString(id, 128), "id: invalid");
    return sidecarFetch(`/api/ltx/tasks/${encodeURIComponent(id)}`);
  });
  ipcMain.handle("api:ltx:cancel", async (_e, id) => {
    assert(isString(id, 128), "id: invalid");
    return sidecarFetch(`/api/ltx/tasks/${encodeURIComponent(id)}/cancel`, { method: "POST" });
  });
  ipcMain.handle("api:ltx:outputs", async () => sidecarFetch("/api/ltx/outputs"));

  // Renderer cannot write to disk directly; forward small log events.
  ipcMain.handle("kevrai:log-event", async (_e, payload) => {
    const p = (payload && typeof payload === "object") ? payload : {};
    const lvl = String(p.level || "info");
    if (!["info", "warn", "error"].includes(lvl)) return false;
    const msg = String(p.msg || "").slice(0, 1000);
    log(lvl, `[renderer] ${msg}`);
    return true;
  });

  ipcMain.handle("kevrai:get-settings", async () => loadSettingsSync());

  ipcMain.handle("kevrai:put-settings", async (_e, s) => {
    assert(s && typeof s === "object", "settings: invalid");
    return saveSettings(s);
  });

  ipcMain.handle("kevrai:start-download", async (_e, opts) => {
    const o = (opts && typeof opts === "object") ? opts : {};
    assert(isString(o.url, 2048), "download.url: invalid");
    const settings = loadSettingsSync();
    const u = new URL(o.url);
    assert(u.protocol === "https:", "download.url: only https allowed");
    const host = u.hostname.toLowerCase();
    assert(isHostAllowed(host, settings), `download.url: host "${host}" not in allowlist`);
    assert(isString(o.dest_filename, 256), "download.dest_filename: invalid");
    const body = { url: o.url, dest_filename: o.dest_filename };
    if (o.sha256) {
      assert(isString(o.sha256, 64), "download.sha256: invalid");
      assert(/^[0-9a-fA-F]{64}$/.test(o.sha256), "download.sha256: must be hex");
      body.sha256 = o.sha256;
    }
    // v2.4.1 — gated repos (e.g. LTX-2.5) require the sidecar to attach the
    // user's HF bearer token; the flag is forwarded as-is.
    body.gated = !!o.gated;
    let r;
    try {
      r = await sidecarFetch("/api/download/start", { method: "POST", body });
    } catch (e) {
      // sidecarFetch rejects on non-2xx with `sidecar <code>: <json-body>`.
      // Surface the structured error so the renderer can show a friendly
      // message instead of a raw "sidecar 422: ..." string.
      const m = /sidecar (\d+):\s*([\s\S]*)$/.exec(String(e && e.message || ""));
      if (m) {
        const status = Number(m[1]);
        let detail = null;
        try { detail = JSON.parse(m[2]).detail; } catch (_) { /* not JSON */ }
        if (detail && typeof detail === "object") {
          throw new Error(JSON.stringify({
            code: detail.error || "download_failed",
            status,
            message: detail.message || e.message,
            ranking: detail.ranking || [],
          }));
        }
      }
      throw e;
    }
    // Kick off a best-effort poller that forwards progress to the renderer.
    const taskId = (r.body && (r.body.task_id || r.body.taskId || r.body.id)) || null;
    if (taskId) pollDownloadProgress(taskId);
    return r.body || { taskId: null };
  });

  ipcMain.handle("kevrai:cancel-download", async (_e, opts) => {
    const o = (opts && typeof opts === "object") ? opts : {};
    assert(isString(o.task_id, 128), "task_id: invalid");
    return sidecarFetch(`/api/download/${encodeURIComponent(o.task_id)}/cancel`, { method: "POST" });
  });

  ipcMain.handle("kevrai:open-path", async (_e, opts) => {
    const o = (opts && typeof opts === "object") ? opts : {};
    const root = path.join(userDataDir(), "models");
    const abs = safePathWithin(root, isString(o.path, 4096) ? o.path : "");
    await fsp.access(abs, fs.constants.F_OK).catch(() => { throw err("path: not found"); });
    shell.showItemInFolder(abs);
  });

  ipcMain.handle("kevrai:show-error-dialog", async (_e, opts) => {
    const o = (opts && typeof opts === "object") ? opts : {};
    assert(isString(o.title, 200), "title: invalid");
    assert(isString(o.message, 4000), "message: invalid");
    await dialog.showMessageBox(mainWindow, {
      type: "error",
      title: o.title,
      message: o.message,
      detail: isString(o.detail, 8000) ? o.detail : undefined,
      buttons: ["OK"],
      defaultId: 0,
      noLink: true,
    });
  });

  ipcMain.handle("kevrai:check-updates", async () => {
    // Stub for future auto-update integration; safe & deterministic for now.
    return { updateAvailable: false, currentVersion: app.getVersion() };
  });
}

// ---------------------------------------------------------------------------
// Renderer signalling helpers (main -> renderer)
// ---------------------------------------------------------------------------

function notifyRenderer(channel, payload) {
  if (mainWindow && !mainWindow.isDestroyed()) {
    try { mainWindow.webContents.send(channel, payload); }
    catch (_) {}
  }
}

// Best-effort progress poller for a single download task. Forwards both the new
// `download:progress` channel and the legacy `api:progress:event` channel so
// pre-existing subscribers still receive events. Stops when status is terminal
// (done/failed/cancelled) or after 5 minutes, whichever comes first.
function pollDownloadProgress(taskId) {
  const start = Date.now();
  const interval = 1000;
  const tick = async () => {
    if (!mainWindow || mainWindow.isDestroyed()) return;
    if (Date.now() - start > 5 * 60 * 1000) return;
    let r;
    try {
      r = await sidecarFetch(`/api/download/${encodeURIComponent(taskId)}`);
    } catch (_) {
      setTimeout(tick, interval);
      return;
    }
    const body = (r && r.body) || {};
    const payload = { taskId, ...body };
    notifyRenderer("download:progress", payload);
    notifyRenderer("api:progress:event", payload);
    const status = String(body.status || "").toLowerCase();
    if (["done", "failed", "cancelled", "error"].includes(status)) return;
    setTimeout(tick, interval);
  };
  setTimeout(tick, 500);
}

// ---------------------------------------------------------------------------
// App lifecycle
// ---------------------------------------------------------------------------

async function bootstrap() {
  setupLogger();
  SETTINGS_PATH = path.join(userDataDir(), "settings.json");

  // Single-instance lock: secondary launches focus existing window.
  const gotLock = app.requestSingleInstanceLock();
  if (!gotLock) {
    logWarn("another instance is running; quitting.");
    app.quit();
    return;
  }
  app.on("second-instance", () => {
    if (mainWindow) {
      if (mainWindow.isMinimized()) mainWindow.restore();
      mainWindow.focus();
    }
  });

  registerIpc();

  // webSecurity default is true; explicit here for clarity.
  try { session.defaultSession.webRequest.onBeforeRequest((_d, cb) => cb({ cancel: false })); } catch (_) {}

  if (!startSidecar()) {
    app.quit();
    return;
  }
  try {
    const info = await waitForSidecar();
    logInfo("sidecar healthy");
    notifyRenderer("sidecar:health", { ok: true, info });
  } catch (e) {
    logError("sidecar NOT ready:", e.message);
    dialog.showErrorBox(
      "Kevrai Omni — Python sidecar failed to start",
      `The Python inference sidecar could not be reached on http://${SIDECAR_HOST}:${SIDECAR_PORT}.\n\n` +
        `Reason: ${e.message}\n\n` +
        `Fix: install Python 3.10+ and the deps in python/pyproject.toml ` +
        `(pip install -r requirements), then relaunch.`
    );
    app.quit();
    return;
  }

  createWindow();

  app.on("activate", () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });
}

app.whenReady().then(bootstrap).catch((e) => {
  logError("bootstrap failed:", e.message);
  app.quit();
});

app.on("window-all-closed", () => {
  if (process.platform !== "darwin") app.quit();
});

app.on("before-quit", async (e) => {
  // Stop the sidecar gracefully; if shutdown takes too long, force-kill.
  if (sidecarProc && !sidecarProc.killed && sidecarProc.exitCode === null) {
    e.preventDefault?.();
    try { await stopSidecar(SHUTDOWN_TIMEOUT_MS); } catch (_) {}
    app.exit(0);
  }
});

process.on("uncaughtException", (e) => logError("uncaughtException:", e.stack || e.message));
process.on("unhandledRejection", (e) => logError("unhandledRejection:", (e && e.stack) || String(e)));
