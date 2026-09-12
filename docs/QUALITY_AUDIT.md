# Kevrai Omni v2.7.0 — 质量底数审计报告

> 审计：Kevrai Omni Team
> 审计日期：2026-09-06（沙箱环境）
> 仓库：`/workspace/Kevrai-omni` @ `5156e9c`
> 范围：Python 侧全量测试实跑 + 前端语法检查 + 安全/健壮性静态审计
> **本轮只审计，未修改任何源码。** 临时脚本置于 `tests/scratch/`，未污染仓库。

---

## 0. 执行环境

| 项 | 值 |
|---|---|
| OS | Ubuntu 22.04 |
| Python | 3.11.1 |
| Node | v22.13.1 |
| pytest | 9.0.2（pytest-asyncio 1.4.0，asyncio_mode=auto） |
| 本轮新增安装依赖 | `xxhash==4.0.1`、`pytest-cov`（`huggingface-hub` 已预装） |

---

## 1. 测试实跑结果

### 1.1 总览

```
cd /workspace/Kevrai-omni/python && python3 -m pytest -q tests/
```

| 指标 | 联网环境 | 断网环境（`unshare -rn`） |
|---|---|---|
| 收集用例 | 421 | 421 |
| **通过** | **421** | **421** |
| 失败 | 0 | 0 |
| 错误 | 0 | 0 |
| 跳过 | 0 | 0 |
| 总耗时 | 36.72 s | 22.85 s |

**结论：README 声称「420 passed」，实际收集并运行 421 项，全部通过。**

差额说明（**不是缺陷，是文档口径不一致**）：
- README 第 34 行写「全量 **420 passed**」，实跑为 **421**，少计 1 项。
- 更严重的是 README 内部数字自相矛盾：第 108 行「pytest 323 项」、第 132 行「pytest（303 项）」、第 205 行「pytest（372 项）」、第 34 行「420 passed」。**四处数字互不相同，均无 CI 校验**。

### 1.2 逐文件结果（全部通过）

| 测试文件 | 用例数 | 通过 | 失败 | 错误 | 状态 |
|---|---:|---:|---:|---:|---|
| test_v270_agent.py | 48 | 48 | 0 | 0 | ✅ |
| test_v260_catalog.py | 49 | 49 | 0 | 0 | ✅ |
| test_ltx_runtime.py | 34 | 34 | 0 | 0 | ✅ |
| test_search.py | 34 | 34 | 0 | 0 | ✅ |
| test_v24_api.py | 26 | 26 | 0 | 0 | ✅ |
| test_catalog_schema.py | 21 | 21 | 0 | 0 | ✅ |
| test_downloader.py | 18 | 18 | 0 | 0 | ✅ |
| test_settings.py | 18 | 18 | 0 | 0 | ✅ |
| test_security.py | 16 | 16 | 0 | 0 | ✅ |
| test_v241_fixes.py | 16 | 16 | 0 | 0 | ✅ |
| test_catalog.py | 10 | 10 | 0 | 0 | ✅ |
| test_engines_lifecycle.py | 12 | 12 | 0 | 0 | ✅ |
| test_engines_state.py | 12 | 12 | 0 | 0 | ✅ |
| test_env.py | 9 | 9 | 0 | 0 | ✅ |
| test_gpu.py | 10 | 10 | 0 | 0 | ✅ |
| test_importer_edge.py | 10 | 10 | 0 | 0 | ✅ |
| test_json_integrity.py | 12 | 12 | 0 | 0 | ✅ |
| test_settings_persistence.py | 12 | 12 | 0 | 0 | ✅ |
| test_importer.py | 6 | 6 | 0 | 0 | ✅ |
| test_mirror_expand.py | 6 | 6 | 0 | 0 | ✅ |
| test_v241_api.py | 6 | 6 | 0 | 0 | ✅ |
| test_concurrency.py | 4 | 4 | 0 | 0 | ✅ |
| test_download_start.py | 5 | 5 | 0 | 0 | ✅ |
| test_markdown_links.py | 5 | 5 | 0 | 0 | ✅ |
| test_no_secrets.py | 4 | 4 | 0 | 0 | ✅ |
| test_smoke.py | 8 | 8 | 0 | 0 | ✅ |
| test_sources.py | 5 | 5 | 0 | 0 | ✅ |
| test_engines.py | 5 | 5 | 0 | 0 | ✅ |
| **合计（28 文件）** | **421** | **421** | **0** | **0** | ✅ |

