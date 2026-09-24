# 故障排查（Troubleshooting）

本文汇总 Kevrai Omni 日常使用与开发中最常见的问题。先从「日志在哪里看」开始，
再按症状对号入座。

---

## 日志位置与查看方法

Kevrai Omni 分两层日志：

### 1. Electron 主进程 / 渲染层

- **开发模式**：`npm run dev` 启动时已带 `--enable-logging`，主进程日志直接打印在
  启动它的终端里；渲染层日志在弹出的 DevTools **Console** 面板。
- **打包后**：主进程写在 Electron `userData` 目录。
  | 平台 | 目录 |
  |---|---|
  | Windows | `%APPDATA%\KevraiOmni\`（即 `C:\Users\<你>\AppData\Roaming\KevraiOmni\`） |
  | macOS | `~/Library/Application Support/KevraiOmni/` |
  | Linux | `~/.config/KevraiOmni/` |

### 2. Python sidecar

- sidecar 的 stdout/stderr 被 Electron 捕获并加上 `[sidecar]` / `[sidecar-stderr]`
  前缀打印到同一终端（开发时）。
- sidecar 内部用结构化 JSON 日志，每条带 `request_id`（也会出现在 HTTP 响应的
  `x-request-id` 头里）。
- 启动失败时，Electron 会把 sidecar stderr 尾部（最近约 80 行）回传给渲染层，
  「环境准备 / 诊断」页能看到 `stderr_tail`，并自动识别
  `ModuleNotFoundError` / `No module named` / `ImportError`。

> 想看 sidecar 到底在干嘛：开发期直接另开终端 `curl http://127.0.0.1:17890/api/health`，
> 或在 `npm run dev` 的终端里翻 `[sidecar-stderr]` 行。

---

## 1. Sidecar 启动失败

**典型症状**：启动后弹窗 "Python sidecar failed to start" / 一直转圈 /
「环境准备」页提示缺 Python 或缺依赖。

### 1a. 检测不到 Python

- **普通用户（Windows 安装包）**：进入「环境准备」页，点「一键安装 Python 环境」
  （会下载约 11MB 的 Python 3.12.7 embeddable 到用户数据目录，不写注册表）。
- **开发者（从源码）**：确认 `python --version`（Windows）/ `python3 --version`
  （Linux/macOS）可用且 ≥ 3.11；多个 Python 时用 `KEVRAI_PYTHON` 环境变量
  显式指定解释器，再启动。详见 [DEVELOPMENT.md](DEVELOPMENT.md)。

### 1b. 缺 Python 依赖（`ModuleNotFoundError`）

诊断页会告诉你缺哪个包。两种修法：

- **普通用户**：诊断页一般有「一键补装依赖」按钮，点一下即可。
- **开发者**：激活 venv 后
  ```bash
  cd python
  pip install -r requirements.txt
  # 国内网络慢可加：-i https://mirrors.tencent.com/pypi/simple/
  ```

### 1c. 端口 17890 被占用

sidecar 固定监听 `127.0.0.1:17890`，被别的进程占用时健康检查超时：

```bash
# Linux / macOS
lsof -i :17890
# Windows PowerShell
netstat -ano | findstr :17890
```

结束占用进程后重启应用。**不要**把 sidecar 改绑到 `0.0.0.0`——那会绕过
localhost + CORS 的安全边界。

---

## 2. 模型下载失败

### 2a. 网络问题 / 连不上 huggingface.co

- 国内网络默认走 `hf-mirror.com`（官方 HF 中国镜像）。若仍失败，进
  「设置」确认镜像源，或在「源健康检查」（`/api/hub/health`）里看哪些源可达。
- 下载器会自动测速并在多个候选源间回退；若报 `all_sources_unreachable`，
  说明当前所有候选源都不可达，检查代理 / 防火墙后重试。
- 支持 HTTP Range 断点续传，中断后重新点下载会接着下，不用从头来。

### 2b. gated（受控访问）模型报 `gated_requires_token` / 401 / 403

