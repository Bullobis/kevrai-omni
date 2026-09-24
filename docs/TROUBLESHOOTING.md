# TROUBLESHOOTING.md — 故障排查

> 按症状分类的排查树。每条都指向日志位置 / 具体代码路径。
> 面向用户的 Q&A 见 [FAQ.md](./FAQ.md)；部署步骤见 [DEPLOYMENT.md](./DEPLOYMENT.md)。

## 目录

- [0. 先收集日志](#0-先收集日志)
- [1. 启动类](#1-启动类)
- [2. 连接类](#2-连接类)
- [3. 下载类](#3-下载类)
- [4. 生成类](#4-生成类)
- [5. 性能类](#5-性能类)
- [6. 常见错误码](#6-常见错误码)
- [7. 重置方法](#7-重置方法)

---

## 0. 先收集日志

### 0.1 日志文件位置

主进程 + sidecar 日志都写在用户数据目录下：

| 平台 | 路径 |
|---|---|
| Windows | `%APPDATA%\KevraiOmni\logs\main.log` |
| macOS | `~/Library/Application Support/KevraiOmni/logs/main.log` |
| Linux | `~/.local/share/KevraiOmni/logs/main.log` |

滚动策略（`electron/main.js`）：`main.log` 写满后 → `main.log.1` → `main.log.2` → 丢弃 `.2`。

sidecar 的 stdout/stderr 被主进程前缀 `[sidecar]` / `[sidecar-stderr]` 写进同一个文件；stderr 尾部在内存里保留最近 80 行（`sidecarStderrTail`），崩溃诊断时用。

### 0.2 打开 debug 日志

- **sidecar HTTP debug**：设置里把 `debug_http_logs` 打开（`Settings.debug_http_logs`），保存后重启 sidecar。
- **Electron 渲染进程日志**：开发模式 `npm run dev` 自动开 DevTools；生产模式在窗口里按 `Ctrl+Shift+I`（macOS `Cmd+Option+I`）尝试打开 DevTools（仅 debug 构建保证可用）。
- **Node/Electron 主进程日志**：`npm run dev` 带 `ELECTRON_ENABLE_LOGGING=1`。

### 0.3 健康检查

浏览器直接访问：

```
http://127.0.0.1:17890/api/health
http://127.0.0.1:17890/docs
```

正常返回 `{"ok": true, "version": "...", "models_dir": "...", "app_root": "..."}`。打不开说明 sidecar 没起来。

---

## 1. 启动类

```
App 启动
├─ 窗口闪一下就退
│   ├─ 看 main.log 末尾
│   │   ├─ "sidecar NOT ready: ..." → sidecar 没起来（见下）
│   │   ├─ "bootstrap failed: ..." → Electron 初始化挂了
│   │   └─ 无日志 → 进程根本没起来：看安装是否完整、杀毒是否删文件
│   └─ 弹错误框 "Python sidecar failed to start"
│       ├─ stderr 含 ModuleNotFoundError / No module named
│       │   → 进「环境准备」页一键补依赖（主进程会自动跳这个页）
│       └─ 其他 stderr → 手动复现：cd python && python -m uvicorn app.main:app --port 17890
├─ 窗口出来但白屏
│   ├─ DevTools console 报错
│   │   ├─ CSP 报错 → 检查 connect-src 是不是被改了
│   │   └─ 模块加载失败 → renderer 文件缺失，重装
│   └─ sidecar 健康检查失败 → 走 [连接类]
└─ 首次启动卡在「环境准备」
    ├─ 下载 Python 慢 → 换网络/手动下 python-3.12.7-embed-amd64.zip
    └─ 装依赖失败 → 换 pip 镜像（腾讯/阿里/清华）
```

**关键代码路径**：`electron/main.js` 的 `bootstrap()` → `waitForSidecar()` → 失败时正则匹配 `/ModuleNotFoundError|No module named|ImportError/` 决定进引导页还是直接退出。

---

## 2. 连接类

```
sidecar 连不上（UI 转圈 / "sidecar:down"）
├─ 17890 端口被占
│   ├─ Windows: netstat -ano | findstr :17890
│   ├─ Linux/macOS: lsof -i :17890
│   └─ 占用方关掉，或设 KEVRAI_PORT=17891 重启
├─ sidecar 崩了自动重启 3 次后放弃
│   ├─ main.log 里搜 "sidecar exited code=" / "restart budget"
│   ├─ 看 [sidecar-stderr] 末尾 80 行
│   └─ 手动起 sidecar 复现（见 FAQ Q7）
├─ 健康检查超时
│   ├─ 首次启动模型目录扫描慢 → 等 30s
│   └─ 磁盘满 / 用户数据目录不可写 → 清磁盘
└─ 防火墙拦了回环
    └─ 罕见；临时关防火墙验证
```

---

## 3. 下载类

```
模型/引擎下载失败
├─ 网络超时 / 401
│   ├─ 401 + gated 提示 → 先在 HF 页面同意协议，再填 token
│   ├─ 超时 → 设置里确认 hf-mirror.com 已启用；lock_source 锁定快源
│   └─ 502 "GGUF 仓库枚举失败" → HF/ModelScope 上游抽风，重试
├─ 下载中断
│   ├─ 支持断点续传：.partial 文件保留着，重新点下载会接着传
│   ├─ 服务器不支持 Range（回 200 而非 206）→ 会从头重下，属预期
│   └─ 磁盘满 → 换盘/清理；max_model_size_gb 默认 200
├─ 下载完但校验失败
│   ├─ 部分任务带 sha256 校验；失败会保留 .partial 供排查
│   └─ 重新下载
├─ 速度一直为 0
│   ├─ 源健康冷却：连续失败 3 次会冷却 300s（cooldown_seconds）
│   ├─ 设置里解锁源 / 换源
│   └─ 测速缓存 TTL 300s，等一会或重启 App
└─ 导入本地模型报 400
    ├─ "invalid path" / "path does not exist" → 路径错/不存在
    ├─ "refusing to import this directory" → 路径被安全策略拒
    └─ "invalid path: UnsafePathError" → 含 .. / 非法字符 / Windows 保留名
```

---

## 4. 生成类

```
生成任务报错
├─ LLM（llama.cpp / MNN）
│   ├─ 模型没加载 → 先在引擎页点「加载」
│   ├─ 显存/内存不足 → 换小量化档或小模型
│   └─ 模型文件损坏 → 重下
├─ 视频（LTX-2.5）
│   ├─ CUDA out of memory → 降预设（quality→balanced→speed→fast）
│   ├─ torch 没装 → 引擎首次运行会拉，等它装完
│   ├─ torch.cuda.is_available()=False → 见 FAQ Q14
│   └─ 帧数/步数超 VRAM 档 → 降 num_frames / num_inference_steps
├─ 图片/音频/3D
│   ├─ 对应引擎没装 → 引擎页安装
│   ├─ 依赖缺失 → 环境准备页补装
│   └─ 模型文件缺失 → 模型市场重下
└─ Agent 不响应
    ├─ ReAct 循环触顶（max iterations）→ 换个问法/清会话
    ├─ 工具报错 → 看 [sidecar] agent 日志的 observation
    └─ 会话损坏 → 删 SQLite 里该会话（见 §7）
```

---

## 5. 性能类

```
App 卡 / 慢
├─ 主进程内存高
│   └─ NODE_OPTIONS 上限 2048 MB；关其他窗口
├─ 下载占满带宽
│   └─ 设置里 max_concurrent_downloads（默认 3，上限 16）调小
├─ 生成时风扇狂转
│   ├─ CPU 模式跑大模型必然如此 → 上 GPU / 换小模型
│   └─ enable_model_cpu_offload 开了会更慢但省显存
├─ UI 卡顿
│   ├─ 模型网格用虚拟滚动（virtual-grid.js），正常
│   └─ 大模型目录下搜索慢 → 用分面/模糊搜索
└─ 磁盘 IO 高
    └─ .partial 续传文件在模型盘上；SSD 上正常
```

---

## 6. 常见错误码与含义

来自 sidecar 实际返回（`python/app/main.py`）：

| HTTP | detail 片段 | 含义 | 处理 |
|---|---|---|---|
| 400 | `invalid path` / `path does not exist` | 导入路径非法/不存在 | 换纯英文无空格路径 |
| 400 | `refusing to import this directory` | 安全策略拒了该目录 | 别导入系统目录 |
| 400 | `unsupported kind: ...` | env install 的 kind 参数错 | 正常使用不该遇到 |
| 400 | `invalid package name` / `invalid version` | pip 安装参数非法 | 正常使用不该遇到 |
| 400 | `mirrors must be list of https urls` | 镜像配置不是 https 列表 | 检查 settings |
| 400 | `unknown_hub` / `bad_repo` | hub 参数非法 | 正常使用不该遇到 |
| 400 | `bad_cursor` | hub 翻页游标坏了 | 回到第一页重新搜 |
| 404 | `model {id} not found` | catalog id 不存在 | 刷新市场 |
| 404 | `unknown engine: {eid}` | 引擎 id 不在目录里 | 检查 engines.json |
| 404 | `engine has no download sources` | 该引擎没有下载源 | 换引擎/报 issue |
| 502 | `GGUF 仓库枚举失败: ...` | HF 上游抽风 | 重试 |
| 502 | hub 上游错误 | HF/ModelScope 不通 | 换源/开镜像 |

主进程侧事件：

| 事件 | 含义 |
|---|---|
| `sidecar:down {reason: "restart-budget-exhausted"}` | sidecar 连续崩 3 次，主进程放弃重启 |
| `sidecar:health {ok: false}` | 健康检查没过 |

---

## 7. 重置方法

### 7.1 只清下载缓存

删掉 `<data_root>/downloads/` 和 `<data_root>/models/*.partial`。不影响已装好的模型/引擎。

### 7.2 重置设置

删掉 `<data_root>/settings.json`。下次启动回到默认（主题 system、默认引擎 llama.cpp、镜像列表重置）。已下载的模型/引擎保留。

### 7.3 重置 Agent 记忆

Agent 存在 SQLite（`python/app/agent/memory.py`），默认在用户数据目录下。删对应的 `.db` 文件即可清空 sessions/messages/preferences/task_history。

### 7.4 干净重装

1. 完全退出 App。
2. 卸载（Windows: 设置→应用；Linux: apt remove；macOS: 拖到废纸篓）。
3. 删除整个用户数据目录：
   - Windows: `%APPDATA%\KevraiOmni\`
   - macOS: `~/Library/Application Support/KevraiOmni/`
   - Linux: `~/.local/share/KevraiOmni/`
4. 重新装新版。

> 注意：这会删掉所有已下载的模型和引擎（数十 GB）。只想换 App 版本而保留模型，**别**删这个目录。

### 7.5 从源码跑的额外清理

```bash
rm -rf node_modules build
cd python && rm -rf __pycache__ app/__pycache__ .pytest_cache .coverage
pip uninstall -r requirements.txt -y   # 可选
```
