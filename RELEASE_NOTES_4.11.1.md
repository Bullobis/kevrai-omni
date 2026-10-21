# Kevrai Omni v4.11.1

聚焦一件事：**让"选引擎"真正生效**。

## 修掉的严重逻辑问题

### 1. `default_engine_id` 是死设置（已修）

```
settings.py:85   default_engine_id = "llama.cpp"    ← 定义
main.py:605      default_engine_id: str | None      ← 允许用户改
                 （原来：全仓库再无任何一处读取它）
```

用户能在设置里改默认引擎、能持久化、测试也覆盖了往返，**但没有任何
runtime 消费它**。根因不是忘写，而是引擎从来没被"解析"过——
`llm_runtime` 与 `mnn_runtime` 是两套独立端点，选引擎 = 调哪个端点，
中间缺一层按设置路由的决策。

新增 `python/app/engine_router.py` 作为**单一真相源**，优先级：

```
显式选择 > 设置里的 default_engine_id > catalog 顺序第一个
```

### 2. 显式选了不可用的引擎会静默跑别的引擎（已修）

现在是 `EngineResolveError` 直接报错。用户选了 A 却静默跑 B，比直接失败
更糟——他以为看到的是 A 的行为，排查方向全错。

### 3. `diffusers` / `vllm` / `comfyui` 等被当作"能跑"（已修）

catalog 里 259 个模型的引擎分布：

| 引擎 | 数量 |
|---|---|
| llama.cpp | 93 |
| diffusers | 51 |
| vllm | 49 |
| transformers | 38 |
| comfyui | 33 |
| sglang | 29 |
| mnn | 15 |
| onnxruntime | 9 |
| ollama | 8 |
| 其他 | 34 |

只有 **llama.cpp（93）+ mnn（15）= 108** 能映射到应用内加载端点。
其余 151 个现在会在详情面板明确显示"当前没有应用内加载端点"，
不再让用户下载完才发现跑不起来。

### 4. 对话历史 `content[:500]`（上一轮已修）

只留头、不留尾、不留标记——工具结果的结论在末尾被整体砍掉，且模型
无从察觉被截断。已由 `app/agent/history.py` 重写：保留头尾 + 显式
`…[已截断 N 字]…` 标记 + 防二次裁剪守卫；并修正"首条必须是 user"、
"结尾不留半轮"两条不变式。

## 新增 / 改动

**新增**
- `python/app/engine_router.py` — 引擎解析与路由
- `python/app/agent/history.py` — 历史块整理与裁剪
- `renderer/modules/engine-picker.js` — 安装时自选引擎（4.11.0）
- `renderer/modules/engine-route.js` — 路由结果显示
- `python/tests/test_engine_router.py` — 20 项
- `python/tests/test_agent_history.py` — 19 项
- `renderer/__tests__/engine-route.test.js` — 12 项
- `renderer/__tests__/engine-picker.test.js` — 18 项

**改动**
- `python/app/main.py` — 新增 `GET/POST /api/engine/route`；
  `/api/mnn/load` 增加 `engine` 字段，不匹配时 409 拒绝
- `python/app/agent/agent.py` — 历史块走 `history.build_history_block`
- `renderer/modules/models.js` — 详情面板显示路由信息
- `renderer/styles.css`、`locales/*.json` — 样式与文案

## 测试

- JS 全量 **430 通过**（新增 30）
- Python 相关回归 **747 通过**
- 已知基线失败 1 项：`test_v411_piper_runtime::test_route_engine_missing`
  返回 422 而非 503 —— pydantic 2.14 的 schema 差异导致请求体校验没过，
  端点逻辑根本没执行。与本版改动无关，未修。

## 打包

沙箱是 Linux，**无法产出 Windows .exe 安装包**（electron-builder 打
Windows 包需要 Wine + Electron 二进制，沙箱不具备）。

依赖已确认齐备，你在本机执行即可：

```
双击 一键打包.bat
```

脚本会产出（build\output）：
- `Kevrai-Omni-4.11.1-x64.exe` 安装版
- `Kevrai-Omni-4.11.1-x64.zip` 绿色版
- `latest.yml` 自动更新元数据

`package.json` 与 `python/app/__init__.py` 均已升到 **4.11.1**。