以 LTX-2.5 为例，这类仓库需要两步：

1. 浏览器登录 HuggingFace，到对应仓库页面**阅读并接受许可协议**（申请获批）；
2. 在 Kevrai Omni「设置 → HuggingFace Token」填入你的 access token
   （仅存本机，不会回显）。

只填 token 没在仓库页点接受许可，HF 仍会返回 401/403——两步缺一不可。

### 2c. 切换镜像源

- 设置里可增删自定义镜像（默认白名单 + 用户自管模式）。
- **永远不要**使用来历不明的第三方 HF CDN 镜像——`hf-cdn.sufy.com` 这类
  typosquat 钓鱼域名在代码层被永久封禁，详见 [SECURITY.md](../SECURITY.md)。

---

## 3. 引擎安装失败

- 引擎二进制只从 GitHub / PyPI / 腾讯·阿里官方镜像下载，版本与 SHA-256 由清单锁定。
- **网络慢/中断**：引擎安装支持多候选源回退与断点续传；失败后在引擎面板
  点「检查引擎更新」再试一次即可。
- **安装目录权限**：引擎装在用户数据目录（`.../KevraiOmni/engines/`），
  不需要管理员权限；若该目录被杀毒软件锁定，暂时放行后重试。
- **架构不匹配**：确认下载的是对应平台（x64 / arm64）的引擎包。

---

## 4. LTX-2.5 视频生成显存不足

**典型症状**：点生成后很快报错（CUDA out of memory / OOM）。

- 官方最低显存约 **16GB**。按你的显卡选显存预设：
  | 预设 | 显存 |
  |---|---|
  | 极致质量 | 24GB+ |
  | 高质量 | 16GB |
  | 平衡 | 12GB（实验） |
  | 速度 | 8GB（实验） |
  | 草稿 | 4GB（实验） |
- 低于 16GB 的预设会标注「实验」，选择时有提示。
- 缓解 OOM 的手段：打开面板里的 **VAE 切片 / 模型 CPU offload** 开关、
  降低分辨率与帧数、减少步数、先出草稿档。
- 确认 NVIDIA 驱动与 CUDA 版 PyTorch 已正确安装（首次使用 LTX 时会提示装
  `torch diffusers transformers accelerate imageio imageio-ffmpeg`）。

---

## 5. 浅色主题显示异常

- 进「设置 → 外观 / theme」切到 `light`。v3.0.0 已修复浅色主题下部分面板、
  代码块、卡片对比度不足的问题。
- 若切换后局部仍有残留深色背景：**彻底退出应用再重开**（主题在启动时加载，
  个别模块有缓存）。
- 确认 `PUT /api/settings` 提交的 `theme` 值是 `light` / `dark` 之一，
  拼错会被 pydantic 拒绝。

---

## 6. 其他高频问题

| 现象 | 处理 |
|---|---|
| 下载卡住不动 | 看「进度」页；必要时取消后重下（断点续传） |
| 引擎「检查更新」一直转圈 | 该接口要访问 GitHub，网络受限会慢；6 小时缓存，稍后再试 |
| Agent 只会给固定话术、不调用工具 | 这是未加载 LLM 时的 `rule_based` 回退模式（正常）；加载 MNN LLM 后进入完整 ReAct |
| `/v1/chat/completions` 报错 | 先确认已通过 `/api/mnn/load` 加载模型，再调用 |
| Electron 报 sandbox / GPU 错误 | 开发者在无特权容器可临时 `npm run dev -- --no-sandbox`；普通用户请升级显卡驱动 |

---

## 还没解决？

- 先翻 [DEVELOPMENT.md](DEVELOPMENT.md)（环境问题）与 [API.md](API.md)（接口问题）。
- 确认你跑的是最新版本（`/api/health` 的 `version`）。
- 如怀疑是安全漏洞，请走 [SECURITY.md](../SECURITY.md) 的私有报告通道，
  不要在公开 issue 里贴可利用细节。
