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

const { app, BrowserWindow, ipcMain, shell, dialog, session, Menu, nativeTheme } = require("electron");
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
const ALLOW_DEFAULT = ["huggingface.co", "github.com", "modelscope.cn"];
const SIDECAR_RESTART_MAX = 3;

// Single source for the Electron-side download User-Agent. Previously this was
// hardcoded as "kevrai-omni/2.5.0" in the bootstrap downloader (a stale literal
// that never tracked the real version). app.getVersion() reads package.json and
// is safe to call once the app is ready (these downloads only run after).
function kevraiUserAgent() {
  try { return `kevrai-omni/${app.getVersion()}`; }
  catch (_) { return "kevrai-omni"; }
}

// Packaged: <resources>/python/app/main.py (extraResources).
// Dev (running from repo): <repo>/python/app/main.py — process.resourcesPath
// points into node_modules/electron/dist which has no python dir, and spawn
// would fail with ENOENT on a non-existent cwd.
const SIDECAR_PY = app.isPackaged
  ? path.join(process.resourcesPath, "python", "app", "main.py")
  : path.join(__dirname, "..", "python", "app", "main.py");

//: 魔搭 (ModelScope) serves its official logo from this CDN. The model market
//: renders it as the source badge, so the host must be allowed in `img-src` —
//: a bare `'self' data:` silently blocked it (verified in a real browser).
const MS_LOGO_ORIGIN = "https://img.alicdn.com";

