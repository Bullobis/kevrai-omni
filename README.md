# 🎬 Kevrai-Omni

Windows 11 上的全能 AI 创作工作站（MiniMax H3 视频生成 + 图片生成）：**模型市场下载 → 硬件智能适配 → 本地推理生成 → 作品管理**，小白友好，全程无需命令行。

> 基于 **DiffSynth-Studio** 引擎（选型依据见 `docs/调研报告-MiniMax-H3与引擎选型.md`）。
> 所有模型数据（仓库、文件名、大小、限制）于 2026-08-07 逐条联网核实，**不含任何虚构数据**。

## 功能总览

| 需求 | 实现 |
|---|---|
| 模型市场 | 10 个已核实的模型包：官方 BF16 / DiffSynth NF4 / Comfy-Org INT8/FP8 / 社区 GGUF / InstantX Turbo LoRA |
| 多下载源 | 魔搭、HF-Mirror、HF 原站（魔乐/GitHub 经核实不托管 H3 权重，已如实移除并说明） |
| 智能选源 | **真实测速**：HTTP Range 采样测延迟+真实吞吐，综合评分=速度75%+延迟25%，不以延迟论英雄 |
| 按硬件选模型 | nvidia-smi/torch 检测显存 → 自动策略分档（旗舰/高性/均衡/低显存/极限），市场页直接推荐 |
| 断点续传 | 自研下载器：Range 续传 + 失败重试 + 已完成文件跳过，中断重下不重头 |
| 显存内存自动分配 | DiffSynth 三级显存管理（硬盘→内存→显存），NF4 版 8GB 显存可跑 |
| 实时进度 | 采样步数级进度（官方 progress 钩子），阶段提示：编码→去噪→解码 |
| 生成主页三栏布局 | 左：正/负提示词；中：参数（比例/时长4~15s/分辨率档/步数/种子）+ 参考素材拖拽导入；右：模式选择 + LoRA + 生成 |
| 参考素材 | 拖拽/点击导入 GIF、MP4、MP3 等；强制官方限制：≤9图 ≤3视频 ≤3音频 总数≤12 |
| 模式 | 文生视频 / 首帧 / 尾帧 / 首尾帧 / 全模态参考 / 音频驱动 / 视频编辑(Retake) |
| 嵌入模型 | LoRA 导入 + 强度调节（官方 load_lora 接口） |
| UI | 无边框玻璃拟态 + 动态极光背景 + 4 主题 + 自定义强调色 + 玻璃透明度调节 |
| 作品库 | 历史作品网格 + **视频首帧真实缩略图** + 内置播放器 + 生成参数元数据 |
| 提示词模板 | 6 类小白常用模板一键套用（产品广告/人物说话/风景运镜/动漫/电商/片头） |
| 批量生成 | 一次出 1~4 个不同种子版本，方便挑片 |
| 素材缩略图 | 参考列表直接显示图片预览与视频首帧 |
| 细节体验 | Ctrl+Enter 快捷生成、窗口大小位置记忆、渐变主按钮 |
| 多芯片支持 | 自动检测 NVIDIA CUDA / AMD ROCm / 华为昇腾 NPU / Intel XPU / DirectML，按后端+显存自动配出最优策略（设置页"一键最优配置"） |
| 最优方案引擎 | planner 按硬件自动权衡**速度×质量×成本**：推荐模型版本+分辨率+步数+卸载策略（速度参考全部来自已核实的社区实测并标注来源）；市场页"一键下载推荐方案"，生成页 ⚡速度/⚖均衡/✨质量 三档预设 |
| ComfyUI 工作流 | 内置 Abiray/MiniMax-H3-GGUF 官方社区工作流（FL2VA + Ref2VA），「我的模型」页一键复制，AMD/低显存用户直接走 ComfyUI 路线 |
| DIY 自定义打包 | 「DIY 打包」页自选 6 类组件拼包：5 条硬性校验规则（引擎匹配/量化成套/分区匹配/显存可行/磁盘可行）违反直接拒绝下载，3 条风险警告；6 个快速预设一键填充 |
| 官方提示词资源 | 内置 MiniMax 官方《视频提示词写作指南》两份（文生/首尾帧 + 全模态参考），生成页一键打开；模板库 12 个模板（6 个原创 + 6 个参考官方 9 技能方法论）；官方输入规格校验（音频不能单独、片段 2~15 秒） |
| 代码质量 | flake8 零警告（无未使用导入/未定义名）；依赖表仅列直接依赖且全部核实存在；探测性导入均带注释说明 |
| 图片生成 | 新增图片生成页：Z-Image-Turbo（通义官方 8 步快速出图）+ Qwen-Image-2512（官方旗舰，中文文字渲染强）；图片模型负向提示词有效 |
| 中英双语 | 跟随系统语言自动切换（中文系统→中文；其他一律→英文），设置页可手动覆盖 |
| 市场筛选 | 模型市场按类型筛选：全部 / 🎬视频 / 🖼️图片 / ⚡LoRA / 🔧ComfyUI 专用；19 个精选模型全部带社区热度与介绍 |
| LoRA 生态 | 6 个已验证社区加速 LoRA（含热度数据与适配说明）：lightx2v Turbo（下载量第一）、larryvrh v4（好评最高）、InstantX 官方双版、Abiray/drbaph Pruned 专用；切换防叠加 + 导入格式预检 |
| 内置教程 | 「帮助教程」页随软件打包分发：三步上手 + 7 条常见问题 + 高级模式（ComfyUI 工作流/官方指南入口）；首次启动引导 |
| 模板优先 | 生成页 12 个快捷模板按钮（小白一键套用）；负面提示词已移除（H3 CFG 蒸馏不生效） |
| 安装包 | PyInstaller + Inno Setup 6 一键构建（`packaging\build_windows.bat`） |

