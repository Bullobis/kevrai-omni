# Kevrai Studio — Architecture

> One-stop local AI workstation: LLM, multimodal LLM, TTS, image, video,
> audio, super-resolution, 3D. Engines download on demand from an
> allowlisted set of hosts; models come from `huggingface.co` only.

This document explains how the pieces fit together. It is intentionally
ASCII-diagram-heavy so it stays diff-friendly.

---

## 1. Process topology

```
                    ┌──────────────────────────────────────────────────────┐
                    │                       User's desktop                │
                    │                                                      │
   ╔══════════════════════════════════╗     ┌──────────────────────────┐    │
   ║   Electron renderer (Chromium)   ║     │   Electron main process  │    │
   ║                                   ║     │                          │    │
   ║   renderer/index.html             ║     │   electron/main.js       │    │
   ║   renderer/app.js                 ║     │     - create BrowserWindow│   │
   ║   renderer/modules/*.js           ║     │     - spawn sidecar       │   │
   ║                                   ║     │     - IPC handlers        │   │
   ║   window.kevrai.* (contextBridge) ║<--->║       (api:* channels)    │   │
   ╚══════════════════════════════════╝     │                          │    │
                ▲                            │   electron/preload.js   │    │
                │ contextBridge              │     - white-list bridge │    │
                │ (sandbox=true,             │                          │    │
                │  contextIsolation=true,    └─────────────┬────────────┘    │
                │  nodeIntegration=false)                  │                 │
                │                                          │ http://127.0.0.1:17890
                │                                          ▼                 │
                │                            ┌──────────────────────────┐    │
                │                            │   Python sidecar          │   │
                │                            │                           │   │
                │                            │   python/app/main.py      │   │
                │                            │   FastAPI on port 17890   │   │
                │                            │                           │   │
                │                            │   ┌─────────────────┐    │   │
                │                            │   │   app.catalog   │    │   │
                │                            │   │   app.engines   │    │   │
                │                            │   │   app.importer  │    │   │
                │                            │   │   app.runner    │    │   │
                │                            │   │   app.downloader│    │   │
                │                            │   └─────────────────┘    │   │
                │                            └─────────────┬─────────────┘   │
                │                                          │                 │
                │                                          │ spawn (in real
                │                                          │ run-time llama.cpp
                │                                          │ binary is launched
                │                                          │ by electron/main.js,
                │                                          │ not the sidecar)
                │                                          ▼                 │
                │                                    ┌──────────┐           │
                │                                    │ llama-*  │           │
                │                                    │  (GGUF)  │           │
                │                                    └──────────┘           │
                └──────────────────────────────────────────────────────────┘

   External:
     • huggingface.co / hf-mirror.com       ← models (allow-listed)
     • github.com / pypi.org / tencent-mirror ← engines (allow-listed)
```

Key boundaries:

| Boundary                 | Mechanism                                                    |
|--------------------------|--------------------------------------------------------------|
| Renderer ↔ preload       | `contextBridge.exposeInMainWorld('kevrai', { … })`            |
| Renderer ↔ main process  | `ipcRenderer.invoke('api:*', …)` (one-way, no leakage)       |
| Main ↔ sidecar           | plain HTTP on `127.0.0.1:17890` (loopback only)              |
| Sidecar ↔ internet       | allowlisted hosts only (`is_host_allowed()`)                 |

---

## 2. Data flow — model browse

```
  [renderer]
     |
     | 1. kevrai.models({category, q})
     v
  [preload]  -> ipcRenderer.invoke('api:models', params)
     |
     v
  [main]     -> ipcMain.handle('api:models', ...)
     |           builds a URLSearchParams and GETs
     v
  [sidecar /api/models?category=...&q=...]
     |
     v
  [python/app/main.py::list_models]
     |  filters CATALOG.models by category + substring search
     v
  JSON response → renderer module 'models.js' renders cards
```

There's no state mutation here — `list_models` is a pure function over the
in-memory `CATALOG` (loaded at startup from `catalog/models.json`).

---

## 3. Data flow — engine install

```
  [renderer]                user clicks "Install llama.cpp"
     |
     |  kevrai.installEngine('llama.cpp')
     v
  [preload]             -> ipcRenderer.invoke('api:engines:install', 'llama.cpp')
     v
  [main]                -> sidecarFetch('POST /api/engines/install', {engine_id})
     v
  [sidecar] /api/engines/install
     |
     v
  [app.engines.ensure_engine(engine_id, CATALOG, ENGINES, APP_ROOT)]
     |
     | 1. look up engine in CATALOG
     | 2. resolve platform-specific URL from engine['platforms'][plat]
     | 3. is_host_allowed(url, ALLOWED_ENGINE_HOSTS)?  ← hard fail otherwise
     | 4. download to engines/<engine_id>/engine.bin (.partial → atomic rename)
     | 5. compare SHA-256 against expected_sha256 if provided
     | 6. update engines/installed.json with EngineRecord
     v
  JSON response: { ok: true, result: {engine_id, path, message} }
```

If anything in step 3/4 fails the user gets `400 + "host not in allowlist"` (or
a `ValueError` for hash mismatch). No silent retries.

---

## 4. Data flow — model import

