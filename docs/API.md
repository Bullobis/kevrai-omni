# Sidecar REST / WebSocket API 参考

Kevrai Omni 的 Python sidecar（FastAPI）是应用的控制面：目录查询、模型下载、
引擎安装、GPU 检测、设置、Kevrai Agent、LTX 视频、以及 OpenAI 兼容接口都在这里。
Electron 渲染层通过主进程 IPC 转发访问它；外部脚本也可以直接 `curl` 调试。

> 本文档端点与字段均对照 `python/app/main.py` 实现整理。若代码后续改动，
> **以代码为准**，并同步更新本文。

---

## 基础信息

| 项 | 值 |
|---|---|
| Base URL | `http://127.0.0.1:17890` |
| 监听地址 | `127.0.0.1`（**仅本机回环**，不对外网暴露） |
| 启动命令 | `python -m uvicorn app.main:app --host 127.0.0.1 --port 17890`（cwd=`python/`） |
| 内容类型 | 请求/响应均为 `application/json`（除 SSE 流式外） |
| 压缩 | 响应体 > 1KB 自动 GZip |
| 请求 ID | 每个响应带 `x-request-id` 头；可在请求里自带 `X-Request-Id` 串联日志 |

### 认证方式

**sidecar 要求 Bearer secret**（4.x 起启用；P0-1 安全强化）。它不再是一个“本机任意进程都能调用”的裸控制面：

1. Electron 主进程每次启动 sidecar 时生成 32 字节随机 secret，通过环境变量
   `KEVRAI_SIDECAR_SECRET` 传入，并在所有 HTTP 请求上附加
   `Authorization: Bearer <secret>`（见 `electron/main.js` 的 `sidecarSecret`）；
2. Python 侧的 `_auth_middleware` 对**除 `/api/health` 外**的所有路径做
   Bearer 校验：缺失或不匹配返回 `401`；未配置 secret（例如直接 `uvicorn`
   启动）时 **fail-closed**，除健康检查外一律返回 `500`；
3. WebSocket 升级同样校验：优先读 `Authorization`，浏览器 WebSocket 无法设
   自定义头时允许 `?token=<secret>`（`_ws_authorize`），失败以 `1008` 关闭；
4. CORS 只约束浏览器读取，不替代认证；下载 URL 另有主机白名单
   （详见 [../SECURITY.md](../SECURITY.md)）。

> 直接调试 sidecar 时，两种方式二选一：由 Electron 启动（推荐，secret 自动注入），
> 或自己设置 `KEVRAI_SIDECAR_SECRET` 并在 `curl` 里带上同名 Bearer。仅访问
> `/api/health` 时无需认证。

> 另一个 `Bearer` 用途是**出站**：下载 HuggingFace gated 模型时，sidecar 用你在
> 「设置」里填写的 **HF Token** 作为 `Authorization: Bearer <hf_token>` 附加给
> HF 请求。这个 Token 只存本机，`GET/PUT /api/settings` 永远**不会**把明文
> Token 回显给前端（返回 `hf_token_set: true` 之类的存在性标记）。

因此：不要因为“只监听回环”就放松警惕——端口仍不要暴露到 `0.0.0.0`，
也不要在不受信任的环境里运行 sidecar。

---

## 通用约定

- 路径参数 `model_id` / `task_id` / `session_id` 均做白名单字符校验
  （`[A-Za-z0-9._-]`），防路径穿越；非法形状返回 `400`。
- 未找到资源返回 `404`；参数非法返回 `400`；下载源全部不可达返回 `422`。
- 错误响应体形如 `{"detail": "..."}`，`detail` 也可能是携带结构化信息的对象。

---

## 1. 健康 / 元信息

### `GET /api/health`

存活探针，返回 sidecar 版本与数据目录。

```bash
curl http://127.0.0.1:17890/api/health
```

```json
{
  "ok": true,
  "version": "4.17.9",
  "models_dir": "/Users/you/Library/Application Support/KevraiOmni/models",
  "app_root": "/Users/you/Library/Application Support/KevraiOmni"
}
```

### `GET /api/categories`

返回模型市场固定的分类清单（id + 中文/英文标签）。

