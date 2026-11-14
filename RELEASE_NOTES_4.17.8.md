# Kevrai Omni v4.17.8 发布说明

> 基线：v4.17.7。本版是**安全性 / 稳定性补丁版**，不改变产品形态。
> 适用平台：Windows x64、macOS Intel / Apple Silicon、Linux x64。

## 一句话总结

修掉 10 类可复现的安全/稳定性缺陷（工作区递归删除路径穿越、插件 `git clone` 参数注入、
`web_search` 非法超时崩溃、MCP 无超时挂死、审批可跨会话解析、下载 DNS 重绑定、
外部内容提示注入、PowerShell 路径逃逸等），补齐 `should_stop`、SSE 错误终止帧、
插件模块卸载、首次启动端口等待、大屏缩放与搜索空态，并把文档里与 4.x 代码不符的
版本号、认证模型、端口与 Docker 启动命令全部校正。

## 安全

| 编号 | 问题 | 影响 | 修复 |
|---|---|---|---|
| S-1 | `kevrai:workspaces:remove` 只挡 `default` 与当前 active，`id="../../x"` 经 `path.join` 后被 `rmSync` 递归删除 | 任意目录被删（P0） | id 必须是 `ws_`+12 位十六进制且存在于工作区列表；`workspaces.json` 载入时过滤畸形 id；`workspaceDir`/`switch`/`rename` 统一断言 |
| S-2 | 插件安装把仓库地址直接交给 `git clone`，以 `-` 开头会被当作选项（如 `--upload-pack`） | 本机命令执行 | 拒绝前导 `-` 与控制字符，clone 参数加 `--` 分隔符 |
| S-3 | `kevrai:measure-sources` 主进程不复校，任意 payload 透传 | 越权探测面 | 主进程复用 `normalizeMeasureArgs`：仅 http(s)、最多 32 条 |
| S-4 | 审批 resolve 只按 `request_id`，`edited_params` 可直接替换实际执行参数 | 跨会话批准 / 参数篡改 | `approve`/`reject` 增加 `session_id` 归属校验，不匹配返回 403；渲染层始终携带当前会话 |
| S-5 | `set_preference` 可写 `tool_approval_policy::` 前缀 | 静默把审批档位改回 `auto` | 保留键拒绝写入 |
| S-6 | 引导下载跟随 30x 不校验目标主机 | 镜像被劫持可替换待执行的 Python 运行时 | 重定向仅允许 https + 受信厂商域名后缀 |
| S-7 | `envUpgrade` 的 pip 镜像未校验 https | 明文索引可被投毒 | 与 `envInstall` 一致，强制 `https://` |
| S-8 | 下载只校验字面 IP，不校验 DNS 解析结果 | 恶意域名解析到 `127.0.0.1` / 云元数据实施 SSRF | 每个重定向 hop 连接前 `getaddrinfo` 解析，拒绝私有/回环/链路本地/保留地址；字面 loopback 保持可用 |
| S-9 | 网页摘要、知识库文档原样拼进 ReAct 上下文 | 外部内容里的“忽略之前指令 / Action: …”可 steering 下一轮 | 系统提示声明外部内容不可信；`web_search` 观察值加 `UNTRUSTED_EXTERNAL_CONTENT` 边界；知识库提示加“仅作事实参考” |
| S-10 | PowerShell 解压路径用单引号拼接 | userData 路径含 `'` 时可逃逸出 `-LiteralPath` | 单引号按 PowerShell 规则双写转义 |

## 稳定性

- **`web_search`**：`timeout` 为 NaN/∞/字符串/负数时不再抛异常中断整个 Agent 回合；
  统一钳制 0.5–30s，查询串压缩空白并限长 400。
- **搜索结果清洗**：剥离上游摘要中的 HTML/`<script>`、反转义实体、URL 归一化去重，
  避免标签与重复条目进入模型上下文。
- **MCP**：`call_tool` 与 `list_tools` 都加 60s 超时，坏服务器返回可读错误而不是冻结
  同步 Agent 循环。
- **规则模式**：5 处直接 `registry.execute` 改为统一走审批闸门，`confirm`/`deny`
  在无 LLM 的规则模式同样生效。
- **媒体工具**：`num_images` 钳制到 1–8；产物文件名加 6 位随机后缀，避免同毫秒并发互相覆盖。
- **`open-path`**：改为在 `normalize` 之前拒绝字面 `..` 段，原检查永远不成立且误伤 `a..b.txt`。
- **Transformers 大脑**：`TextBrainManager.answer()` 补上 `should_stop` 形参并接
  `StoppingCriteriaList`；此前 `ModelRouter.chat` 恒传该参数，Transformers 后端每轮
  都 `TypeError` 被吞成“LLM 调用失败”。