```
  [renderer]        user picks a folder or .gguf file via dialog
     |
     |  kevrai.importModel(p) or kevrai.pickFolder() followed by importModel
     v
  [preload]         ipcRenderer.invoke('api:models:import', path)
     |
     v
  [main]            sidecarFetch('POST /api/models/import', {path})
     |
     v
  [sidecar /api/models/import]
     |
     v
  [app.importer.import_local(src, MODELS_DIR)]
     |
     | 1. resolve src, ensure exists
     | 2. compute SHA-256 (stream) → idempotency short hash
     | 3. if short hash already in _local.json → return duplicate=True
     | 4. otherwise copy (or symlink) into MODELS_DIR; rename on collision
     | 5. append a record to _local.json (atomic temp-file + rename)
     v
  JSON response: { ok: true, imported: {path, size_bytes, sha256, duplicate} }
```

Thread-safety: the read-modify-write of `_local.json` is serialised via a
per-`models_dir` `RLock` (see `app/importer.py:_per_models_dir_lock`). Atomic
write uses PID-stamped `.tmp` files so concurrent processes don't collide.

---

## 5. Data flow — inference

The user-facing flow "load a model, send a prompt, stream tokens":

```
  [renderer]                  "run inference" button
     |  (no IPC: the actual launch is the llama-server running on localhost)
     v
  [preload]                   not bridged — main.js owns the spawn
     v
  [main.js startLLMServer]
     | 1. resolve model path (imported or downloaded gguf)
     | 2. resolve llama.cpp binary via app.runner.find_engine_binary
     | 3. spawn(`llama-server -m <model> --port 8080 ...`)
     v
  [llama-server :8080]        plain HTTP (OpenAI-compatible)
     ^
     |
  [renderer]                  fetch('/v1/chat/completions', {stream:true})
```

The Python sidecar is intentionally NOT in the hot path: its job is to manage
**engine lifecycle** and **model provenance**, not to forward tokens.

---

## 6. Security model

### 6.1 Three concentric rings

```
   ┌──────────────────────────────────────────────────────────────────┐
   │ 1. NETWORK (untrusted)                                            │
   │     huggingface.co, github.com, pypi.org, mirrors.tencent.com     │
   │     — every URL is checked against ALLOWED_MODEL_HOSTS /         │
   │       ALLOWED_ENGINE_HOSTS, and against DEFAULT_BLOCKED_MIRRORS.  │
   │                                                                  │
   │ 2. PRELOAD BRIDGE (sandboxed)                                    │
   │     contextIsolation=true, nodeIntegration=false, sandbox=true.   │
   │     Only the kevrai.* surface is exposed; no `require`, no Node. │
   │                                                                  │
   │ 3. MAIN PROCESS (privileged)                                     │
   │     - dialog.showOpenDialog for folder/file pick (no arbitrary   │
   │       paths).                                                    │
   │     - shell.openExternal only for https?:// schemes.             │
   │     - sidecar bound to 127.0.0.1 only (no LAN exposure).         │
   └──────────────────────────────────────────────────────────────────┘
```

### 6.2 URL allowlist + blocklist

Both are defined in `app/catalog.py` and enforced at THREE layers:

1. **Schema load** — `ModelEntry` (Pydantic) refuses any `repo` /
   `gguf_repo` containing a `DEFAULT_BLOCKED_MIRRORS` substring.
2. **downloads** — `app.downloader._check_url` and `app.engines.download_zip_engine`
   re-check before issuing the HTTP request.
3. **belt-and-suspenders** — `app.catalog._belt_and_suspenders_block` scans
   the entire catalog payload (including `description` and `note` text) for
   any URL containing a blocked mirror.

### 6.3 IPC validation

Every IPC channel is a one-way `ipcRenderer.invoke`. The preload script
exposes an EXACT set of functions; nothing else leaks. Channel names use a
`api:` prefix so arbitrary renderer code can't accidentally call them.

### 6.4 Filesystem policy

* User data lives in `~/.local/share/KevraiOmni` (Linux), `…/Application
  Support/KevraiOmni` (macOS), or `%LOCALAPPDATA%\KevraiOmni` (Windows).
* Imports are sized-capped (default 200 GiB; caller can clamp lower).
* `import_local` is idempotent: re-importing the same SHA returns the
  existing record with `duplicate=True` — never duplicates disk state.
* Atomic writes everywhere (`_local.json`, `settings.json`,
  `installed.json`) — write to `*.tmp.<pid>.<tid>`, fsync, rename.

---

## 7. Catalog provenance

`catalog/models.json` (62 entries) and `catalog/engines.json` (20 entries)
are STATIC assets shipped with the installer. The harness enforces:

* Every `repo` / `gguf_repo` is a well-formed `owner/repo` (or single-segment
  ID like `buffalo_l` for InsightFace).
* Every `engines.json` `platforms[*]` URL lives on an allowlisted host.
* No `version` field is malformed semver.
* `catalog/schema.py` validates the WHOLE document via `jsonschema`, and the
  test suite (`python/tests/test_catalog_schema.py`) parametrises the
  checks per-entry so a regression in any one entry is caught in CI.

---

## 8. Threat model highlights

| Threat                                            | Mitigation                                              |
|--------------------------------------------------|---------------------------------------------------------|
| Phishing mirror slips into a model entry          | Pydantic validator + jsonschema `_not_blocked_url` + allowlist at download |
| Path traversal in `model_id` (`../../etc/passwd`) | Lookup-by-exact-id only; non-existent → 404             |
| Race condition overwriting `_local.json`          | `RLock` per `models_dir` + PID-stamped `.tmp` files     |
| PAT leak in source tree                           | `tests/test_no_secrets.py` scans every file             |
| Tampered model file (SHA mismatch)                | Stream SHA-256 verify before renaming `.partial` → final |
| Electron renderer exploit                         | `contextIsolation=true`, `sandbox=true`, white-list bridge |
| Main process LFI via IPC args                     | `dialog:` channel returns paths only via OS picker     |
