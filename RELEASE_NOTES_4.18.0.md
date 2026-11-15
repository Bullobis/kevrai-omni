# Kevrai Omni v4.18.0 源码更新

> 这是源码版本说明。v4.18.0 没有生成安装包、没有创建 GitHub Release；最近一次已发布的安装包仍是 v4.17.9。

## Agent 工作区重构

- Agent 页改为单一会话壳：顶部标题 / 状态 / 会话操作，中间 transcript，底部 docked composer。
- 用户 turn 用左侧细线区分，不再用高饱和聊天气泡；助手回复保持正文，工具过程默认折叠为过程组。
- 空态改为内容型，只保留标题、说明和三个可执行入口：检测这台电脑、推荐可跑模型、查看本地模型。点击只填入输入框，不自动发送。
- 状态从填充式徽标改为 quiet status line（圆点 + 文本），基础模式 / 就绪状态使用语义色。
- 移除 Agent UI 内的 emoji 操作标签和 emoji 技能名，统一使用字母 glyph。
- 审批卡改为检查点：
  - 显示工具名与“批准后仅执行这一次”；
  - 参数默认折叠，可按需展开编辑；
  - 焦点在卡片时 Enter 批准，Esc 拒绝；
  - 批准 / 拒绝后按钮锁定。
- 技能库展开不再把 transcript 压成窄条，页面可增长并保留可用的会话区域。

## 本轮修复的真实问题

- 审批轮询此前每秒重建 `innerHTML`，会清空用户正在编辑的 JSON 参数；现在按 pending request id 集合判断，集合未变化时保留已挂载卡片。
- 追加消息时没有移除 `.agent-welcome`，导致空态残留在 transcript 顶部；现在追加任何消息都会移除空态。
- 技能库展开时会话区被压缩到不可用宽度；现在页面可增长，布局在 1360×768、1440×900 下均可用。
- 空态 starter 点击后正确填入 composer，并聚焦输入框，不自动发送。
- 规则模式的 5 个确定性工具分支只把 step 放进结果、没有触发 step 回调，WebSocket UI 收不到过程事件；现在统一发出 step，过程组可见，并补了回归测试。
- 过程组展开后仍显示“点击展开”，以及过程组下面重复出现同一份“工具: …”列表；现在展开态显示“收起”，有过程组时不再重复工具列表。
- 侧栏导航的 `title` / `data-i18n-title` 会触发 Electron 原生黑色 tooltip；导航行已移除原生 title，保留可见标签与 aria 名称。
- Kokoro 与 Chatterbox 实际走应用内 Piper/TTS 适配器，却被 availability / engine_router 标成外部服务；现在改为本地 TTS 路径。

## 参考文档

新增 `docs/REFERENCE-UPSTREAM.md`，记录实际下载的 Cherry Studio、Codex、DeepSeek Harness 仓库 HEAD、读到的关键源码路径与许可证边界。只借鉴信息架构、交互模型和设计 token 思路，不复制 Cherry 的 AGPL 源码。

## 验证

- `npm run test:js`：72 个 JS 文件语法通过。
- `npm run test:renderer`：441/441 通过。
- `npm run smoke`：9/9 步通过；pytest `2764 passed`。
- `test_v270_agent.py`：53/53 通过；审批定向测试 7/7。
- 实机：1360×768、1440×900，浅色 / 深色主题，空态、有消息 transcript、技能库展开、审批卡预览均检查。

## 未包含

- 没有生成安装包、没有发布、没有 GitHub Release。
- 模型市场目录与 20 条应用适配器没有在本版本逐条重跑；不把“目录存在”写成“本机已能跑”。

## 测试

- `npm run smoke`：2766 passed，9/9 步通过。
- `npm run test:renderer`：441/441。
- `npm run test:js`：72 个 JS 文件语法检查通过。
- 实机复核：新侧车 `4.18.0`，21 个可见页面无 `undefined` / 原始 i18n key，导航行不再触发原生黑色 tooltip。

## 4.18.0 追加修复（本地三维与外部服务）

- **3D 不再被误判为外部服务**：`shap-e`、`instant-mesh`、`midi-3d` 同时带
  `comfyui`，此前被 availability 判成 external。现在 `diffusers` 可以覆盖
  `3d`，三条模型改由本地三维运行时执行；3D 页新增对应选项。
- **3D 路由端点**：`resolve_engine` 接受 `category`，三维模型路由到
  `/api/3d/generate`，不再错误返回图像端点；IPC 同步透传。
- **模型详情显示运行引擎**：详情页补上 `#engine-route-slot`，此前异步查询
  成功却没有任何节点可显示；现在会显示引擎、来源、实际端点和“应用内不拉起”
  的原因。
- **表单页宽度**：ASR、Embeddings、Compare、Translate、Local 等页不再被
  锁死在 940px；1440 窗口下按 `min(1180px, 100%)` 铺开。
- **外部推理服务入口**：AI 引擎页新增 vLLM / SGLang / Ollama 配置卡，可填
  地址、模型名和 API Key，检测连接并保存；模型详情对外部模型给出“配置外部
  服务”入口并预填模型名。