### 1.3 失败用例明细

**无。0 失败、0 错误。**

补充说明：3 处 `pytest.skip` 为平台条件跳过（非失败），本次在 Linux 上未触发：
- `test_importer_edge.py:54`（文件系统拒收该文件名）
- `test_importer_edge.py:78`（平台不支持符号链接）
- `test_json_integrity.py:85`（tomllib 需 3.11+，本机 3.11.1 满足）

### 1.4 前端语法检查

`npm run test:js` 覆盖的 11 个文件**全部通过** `node --check`：

```
electron/main.js            OK
electron/preload.js         OK
renderer/app.js             OK
renderer/modules/api.js     OK
renderer/modules/models.js  OK
renderer/modules/search.js  OK
renderer/modules/ltx.js     OK
renderer/modules/engines.js OK
renderer/modules/onboarding.js OK
renderer/modules/downloads.js OK
renderer/bootstrap.js       OK
```

我另外对 `renderer/` 下**全部 22 个** `.js` 做了 `node --check`：**22/22 全部通过**，无语法错误。

⚠️ 但 `package.json` 的 `test:js` **只检查了 11 个**，其余 **13 个文件完全不在 CI 语法门禁内**（详见 §2.3）。

### 1.5 断网验证（clean-environment 结论）

为验证「干净环境可用性」，我用 `unshare -rn`（无网络命名空间）重跑全量：

```
421 passed in 22.85s
```

**结论：测试套件不依赖真实网络即可全绿。** 但存在 2 个**非封闭（non-hermetic）**用例会发起真实网络请求，只是断言写得宽容所以断网也过：
- `test_smoke.py::test_gguf_repos_endpoint`（联网耗时 **14.14 s**，占全量 38%）
- `test_sources.py::test_measure_sources_handles_unreachable`（5.02 s，真连 `https://huggingface.co/api/models`）

这两个用例应打 `@pytest.mark.network` 并 mock，否则在 CI 上既慢又不可靠。

---

## 2. 测试组织与覆盖缺口

### 2.1 真实覆盖率（`pytest --cov=app`）

```
TOTAL   5539 stmts   2370 miss   57%
```

**57% 行覆盖率，与「420 passed」的表象形成强烈反差——用例数量多，但覆盖的代码少。**

| 模块 | 语句数 | 未覆盖 | 覆盖率 | 有无测试直接引用 |
|---|---:|---:|---:|---|
| app/main.py | 1184 | 646 | **45%** | 有（部分） |
| app/gpu.py | 192 | 101 | **47%** | test_gpu.py |
| app/ltx_runtime.py | 311 | 124 | **60%** | test_ltx_runtime.py |
| app/catalog.py | 195 | 69 | 65% | 多个 |
| app/hardware.py | 146 | 45 | 69% | **无直接引用** |
| app/importer.py | 236 | 63 | 73% | 有 |
| app/env.py | 177 | 47 | 73% | test_env.py |
| app/downloader.py | 264 | 69 | 74% | 有 |
| app/engines.py | 437 | 109 | 75% | 有 |
| app/settings.py | 129 | 15 | 88% | 有 |
| app/sources.py | 118 | 12 | 90% | 有 |
| app/search.py | 334 | 19 | **94%** | test_search.py |
| app/converter.py | 397 | 316 | **20%** | **无** |
| app/mnn_catalog.py | 69 | 56 | **19%** | **无** |
| app/mnn_runtime.py | 266 | 229 | **14%** | **无** |
| app/drama.py | 275 | 241 | **12%** | **无** |
| app/recommend.py | 95 | 88 | **7%** | **无** |
| app/runner.py | 21 | 21 | **0%** | **无** |
| app/agent/agent.py | 231 | 24 | 90% | test_v270_agent.py |
| app/agent/memory.py | 96 | 4 | 96% | 有 |
| app/agent/tool_registry.py | 106 | 5 | 95% | 有 |
| app/agent/model_router.py | 37 | 14 | 62% | 有 |
| app/agent/tools/catalog_tools.py | 100 | 24 | 76% | 有 |
| app/agent/tools/system_tools.py | 105 | 29 | 72% | 有 |