## 📦 下载 / Download（v2.2.0）

开箱即用的安装包与源码已发布在 [Releases](https://github.com/Bullobis/kevrai-omni/releases/tag/v2.2.0)：

| 平台 | 文件 | 说明 |
|---|---|---|
| Windows 11 | [Kevrai-Omni-Setup-2.2.0.exe](https://github.com/Bullobis/kevrai-omni/releases/download/v2.2.0/Kevrai-Omni-Setup-2.2.0.exe) | 安装包（PyInstaller + Inno Setup 6） |
| Linux（amd64 / Debian·Ubuntu） | [Kevrai-Omni-2.2.0-amd64.deb](https://github.com/Bullobis/kevrai-omni/releases/download/v2.2.0/Kevrai-Omni-2.2.0-amd64.deb) | Debian/Ubuntu 安装包 |
| Linux（x86_64 便携版） | [Kevrai-Omni-2.2.0-x86_64.AppImage](https://github.com/Bullobis/kevrai-omni/releases/download/v2.2.0/Kevrai-Omni-2.2.0-x86_64.AppImage) | 赋予可执行权限后直接运行 |
| 源码 | [kevrai-omni-2.2.0-source.tar.gz](https://github.com/Bullobis/kevrai-omni/releases/download/v2.2.0/kevrai-omni-2.2.0-source.tar.gz) · [.zip](https://github.com/Bullobis/kevrai-omni/releases/download/v2.2.0/kevrai-omni-2.2.0-source.zip) | 完整源代码 |

校验和见同版本 `SHA256SUMS.txt`。各平台校验命令：
- Windows（PowerShell）：`Get-FileHash -Algorithm SHA256 文件名`
- Linux / macOS：`sha256sum 文件名`

## 快速开始（两种玩法）

**玩法一：一键启动（推荐日常使用，免打包）**
1. 安装 Python 3.10~3.14（推荐 3.12/3.13，勾选 Add to PATH；勿用 3.15）
2. 把文件夹放在**纯英文路径**（如 `D:\KevraiOmni`，避免中文/OneDrive 目录）
3. 双击 `一键启动.bat`：首次自动装环境，之后秒开

**玩法二：打包成安装包**
双击 `一键打包.bat`（需安装免费的 Inno Setup 6），产出 `Kevrai-Omni-Setup-2.2.0.exe` 安装包 + 绿色版 exe。

> 详细小白教程见压缩包内的《使用教程.md》（含桌面/中文路径报错排查）。

## 开发运行（手动方式）

```bat
:: 1. 安装 PyTorch CUDA（cu124）
pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu124

:: 2. 安装依赖
pip install -r requirements.txt

:: 3. 启动
python -m h3studio.main
```

首次启动会弹出模型协议确认（H3 Community License 排除美国/欧盟/英国/韩国）。
使用流程：**模型市场 → 测速 → 下载 NF4 版（约 35GB）→ 生成页写提示词 → 开始生成**。

## 打包安装包（一键自动）

**双击项目根目录的 `一键打包.bat`**（Windows 11，需已安装 Python 3.10~3.12）。
脚本自动完成：创建虚拟环境 → 安装 PyTorch CUDA 12.4（阿里云镜像，自动回退官方源）→
安装依赖（清华/腾讯镜像）→ PyInstaller 打包 → Inno Setup 编译安装包。

产物：
- **安装包**：`packaging\Output\Kevrai-Omni-Setup-2.2.0.exe`（需安装免费的 Inno Setup 6：jrsoftware.org/isdl.php）
- **绿色版**：`dist\Kevrai-Omni\Kevrai-Omni.exe`（未装 Inno Setup 时直接可用）

说明：安装包含 PyTorch CUDA 运行库（约 1.5~2GB，本地 AI 软件的共同现状）；
模型权重不打进安装包，由用户在软件内按需下载（NF4 版约 35GB）。
打包脚本细节与常见问题见 `packaging/BUILD_GUIDE.md`。

## 代码质量

- flake8 F 类检查零警告（无未使用导入/未定义名）
- 13 个 Python 模块全量人工审计（v1.8.0）：修复 5 个严重 bug + 15 项健壮性问题
- 测试链：17 项冒烟测试 + DIY 校验 16 场景单测 + 全页面 GUI 回归

## 项目结构

```
Kevrai-Omni/
├── h3studio/
│   ├── facts.py            # 事实库：全部模型/仓库/限制数据（已核实，含来源）
│   ├── config.py           # 设置持久化 + 主题定义
│   ├── hardware.py         # 硬件检测 + 显存策略分档
│   ├── sources.py          # 下载源定义 + 真实测速 + 智能选源
│   ├── downloader.py       # 统一下载器（Range 断点续传/重试/进度/取消）
│   ├── engine.py           # DiffSynth MiniMaxH3Pipeline 封装（仅用已验证 API）
│   ├── main.py             # 入口
│   └── ui/
│       ├── styles.py       # QSS 玻璃拟态主题引擎
│       ├── widgets.py      # 极光背景/玻璃面板/标题栏/拖拽区/流式布局
│       ├── main_window.py  # 主窗口 + AppContext + 协议弹窗
│       ├── page_generate.py# 生成主页（三栏布局 + 进度 + 播放器）
│       ├── page_market.py  # 模型市场（测速 + 卡片 + 下载队列）
│       ├── page_library.py # 我的模型 + LoRA 管理
│       ├── page_gallery.py # 作品库
│       └── page_settings.py# 设置/协议/关于
├── packaging/              # PyInstaller spec + Inno Setup + 一键构建
├── docs/调研报告-MiniMax-H3与引擎选型.md
└── requirements.txt
```

## 诚实声明

1. **无模拟/伪造**：没有"假装在生成"的模拟模式；引擎或模型缺失时明确报错并引导安装。
2. **测速是真实的**：不存在随机数生成的速度。
3. **2K 限制如实呈现**：开源 H3-Base 最高短边 768 像素；2K 再生成模块未开源（仅官方 API）。
4. **负向提示词如实说明**：H3 为 CFG 蒸馏模型，负向提示词默认无效。
5. **取消生成如实说明**：去噪循环不可安全中断，软件不会假装能取消。

## 协议

- 软件代码：**CC BY-NC-SA 4.0**（署名-非商业性使用-相同方式共享）——免费开源，**禁止商用**；衍生作品须同协议发布
- MiniMax H3 权重：MiniMax H3 Community License（排除美国/欧盟/英国/韩国）
- 依赖：diffusers 生态 Apache-2.0；PySide6 LGPL（动态链接，商用分发友好）；bitsandbytes MIT

---

**创作者：Bullobis** · 开源地址：https://github.com/Bullobis/Kevrai-omni
本项目免费开源，仅供个人学习、创作与研究使用，严禁商用。
