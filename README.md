# Kevrai Omni

> 版本：**v3.4.2**
> 一键本地 AI 工作站：LLM / TTS / 图像 / 视频 / 3D / 音频 / 超分辨率，llama.cpp + MNN 双引擎，硬件感知推荐，内置 LTX-2.5 视频生成、Agent 助手与超级搜索。

Kevrai Omni 是一个 **Electron + 原生 HTML/CSS/JS** 的桌面应用，搭配一个本地 Python（FastAPI）sidecar 负责模型目录、下载、引擎与推理。所有数据默认落在本机，不依赖远程服务即可使用已下载的模型。

---

## 功能一览

以下功能均可在 `renderer/` 与 `python/` 源码中找到对应实现：

- **模型市场**：浏览 / 搜索 / 筛选模型，支持模糊匹配、拼音式缩写、中文搜索；按分类、排序（相关度 / 热门 / 名称 / 体积）、热门开关过滤；虚拟滚动网格。
- **模型收藏与最近使用**：一键收藏模型（上限 200 个），记录最近打开的模型（上限 20 条），可在工具栏「收藏 / 最近使用」筛选。
- **本地模型管理**：从硬盘导入 GGUF / safetensors 文件或整个文件夹，也可直接把文件拖入窗口。
- **GGUF 仓库**：列出所有可用 GGUF 量化文件，按需单独下载，由 llama.cpp 或 MNN 加载。
- **AI 引擎管理**：llama.cpp 等引擎按需下载、安装 / 卸载 / 检查更新，已装自动跳过。
- **MNN 推理**：MNN 引擎页提供模型市场、下载、加载与对话。
- **硬件检测与推荐**：自动探测 CPU / 内存 / 磁盘 / GPU（nvidia-smi / rocm-smi / npu-smi / system_profiler），据此推荐合适的模型与量化档。
- **LTX-2.5 视频生成**：文生视频 / 图生视频，质量预设、宽高 / 帧数 / FPS / 步数 / CFG / 种子可调，MP4 / GIF 输出，任务进度与结果画廊。
- **AI Agent 助手**：通用 ReAct 助手面板，内置技能（含短剧工坊），技能可从本地目录 / zip / git 导入并启停。
- **待官方开源**：保留尚未开源的模型条目，官方公开权重后自动上架。
- **环境管理**：应用内检测缺失的 Python 依赖 / 引擎，缺什么装什么；离线 / 多源镜像支持。
- **命令面板**（Ctrl/⌘+K）：模糊搜索并执行任意操作，最近使用的命令置顶。
- **快捷键速查**（Ctrl/⌘+/）：应用内随时查看全部快捷键。
- **深色 / 浅色 / 跟随系统主题**：`<html data-theme>` 即时切换。
- **设置中心**：模型 / 引擎目录、主题、硬件加速、遥测、下载主机白名单、HuggingFace / 魔搭 Token。
- **Toast 通知**：右上角消息栈（成功 / 警告 / 错误 / 信息），自动消失、悬停暂停。
- **空状态引导与首次上手**：每个可空视图有统一空状态；首次启动三步引导（装引擎 → 下模型 → 生成）。
- **自动更新**：基于 electron-updater，检查 → 下载进度 → 重启安装。
- **中英双语**：界面文案支持中文 / 英文切换（命令面板内可一键切换）。

---

## 安装与运行

### 从发行包运行

不需要预装 Python。首次启动会自动引导便携版 Python 运行时与所需依赖，按界面提示完成环境准备即可。

### 从源码运行

前置：Node.js 22+、Python 3.11+（详见 [docs/DEVELOPMENT.md](./docs/DEVELOPMENT.md)）。

```bash
# 1. 安装前端依赖
npm install

# 2.（可选）安装 Python sidecar 依赖；不装时由应用内「环境管理」引导
cd python && pip install -r requirements.txt && cd ..

# 3. 启动
npm start
```

常用 npm 脚本（来自 `package.json`）：

| 命令 | 作用 |
|---|---|
| `npm start` | 启动应用（Electron） |
| `npm run dev` | 启动并打开 DevTools、开启渲染日志 |
| `npm run test:js` | 对 `electron/*.js`、`renderer/*.js`、`renderer/modules/*.js` 逐个做 `node --check` 语法检查 |
| `npm run test:renderer` | 运行渲染层单元测试（`node --test renderer/__tests__/*.test.js`） |
| `npm run test:python` | 运行 Python sidecar 测试（pytest） |
| `npm run smoke` | 端到端冒烟脚本（拉起 sidecar、打关键接口） |
| `npm run build:win` / `build:linux` / `build:mac` | 分别打包对应平台 |

---

## 数据备份与迁移

Kevrai Omni 的用户数据分为两部分：