```json
{ "categories": [
  {"id": "llm", "label": "大语言模型 / LLM"},
  {"id": "tts", "label": "语音合成 / TTS"},
  {"id": "video", "label": "视频生成 / Video"},
  {"id": "image", "label": "图像生成 / Image"},
  {"id": "superres", "label": "超分辨率 / Super-Resolution"},
  {"id": "audio", "label": "音频生成 / Audio"},
  {"id": "3d", "label": "3D 生成 / 3D"},
  {"id": "vision", "label": "视觉工具 / Vision"},
  {"id": "other", "label": "其它 / Other"}
] }
```

### `GET /api/gpu`

异步探测本机 GPU（型号、显存、厂商）。

```bash
curl http://127.0.0.1:17890/api/gpu
```

```json
{ "gpus": [{"name": "NVIDIA GeForce RTX 4090", "vram_gb": 24, "vendor": "nvidia"}],
  "count": 1 }
```

### `GET /api/hardware` · `GET /api/recommend`

- `/api/hardware`：CPU / 内存 / 磁盘等整机硬件探测（查询参数可选过滤）。
- `/api/recommend`：根据硬件能力从目录推荐合适模型。

---

## 2. 模型目录

### `GET /api/models`

列出目录模型，支持过滤与排序。

| 查询参数 | 说明 |
|---|---|
| `category` | 按分类 id 过滤（见 `/api/categories`） |
| `q` | 名称/ID/标签/引擎/仓库/描述子串匹配 |
| `sort` | `name` / `size_desc` / `trending` |

```bash
curl "http://127.0.0.1:17890/api/models?category=llm&sort=trending"
```

```json
{ "count": 121, "models": [ /* ModelEntry */ ], "gguf_repos": [ /* 动态 GGUF 仓库 */ ] }
```

> 带纠错/中文分词/分面/高亮的**超级搜索**用 `GET /api/search`（见第 7 节）。

### `GET /api/models/local`

列出用户本地导入的模型（每条带运行时可用引擎标注，如 `.gguf`→llama.cpp）。

### `GET /api/models/{model_id}`

单个模型详情。`model_id` 非法形状返回 `400`，不存在返回 `404`。

### `GET /api/models/{model_id}/gguf-files`

惰性枚举某模型对应的 GGUF 仓库文件树（在详情面板打开时才调用，避免冷启动阻塞）。

### `GET /api/gguf-repos`

枚举全部 GGUF 仓库及其 `.gguf` 文件（实时拉 HF 文件树；失败条目带 `error` 字段）。

### `POST /api/models/import`

导入本地模型文件/目录。Body：

```json
{ "path": "/absolute/path/to/model.gguf" }
```

允许的扩展名见 `main.py` 的 `_IMPORT_ALLOWED_EXTS`（`.gguf/.safetensors/.bin/.onnx/.mnn/.zip/.tar` 等）。该端点有内存令牌桶限流。

---

## 3. 引擎

### `GET /api/engines`

列出全部引擎及其安装状态、缓存的最新版本标签、是否可更新。

```json
{ "engines": [
  {"id": "llama.cpp", "installed": true, "version": "bxxxx",
   "latest_tag": "", "update_available": false}
] }
```

### `POST /api/engines/install` · `POST /api/engines/update`

```json
{ "engine_id": "llama.cpp" }
```

成功返回 `{"ok": true, "result": {"engine_id": "...", "path": "...", "message": "..."}}`；
失败返回 `400` + `detail`。

### `POST /api/engines/check-updates`

主动查询各引擎在 GitHub 的最新 release（body `{"force": true}` 可跳过缓存）。

### `GET /api/progress`

返回所有导入/安装类任务的快照应。

---

## 4. 设置与环境

### `GET /api/settings`

读取当前设置。**敏感字段（`hf_token`、`ms_token`）会被剥离**，仅保留存在性标记：

```json
{ "model_dir": "...", "theme": "dark", "hf_token_set": true, "ms_token_set": false }
```

### `PUT /api/settings`

部分更新设置，body 为 `SettingsUpdate` 字段子集（均可选）：

```json
{
  "theme": "light",
  "max_concurrent_downloads": 3,
  "hf_token": "hf_xxx",
  "allow_custom_blocked_mirrors": false
}
```

提交后会自动重建 hub 注册表与下载器并发池；响应同样**回显脱敏后的设置**。

### `GET /api/env/status` · `POST /api/env/install` · `POST /api/env/upgrade` · `POST /api/env/install-engine`

- `GET /api/env/status`：探测 Python / Node / pip 包 / 引擎 / 磁盘 / GPU 状态
  （结果短暂缓存，`?refresh=1` 强制重探）。