### 2.2 关键模块覆盖缺口（P0 级）

以下 **6 个模块完全没有对应测试文件**，且都是核心业务逻辑：

| 模块 | 行数 | 覆盖率 | 风险说明 |
|---|---:|---:|---|
| `app/converter.py` | 667 | **20%** | 模型格式转换，会 `subprocess.Popen` 拉起外部进程；395 行未覆盖。转换失败/取消路径完全没测。 |
| `app/drama.py` | 622 | **12%** | 短剧流水线（剧本→分镜→渲染计划），v2.7 主打功能之一；241 行未覆盖。 |
| `app/mnn_runtime.py` | 414 | **14%** | MNN 引擎运行时，含模型转换编排；229 行未覆盖。 |
| `app/mnn_catalog.py` | 323 | **19%** | MNN 模型目录解析；56 行未覆盖。 |
| `app/recommend.py` | 166 | **7%** | 硬件感知推荐算法，是产品卖点「hardware-aware recommendation」；88 行未覆盖。 |
| `app/runner.py` | 33 | **0%** | `spawn_llama_server` 起推理服务；**一行没测**。 |

外加 `app/hardware.py`（257 行，69%）**没有任何测试文件直接 import**，其覆盖全部来自 `main.py` 的间接调用。

`app/main.py`（2154 行，1184 语句）是最大的未覆盖面：**646 条语句未执行**，包括大量 POST 端点（转换、drama、mnn 系列）。

### 2.3 组织问题

| # | 问题 | 证据 |
|---|---|---|
| 1 | **按版本号堆砌命名** | `test_v24_api.py`、`test_v241_api.py`、`test_v241_fixes.py`、`test_v260_catalog.py`、`test_v270_agent.py` 共 5 个文件（145 用例，占 34.4%）以版本号而非模块命名。新版本不断加文件，老文件永不重构，形成「版本考古层」。 |
| 2 | **重复覆盖** | `test_pip_install_dryrun` 同时存在于 `test_engines.py:48` 和 `test_engines_lifecycle.py:64`；`test_settings_hf_token_roundtrip` 同时存在于 `test_v241_api.py:42` 和 `test_v241_fixes.py:90`。同一逻辑两处断言，改一处忘一处。 |
| 3 | **模块↔测试无映射** | 无 `tests/api/`、`tests/core/` 等分层，`tests/` 平铺 28 个文件；`catalog` 被 7 个测试文件引用、`engines` 被 5 个引用，职责边界靠猜。 |
| 4 | **CI 门禁残缺** | `npm run test:js` 只检查 11/22 个 renderer 文件。未在门禁内的 13 个：`agent.js`、`debounce.js`、`dragdrop.js`、`drama.js`、`environments.js`、`generation-wait.js`、`hardware.js`、`mnn.js`、`settings.js`、`state.js`、`theme.js`、`toast.js`、`virtual-grid.js`。其中 `drama.js`、`mnn.js` 是 v2.7 新功能主文件。 |
| 5 | **无覆盖率门禁** | `pyproject.toml` 未配置 `--cov-fail-under`，57% 覆盖率不会让 CI 变红。 |
| 6 | **无网络/硬件用例标记** | 未定义 `@pytest.mark.network` / `@pytest.mark.gpu`，`addopts` 只有 `-ra --strict-markers`。 |