// Strict CSP for the renderer. Same policy is set as a meta tag in HTML for
// defense-in-depth, but the real enforcement happens here on every response.
const RENDERER_CSP = [
  "default-src 'self'",
  // Renderer talks ONLY to the sidecar (HTTP + WS upgrade). No third-party.
  "connect-src 'self' http://127.0.0.1:17890 ws://127.0.0.1:17890",
  // `img-src` additionally allows the one external image the UI needs (the
  // 魔搭 badge). Deliberately host-pinned rather than a wildcard.
  `img-src 'self' data: ${MS_LOGO_ORIGIN}`,
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
  msToken: "",                // v2.8.0 — ModelScope (魔搭) token, optional
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
  // whitelist persisted keys. v2.8.0: `hfToken`/`msToken` MUST be here —
  // they were declared in DEFAULT_SETTINGS but never persisted (bug), so a
  // typed-in token silently vanished on restart.
  const allowed = ["theme", "hardwareAccel", "telemetry",
                   "allowlistAdvanced", "allowlist", "modelDir", "engineDir",
                   "hfToken", "msToken"];
  const out = {};
  for (const k of allowed) if (k in next) out[k] = next[k];
  await fsp.writeFile(SETTINGS_PATH, JSON.stringify({ ...loadSettingsSync(), ...out }, null, 2),
                      { encoding: "utf-8", mode: 0o600 });
  const merged = loadSettingsSync();
  // Tokens are consumed by the *sidecar* (it makes the remote requests), not by
  // Electron. Persist locally AND push to Python so gated/private repos work.
  // Best-effort: a sidecar that is still booting must not fail the save.
  if ("hfToken" in out || "msToken" in out) {
    try {
      await sidecarFetch("/api/settings", {
        method: "PUT",
        body: { hf_token: merged.hfToken || "", ms_token: merged.msToken || "" },
      });
    } catch (e) {
      logWarn("token sync to sidecar failed", String(e && e.message || e));
    }
  }
  return merged;
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

// ---------------------------------------------------------------------------
// v2.5.0 — Python runtime bootstrap: find / install Python from inside the app
// ---------------------------------------------------------------------------
// 设计：安装包保持小巧，Python 运行环境与引擎一样「随选下载」。
// 解析顺序：KEVRAI_PYTHON 环境变量 > 软件托管的 python-runtime > 系统 Python。
// 全部缺失时进入引导页，一键下载 Python embeddable（仅 Windows 自动化）。

const MANAGED_PY_DIR = () => path.join(userDataDir(), "python-runtime");
const BOOTSTRAP_PY_VERSION = "3.12.7";
const PY_EMBED_ZIP_NAME = `python-${BOOTSTRAP_PY_VERSION}-embed-amd64.zip`;
const PY_EMBED_MIRRORS = [
  `https://registry.npmmirror.com/-/binary/python/${BOOTSTRAP_PY_VERSION}/${PY_EMBED_ZIP_NAME}`,
  `https://mirrors.huaweicloud.com/python/${BOOTSTRAP_PY_VERSION}/${PY_EMBED_ZIP_NAME}`,
  `https://www.python.org/ftp/python/${BOOTSTRAP_PY_VERSION}/${PY_EMBED_ZIP_NAME}`,
];
const GET_PIP_URL = "https://bootstrap.pypa.io/get-pip.py";
const BOOTSTRAP_PIP_INDEX = "https://mirrors.tencent.com/pypi/simple/";

let bootstrapBusy = false;
let sidecarStderrTail = [];

function findManagedPython() {
  const exe = process.platform === "win32"
    ? path.join(MANAGED_PY_DIR(), "python.exe")
    : path.join(MANAGED_PY_DIR(), "bin", "python3");
  try { return fs.existsSync(exe) ? exe : null; } catch (_) { return null; }
}

function findSystemPython() {
  const { spawnSync } = require("node:child_process");
  const cands = process.platform === "win32"
    ? ["python", "py", "python3"]
    : ["python3", "python"];
  for (const c of cands) {
    try {
      const r = spawnSync(c, ["--version"], { timeout: 8000, windowsHide: true });
      if (r.status === 0) return c;
    } catch (_) { /* try next */ }
  }
  return null;
}

function resolvePython() {
  const envPy = process.env.KEVRAI_PYTHON;
  if (envPy) { try { if (fs.existsSync(envPy)) return envPy; } catch (_) {} }
  return findManagedPython() || findSystemPython();
}

function requirementsPath() {
  return path.join(path.dirname(path.dirname(SIDECAR_PY)), "requirements.txt");
}

function bootstrapProgress(stage, pct, text) {
  logInfo(`[bootstrap] ${stage} ${pct}% ${text || ""}`);
  notifyRenderer("bootstrap:progress", { stage, pct, text: String(text || "").slice(0, 300) });
}

// https download with redirect follow + mirror fallback.
function downloadFile(urls, dest, stage) {
  const https = require("node:https");
  const tryOne = (url, redirectsLeft) => new Promise((resolve, reject) => {
    const req = https.get(url, { headers: { "User-Agent": kevraiUserAgent() }, timeout: 60000 }, (res) => {
      if ([301, 302, 303, 307, 308].includes(res.statusCode) && res.headers.location && redirectsLeft > 0) {
        res.resume();
        let next = res.headers.location;
        try { next = new URL(next, url).toString(); } catch (_) {}
        return tryOne(next, redirectsLeft - 1).then(resolve, reject);
      }
      if (res.statusCode !== 200) {
        res.resume();
        return reject(new Error(`HTTP ${res.statusCode}: ${url}`));
      }
      const total = parseInt(res.headers["content-length"] || "0", 10);
      let got = 0;
      const out = fs.createWriteStream(dest);
      res.on("data", (c) => {
        got += c.length;
        if (total > 0) bootstrapProgress(stage, Math.min(99, Math.round(got / total * 100)),
          `${(got / 1048576).toFixed(1)} / ${(total / 1048576).toFixed(1)} MB`);
      });
      res.pipe(out);
      out.on("finish", () => out.close(() => resolve()));
      out.on("error", reject);
    });
    req.on("timeout", () => req.destroy(new Error("download timeout: " + url)));
    req.on("error", reject);
  });
  return (async () => {
    let lastErr = null;
    for (const u of urls) {
      try { await tryOne(u, 5); return; }
      catch (e) { lastErr = e; logWarn(`download failed ${u}: ${e.message}`); }
    }
    throw lastErr || new Error("no download url");
  })();
}

function runCmd(cmd, args, opts = {}) {
  return new Promise((resolve, reject) => {
    const p = spawn(cmd, args, { windowsHide: true, ...opts });
    let tail = "";
    const onOut = (d) => {
      const t = d.toString().trimEnd();
      tail = t.split("\n").slice(-2).join("\n");
      if (opts.onLine) opts.onLine(tail);
    };
    p.stdout.on("data", onOut);
    p.stderr.on("data", onOut);
    p.on("error", reject);
    p.on("exit", (code) => code === 0 ? resolve() : reject(new Error(`${cmd} exit ${code}: ${tail.slice(-200)}`)));
  });
}

async function installManagedPythonWindows() {
  const dir = MANAGED_PY_DIR();
  fs.mkdirSync(dir, { recursive: true });
  const zipPath = path.join(userDataDir(), PY_EMBED_ZIP_NAME);

  bootstrapProgress("download", 0, "正在下载 Python 运行环境（约 11 MB）…");
  await downloadFile(PY_EMBED_MIRRORS, zipPath, "download");

  bootstrapProgress("extract", 0, "正在解压…");
  // Windows 10+ 自带 bsdtar；失败则退回 PowerShell。
  try {
    await runCmd("tar", ["-xf", zipPath, "-C", dir]);
  } catch (_) {
    await runCmd("powershell", ["-NoProfile", "-Command",
      `Expand-Archive -LiteralPath '${zipPath}' -DestinationPath '${dir}' -Force`]);
  }
  try { fs.unlinkSync(zipPath); } catch (_) {}

  // embeddable 默认不带 site-packages 支持：取消 ._pth 里 "import site" 的注释，
  // 否则 pip 装的依赖无法被 import。
  bootstrapProgress("patch", 0, "配置嵌入式 Python…");
  const pth = path.join(dir, `python${BOOTSTRAP_PY_VERSION.replace(/\.\d+$/, "")}._pth`);
  try {
    const txt = fs.readFileSync(pth, "utf-8");
    fs.writeFileSync(pth, txt.replace(/^#\s*import site/m, "import site"));
  } catch (e) { logWarn("._pth patch skipped:", e.message); }

  const pyExe = path.join(dir, "python.exe");
  bootstrapProgress("get-pip", 0, "正在安装 pip…");
  const getPip = path.join(dir, "get-pip.py");
  await downloadFile([GET_PIP_URL], getPip, "get-pip");
  await runCmd(pyExe, [getPip, "--no-warn-script-location",
    "-i", BOOTSTRAP_PIP_INDEX, "--extra-index-url", "https://pypi.org/simple"],
    { onLine: (t) => bootstrapProgress("get-pip", 50, t) });
  try { fs.unlinkSync(getPip); } catch (_) {}

  await installDepsWith(pyExe);
  return pyExe;
}

async function installDepsWith(pyExe) {
  const req = requirementsPath();
  bootstrapProgress("deps", 0, "正在安装软件运行依赖（首次约 2-5 分钟）…");
  await runCmd(pyExe, ["-m", "pip", "install", "--no-warn-script-location",
    "-r", req, "-i", BOOTSTRAP_PIP_INDEX, "--extra-index-url", "https://pypi.org/simple"],
    { onLine: (t) => bootstrapProgress("deps", 50, t) });
  bootstrapProgress("deps", 100, "依赖安装完成");
}

async function relaunchAfterBootstrap() {
  sidecarRestartCount = 0;
  sidecarManualStop = false;
  sidecarStderrTail = [];
  if (!startSidecar()) throw new Error("sidecar spawn failed after bootstrap");
  const info = await waitForSidecar();
  notifyRenderer("sidecar:health", { ok: true, info });
  if (mainWindow) mainWindow.loadFile(path.join(__dirname, "..", "renderer", "index.html"));
  return true;
}

function startSidecar() {
  const py = resolvePython();
  if (!py) {
    logError("no Python interpreter found — entering in-app bootstrap mode");
    return "no-python";
  }
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
  sidecarProc.stderr.on("data", (d) => {
    const t = d.toString().trimEnd();
    logWarn("[sidecar-stderr]", t);
    sidecarStderrTail.push(t);
    if (sidecarStderrTail.length > 80) sidecarStderrTail.shift();
  });
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
    // `timeoutMs` lets a slow-but-legitimate route (e.g. the hub health probe,
    // which walks the HF mirror rotation on a cold network) get a longer
    // budget than the default. Clamped to a sane range.
    const timeoutMs = Math.max(1_000, Math.min(
      Number.isFinite(opts.timeoutMs) ? opts.timeoutMs : 30_000,
      120_000,
    ));
    const req = http.request({
      host: u.hostname, port: u.port, path: u.pathname + u.search,
      method: opts.method || "GET",
      headers: { "Content-Type": "application/json", ...(opts.headers || {}) },
      timeout: timeoutMs,
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

// Resolve the pre-paint window background from the saved theme so there is no
// white/dark flash before the renderer loads. The old constant "#0b1020" was a
// stale navy from the pre-v2.9 palette and also forced a dark flash on light
// theme users. Values match the renderer tokens --stack-0 (dark) and light base.
function resolveBackgroundColor() {
  let theme = "system";
  try { theme = loadSettingsSync().theme || "system"; } catch (_) {}
  const isDark = theme === "dark" || (theme !== "light" && nativeTheme.shouldUseDarkColors);
  return isDark ? "#0d0e11" : "#f6f7f9";
}

function createWindow(bootstrapMode = false) {
  mainWindow = new BrowserWindow({
    width: 1380,
    height: 900,
    minWidth: 1024,
    minHeight: 700,
    title: "Kevrai Omni",
    // PNG works from inside the asar archive on every platform (.ico does not).
    icon: path.join(__dirname, "..", "assets", "icons", "icon-256.png"),
    backgroundColor: resolveBackgroundColor(),
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

  mainWindow.loadFile(path.join(__dirname, "..", "renderer",
    bootstrapMode ? "bootstrap.html" : "index.html"));

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

  // Notify a custom title bar when the maximize state flips so its button icon
  // can switch between "maximize" and "restore". No-op if no listener.
  const sendMax = (v) => {
    try { mainWindow.webContents.send("window:maximize-change", v); } catch (_) {}
  };
  mainWindow.on("maximize", () => sendMax(true));
  mainWindow.on("unmaximize", () => sendMax(false));
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
// ---------------------------------------------------------------------------
// Auto-update (electron-updater ↔ GitHub releases)
// ---------------------------------------------------------------------------
// In packaged builds electron-builder writes resources/app-update.yml from
// the `publish` block in electron-builder.yml; electron-updater then reads
// latest.yml / latest-linux.yml from the matching GitHub release. In dev
// (unpackaged) there is no app-update.yml, so we short-circuit with a `dev`
// flag instead of throwing. Every failure is returned as {error} — never
// rejected — so the renderer can show a friendly toast. Progress and the
// "downloaded" event are pushed to the renderer via notifyRenderer.

let _auInstance = null;
let _auChecking = false;
let _auPending = null; // {version, releaseNotes}

function getAutoUpdater() {
  if (_auInstance) return _auInstance;
  if (!app.isPackaged) return null; // dev: no app-update.yml
  try {
    const { autoUpdater } = require("electron-updater");
    autoUpdater.autoDownload = false;        // user confirms before downloading
    autoUpdater.autoInstallOnAppQuit = true;
    autoUpdater.on("download-progress", (p) => {
      notifyRenderer("kevrai:update-progress", {
        percent: Math.round(p.percent || 0),
        bytesPerSecond: p.bytesPerSecond || 0,
        transferred: p.transferred || 0,
        total: p.total || 0,
      });
    });
    autoUpdater.on("update-downloaded", (info) => {
      notifyRenderer("kevrai:update-downloaded", {
        version: info && info.version ? info.version : (_auPending && _auPending.version),
      });
    });
    autoUpdater.on("error", (e) => {
      notifyRenderer("kevrai:update-error", { message: (e && e.message) ? e.message : String(e) });
    });
    _auInstance = autoUpdater;
    return _auInstance;
  } catch (_) {
    return null;
  }
}

async function checkForUpdates() {
  const currentVersion = app.getVersion();
  if (!app.isPackaged) {
    return { updateAvailable: false, currentVersion, dev: true };
  }
  const au = getAutoUpdater();
  if (!au) return { updateAvailable: false, currentVersion, error: "auto-updater unavailable" };
  if (_auChecking) return { updateAvailable: false, currentVersion, busy: true };
  _auChecking = true;
  try {
    return await new Promise((resolve) => {
      let settled = false;
      const timer = setTimeout(() => finish({ updateAvailable: false, currentVersion, error: "check timeout" }), 30000);
      function finish(payload) {
        if (settled) return;
        settled = true;
        clearTimeout(timer);
        au.removeListener("update-available", onAvail);
        au.removeListener("update-not-available", onNotAvail);
        au.removeListener("error", onErr);
        resolve(payload);
      }
      function onAvail(info) {
        _auPending = { version: info.version, releaseNotes: info.releaseNotes || null };
        finish({
          updateAvailable: true,
          currentVersion,
          version: info.version,
          releaseNotes: info.releaseNotes || null,
        });
      }
      function onNotAvail() { finish({ updateAvailable: false, currentVersion }); }
      function onErr(e) { finish({ updateAvailable: false, currentVersion, error: (e && e.message) ? e.message : String(e) }); }
      au.once("update-available", onAvail);
      au.once("update-not-available", onNotAvail);
      au.once("error", onErr);
      au.checkForUpdates().catch(onErr);
    });
  } finally {
    _auChecking = false;
  }
}

async function downloadUpdate() {
  const au = getAutoUpdater();
  if (!au) return { ok: false, error: "auto-updater unavailable" };
  try {
    await au.downloadUpdate();
    return { ok: true };
  } catch (e) {
    return { ok: false, error: (e && e.message) ? e.message : String(e) };
  }
}

function installUpdate() {
  const au = getAutoUpdater();
  if (!au) return { ok: false, error: "auto-updater unavailable" };
  try {
    au.quitAndInstall();
    return { ok: true };
  } catch (e) {
    return { ok: false, error: (e && e.message) ? e.message : String(e) };
  }
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

  // --- Window controls (custom / frameless title bar support) -------------
  // These let a future custom title bar (Kova-Aurora owns the HTML/CSS) drive
  // the OS window without exposing the raw window API to the renderer. They are
  // safe to register now — inert until the renderer calls them.
  ipcMain.handle("window:minimize", () => { try { if (mainWindow) mainWindow.minimize(); } catch (_) {} });
  ipcMain.handle("window:toggle-maximize", () => {
    try {
      if (!mainWindow) return;
      if (mainWindow.isMaximized()) mainWindow.unmaximize(); else mainWindow.maximize();
    } catch (_) {}
  });
  ipcMain.handle("window:close", () => { try { if (mainWindow) mainWindow.close(); } catch (_) {} });
  ipcMain.handle("window:is-maximized", () => !!(mainWindow && mainWindow.isMaximized()));

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

  // v2.8.1 — source registry / health / lock (design T03).
  ipcMain.handle("kevrai:source-registry", async () => {
    return sidecarFetch("/api/sources/registry");
  });
  ipcMain.handle("kevrai:source-health", async () => {
    return sidecarFetch("/api/sources/health");
  });
  ipcMain.handle("kevrai:lock-source", async (_e, body) => {
    assert(body && typeof body === "object", "body: invalid");
    const sourceId = (typeof body.source_id === "string") ? body.source_id : "";
    assert(sourceId.length <= 128, "source_id: too long");
    return sidecarFetch("/api/sources/lock", { method: "POST", body: { source_id: sourceId } });
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

  // --- v2.8.0 DIY: llama.cpp local GGUF runtime -----------------------------
  // Lets DIY-imported *.gguf models actually run: spawn the installed
  // llama-server, health-poll it, and hand the port to the renderer.
  // Single instance at a time; the exit hook below reaps it on quit.
  let llmProc = null;      // active llama-server child process
  let llmPort = 0;         // port it is serving on
  let llmModelPath = "";   // model currently loaded

  const LLM_BOOT_TIMEOUT_MS = 30000;
  const LLM_HEALTH_INTERVAL_MS = 400;

  function llmAssertGguf(modelPath) {
    const p = String(modelPath || "").trim();
    if (!p || p.includes("\0") || /[\r\n]/.test(p)) throw new Error("model_path 非法");
    if (path.extname(p).toLowerCase() !== ".gguf") throw new Error("仅支持 .gguf 模型文件");
    if (!fs.existsSync(p)) throw new Error("模型文件不存在: " + p);
    return p;
  }

  function llmReset(proc) {
    if (llmProc === proc) { llmProc = null; llmPort = 0; llmModelPath = ""; }
  }

  function llmKillProc(proc) {
    if (process.platform === "win32") {
      try { spawn("taskkill", ["/PID", String(proc.pid), "/T", "/F"], { windowsHide: true }); } catch (_) {}
    } else {
      try { proc.kill("SIGTERM"); } catch (_) {}
    }
  }

  function httpGetOk(port, urlPath, timeoutMs) {
    return new Promise((resolve) => {
      const req = http.get({ host: "127.0.0.1", port, path: urlPath, timeout: timeoutMs }, (res) => {
        res.resume();
        resolve(res.statusCode === 200);
      });
      req.on("timeout", () => { req.destroy(); resolve(false); });
      req.on("error", () => resolve(false));
    });
  }

  function llmFreePort() {
    return new Promise((resolve, reject) => {
      const srv = require("node:net").createServer();
      srv.unref();
      srv.on("error", reject);
      srv.listen(0, "127.0.0.1", () => {
        const port = srv.address().port;
        srv.close(() => resolve(port));
      });
    });
  }

  async function findLlamaServerBinary() {
    const st = await sidecarFetch("/api/engines");
    const items = Array.isArray(st) ? st : (st.engines || []);
    const rec = items.find((x) => x && x.id === "llama.cpp");
    if (!rec || !rec.installed) throw new Error("llama.cpp 引擎未安装——请先在「引擎」页安装");
    const base = String(rec.install_path || "");
    if (!base) throw new Error("llama.cpp 安装路径未知");
    const exe = process.platform === "win32" ? "llama-server.exe" : "llama-server";
    const direct = path.join(base, exe);
    if (fs.existsSync(direct)) return direct;
    // zip layout: llama.cpp-<ver>-bin-…/llama-server (bounded-depth search).
    const stack = [{ dir: base, depth: 0 }];
    while (stack.length) {
      const { dir, depth } = stack.pop();
      if (depth > 4) continue;
      let entries;
      try { entries = fs.readdirSync(dir, { withFileTypes: true }); } catch (_) { continue; }
      for (const ent of entries) {
        const full = path.join(dir, ent.name);
        if (ent.isDirectory()) stack.push({ dir: full, depth: depth + 1 });
        else if (ent.name === exe) return full;
      }
    }
    throw new Error("llama.cpp 已安装但未找到 llama-server 可执行文件");
  }

  async function llmStart(opts) {
    if (llmProc && llmProc.exitCode === null) {
      throw new Error("已有模型在运行——请先停止当前模型");
    }
    const modelPath = llmAssertGguf(opts.model_path);
    const bin = await findLlamaServerBinary();
    const port = await llmFreePort();
    const proc = spawn(bin, ["-m", modelPath, "--host", "127.0.0.1", "--port", String(port)], {
      windowsHide: true,
      stdio: ["ignore", "pipe", "pipe"],
    });
    llmProc = proc; llmPort = port; llmModelPath = modelPath;
    let stderrTail = "";
    proc.stderr.on("data", (d) => {
      stderrTail = (stderrTail + d.toString()).split("\n").slice(-5).join("\n").slice(-800);
    });
    proc.on("exit", (code) => {
      logInfo(`llama-server exit ${code}`, stderrTail ? stderrTail.slice(-200) : "");
      llmReset(proc);
    });
    const start = Date.now();
    while (Date.now() - start < LLM_BOOT_TIMEOUT_MS) {
      if (llmProc !== proc || proc.exitCode !== null) {
        throw new Error("llama-server 启动失败: " + (stderrTail.slice(-300) || `exit ${proc.exitCode}`));
      }
      if (await httpGetOk(port, "/health", 1500)) {
        logInfo("llama-server ready", `port=${port} model=${path.basename(modelPath)}`);
        return { ok: true, port, pid: proc.pid, model_path: modelPath };
      }
      await new Promise((r) => setTimeout(r, LLM_HEALTH_INTERVAL_MS));
    }
    llmKillProc(proc);
    llmReset(proc);
    throw new Error("llama-server 健康检查超时（30s）");
  }

  async function llmStop() {
    if (!llmProc || llmProc.exitCode !== null) {
      llmProc = null; llmPort = 0; llmModelPath = "";
      return { ok: true, stopped: false };
    }
    const proc = llmProc;
    const port = llmPort;
    llmKillProc(proc);
    const start = Date.now();
    while (Date.now() - start < 5000 && llmProc === proc && proc.exitCode === null) {
      await new Promise((r) => setTimeout(r, 100));
    }
    if (llmProc === proc && proc.exitCode === null) {
      try { proc.kill("SIGKILL"); } catch (_) {}
    }
    llmReset(proc);
    return { ok: true, stopped: true, port };
  }

  ipcMain.handle("kevrai:llm-start", async (_e, opts) => {
    assertString(opts && opts.model_path, "opts.model_path", 4096);
    return llmStart(opts);
  });
  ipcMain.handle("kevrai:llm-stop", async () => llmStop());
  ipcMain.handle("kevrai:llm-status", async () => {
    const running = !!(llmProc && llmProc.exitCode === null && llmPort > 0);
    const healthy = running ? await httpGetOk(llmPort, "/health", 1500) : false;
    return {
      running, healthy,
      port: running ? llmPort : 0,
      model_path: running ? llmModelPath : "",
    };
  });

  // Reap llama-server on quit (registerIpc runs exactly once).
  app.on("before-quit", () => {
    if (llmProc && llmProc.exitCode === null) llmKillProc(llmProc);
  });

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
  ipcMain.handle("kevrai:drama-storycraft", async () => sidecarFetch("/api/drama/storycraft"));
  ipcMain.handle("kevrai:drama-brainstorm", async (_e, opts) => {
    assert(opts && typeof opts === "object", "opts: invalid");
    assertString(opts.topic, "opts.topic", 1000);
    return sidecarFetch("/api/drama/brainstorm", { method: "POST", body: {
      topic: opts.topic,
      mode: opts.mode === "hook_drama" ? "hook_drama" : "micro_film",
    } });
  });
  ipcMain.handle("kevrai:drama-script", async (_e, opts) => {
    assert(opts && typeof opts === "object", "opts: invalid");
    assertString(opts.topic, "opts.topic", 1000);
    const body = { topic: opts.topic, mode: opts.mode === "hook_drama" ? "hook_drama" : "micro_film" };
    if (typeof opts.angle === "string" && opts.angle.length > 0) {
      body.angle = String(opts.angle).slice(0, 500);
    }
    if (typeof opts.style_anchor === "string" && opts.style_anchor.length > 0) {
      body.style_anchor = String(opts.style_anchor).slice(0, 200);
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

  // v2.7.0 — Kevrai Agent (通用 AI 助手)
  ipcMain.handle("kevrai:agent-status", async () => sidecarFetch("/api/agent/status"));
  ipcMain.handle("kevrai:agent-chat", async (_e, opts) => {
    assert(opts && typeof opts === "object", "opts: invalid");
    return sidecarFetch("/api/agent/chat", { method: "POST", body: opts });
  });
  ipcMain.handle("kevrai:agent-sessions", async (_e, limit) => {
    const l = (typeof limit === "number" && limit > 0) ? limit : 20;
    return sidecarFetch(`/api/agent/sessions?limit=${l}`);
  });
  ipcMain.handle("kevrai:agent-session-messages", async (_e, sessionId, limit) => {
    assert(typeof sessionId === "string" && sessionId.length > 0, "sessionId: invalid");
    const l = (typeof limit === "number" && limit > 0) ? limit : 100;
    return sidecarFetch(`/api/agent/sessions/${encodeURIComponent(sessionId)}/messages?limit=${l}`);
  });
  ipcMain.handle("kevrai:agent-prefs-get", async () => sidecarFetch("/api/agent/preferences"));
  ipcMain.handle("kevrai:agent-prefs-set", async (_e, key, value) => {
    assert(typeof key === "string" && key.length > 0, "key: invalid");
    return sidecarFetch("/api/agent/preferences", { method: "PUT", body: { key, value } });
  });

  // v2.8.0 — 可插拔技能库
  ipcMain.handle("kevrai:agent-skills", async () => sidecarFetch("/api/agent/skills"));
  ipcMain.handle("kevrai:agent-toggle-skill", async (_e, skillId, enabled) => {
    assert(typeof skillId === "string" && /^[a-z][a-z0-9_]{1,63}$/.test(skillId), "skillId: invalid");
    assert(typeof enabled === "boolean", "enabled: invalid");
    return sidecarFetch(`/api/agent/skills/${encodeURIComponent(skillId)}`, {
      method: "POST", body: { enabled },
    });
  });
  ipcMain.handle("kevrai:agent-reset-skills", async () =>
    sidecarFetch("/api/agent/skills/reset", { method: "POST", body: {} }));

  // v2.9.0 — skill hub（导入外部 Anthropic SKILL.md 技能）
  // 注意：/skill-hub 系列必须由 sidecar 端声明在 /skills/{skill_id} 之前，
  // 否则 "skill-hub" 会被当成 skill id 匹配掉（见 python/app/main.py）。
  ipcMain.handle("kevrai:skill-hub-list", async () => sidecarFetch("/api/agent/skill-hub"));
  ipcMain.handle("kevrai:skill-hub-import", async (_e, dirPath) => {
    assert(typeof dirPath === "string" && dirPath.length > 0 && dirPath.length <= 4096,
           "path: invalid");
    return sidecarFetch("/api/agent/skill-hub/import", { method: "POST", body: { path: dirPath } });
  });
  ipcMain.handle("kevrai:skill-hub-import-zip", async (_e, zipPath) => {
    assert(typeof zipPath === "string" && zipPath.length > 0 && zipPath.length <= 4096,
           "zipPath: invalid");
    return sidecarFetch("/api/agent/skill-hub/import-zip", { method: "POST", body: { path: zipPath } });
  });
  ipcMain.handle("kevrai:skill-hub-import-git", async (_e, url) => {
    assert(typeof url === "string" && url.length > 0 && url.length <= 2048, "url: invalid");
    return sidecarFetch("/api/agent/skill-hub/import-git", { method: "POST", body: { url } });
  });
  ipcMain.handle("kevrai:skill-hub-remove", async (_e, skillId) => {
    assert(typeof skillId === "string" && /^[a-z][a-z0-9_]{1,63}$/.test(skillId),
           "skillId: invalid");
    return sidecarFetch(`/api/agent/skill-hub/${encodeURIComponent(skillId)}`, { method: "DELETE" });
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

  // v2.5.0 — Python runtime bootstrap (in-app environment install)
  ipcMain.handle("kevrai:bootstrap-status", async () => {
    const tail = sidecarStderrTail.join("\n");
    return {
      platform: process.platform,
      python: resolvePython(),
      managed_python: findManagedPython(),
      deps_missing: /ModuleNotFoundError|No module named|ImportError/.test(tail),
      stderr_tail: sidecarStderrTail.slice(-8),
      busy: bootstrapBusy,
    };
  });
  ipcMain.handle("kevrai:install-python", async () => {
    if (bootstrapBusy) throw new Error("安装进行中，请稍候");
    if (process.platform !== "win32") {
      throw new Error("自动安装目前仅支持 Windows。Linux/macOS 请手动执行：python3 -m pip install -r " + requirementsPath());
    }
    bootstrapBusy = true;
    try {
      await installManagedPythonWindows();
      await relaunchAfterBootstrap();
      return { ok: true };
    } finally { bootstrapBusy = false; }
  });
  ipcMain.handle("kevrai:install-deps", async () => {
    if (bootstrapBusy) throw new Error("安装进行中，请稍候");
    const py = resolvePython();
    if (!py) throw new Error("未找到 Python，请先安装 Python 环境");
    bootstrapBusy = true;
    try {
      await installDepsWith(py);
      await relaunchAfterBootstrap();
      return { ok: true };
    } finally { bootstrapBusy = false; }
  });
  ipcMain.handle("kevrai:bootstrap-retry", async () => {
    if (bootstrapBusy) throw new Error("进行中，请稍候");
    bootstrapBusy = true;
    try {
      await relaunchAfterBootstrap();
      return { ok: true };
    } finally { bootstrapBusy = false; }
  });

  ipcMain.handle("kevrai:get-settings", async () => loadSettingsSync());

  ipcMain.handle("kevrai:put-settings", async (_e, s) => {
    assert(s && typeof s === "object", "settings: invalid");
    return saveSettings(s);
  });

  // ----- v2.8.0 dual-source hub (HF + ModelScope) -----
  // These forward to the sidecar's /api/hub/* endpoints. Host validation for
  // hub downloads is enforced server-side (Python allowlist); the renderer is
  // trusted only for the *shape* of the parameters.
  ipcMain.handle("kevrai:hub-sources", async () => sidecarFetch("/api/hub/sources"));

  // v2.9.0 — source reachability probe. The sidecar may take up to ~12s on a
  // cold, partially-blocked network (it walks the HF mirror rotation), so this
  // gets a longer budget than the default fetch timeout.
  ipcMain.handle("kevrai:hub-health", async () =>
    sidecarFetch("/api/hub/health", { timeoutMs: 20000 }));

  ipcMain.handle("kevrai:hub-search", async (_e, opts) => {
    const o = (opts && typeof opts === "object") ? opts : {};
    const qs = new URLSearchParams();
    if (typeof o.q === "string") qs.set("q", o.q.slice(0, 200));
    if (typeof o.sources === "string") qs.set("sources", o.sources.slice(0, 120));
    if (typeof o.category === "string") qs.set("category", o.category.slice(0, 64));
    if (typeof o.engine === "string") qs.set("engine", o.engine.slice(0, 64));
    if (typeof o.license === "string") qs.set("license", o.license.slice(0, 128));
    if (typeof o.sort === "string") qs.set("sort", o.sort.slice(0, 32));
    if (o.page_size != null) qs.set("page_size", String(Math.max(1, Math.min(100, parseInt(o.page_size, 10) || 30))));
    if (typeof o.cursor === "string") qs.set("cursor", o.cursor.slice(0, 4096));
    return sidecarFetch(`/api/hub/search?${qs.toString()}`);
  });

  ipcMain.handle("kevrai:hub-model", async (_e, opts) => {
    const o = (opts && typeof opts === "object") ? opts : {};
    assert(isString(o.hub, 32), "hub: invalid");
    assert(isString(o.repo, 256), "repo: invalid");
    const qs = new URLSearchParams({ hub: o.hub, repo: o.repo });
    if (typeof o.revision === "string") qs.set("revision", o.revision.slice(0, 128));
    return sidecarFetch(`/api/hub/model?${qs.toString()}`);
  });

  ipcMain.handle("kevrai:hub-files", async (_e, opts) => {
    const o = (opts && typeof opts === "object") ? opts : {};
    assert(isString(o.hub, 32), "hub: invalid");
    assert(isString(o.repo, 256), "repo: invalid");
    const qs = new URLSearchParams({ hub: o.hub, repo: o.repo });
    if (typeof o.revision === "string") qs.set("revision", o.revision.slice(0, 128));
    return sidecarFetch(`/api/hub/model/files?${qs.toString()}`);
  });

  ipcMain.handle("kevrai:hub-download", async (_e, opts) => {
    const o = (opts && typeof opts === "object") ? opts : {};
    assert(isString(o.hub, 32), "hub: invalid");
    assert(isString(o.repo, 256), "repo: invalid");
    assert(Array.isArray(o.files), "files: must be an array");
    const files = o.files.filter((f) => typeof f === "string" && f.length <= 1024).slice(0, 200);
    assert(files.length > 0, "files: empty after validation");
    const body = {
      hub: o.hub, repo: o.repo,
      revision: typeof o.revision === "string" ? o.revision.slice(0, 128) : "",
      files, auto_pick: o.auto_pick !== false,
    };
    return sidecarFetch("/api/hub/download", { method: "POST", body });
  });

  ipcMain.handle("kevrai:hub-job", async (_e, jobId) => {
    assert(isString(jobId, 128), "jobId: invalid");
    return sidecarFetch(`/api/hub/jobs/${encodeURIComponent(jobId)}`);
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

  // Auto-update (electron-updater). Results never reject; errors come back
  // as {error} so the renderer can toast gracefully. Progress / downloaded /
  // error events are pushed as kevrai:update-progress / -downloaded / -error.
  ipcMain.handle("kevrai:check-updates",  async () => checkForUpdates());
  ipcMain.handle("kevrai:download-update", async () => downloadUpdate());
  ipcMain.handle("kevrai:install-update",  async () => installUpdate());
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

  // Deny every Chromium permission by default (camera, microphone, geolocation,
  // notifications, clipboard-read, media keys, etc.). The app does not request
  // any of these; an explicit deny removes the default-allow ambiguity and
  // shrinks the attack surface.
  try {
    session.defaultSession.setPermissionRequestHandler((_wc, _permission, callback) => callback(false));
    session.defaultSession.setPermissionCheckHandler(() => false);
  } catch (e) { logWarn("permission handler setup failed", String(e && e.message || e)); }

  const sc = startSidecar();
  if (sc === "no-python") {
    // 没有任何 Python：进引导页，软件内一键安装运行环境。
    createWindow(true);
    return;
  }
  if (!sc) {
    app.quit();
    return;
  }
  try {
    const info = await waitForSidecar();
    logInfo("sidecar healthy");
    notifyRenderer("sidecar:health", { ok: true, info });
    createWindow();
  } catch (e) {
    logError("sidecar NOT ready:", e.message);
    const tail = sidecarStderrTail.join("\n");
    const depsMissing = /ModuleNotFoundError|No module named|ImportError/.test(tail);
    if (depsMissing) {
      // 有 Python 但缺依赖：同样进引导页，一键补装依赖。
      createWindow(true);
      return;
    }
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

  app.on("activate", () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });
}

// Defense-in-depth for every WebContents (main window or otherwise):
//   - block <webview> attachment (webviewTag is already false in webPreferences)
//   - default-deny new windows even if a future surface forgets to set its own
//     setWindowOpenHandler.
// Registered at module load, before app `ready`, per Electron security guidance.
app.on("web-contents-created", (_event, contents) => {
  contents.on("will-attach-webview", (e) => e.preventDefault());
  contents.setWindowOpenHandler(() => ({ action: "deny" }));
});

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
