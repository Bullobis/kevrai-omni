# Kevrai Omni v4.17.6 发布说明

> 本版本接替已发布的 v4.17.5，重点是把“能装、能出声、能对话”做成可验证的事实，而不是只在界面上显示成功。

## 一、本版最重要的事：TTS 真的出声了

三个 TTS 引擎都在真实运行环境里跑出了音频文件，并用 RMS/峰值/采样率做了客观校验，不是仅返回 200。

| 引擎 | 音色/语言 | 实测结果 |
|---|---|---|
| Piper | `zh_CN-huayan-medium` 中文 | 最终安装包内 **4.040 s / 178,176 B / 22,050 Hz / RMS 5453 / peak 32767**；源码版 8.208 s、0.36 MB，桌面端可直接播放 |
| Kokoro | `af_heart` 英文 | **4.175 s / 200,444 B / 24,000 Hz / RMS 1521** |
| Kokoro | `zf_xiaobei` 中文 | 5.225 s / 250,844 B |
| Chatterbox | 零样本克隆 | 安装、克隆档案上传/删除/选用接口全部可用（`chatterbox:testvoice`）；受测试机 3 GB 内存限制，合成阶段 OOM，未产出最终音频，已在下方“已知限制”如实说明 |

修复的关键问题：
- frozen 安装包里 `sys.executable -m pip` 必然失败 → 内置 `pip-runner`，AppImage 内实测安装 Piper **13.666 秒成功**。
- 冻结包漏打包 `rfc3987_syntax.lark` 导致 39 个引擎全部为空且无日志 → PyInstaller `--collect-all` + 缺失告警；最终包实测 **39 个引擎**。
- Kokoro 返回 numpy 被 JSON 序列化 500 → 改为落盘 WAV 并返回元信息。
- Kokoro（torch 2.15）与 Chatterbox（torch 2.6）同进程冲突 → 统一 `torch==2.6.0+cpu`、引擎目录 `sys.path` 隔离、预加载共享库。
- 三个 TTS 引擎共用模块缓存导致串音/互相污染 → 引擎目录隔离并清理跨引擎模块缓存。

## 二、LLM 真跑通（llama.cpp）

- 修复 **GitHub API 403 限流时把源码 tar.gz 当成二进制引擎**的严重缺陷：此前安装返回 `ok:true`，目录 187 MB 全是源码，根本没有 `llama-server`，用户点对话才失败。
- 新增 release 页面 HTML 兜底解析：API 限流时从 `/releases` + `/releases/expanded_assets/<tag>` 取资产，并按平台/架构打分（纯 CPU 包优先，自动排除 CUDA/ROCm/OpenVINO/SYCL/Vulkan 变体与 arm64/s390x 等不匹配架构）。
- 新增关键可执行文件硬校验：`llama.cpp` 安装结果必须包含 `llama-server`，否则判失败并提示重试，不再“假成功”。
- 实测：`llama-b12224-bin-ubuntu-x64.tar.gz` **9.7 秒安装完成**，`llama-server` 正常启动；Gemma-3-270M Q8_0 对话返回「1+1等于2」，**0.32 秒 / 8 tokens**。

## 三、ASR 兼容官方尺寸别名

faster-whisper 官方支持 `WhisperModel("tiny")`、`"base"`、`"small"`、`"medium"`、`"large-v3"`、`"turbo"` 等尺寸名，原实现只接受 `owner/name`，会把最常见的官方用法直接拒绝。v4.17.6 改为只校验非空，合法性别名交给上游映射；实测 `{"model":"tiny"}` 返回中文转写与分段时间戳。

## 四、安装包/运行时修复（累计）

- Ubuntu-latest（glibc 2.38）构建的 sidecar 在 Ubuntu 22.04 报 `GLIBC_2.38 not found` → Linux 构建固定 `ubuntu-22.04`。
- PyInstaller 被 site-packages 同名 `app` 包抢占 → 构建时 `PYTHONPATH=.`。
- 插件页 `escapeHtml` 重复声明导致整页 `SyntaxError`、插件页完全打不开 → 删除重复声明。
- 插件存储型 XSS、`../` 安装目录逃逸、二次 deactivate 500 → 全部修复。
- pip 目标目录为空时误触发 import 校验 → 跳过空目录校验。
- CPU torch 从默认 CUDA 轮子（>3 GB，写满 `/tmp`）切换为 `2.6.0+cpu`，并锁定 `av`、`numpy` 等易被镜像源替换的包。

## 五、质量门

- 全量 `pytest`：**2629 passed**（版本一致性 10 passed）。
- 渲染进程测试：**436 pass / 0 fail**；所有 renderer JS 逐个 `node --check` 通过。
- 安装包实机：AppImage 启动、sidecar 39 引擎、Piper 安装与合成、ASR、llama.cpp 安装与对话均已实测。
- `ruff check .` 仍有 139 条历史存量告警（E402/EXE001/BLE001/S603 等，非本版引入），不影响 release 构建。

## 六、下载与校验

本页资产由 GitHub Actions 在 Ubuntu 22.04 / Windows / macOS 原生构建，包含：

- Linux：`Kevrai-Omni-4.17.6-x86_64.AppImage`、`Kevrai-Omni-4.17.6-amd64.deb`
- Windows：`Kevrai-Omni-4.17.6-x64.exe`（NSIS）、`Kevrai-Omni-4.17.6-x64.zip`（便携版）
- macOS：`Kevrai-Omni-4.17.6-x64.dmg`、`Kevrai-Omni-4.17.6-arm64.dmg`
- 独立 sidecar 与 `SHA256SUMS.txt`

下载后请用 `sha256sum -c SHA256SUMS.txt` 校验。Linux AppImage 需 `chmod +x` 后运行；Windows 便携版解压后运行 `Kevrai Omni.exe`。

## 七、已知限制（如实说明）

1. Chatterbox 在 3 GB 内存测试机上合成阶段 OOM，仅验证安装与克隆档案管理；建议 8 GB 以上内存使用。
2. MNN / vLLM / ComfyUI / Demucs / OCR / 3D / LTX / 音乐 / 短剧 / Agent / MCP / WebDAV 等模块本版未逐项真跑，仅做路由与静态检查，后续版本继续补实跑证据。
3. 首次安装大模型仍受网络与磁盘影响；国内网络建议配置镜像（内置腾讯 PyPI 镜像与 hf-mirror）。
4. 本项目依赖大量第三方推理引擎，模型许可请以各上游仓库为准。

---

**完整变更范围**：v4.17.5 已发布内容 + 本版 ASR 别名兼容、llama.cpp 二进制资产解析与硬校验、版本号统一 4.17.6。