---

## 3. 安全与健壮性问题清单

### 3.1 先说结论：Electron 主进程加固做得不错

以下**已确认是安全的**，避免误报：

| 检查项 | 位置 | 状态 |
|---|---|---|
| `contextIsolation` | `electron/main.js:484` | ✅ true |
| `nodeIntegration` | `electron/main.js:485` | ✅ false |
| `sandbox` | `electron/main.js:486` | ✅ true |
| `webviewTag` | `electron/main.js:487` | ✅ false |
| `allowRunningInsecureContent` | `electron/main.js:492` | ✅ false |
| 新窗口 | `electron/main.js:505` | ✅ `setWindowOpenHandler` deny |
| 导航 | `electron/main.js:508` | ✅ `will-navigate` preventDefault |
| 重定向 | `electron/main.js:509` | ✅ `will-redirect` preventDefault |
| **`shell:openExternal` 协议白名单** | `electron/main.js:636-643` | ✅ **仅允许 http/https**（已核对源码，非漏洞） |
| preload 二次校验 | `electron/preload.js:398-403` | ✅ 正则 `^https?://` 再拦一道 |
| IPC 通道白名单 | `electron/preload.js:45-48` | ✅ `invoke()` 封装，renderer 无法自选 channel |
| 参数校验 | `electron/preload.js:28-43` | ✅ assertString/assertEnum/assertObject |
| 错误信息脱敏 | `electron/preload.js:21-25` | ✅ 剥离 stack，仅回传 message |
| `kevrai:open-path` 路径限制 | `electron/main.js:996-1002` | ✅ `safePathWithin(root=.../models)` |
| 命令注入（Python 侧） | 全仓 grep `shell=True` | ✅ **0 处**；`subprocess` 全程列表参数 |

### 3.2 P0 — 必须修

| # | 位置 | 问题 | 触发场景 | 建议修法 |
|---|---|---|---|---|
| **P0-1** | `python/app/main.py:700-703` | **`GET /api/settings` 明文返回 HF Token**。`return s.model_dump()` 把 `Settings.hf_token`（`settings.py:106`）原样吐回。Sidecar 监听 `127.0.0.1:17890`（`electron/main.js:34-35`）**无任何鉴权**，同机任意进程 `curl http://127.0.0.1:17890/api/settings` 即可拿到用户 HF Token。 | 用户填了 gated 模型 Token 后，同机任何进程/浏览器页面可窃取 | 返回前 `payload.pop("hf_token", None)`；或改为 `model_dump(exclude={"hf_token"})` 并单独提供只写接口。长期应给 sidecar 加一次性 bearer token（写入 `app.state`，preload 注入）。 |
| **P0-2** | `python/app/engines.py:295` 与 `python/app/engines.py:524` | **Zip Slip（任意文件写入）**。两处均为 `with zipfile.ZipFile(tmp,"r") as zf: zf.extractall(target_dir)`，**未校验 member 路径**。恶意 zip 内含 `../../../../home/user/.bashrc` 即可逃逸 `target_dir`。 | 下载源（含第三方镜像 `hf-mirror.com` / `hf-cdn.sufy.com`）被劫持或 MITM 时 | 逐 member 校验：`dst = (target_dir/name).resolve(); if not dst.is_relative_to(target_dir.resolve()): raise`。或改用 `zf.extract(member, path)` 循环 + 同名校验。 |
| **P0-3** | `python/app/main.py:512` | **`/api/models/import` 任意文件读取**。`src = Path(req.path).expanduser().resolve()` 后直接 `import_local(src, MODELS_DIR)`，**既无根目录包含校验，也无扩展名白名单**（`importer.py` 全文无 suffix 过滤，已 grep 确认）。攻击者可 POST `{"path":"/etc/shadow"}` 或 `~/.ssh/id_rsa`，该文件会被**复制进 models 目录并登记到本地清单**，随后可通过本地模型列表接口读取内容。 | 本地任意进程调用该端点（无鉴权）；或 renderer 被 XSS 后调用 | 校验 `src.resolve().is_relative_to(允许的用户目录)`；加扩展名白名单（`.gguf/.safetensors/.bin/.onnx/.pt/.ggml`）；拒绝符号链接与非常规文件。 |