- `POST /api/env/install`：`{"kind":"pip","name":"torch","version":null}` 按需装 pip 包。
- `POST /api/env/install-engine`：按引擎声明的 `sources[]` 测速后选最快源安装。

---

## 5. 模型下载（downloader）

### `POST /api/download/start`

启动一个后台下载任务。Body（`DownloadStartReq`）：

```json
{
  "url": "https://huggingface.co/.../model.safetensors",
  "candidates": [],
  "dest_filename": "my-model.safetensors",
  "sha256": null,
  "auto_pick": true,
  "gated": false
}
```

行为：

- `url` 或 `candidates` 至少给一个；`auto_pick=true` 时 sidecar 会测速各镜像候选
  并选最快的一个（结果里带 `ranking` / `skipped`）。
- `gated=true` 时会要求已配置 HF Token，否则返回 `422 gated_requires_token`；
  随后由 sidecar 附带 `Authorization: Bearer <hf_token>`。
- 目标文件已存在返回 `409`；全部源不可达返回 `422 all_sources_unreachable`。

响应：

```json
{ "task_id": "dl_abc123", "started_at": 1730000000.0,
  "url": "https://...", "candidates_tried": 3,
  "ranking": [ /* top5 */ ], "skipped": [ /* 被熔断跳过的源 */ ],
  "dest": "/abs/path/to/my-model.safetensors" }
```

### `GET /api/download/{task_id}`

查询单个下载任务快照（进度、已下载字节、状态）；不存在返回 `404`。

### `POST /api/download/{task_id}/cancel`

取消进行中的下载任务。

### `WS /ws/download/{task_id}`

下载进度推送 WebSocket。连接后 server 先推一次当前快照，之后持续推任务事件，
任务到达终态时关闭；另有 `{"event":"heartbeat"}` 心跳。

---

## 6. Kevrai Agent

Agent 运行在 sidecar 内，支持 ReAct 工具调用与可插拔技能库。

### `GET /api/agent/status`

```json
{ "llm_ready": false, "model_name": "", "tool_count": 16, "mode": "rule_based" }
```

`mode` 为 `llm`（已加载模型走 ReAct）或 `rule_based`（无模型时的确定性路由）。

### `GET /api/agent/tools` · `GET /api/agent/skills`

- `/tools`：列出全部可用工具。
- `/skills`：列出可插拔技能包及启用状态、激活工具数。

### `POST /api/agent/chat`

同步发送一条消息并等待完整结果（非流式）。Body（`AgentChatReq`）：

```json
{ "message": "帮我找一个能在 8GB 显存跑的视频模型", "session_id": "default" }
```

响应包含 `answer`、`tools_used`、`llm_used`、`model_name`、`duration_ms`、
逐步 `steps`（thought / action_tool / action_params / observation）等。
> 实时流式推理步骤请改用下面的 WebSocket。

### `WS /ws/agent/{session_id}`

实时流式对话。客户端发 JSON：

```json
{ "message": "你的问题" }
```

服务端逐条推事件（思考步、工具调用、最终答案、错误），例如：

```json
{ "event": "step", "iteration": 1, "thought": "...", "action_tool": "search_models", "action_params": {"q": "video"} }
{ "event": "answer", "answer": "..." }
{ "event": "error", "message": "..." }
```

单条消息上限 5000 字符；`session_id` 须匹配 `[A-Za-z0-9._-]{1,128}`。

### 会话与偏好

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/agent/sessions?limit=50` | 列出历史会话（最近优先） |
| GET | `/api/agent/sessions/{session_id}/messages?limit=100` | 某会话消息 |
| DELETE | `/api/agent/sessions/{session_id}` | 删除会话及其消息 |
| GET | `/api/agent/preferences` | 读取全部 Agent 偏好键值 |
| PUT | `/api/agent/preferences` | `{"key":"...","value":"..."}` 写入偏好 |

### 技能库（skill-hub）

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/agent/skill-hub` | 列出本地技能库 |
| POST | `/api/agent/skill-hub/import` | `{"path":"/dir"}` 导入本地 SKILL.md 目录 |
| POST | `/api/agent/skill-hub/import-zip` | `{"path":"/x.zip"}` 导入磁盘上的 zip |
| POST | `/api/agent/skill-hub/import-git` | `{"url":"https://github.com/..."}` 从 git 导入 |
| DELETE | `/api/agent/skill-hub/{skill_id}` | 删除已导入技能 |
| POST | `/api/agent/skills/{skill_id}` | `{"enabled":true}` 启用/禁用技能 |
| POST | `/api/agent/skills/reset` | 恢复技能默认启用状态 |

