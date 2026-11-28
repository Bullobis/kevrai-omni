# Kevrai Agent 执行方式

> 这是产品行为说明，不是安全白皮书。三档执行方式都**不是操作系统级沙箱**。

## 三档选择

| 模式 | 能做什么 | 不能做什么 | 适合谁 |
|---|---|---|---|
| `none` 不执行命令 | 读写固定工作区文件、看目录、搜索文本 | 运行测试、运行任意命令 | 只想让 Agent 看代码/改文案的人 |
| `test_runner` 测试运行器 | 运行白名单测试命令：`pytest`、`python -m unittest`、`python -m compileall`、`npm test`、`npm run lint`、`cargo test/check`、`go test`、`ruff check`、`mypy` 等 | 任意 shell、`python -c`、`npm run deploy`、绝对路径参数 | 信任当前工作区、需要跑回归的人 |
| `approval_shell` 完整 Shell（无隔离） | 任意命令；每条命令先展示、再等待确认 | 自动放行；网络/进程/文件系统隔离 | 明确知道风险、临时排障的开发者 |

默认是 `none`，并且未选择时状态条会明确写“未选择（默认不执行命令）”。第一次开启「代码工作区」技能会弹出三档选择面板；选择入口常驻在 Agent 顶部，不在高级设置里。

## 边界

- 固定工作区：`APP_ROOT/agent/workspace`。不接受绝对路径、`..`、`~`、Windows 盘符路径；symlink 解析到工作区外会被拒绝。
- 写文件、编辑文件、`edit_lines`、完整 Shell 都标了 `requires_confirmation=True`。
- 工具审批有三档用户可见选择：
  - `confirm` 全部需确认：每次工具调用都询问。
  - `auto` 自动确认（推荐）：普通工具直接执行，敏感工具仍然弹确认。
  - `no_confirm` 无需确认（高风险）：所有工具直接执行，连敏感工具的强制确认也跳过；选择时会二次确认风险。
- 全局 `auto` 下，写工具仍然必须弹确认；全局 `deny` 是内部/单工具级拒绝档，不放进用户三档选择。
- 执行模式存在 `APP_ROOT/agent/workspace.json`，由用户侧 API/IPC 写入；模型没有修改模式或给自己提权的工具。
- 命令执行有超时（默认 120 秒，最大 300 秒）、输出上限 64 KB、进程组 kill、环境变量清洗（去掉 key/token/secret/password 等）和 JSONL 审计日志。
- `read_file` 支持 `with_line_numbers=true` 返回行号，方便按行编辑。
- `edit_file`（按原文片段）和 `edit_lines`（按 1-based 行号，支持 replace / delete / insert_before / insert_after）都能只改局部；`edit_lines` 可带 `expected_text` 防止行号漂移。
- `edit_file` / `edit_lines` / `write_file` 返回 `additions`、`deletions`、`diff`；思考过程里的工具卡显示“已编辑文件 xxx · 第 N-M 行 +N -M”，可展开完整 diff。
- `test_runner` 仍然执行项目代码，`approval_shell` 没有任何 OS 隔离。路径守卫和审批只能降低误操作，不能隔离网络、进程或文件系统。

## 已知未做

- 沙箱 Shell（容器/命名空间/只读挂载）暂不做。
- 并行子 agent、上下文压缩、会话级 worktree 尚未落地。
- Windows `.cmd` 启动、跨平台进程组细节还需要在 Windows 实机补测。