### 3.3 P1 — 应修

| # | 位置 | 问题 | 触发场景 | 建议修法 |
|---|---|---|---|---|
| **P1-1** | `electron/main.js:322-324` | **`get-pip.py` 下载后无完整性校验即执行**。`downloadFile([GET_PIP_URL], getPip, "get-pip")` 之后立刻 `runCmd(pyExe, [getPip, ...])`。`downloadFile`（`main.js:237-271`）**全程无 SHA256/签名校验**。镜像源为第三方（`registry.npmmirror.com`、`mirrors.huaweicloud.com`，`main.js:189-193`）。同理 `main.js:298-303` 的 Python embeddable zip 下载后直接 `tar -xf`，无校验。 | 镜像被污染 / 中间人劫持 → 任意代码执行（bootstrap 阶段已在高权限运行） | 内置 `get-pip.py` 与 Python zip 的官方 SHA256 常量，下载后比对，不符即中止；或优先使用 `python.org` 官方源并强制 TLS 证书校验。 |
| **P1-2** | `python/app/main.py:1362` | **前缀字符串匹配做路径包含判断**。`if not str(dst_resolved).startswith(str(data_root))`。若 `data_root = /data/models`，则 `/data/models-evil/x` 也能通过 `startswith`。属经典 TOCTOU/前缀绕过。 | 用户可控 `req.dst` 为 `/…/models-xxx/...` 时越界写入 | 改用 `dst_resolved.is_relative_to(data_root)`（Python 3.9+ `Path.is_relative_to`）或 `os.path.commonpath`。 |
| **P1-3** | `python/app/main.py:123-132` | **CORS `allow_origins` 含 `"file://"`**。`"file://"` 作为 origin 语义宽泛，且列表中的 `"app://."` 是**正则写法但用的是精确匹配字段**（Starlette 的 `allow_origins` 不做正则，正则要用 `allow_origin_regex`），因此该条实际**不生效**——Electron 下 origin 为 `file://` 时被前一条兜住，配置意图与实际行为不一致。 | 本地 HTML 文件（用户双击任意 .html）可跨域调用 sidecar 全部接口 | 移除 `"file://"`；`"app://."` 改到 `allow_origin_regex`；生产构建只保留实际使用的 origin。 |
| **P1-4** | `python/app/downloader.py:171` | **`Downloader.start()` 的 `dest` 无包含校验**。`dest_path = Path(dest).expanduser().resolve()` 后直接 `mkdir` + 写入。当前 `main.py:733-736` 用 `dest_filename` 正则挡了一层，但 `Downloader` 作为公共 API 本身不设防。 | 未来任何新增调用点忘了校验即成任意文件写 | 在 `Downloader.start()` 内加根目录参数并校验 `dest_path.is_relative_to(root)`。 |
| **P1-5** | `python/app/main.py:733` | **`dest_filename` 正则允许 `..`**。实测 `re.fullmatch(r"^[A-Za-z0-9._-]{1,128}$", "..")` → **True**。当前靠 `dest.exists()`（父目录已存在）返回 409 侥幸拦住，属**非显式防护**。 | 若后续去掉 `exists()` 检查或改写入逻辑，即为路径穿越 | 显式拒绝 `"."` / `".."` 及任何 `Path(name).name != name` 的输入；并加 `dest.resolve().is_relative_to(dest_dir)`。 |
| **P1-6** | `renderer/bootstrap.js:31` | **未转义插入 `innerHTML`**。`$("#bs-status").innerHTML = \`<span class="err">状态检测失败：${e.message}</span>\``。`e.message` 来自 IPC 错误，经 `preload.js:21-25` 透传，可能携带 sidecar 返回的远端错误文本。 | sidecar 返回含 HTML 的错误信息时触发 DOM 注入 | 改用 `textContent`，或对 `e.message` 施加 `esc()`。`renderer/modules/search.js:299` 已有 `escapeHtml` 可直接复用。 |