---

## 7. 搜索 / LTX / MNN / 其他业务端点

### 超级搜索

`GET /api/search?q=&category=&engine=&license=&size_bucket=&trending=&sort=&page=&page_size=`
加权模糊搜索（纠错、中文分词、分面、高亮）。`GET/DELETE /api/search/recent` 管理最近搜索。

### LTX-2.5 视频生成

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/ltx/capabilities` | 能力描述 + 引擎就绪状态 |
| POST | `/api/ltx/generate` | 提交文生/图生视频任务（宽高/帧数/步数/CFG/种子/FPS/格式） |
| GET | `/api/ltx/tasks` | 任务列表 + 当前任务 |
| GET | `/api/ltx/tasks/{task_id}` | 单任务状态（进度、步计数） |
| POST | `/api/ltx/tasks/{task_id}/cancel` | 取消任务 |
| GET | `/api/ltx/outputs` | 已生成 MP4/GIF 列表 |

### MNN 运行时

`GET /api/mnn/models`、`GET /api/mnn/status`、`POST /api/mnn/load`、
`POST /api/mnn/unload`、`POST /api/mnn/chat`、`POST /api/mnn/download`、
`POST /api/mnn/download/cancel`、`GET /api/mnn/download`、`GET /api/mnn/local`。

### 模型格式转换

`GET /api/convert/capabilities`、`POST /api/convert/start`、`GET /api/convert/tasks`、
`GET /api/convert/{task_id}`、`POST /api/convert/{task_id}/cancel`
（支持 HF→GGUF/MLX/MNN/ONNX、ONNX→MNN、Torch→MNN 等）。

### 短剧工坊（drama）

`GET /api/drama/options`、`GET /api/drama/storycraft`、`POST /api/drama/brainstorm`、
`POST /api/drama/script`、`POST /api/drama/storyboard`、`POST /api/drama/render-plan`。

### 双源模型 hub（HuggingFace + ModelScope）

`GET /api/hub/sources`、`GET /api/hub/health`、`GET /api/hub/search`、
`GET /api/hub/model`、`GET /api/hub/model/files`、`POST /api/hub/download`、
`GET /api/hub/jobs/{job_id}`。

### 源调度 / 镜像测速

`POST /api/sources/measure`、`GET /api/sources/registry`、`GET /api/sources/health`、
`POST /api/sources/lock`。

---

## 8. OpenAI 兼容端点 `/v1/*`

可供 OpenAI SDK / OpenClaw 等外部框架直接对接本机已加载的 MNN 模型。

### `GET /v1/models`

```bash
curl http://127.0.0.1:17890/v1/models
```

```json
{ "object": "list", "data": [
  {"id": "qwen2.5-3b-mnn", "object": "model", "owned_by": "kevrai", "loaded": false}
] }
```

### `POST /v1/chat/completions`

OpenAI 兼容对话。支持纯文本 `messages`、多段 content（`text` / `image_url` /
`audio`），以及 `stream=true` 的 SSE 流式返回。

```bash
curl http://127.0.0.1:17890/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "kevrai-mnn",
    "messages": [{"role": "user", "content": "你好，介绍下你自己"}],
    "max_tokens": 512,
    "temperature": 0.7,
    "stream": false
  }'
```

```json
{
  "id": "chatcmpl-...",
  "object": "chat.completion",
  "model": "kevrai-mnn",
  "choices": [{"index": 0, "message": {"role": "assistant", "content": "..."}, "finish_reason": "stop"}],
  "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
}
```

> 注意：未加载任何 MNN 模型时，`/v1/chat/completions` 没有可用后端；
> 请先通过 `/api/mnn/load` 或 UI 加载一个模型。`max_tokens` 上限 4096，
> `temperature` 范围 0–2。

---

## 快速自检清单

```bash
curl http://127.0.0.1:17890/api/health        # sidecar 活着？
curl http://127.0.0.1:17890/api/categories     # 分类加载正常？
curl http://127.0.0.1:17890/api/gpu            # GPU 探测正常？
curl http://127.0.0.1:17890/v1/models          # OpenAI 兼容面可用？
```

更多排查见 [TROUBLESHOOTING.md](TROUBLESHOOTING.md)，开发环境搭建见
[DEVELOPMENT.md](DEVELOPMENT.md)。
