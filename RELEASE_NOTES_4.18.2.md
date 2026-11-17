# Kevrai Omni v4.18.2

> 本版本由 GitHub Actions 原生构建并发布 Linux AppImage/deb、Windows NSIS 安装器 + 便携 zip、macOS dmg（x64 + arm64）。安装包内置冻结 sidecar 与 pip-runner。
>
> 4.18.2 是 4.18.1 实机验证（真实安装引擎、真实下载模型、真实推理、Wine 实跑 Windows 版）中发现并修复的三个阻断级问题的热修复版本。

## 修复的阻断级问题（全部由实机验证发现）

- **模型下载全被拒绝（HF / ModelScope 双源失效）**：huggingface.co 与 hf-mirror.com 的 `/resolve/` 下载如今 302 跳转到 Xet CAS 桥（`cas-bridge.xethub.hf.co`）与区域 LFS CDN，modelscope.cn 跳转到 `cdn-lfs-cn-*.modelscope.cn`。下载器对最终 URL 做白名单校验，这些官方 CDN 主机不在名单内，导致**所有**重定向后的模型下载报 "host not in allowlist"。已将 5 个实测可解析的官方 CDN 主机加入白名单（保持精确匹配，仿冒子域仍被拒绝），并补回归测试。实测：Qwen3-0.6B Q4_K_M（397MB）经 hf-mirror → Xet CDN 以 43MB/s 下载完成。
- **GGUF 模型对话完全不可用**：主进程 `findLlamaServerBinary` 直接读 `sidecarFetch` 返回值的 `.engines`，但该函数返回 `{status, body}` 信封——引擎列表永远为空，llama.cpp 明明已安装却始终报"引擎未安装"。修复为正确解包，抽出纯函数 `electron/engine-records.js` 并补 4 个回归测试。
- **CPU 机器安装 pip 引擎拉取数 GB CUDA 轮子**：`/api/engines/install` 走的旧 `install_pip_engine` 是第二套未加固实现，缺少 CPU 版 torch 索引（`whl/cpu`）、TMPDIR  relocation、流式进度与孤儿进程清理——这些加固只做进了 `EngineManager.install_pip`。旧函数改为委托加固版（单一实现），消除双路径漂移；更新 3 个旧实现测试。实测：faster-whisper / sentence-transformers 安装命令已带正确索引与临时目录。

- **引擎二进制丢失可执行位**：tar/zip 安全解包用 `open("wb")` 写文件，成员权限位被丢弃，llama-server 等 ELF 变成 0644，GGUF 对话报 "Permission denied"（spawn exit -13）。两条解包路径均恢复成员权限位（掩码 0o777，setuid/setgid 永不落地），补回归测试。
- **soname 符号链接被安全策略误杀**：llama.cpp v1.0.0 起以共享库分发，tar 包内 `libllama.so.1 -> libllama.so.1.0.0` 等 soname 链接被"跳过一切符号链接"的旧策略丢弃，动态加载器报 "cannot open shared object file"。改为**仅允许目录内部符号链接**（绝对路径与逃逸链接仍一律拒绝），tar/zip 双路径同步修复，补回归测试。
- **pip 安装超时看门狗误杀慢网安装**：900s 对 torch 级引擎在慢网下不够（实测 faster-whisper 被杀）。默认放宽到 1800s，支持 `KEVRAI_PIP_TIMEOUT` 覆盖；看门狗依旧兜底，安装互斥锁不会被永久占用。
- **超时只杀 bootloader、真 pip 成孤儿**：冻结 pip-runner 是 PyInstaller onefile 引导器，`proc.kill()` 只杀引导器，真正的 pip 孙进程继续下载 18+ 分钟（实测 ppid=1 孤儿）。POSIX 改 `start_new_session` + `killpg` 整组清理，Windows 用 `taskkill /T /F`。
- **pip 网络等待无界**：实测镜像本身 9MB/s，但 pip 会在被黑洞的连接上干等 20+ 分钟。安装环境注入 `PIP_DEFAULT_TIMEOUT=30`、`PIP_RETRIES=5`，卡死快速失败并换新连接重试。
- **pip 安装进度遥测空白**：pip-runner stdout 是管道时 Python 块缓冲，"正在下载/安装 xx" 进度行数分钟才到一次。改行缓冲，逐行实时上报。

- **Piper 语音下载被镜像 403 拒绝**：hf-mirror.com 对默认 `Python-urllib/x.y` UA 返回 403（实测），而国内网络 HF 主站不可达——piper 语音下载用裸 urlopen 不带 UA，导致**所有语音下载失败**（"主站与镜像均不可达"）。改带产品 UA，补回归测试。实测中文语音合成成功。
- **冻结 sidecar 内 import 校验误判**：PyInstaller 只打包应用导入图追踪到的标准库子集，引擎依赖动态导入的标准库模块（实测 sentence-transformers → filecmp）触发 ModuleNotFoundError，把 pip 成功的安装误判为损坏。冻结环境下标准库缺失改为"存疑接受"并记日志；真正的引擎文件缺失仍拒绝。
- **CPU torch 钉版**：仅加 CPU extra-index 不够——pip 跨索引取最高版本，CN 镜像的 CUDA torch 比 CPU 索引更新（实测装出 5.8GB nvidia_* 轮子）。新增解析 PyTorch CPU 索引最新 +cpu 轮子并按解释器/平台钉住 `torch==X.Y.Z+cpu`；网络失败时优雅回退旧行为。
- **pip extra-index 可配置**：`KEVRAI_PIP_EXTRA_INDEX=""` 可完全禁用 pypi.org 附加索引（国内网络下 pip 会漂到被黑洞的 Fastly CDN，实测同一 wheel 主镜像 9MB/s 而 Fastly 卡死 20+ 分钟）。

## 验证（全部在本机实际执行）

- pytest 全量通过；renderer 测试全过（含新增 engine-records 回归）；ruff / mypy（CI 口径）零错误。
- 冻结 sidecar + pip-runner 重新构建；Linux deb 实际安装运行。
- 真实下载 Qwen3-0.6B-Q4_K_M.gguf（396,705,472 字节）并经应用 IPC 启动 llama-server **实测推理成功**（模型正确回答提问，OpenAI 兼容接口返回正常 usage）。
- Windows 安装包 sha256 与发布清单一致；**Wine 11 中实际运行 Windows 版**：sidecar.exe 健康、21 个页面逐一点击遍历 0 控制台报错、截屏核验。
- Markdown / 表格渲染实测：表格、代码块、列表、引用、链接全部正确渲染并截屏核验。
- **Piper 中文 TTS 实测成功**（zh_CN-huayan-medium，5.47s / 22050Hz WAV）；**faster-whisper tiny 实测转写成功**（直接转写 TTS 产物，中文识别、语言检测概率 1.0、CPU int8）——TTS→ASR 闭环验证。
- llama.cpp b12458 引擎经加速镜像落盘后，应用 IPC 启动 llama-server 加载 Qwen3-0.6B Q4_K_M **推理成功**（OpenAI 兼容接口正常返回）。
