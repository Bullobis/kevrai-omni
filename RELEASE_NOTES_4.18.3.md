# Kevrai Omni v4.18.3

> 2026-11-28 重发版本，替代 2026-11-27 的首发构建。本版本由 GitHub Actions 原生构建并发布 Linux AppImage/deb、Windows NSIS 安装器 + 便携 zip、macOS dmg（x64 + arm64）。安装包内置冻结 sidecar 与 pip-runner。

## 重点变化

- **Agent「深度思考」卡**：Agent 每一步原有的 `thought` 不再被丢弃；执行时实时展开灰色推理文本和工具调用，回答开始流式输出后自动收起。收起态保留两行、190 字以内的最新推理预览，点「点击展开」看完整推理；手动开合不会被后续步骤强行覆盖。浅色、深色主题均已适配，收起/展开两端都在打包后的应用里做了界面验证。
- **引擎与模型彻底解耦**：模型市场卡片同时提供「安装引擎 / 下载模型」两个独立按钮。引擎未安装时，下载按钮明确提示「引擎 X 未安装，请先点『安装引擎』」，不再自动替用户安装；引擎已安装后按钮变「引擎已安装」，下载入口始终保留。多引擎模型先选引擎，所有模型下载前先选仓库版本，GGUF 再选量化版本。
- **ModelScope 仓库版本修正**：按模型来源选择 `modelscope` 或 `hf`，ModelScope 默认 revision 为 `master`（HF 仍为 `main`）；修复 ModelScope 模型 404 与 revision 查询红 toast。
- **MNN 模型权重漏选修复**：自动挑文件补齐 `.weight`、`llm_config.json`、`llm.mnn.json`、`configuration.json`、`tokenizer.mtok`，不再只下载 1.7 MB 的 `llm.mnn` 而漏掉 365 MB 的 `llm.mnn.weight`。
- **MNN 下载完成态轮询递归修复**：完成/取消/失败先隐藏进度卡，停止 `pollDownload → refreshAll → pollDownload` 递归；完成提示只弹一次；只有无 `.part` 的文件才判定完整，取消后再次下载可续传。

## Agent 能力加强

- **三档执行方式，用户显式选择**：`不执行命令` 只能读写工作区文件；`测试运行器` 只运行 `pytest`、`npm test`、`cargo test` 等白名单命令；`完整 Shell` 可运行任意命令，但必须先勾选风险确认并逐条确认。模式常驻显示在 Agent 顶部，第一次启用代码工作区时弹出完整选择面板。
- **固定代码工作区**：工作区根固定为应用数据目录下的 `agent/workspace`，拒绝绝对路径、`..`、`~`、Windows 盘符和 symlink 逃逸；单文件读写上限 1 MB，命令输出上限 64 KB，默认超时 120 秒、最大 300 秒。所有执行写入审计记录。
- **代码工作区 8 个工具**：`read_file`、`list_dir`、`search_text`、`write_file`、`edit_file`、`edit_lines`、`run_test`、`run_command`；启用后 Agent 可用工具从 16 个增加到 24 个。
- **行级编辑 `edit_lines`**：支持 1-based 闭区间 `replace`、`delete`、`insert_before`、`insert_after`，可用 `expected_text` 防止行号漂移；越界、空文件替换、空插入和超过 1 MB 的结果都会被拒绝。返回 `additions`、`deletions` 与 unified diff。
- **工具审批三档**：`全部需确认` 每次工具调用都询问；`自动确认（推荐）` 普通工具直接执行、敏感工具仍确认；`无需确认（高风险）` 连写文件与完整 Shell 也直接执行。后端仍保留内部 `deny` 档，不暴露在用户选择中。
- **编辑结果可见**：`edit_file`、`write_file`、`edit_lines` 的工具卡按真实工作区相对路径显示“已编辑文件 … · 第 N-M 行 +N -M”，可展开 diff，不再只显示文件名。

## 验证

- Python 全量：**2830 passed，7 warnings，0 failed**（1081 秒）。
- renderer 全量：**469 pass，0 fail，0 skipped**（546 秒）。
- 代码工作区与审批专项：`test_agent_workspace.py` + `test_agent_approval.py` **38 passed**；`agent-execution-dom.test.js` **7 passed**；JS 语法检查 74 个文件通过。
- 打包后的 `.deb` 与 AppImage 均确认 `app.asar` 内包含深度思考卡；在无 FUSE 环境用 AppImage 解包运行，Agent 页折叠/展开与预览样式实际渲染正常。
- 安装版 `.deb` 实际启动后逐项切换三档工具审批，`confirm → confirm`、`no_confirm → no_confirm`、`auto → auto`，API 均返回 200；界面截图确认下拉框显示“全部需确认 / 自动确认（推荐）/ 无需确认（高风险）”。
- MNN 引擎真实安装、Hunyuan-0.5B-Instruct-MNN 真实下载 11/11 文件（0.37 GB），加载后真实对话返回 10.83 秒 / 37.2 字/秒。
- 实机复跑还验证了系统包安装路径、模型市场首屏 21 张卡、未装引擎拦截提示、ModelScope 文件 200 响应。

## 已知限制

- 测试运行器与完整 Shell 只是**执行权限分层**，不是沙箱：没有网络、进程或文件系统隔离。完整 Shell 仍会执行项目代码，只应在可信项目中使用。
- 测试机无 GPU、3.9 GiB 内存，只验证 0.5B 级模型；Windows 与 macOS 安装包由 GitHub Actions 构建，未在本机实机运行。
- 直接 `dpkg -i` 在缺少 `libnotify4`、`libsecret-1-0`、`libsecret-common` 的干净系统上需要先补依赖；应用本体不受影响。
- 本轮未覆盖 TTS、ASR、向量、分离、多模态、LTX、图片、3D 等页面的逐页真实生成。
