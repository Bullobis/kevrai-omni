# FAQ.md — 常见问题

> 基于 v2.9.0 源码与实际错误消息整理。每条都能在代码里找到出处；如果你的问题不在这，先看 [TROUBLESHOOTING.md](./TROUBLESHOOTING.md)。

## 目录

- [安装与启动](#安装与启动)
- [sidecar 与连接](#sidecar-与连接)
- [模型下载](#模型下载)
- [GPU / 显存](#gpu--显存)
- [使用体验](#使用体验)
- [更新与离线](#更新与离线)

---

## 安装与启动

### Q1. 双击图标后窗口根本不出现，或闪一下就退了

**症状**：Windows 上看到错误框 *"Kevrai Omni — Python sidecar failed to start"*，或日志里 `sidecar exited code= ...`。

**原因**：主进程 spawn 的 Python sidecar 没在 17890 端口起来。主进程会先看 sidecar stderr 里有没有 `ModuleNotFoundError` / `No module named` / `ImportError`——有的话自动进「环境准备」页补装依赖；没有的话直接弹错误框退出。

**处理**：
1. 看 `<userData>/logs/main.log` 末尾（Windows: `%APPDATA%\KevraiOmni\logs\main.log`）。
2. 如果是缺 Python：点「一键安装 Python 环境」。
3. 如果是缺 pip 包：进「环境准备」页点「补装依赖」。
4. 从源码跑的话：确认 `pip install -r python/requirements.txt` 成功。

---

### Q2. 启动后一直卡在「正在连接 sidecar」

**症状**：窗口出来了，但所有面板转圈，顶部显示 sidecar 重连中。

**原因**：主进程健康检查 `http://127.0.0.1:17890/api/health` 没通过。可能：
- sidecar 崩了，主进程在自动重启（最多 3 次，`SIDECAR_RESTART_MAX = 3`）；
- 17890 端口被别的程序占了；
- 防火墙/安全软件拦了回环连接（少见）。

**处理**：见 [TROUBLESHOOTING §连接类](./TROUBLESHOOTING.md#连接类)。

---

### Q3. Windows 上提示「找不到 Python」/ 我没有 Python 能用吗？

能。安装包形态**不要求**你预装 Python——App 首次启动会自动下载便携版 Python 3.12.7 到 `%APPDATA%\KevraiOmni\python-runtime\`（约 11 MB，从 npmmirror / 华为云镜像拉）。

只有从源码运行时才需要系统 Python 3.10+。

---

### Q4. 安装路径可以含中文/空格吗？

- 安装目录本身**可以**有空格（artifactName 特意用无空格品牌名 `Kevrai-Omni-<ver>-<arch>.exe` 避免 electron-updater 的 URL 问题）。
- **模型目录建议纯英文、无空格**。部分引擎对非 ASCII 路径支持不好；sidecar 的路径校验（`python/app/hub/paths.py`）会直接拒绝非法字符 `<>:"/\|?*` 和 Windows 保留名（CON/PRN/AUX/NUL/COM1…）。

---

### Q5. 杀毒软件把 `python-runtime` 或引擎文件删了

便携 Python 运行时和引擎二进制（`.exe`/`.so`）容易被国产杀毒误报。

**处理**：把整个 `%APPDATA%\KevraiOmni\` 加进杀毒白名单，然后在「环境准备」页重新安装 Python 依赖。

---

## sidecar 与连接

### Q6. sidecar 反复重启，最后提示 "restart-budget-exhausted"

主进程对 sidecar 崩溃有**最多 3 次**自动重启预算（`SIDECAR_RESTART_MAX = 3`），超过后推 `sidecar:down` 事件并停止重试。

**处理**：
1. 看 `main.log` 里 `[sidecar-stderr]` 段的最后 80 行（主进程会留这个尾巴）。
2. 常见原因：依赖装了一半（中断）、端口被占、引擎目录损坏。
3. 从源码跑的话，手动 `cd python && python -m uvicorn app.main:app --port 17890` 看真实报错。

---

### Q7. 我能自己手动起 sidecar 调试吗？

可以，但别和 Electron 主进程同时起（端口冲突）：

```bash
cd python
python3 -m uvicorn app.main:app --host 127.0.0.1 --port 17890 --reload
```

然后单独用浏览器/`curl` 打 `http://127.0.0.1:17890/docs` 看 FastAPI 自动生成的 Swagger。

---

### Q8. 17890 端口被别的程序占了怎么办？

临时改端口：启动前设环境变量 `KEVRAI_PORT=17891`。注意 Electron 主进程和 sidecar 都读这个变量，两边要一致（主进程 spawn sidecar 时会把 `KEVRAI_PORT` 传下去）。

---

## 模型下载

### Q9. 模型下载特别慢

**处理**：
1. 进「设置 → 多源镜像」，确认 `https://hf-mirror.com` 已加进 `extra_model_mirrors`。
2. App 默认 `auto_pick_best_source=true`，会对每个源测速后选最快的。
3. 如果某源一直慢/失败，用「源健康」页把它冷却掉，或用 `lock_source` 锁定你信得过的源。
4. 大文件下载**支持断点续传**：写 `.partial` 文件，中断后下次自动 `Range: bytes=<已传>-` 续传，不用重下。

---

### Q10. 下载到一半断了，会重新下吗？

不会重头来。`downloader.py` 在目标路径旁写 `<filename>.partial`，下次启动同一个任务时读 `.partial` 的大小作为续传起点，发 `Range: bytes=<n>-` 请求。如果服务器回 `200` 而不是 `206`（不支持续传），代码会检测到并从头写（见 downloader.py 里 `resumed = ... == 206` 的判断）。

---

### Q11. 提示 gated 模型 / 401 / 需要 token

LTX-2.5 等仓库是 HuggingFace gated 仓库。两步：
1. 浏览器登录 HuggingFace，到对应模型仓库页面**点同意许可协议**（申请获批后生效）。
2. 在 Kevrai Omni「设置 → HuggingFace Token」填你的 Access Token（只存本机 `settings.json`）。

只填 token 不点协议是不够的。ModelScope 公开模型不需要 token。

---

### Q12. 我想从别的机器下好模型再拷过来

把整个 `<data_root>/models/` 目录（或对应模型文件）拷到离线机器相同位置即可；App 启动时扫描本地注册表识别。也可以拖文件到窗口里走「导入」流程。详见 [DEPLOYMENT §6](./DEPLOYMENT.md#6-离线环境部署)。

---

### Q13. 提示 `model xxx not found` / `unknown_hub` / `bad_repo`

- `model {id} not found`：你访问的 catalog id 不在本地注册表里。刷新模型市场或重启 App。
- `unknown_hub`：`hub` 参数不是 `curated`/`hf`/`modelscope` 之一。
- `bad_repo`：仓库路径为空或格式不对。

这些是 sidecar 返回的 4xx，正常使用时不该出现；如果是在 UI 里遇到，截图到 issue。

---

## GPU / 显存

### Q14. 「硬件」页看不到我的 NVIDIA 显卡

sidecar 通过 `nvidia-smi` 探测。检查：
1. 终端里跑 `nvidia-smi` 能不能出表。不能 → 装 NVIDIA 驱动。
2. 确认 `nvidia-smi` 在 PATH 里（Windows 默认在 `C:\Windows\System32\` 或 `C:\Program Files\NVIDIA Corporation\NVSMI\`）。
3. 还是看不到：看 `main.log` 里 `[sidecar]` 段的 GPU 探测日志。

---

### Q15. 显存不够 / 生成时 CUDA out of memory

- LTX 引擎按 VRAM 档位切预设（24/16/12/8/4 GB 对应 quality/balanced-high/balanced/speed/fast）。显存不够时在生成面板选更低预设，或开 `enable_model_cpu_offload`。
- LLM（llama.cpp）换更小量化档（Q4_K_M → Q3_K_S）或更小参数量模型。
- 关掉其他占显存的程序（浏览器硬件加速、其他 AI 工具）。

---

### Q16. AMD 卡 / 昇腾卡会被支持吗？

探测层支持（`rocm-smi` / `npu-smi`），但**具体哪些引擎已适配**要看引擎页标注。ROCm/CANN 的用户态栈需要你自己装好；App 不捆绑。详见 [DEPLOYMENT §5](./DEPLOYMENT.md#5-gpu--驱动配置)。

---

### Q17. Apple Silicon 上会调用 GPU 吗？

会。macOS 上通过 `system_profiler` 识别 Apple Silicon，MLX 引擎走 Metal 统一内存。非 MLX 的引擎在 M 系列 Mac 上可能回退 CPU。

---

## 使用体验

### Q18. Agent 不响应 / 一直转圈

Agent 走 ReAct 循环（`python/app/agent/agent.py`），有最大迭代上限。常见原因：
- 没选 LLM 模型或模型没加载；
- 模型推理慢（CPU 模式下尤其）；
- 某工具报错导致循环卡住。

看 `main.log` 里 `[sidecar]` 段的 agent 日志；或开 `/ws/agent/{session_id}` 看每步 thought/observation。Agent 记忆存在 SQLite（`sessions`/`messages`/`preferences`/`task_history` 表），可在「Agent 设置」里清会话。

---

### Q19. 图片/视频/音频生成失败

- 先确认对应引擎已安装（「AI 引擎」页）。
- 视频（LTX-2.5）对显存要求高，见 Q15。
- 音频/TTS 类引擎（kokoro、fish-speech、cosyvoice 等）首次运行会拉较大的模型/依赖，慢是正常的。
- 生成任务在 `ltxTasks` / `convertTasks` 里可查状态；失败的会标 error 并带 detail。

---

### Q20. 中文路径 / 文件名导致导入失败

sidecar 导入接口会拒绝：
- 路径不存在（`path does not exist`）；
- 可疑目录（`refusing to import this directory`）；
- 路径穿越 / 非法字符（`invalid path: ...`）。

把模型文件移到一个纯英文无空格路径再试。

---

## 更新与离线

### Q21. 应用内「检查更新」失败

electron-updater 从 GitHub Releases 拉 `latest.yml`。离线网络或被墙时会失败——这是预期，不影响使用。手动更新：去 [Releases](https://github.com/Bullobis/kevrai-omni/releases) 下新版安装包覆盖装即可，用户数据（模型/设置）保留。

---

### Q22. 离线机器怎么用？

详见 [DEPLOYMENT §6](./DEPLOYMENT.md#6-离线环境部署)。要点：在有网机器下好安装包 + Python runtime + 引擎 + 模型，整体拷过去；设 `HF_HUB_OFFLINE=1` 并锁定本地源。

---

### Q23. 卸载后模型还在吗？

Windows 卸载默认**保留** `%APPDATA%\KevraiOmni\`（`deleteAppDataOnUninstall: false`），重装后自动识别已下模型。要彻底清理就手动删这个目录。

---

### Q24. 我想彻底重置

1. 完全退出 App。
2. 删除用户数据目录：
   - Windows: `%APPDATA%\KevraiOmni\`
   - macOS: `~/Library/Application Support/KevraiOmni/`
   - Linux: `~/.local/share/KevraiOmni/`
3. 重新启动。详见 [TROUBLESHOOTING §重置](./TROUBLESHOOTING.md#重置方法)。

---

### Q25. 日志在哪？我要报 issue 时给什么？

- 主进程 + sidecar 日志：`<userData>/logs/main.log`（滚动到 `main.log.1` / `main.log.2`）。
- 报 issue 时附：OS 版本、显卡、App 版本（「关于」页）、`main.log` 末尾 200 行、复现步骤。
