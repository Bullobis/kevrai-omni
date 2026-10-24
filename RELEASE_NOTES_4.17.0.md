## Kevrai-omni v4.17.0

开源本地 AI 工作台：Electron 33 + 原生 HTML/CSS/JS + Python FastAPI sidecar。本版集中修复实机安装链路的一批硬伤，并新增 Ming TTS 引擎。

### 实机安装链路修复

**引擎安装进度条终于能动了**
- 以前 /api/engines 只回静态字段，pip 引擎（piper、faster-whisper 等）安装时前端轮询拿不到任何进度，按钮一直干等。
- 现在后端把 active_url / progress / downloaded_bytes / total_bytes / state 实时吐出来，pip 安装过程会显示"正在下载/安装 xx 包…"，二进制下载显示百分比和已下/总 MB。

**pip 引擎装不上（unsupported kind: ''）**
- 环境页手动装包、MNN 安装两处前端漏传 kind:"pip"，后端一律 400。已补。

**大脑设置空选项报错**
- 大脑下拉切回"规则"空选项时，前端不再误调后端，直接提示。

**检查更新网络错误不再弹窗**
- ERR_NETWORK_ACCESS_DENIED 这类网络问题只在状态行显示，不再弹错误 toast 打扰。

**魔搭不再被误隐藏**
- 网络探测慢/失败时，以前会直接把 ModelScope 源从搜索里删掉。现在只弹一次提示，魔搭源保留，你仍可手动勾选搜索。

### 模型市场

**所有模型都能下载了**
- 以前没匹配到引擎的模型会被"未指定引擎"挡回，只能手动导入。
- 现在这类模型直接走自动挑文件下载；GGUF 模型照旧可选量化档（Q4_K_M、Q5_K_M 等）。

### 新引擎：Ming-3.0-tts-0.5B-MNN

- 蚂蚁 inclusionAI 开源的 CPU 友好中英文 TTS，0.5B 量级，int8 档约 1.5GB。
- 支持语气指令控制（instruction）、中英文混合、44.1kHz 输出。
- 通过 ming-tts-mnn pip 包一键安装，模型走 hf-mirror 镜像下载。
- 路由 POST /api/tts-ming/synthesize。

### 验证

- 后端 engine/catalog/tts 相关 479 项测试通过
- 前端 71 个 JS 文件语法检查全过
- mypy 未引入新错误

> 注：Ming TTS 在低内存机器（<8GB）首次加载较慢，属正常现象。
