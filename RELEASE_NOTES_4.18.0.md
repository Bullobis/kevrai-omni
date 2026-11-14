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