### 3.4 P2 — 建议修

| # | 位置 | 问题 | 建议修法 |
|---|---|---|---|
| **P2-1** | `renderer/modules/ltx.js:57` | `<option value="${p.id}">${p.label}</option>` 未转义。`p` 来自 `/api/ltx/capabilities` 的 presets。当前 presets 是服务端常量，不可控；但属属性上下文拼接，缺防御。 | 加 `esc()` 或用 `DOM Option()` 构造。 |
| **P2-2** | `python/tests/test_no_secrets.py:39-53` | **密钥扫描覆盖面不足**。仅覆盖 AWS / `ghp_` / `github_pat_` / `sk-` / `hf_` / PEM 六种。**未覆盖**：Slack `xox*`、`AIza` (Google)、JWT (`eyJ...`)、Telegram bot token、`pypi-`、`npm_`、以及**通用的 `api_key = "..."` / `password = "..."` 硬编码赋值**。我另写了更宽的扫描脚本（`tests/scratch/scan_secrets.py`，13 类模式）全仓扫过，**当前未发现真实泄露**（仅 3 处 `ghp_xxxx...` 文档占位符，位于 `SECURITY.md:102`、`RELEASE.md:5`、`scripts/release.sh:54`）。另：该测试**只扫工作区，不扫 git 历史**。 | 扩充 PATTERNS；补 `git log --all -p` 历史扫描（我已手动验证历史中同样只有占位符）。 |
| **P2-3** | `package.json` `test:js` | 13/22 个 renderer 文件不在语法门禁内（清单见 §2.3）。 | 改为 `find renderer -name "*.js" -exec node --check {} \;`。 |
| **P2-4** | `python/pyproject.toml` | 无 `--cov-fail-under`。 | 加 `addopts = "--cov=app --cov-fail-under=70"`。 |
| **P2-5** | `tests/test_smoke.py:69`、`tests/test_sources.py:114` | 发起真实网络请求（`huggingface.co`），非封闭用例，联网时耗时 14 s / 5 s。 | 打 `@pytest.mark.network` 并 mock；CI 默认 `-m "not network"`。 |
| **P2-6** | `python/app/engines.py:404-409`、`488-496` | `pip install` 使用环境变量 `KEVRAI_PIP_INDEX`（`_pip_index()`）指定索引，无白名单。环境变量被污染时可指向恶意索引。 | 校验索引 URL 必须在已知镜像白名单内。 |

### 3.5 已核查但判定为**非漏洞**的点（避免误报）

- `shell:openExternal`（`electron/main.js:636`）：**已有 `https:`/`http:` 协议白名单**，`file://` / `javascript:` 被拒。任务书提示的疑点不成立。
- renderer `innerHTML` 注入面：我写了启发式扫描脚本（`tests/scratch/scan_xss.py`）扫出 64 处可疑插值，逐条人工复核后确认——`app.js`、`models.js`、`search.js`（含 `highlight()` 函数，对每段切片都做了 escape）、`drama.js`、`hardware.js`、`agent.js`、`mnn.js`、`downloads.js` **均已正确转义**（各文件自带 `esc()` / `escapeHtml()`）。**仅 2 处真实缺失**，已列为 P1-6 / P2-1。
- `save_settings`（`app/settings.py:200-210`）：用 `tempfile.mkstemp`（0600）+ `os.replace`，**文件权限是安全的**，未列为漏洞；但内容仍是明文 JSON（配合 P0-1 才构成风险）。
- `install_pip_engine`（`app/engines.py:488`）：`--target` 与包名的参数注入——上游 `/api/env/install`（`main.py:614`）已用 `^[A-Za-z0-9._-]{1,128}$` 校验 `name`，挡住了 `-` 前缀与 `/`，**当前不可利用**。