- **桌面图标**：圆角从 208 加到 320，保持与应用内 Logo 分离；不是把两套
  identity 混成一个。

## 4.18.0 追加修复（路由与外部服务入口）

- **本机引擎优先**：模型同时列出 `comfyui` 和 `diffusers` 时，详情页不再按
  catalog 顺序误报 ComfyUI；现在优先选择应用能进程内加载的引擎。MiniMax H3
  实测从“需要 ComfyUI”改回 `diffusers / /api/local/video`。
- **Diffusers 按类目分端点**：视频、音频、三维分别路由到
  `/api/local/video`、`/api/local/audio`、`/api/3d/generate`，不再一律返回图像端点。
- **MusicGen 路由**：详情实测 `music-runtime / /api/music/generate`，不再显示
  Transformers 对话端点。
- **POST 路由透传 category**：此前只有 GET 带 `category`，显式/默认引擎的 POST
  路径会把音频/视频送错端点。
- **图片页可配置 ComfyUI 地址**：不再只能靠环境变量；地址会持久化，并用于状态、
  checkpoint 列表与实际生成请求。
- **不再假装 ComfyUI 音乐/3D 有一键页**：这两个类目需要模型专属工作流，详情页
  现在只给“查看 ComfyUI 服务状态”和明确说明，不把用户带到一个跑不了该模型的页面。

## 4.18.0 追加修复（模型能力清单）

- **259 个模型逐个标注能力**：新增 `capability` 字段，记录任务、输入角色与是否必填、输出是否音画同出、可用控制项、来源与置信度；不再按“图片 / 视频 / 音乐”三类共用一套字段。
- **不再把 ComfyUI 节点暴露给普通用户**：ComfyUI 仍作为后台执行引擎，模型市场详情只显示普通生成软件会用的“任务 / 输入 / 输出 / 控制 / 来源”。
- **能力证据来自官方仓库**：按 Hugging Face 官方 API 的 `pipeline_tag`、仓库 tags 与模型 README 生成；无法确定的项不写进能力，未知项标为“推断”。
- **视频表单按模型变化**：SVD / Tiny SVD 只显示首帧图片，不显示提示词与负提示词；Wan 2.2 T2V 只显示文本；Wan 2.2 I2V 首帧必填；LTX-2.5 支持文生视频与图生视频。
- **修复 SVD 空 prompt 被后端拒绝**：`PROMPT_NONE_MODELS` 允许纯图生视频模型不提交提示词，避免 UI 已隐藏提示词但请求永远失败。
- **修复隐藏字段被 CSS 覆盖**：表单的 `display:grid` 曾让 `[hidden]` 失效，SVD 的提示词/负提示词实际仍然显示；已加显式隐藏规则。
- **3D 表单按模式显隐**：图生 3D 隐藏文生提示词，文生 3D 隐藏输入图片；模型本身不支持的模式保持禁用。

## 4.18.0 追加修复（建议时长）

- **每个视频模型单独声明建议时长**：Wan 2.2 T2V/I2V 5 秒，LTX-2.3/2.5 20 秒，MiniMax H3 4–15 秒，CogVideoX 6 秒，SVD 不超过 4 秒，Magi2 10 秒，HunyuanVideo 5 秒，Pyramid Flow 10 秒；来源写入能力字段，模型详情直接显示。
- **时长是软的**：超过建议时长时，视频页显示黄色提示；点击生成会弹出确认框，用户点“仍要生成”后继续提交，不硬拦。
- **帧数上限从 257 扩到 1025**：20 秒 @24fps 需要 481 帧、@50fps 需要 1000 帧；原来 257 帧会在提交前直接失败。
- **未查到的模型不编数字**：没有官方时长依据的视频模型不写建议时长，不拿类目平均值冒充事实。

## 4.18.0 追加修复（按模型能力的图片与音乐）

- **图片页接入模型市场模型**：`#image-model` 读取 `category=image` 的 capability，
  只列出输出图片且任务为文生图 / 图生图的模型；选模型后隐藏 ComfyUI 模板字段，
  走 `/api/multimodal/generate-image` 的标准 Diffusers 管线。ComfyUI 仍是可选后台，
  不再是唯一入口。
- **图生图输入**：capability 声明图片输入时显示输入图片行，支持文件选择；后端
  `generate_image` 增加 `image_path` / `strength`，用 `AutoPipelineForImage2Image`
  加载本地或 Hub 的 Diffusers 管线。图生图模型可以没有提示词。
- **负向提示词按模型显隐**：capability 没有 `negative_prompt` 的模型不再显示负向字段；
  没有 `prompt` 的图生图模型不再强制提示词。
- **音乐页按模型能力**：音乐模型返回每个模型的 `duration_max_s` 与 `supports_lyrics`；
  MusicGen 最长 60 秒、ACE-Step 最长 600 秒，时长选项随模型收敛；歌词只对
  ACE-Step 显示并传给后端。
- **音频生成 API**：`MusicGenReq` 增加 `lyrics`，路由把它透传到 ACE-Step 的
  `synthesize`。
- 测试：图片 / 音乐 / LTX 定向 90 passed；全量 renderer 450/450 通过。