- **SSE 终止帧**：`/v1/chat/completions` 与多模态流只在成功结束时发 `[DONE]`；失败改为
  `event: error` 帧后 `return`，客户端不会再把错误当完成。
- **下载重定向**：规范化 URL 做循环检测，超过 5 个重定向主机直接拒绝，不再烧完 10 次连接。
- **插件生命周期**：停用时从 `sys.modules` 移除 `kevrai_plugin_<id>`，重复激活重新导入，
  不再复用旧模块的全局状态（仍非沙箱，插件与主进程同权限）。
- **首次启动**：spawn sidecar 前 `await waitForPortFree()`，避免上一实例残留端口耗光重启预算。
- **搜索首页**：首屏结果也按 `hub:repo` 去重，和翻页逻辑一致。

## 界面 / 大屏

- 设置 → 外观新增「界面缩放」：90% / 100% / 110% / 125% / 150%，主进程侧钳制
  80%–175%，选择持久化并在启动时恢复。
- 新增 1600 / 2200 / 3000px 三档网格几何：4K 下卡片最小宽度 280→380px、行高
  168→220px；compact 密度单独覆盖，避免“小方块墙”。
- 修复模型详情面板竞态：A→B 快速点选时，A 的迟到响应不再覆盖 B 的仓库/引擎/安装按钮。
- 修复 Agent 会话切换竞态：A 会话的迟到消息不再填进 B 会话容器。
- 搜索空态补齐：首屏零结果、仅收藏/筛选为空都有明确引导；分面计数统一转义；
  虚拟网格卡片 `overflow:hidden`，长文案不再压到下一行。
- 新增 `renderer/styles/large-screen.css` 的 ≥1600px 布局与 4.17.8 网格缩放并存，
  超宽屏下详情列与卡片密度分别调优。

## 文档

- `API.md`：按 4.x 实际行为重写认证章节——除 `/api/health` 外强制 Bearer secret，
  WebSocket 支持 `?token=`，未配置 secret 时 fail-closed。
- 纠正 `KEVRAI_PORT`：端口是主进程常量 17890，该变量只透传、不可配置。
- 修正 Docker 示例：`app.main` 无 `__main__` 入口，改用 `uvicorn app.main:app`，
  并说明容器内必须自设 `KEVRAI_SIDECAR_SECRET`。
- 同步 `ARCHITECTURE.md` / `DEPLOYMENT.md` / `FAQ.md` / `DEVELOPMENT.md` /
  `COVERAGE.md` 的版本头与示例；官网下载页更新到 v4.17.8 并按实际资产更新体积。
- 新增本文件 `RELEASE_NOTES_4.17.8.md`。

## 验证

- Python：**2686 passed**；唯一失败 `test_missing_engine_raises` 是本机预装 RapidOCR
  导致测试的环境假设不成立，干净 CI 通过。
- Renderer：**440 passed**（新增搜索首页去重用例）；JS 语法 72 个文件 0 失败。
- 针对性回归：`test_v4178_hardening`（should_stop、DNS 重绑定、不可信内容标记、
  SSE 终止帧）、`test_v4175_plugins`（模块卸载）、`test_web_tools` / `test_v540_mcp` /
  `test_agent_approval` / `test_tools_input_validation` 全部通过。
- `scripts/smoke.sh`：catalog/schema/allowlist/secret 扫描 + 全量 pytest + JS 语法。

## 已知限制

- 自动更新仍未强制代码签名（仓库使用自签名构建，`forceCodeSigning` 会让无证书
  的本地构建直接失败）。信任锚仍是 GitHub HTTPS + `latest.yml` 的 SHA512。
- 媒体工具仍接受任意绝对路径的本地音/图/视频输入（只做了存在性、数量与命名约束）；
  若需要“仅允许数据根目录”，需要产品层先定义“用户授权目录”交互。
- 审批 pending 仅存内存：重启后进行中的请求随进程消失，按“未批准”处理。
- 插件与 sidecar 同进程执行，停用只做模块卸载，不构成沙箱。
- Python 核心的 `backup` / `importer` / `webdav_sync` 本轮做了路径与原子写复核，
  未做真实 WebDAV 端到端联调。
- 构建未做代码签名，Windows SmartScreen / macOS Gatekeeper 可能提示“未知开发者”。