1. **应用设置与下载的模型 / 引擎**：存放在操作系统的用户数据目录（userData）下。设置保存在 `settings.json`，模型默认在 `<userData>/models/`，引擎在 `<userData>/engines/`。
   - Windows：`%APPDATA%\KevraiOmni\`
   - macOS：`~/Library/Application Support/KevraiOmni/`
   - Linux：`~/.local/share/KevraiOmni/`（或 `$XDG_DATA_HOME/KevraiOmni/`）
2. **收藏、最近使用、命令面板历史**：保存在渲染层本地存储（localStorage）中，随浏览器配置目录一起持久化。

### 打开数据目录

最快捷的方式：按 `Ctrl/⌘+K` 打开命令面板，输入「打开数据目录」并回车，应用会在系统文件管理器中定位到数据根目录。

### 迁移到另一台机器

- **完整迁移（推荐）**：在旧机器上打开数据目录，将整个 `KevraiOmni` 文件夹（或至少 `models/`、`engines/`、`settings.json`）复制到新机器的相同位置，启动后自动识别。
- **仅迁移模型**：把 `<data_root>/models/` 目录整体拷到目标机器相同路径即可；也可在「本地模型」页把文件拖入窗口重新导入。
- **一键导出 / 导入**：通过 **设置中心 → 数据管理** 可将配置与个性化数据导出为备份文件，在新机器上导入恢复（收藏、最近使用等会一并还原）。

> 提示：卸载安装包默认**保留**用户数据目录；要彻底重置需手动删除上述目录（见 [docs/FAQ.md](./docs/FAQ.md)）。

---

## 快捷键

下表与 `renderer/modules/command-palette.js` 中实际注册的快捷键一一对应。Windows / Linux 用 `Ctrl`，macOS 用 `⌘`（Command）。

| 按键 | 功能 |
|---|---|
| `Ctrl/⌘ + K` | 打开 / 关闭命令面板 |
| `Ctrl/⌘ + /` | 打开快捷键速查 |
| `Ctrl/⌘ + ,` | 打开设置 |
| `Ctrl/⌘ + D` | 打开下载面板 |
| `Ctrl/⌘ + B` | 收起 / 展开侧边栏 |
| `Ctrl/⌘ + 1 … 9` | 切换到第 1–9 个页面 |
| `Ctrl/⌘ + 0` | 切换到最后一个页面（环境管理） |
| `/` | 聚焦搜索框（焦点不在输入框内时） |
| `Ctrl/⌘ + F` | 聚焦搜索框并全选已有内容 |
| `↑` / `↓` | 在命令面板中选择命令 |
| `Enter` | 执行选中命令面板项 |
| `Esc` | 关闭命令面板 / 速查 / 设置 / 下载 / 更新 / 引导弹窗；在输入框中先退出输入焦点 |

页面与 `Ctrl/⌘ + 数字` 的对应关系：

| 按键 | 页面 |
|---|---|
| `⌘1` | 模型市场 |
| `⌘2` | 硬件推荐 |
| `⌘3` | AI 引擎 |
| `⌘4` | MNN 引擎 |
| `⌘5` | AI Agent |
| `⌘6` | LTX-2.5 视频 |
| `⌘7` | 本地模型 |
| `⌘8` | GGUF 仓库 |
| `⌘9` | 待官方开源 |
| `⌘0` | 环境管理 |

应用内按 `Ctrl/⌘ + /` 可随时呼出同一份速查表。

---

## 配置说明

设置中心（`Ctrl/⌘ + ,`）分为以下分类，选项与 `renderer/modules/settings.js` 一致：

**通用**

- 模型存储目录：下载模型的存放位置（可点「浏览…」选择文件夹）。
- 引擎目录：llama.cpp / MNN 等引擎的存放位置。
- 主题：跟随系统 / 深色 / 浅色。
- 硬件加速：自动检测 / NVIDIA / AMD / 仅 CPU。
- 启用遥测：匿名使用统计（默认关闭）。

**下载与令牌**

- 高级：编辑下载主机白名单（默认 `huggingface.co, github.com`）。
- HuggingFace Token：仅 gated（受控访问）模型需要，如 LTX-2.5；填前需先在 HF 仓库页接受许可协议。仅保存在本机。
- 魔搭 ModelScope Token：仅私有 / 受控仓库需要。

**外观**

- 主题预览说明（主题在「通用」分类中切换）。

**关于**

- 当前版本号与项目主页链接。

设置底部支持「重置默认」「预览生成等待动画」「取消 / 保存」。

---

## 文档

- 开发环境搭建与命令：[docs/DEVELOPMENT.md](./docs/DEVELOPMENT.md)
- 常见问题（含数据备份 / 迁移）：[docs/FAQ.md](./docs/FAQ.md)
- 故障排查：[docs/TROUBLESHOOTING.md](./docs/TROUBLESHOOTING.md)
- 系统架构：[docs/ARCHITECTURE.md](./docs/ARCHITECTURE.md)
- API 参考：[docs/API.md](./docs/API.md)

## 许可

见 LICENSE 文件。