## 4.18.0 追加修复（3D 模型市场与运行时路径）

- 3D 页不再只列 7 个硬编码模型：启动时读取 `category=3d` 的模型市场 capability，
  合并已适配模型与市场条目，当前可见 26 个 3D 选项。
- 3D 选项会标出“图生 3D / 文生 3D”和“市场条目，首次使用需下载权重”；
  不再把市场里不存在的模型伪装成已适配。
- 3D 运行时和通用媒体运行时补上应用内 `pip-diffusers` / `pip-transformers`
  的 `sys.path` 加载，修复“引擎页已安装、生成时 ImportError”的路径问题。

## 4.18.0 追加修复（视频页接模型市场）

- 视频页除运行时已有模型外，读取 `category=video` 的市场 capability，新增
  Open-Sora、LTX-2.3、MiniMax H3、MAGI-2、LingBot、HunyuanVideo 1.5、FramePack、
  Pyramid Flow 等文本/图片驱动模型；当前共 19 个视频选项。
- 市场模型的模式、提示词、负向提示词、首帧图片和时长提示由 capability 生成；
  音频/视频必填的模型暂不伪装成已适配，避免表单给出假字段。

## 4.18.0 追加修复（音乐市场模型与 3D 表单）

- 音乐页读完 6 个运行时模型后，继续读取市场里可走通用 Diffusers 音频管线的
  模型：Stable Audio Open、DiffRhythm、Stable Audio 3 Medium、YuE2 等；
  当前音乐页 12 个选项。市场模型按 120 秒上限显示，不显示虚假歌词字段。
- 通用音频生成在返回结果里附带 `data_url`，音乐页可以直接试听和下载，
  不会再出现“生成成功但没有可播放音频”的情况。
- 3D 引擎未安装时不再把整个表单隐藏：模型选择、图生/文生模式和字段仍然可见，
  安装提示卡单独显示。

## 4.18.0 追加修复（3D 运行时安装入口）

- 3D 引擎缺失卡片新增“安装 3D 运行时”按钮，安装应用内 Diffusers/Transformers
  引擎后自动重新检测；不再只给一条 `pip install` 命令让用户自己复制。

## 4.18.0 追加修复（MiniMax 音乐与 3D 专用运行时）

- MiniMax Music 3 不再被误当成 MusicGen。按官方模型卡改用 Diffusers
  `ModularPipeline`，支持 `lyrics + prompt + audio_duration`，最长 300 秒；
  修正了错误的仓库名（`MiniMaxAI/MiniMax-Music3`）。
- 3D 运行时新增官方适配：Hunyuan3D-2（`hy3dgen.shapegen`）、TRELLIS
  （`trellis.pipelines`）、Direct3D-S2（`direct3d_s2.pipeline`）。
  这些仓库没有 `model_index.json`，原先用通用 Diffusers 加载会失败；
  现在按官方 README 的入口调用，缺少官方代码时给出明确的安装命令。
- 3D 测试中用于 stub 的模型 ID 改为非厂商 ID，避免把真实 TRELLIS
  适配路径绕回通用管线。

## 4.18.0 追加修复（3D 可用性判定）

- 3D `capabilities()` 不再要求必须安装 diffusers：只要 torch + pillow 加任意
  一个官方 3D 包（hy3dgen / trellis / direct3d_s2）就认为可运行，并返回
  `vendor_backends` 详情；避免装好 Hunyuan3D 却仍被页面判成“引擎未就绪”。

## 4.18.0 追加修复（Pixal3D 专用入口）

- Pixal3D 按官方 README 使用 `inference.py --image ... --output ... --low_vram`，
  不再走通用 Diffusers（该仓库没有 model_index.json）。

## 4.18.0 追加修复（视频音频/视频输入）

- 视频页不再把音频/视频必填的模型直接删除；当前 22 个视频选项。
- 选中 `Wan2.2-S2V` 等模型时显示音频输入行，选中 Animate 类模型时显示视频输入行；
  提示词可选时不再被后端 `min_length=1` 硬拦。
- `LtxGenerateReq` / `LtxParams` 新增 `audio_path`、`video_path`；运行时按管线
  签名传入 `audio` / `audio_path` 或 `video` / `video_path`，不接受就明确报错。

## 4.18.0 追加修复（3D 官方入口与不可用声明）

- 新增 TRELLIS.2（`trellis2.pipelines.Trellis2ImageTo3DPipeline`）与
  TRELLIS-text（`TrellisTextTo3DPipeline`）专用调用。
- 新增 PartCrafter / TripoSG / Stable Fast 3D / Pixal3D 官方 CLI 适配；
  源码目录统一放在 `<data_root>/third_party/<官方仓库名>`，缺失时给出明确目录，
  不再用通用 Diffusers 猜。
- `/api/models` 返回每个模型的 availability；3D 页对没有本地适配的条目显示
  “本地暂无适配，仅收录展示”，并阻止直接提交，避免“有按钮但跑不了”。
- `HunyuanWorld` / SAM-3D / 4DAnyone 尚未接官方推理，已从 native 声明中移除。