---

## 4. 结论：质量底数评分

| 维度 | 评分 | 判断依据 |
|---|:---:|---|
| **测试充分性** | **5 / 10** | 421 项全绿、断网也能过，说明**已有测试写得扎实**（尤其是 agent / search / catalog / ltx_runtime）。但真实行覆盖率仅 **57%**，`main.py` 45%、`converter.py` 20%、`drama.py` 12%、`mnn_runtime.py` 14%、`recommend.py` 7%、`runner.py` 0%——**6 个核心模块零测试**。用例数（421）与覆盖质量严重脱节，"420 passed" 是虚高的信心指标。 |
| **可维护性** | **4 / 10** | 5 个按版本号命名的测试文件（145 用例，34%）形成考古层；2 组重复用例；`tests/` 28 文件平铺无分层；README 四处测试数字互相矛盾（303/323/372/420）且无 CI 校验；`main.py` 2154 行、`converter.py` 667 行未拆分。 |
| **安全性** | **6 / 10** | **Electron 主进程加固是亮点**：contextIsolation+sandbox+nodeIntegration:false 三件套齐全，导航/重定向/新窗口全部拦截，`openExternal` 有协议白名单，preload 有类型校验与错误脱敏，Python 侧 **0 处 `shell=True`**。扣分在于**边界处的一致性问题**：sidecar 无鉴权 + 明文回吐 HF Token（P0-1）、两处 Zip Slip（P0-2）、import 无路径/扩展名校验（P0-3）、bootstrap 下载无完整性校验即执行（P1-1）。这些不是"没想到"，而是"想到了一部分"。 |
| **综合** | **5 / 10** | 项目有明确的硬化意识和大量正确实践（这比多数同类项目好），但**加固覆盖率不均匀**：主进程做完了，sidecar 与文件系统边界没做完；测试数量堆够了，覆盖质量没跟上。 |

### 最该优先修的三件事

1. **P0-1 `GET /api/settings` 明文回吐 HF Token** —— 一行代码改动（`model_dump(exclude={"hf_token"})`），消除真实凭据泄露路径。
2. **P0-2 `engines.py:295/524` Zip Slip** —— 两处 `extractall` 加 member 路径校验，消除任意文件写入。
3. **P0-3 `/api/models/import` 任意文件读取** —— 加根目录包含校验 + 扩展名白名单。

> 三者加起来约 20 行改动，能砍掉当前全部 P0。之后按 P1 → P2 顺序推进，并同步补 `converter.py` / `drama.py` / `mnn_runtime.py` / `recommend.py` 的测试。

---

## 附录：本轮使用的临时脚本（均在 `tests/scratch/`，未写入仓库）

| 脚本 | 用途 |
|---|---|
| `scan_xss.py` | 启发式扫描 renderer 中未转义的 `innerHTML` 插值（64 处候选 → 人工复核后 2 处确认） |
| `scan_secrets.py` | 13 类密钥模式全仓扫描（验证 `test_no_secrets.py` 的覆盖盲区） |

复现命令：

```bash
cd /workspace/Kevrai-omni/python && python3 -m pytest -q tests/ --cov=app --cov-report=term-missing
cd /workspace/Kevrai-omni && npm run test:js
unshare -rn bash -c "cd /workspace/Kevrai-omni/python && python3 -m pytest -q tests/"   # 断网验证
```
