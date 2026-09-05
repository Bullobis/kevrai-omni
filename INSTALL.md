# Install — Kevrai Omni v2.6.0

Kevrai Omni 采用**标准安装包**交付（不是解压即用的便携包）：安装后自动创建桌面快捷方式，
推理引擎与模型不随安装包分发，首次使用时在软件内按需下载（已存在自动跳过，可检查更新）。

## Windows（推荐，面向普通用户）

1. 到 [Releases](https://github.com/Bullobis/kevrai-omni/releases) 下载 `Kevrai-Omni-Setup-2.6.0.exe`
2. 双击安装 → 桌面自动出现 **Kevrai Omni** 快捷方式（也可在设置中改变安装目录）
3. 双击快捷方式启动：若机器上没有 Python，会进入「环境准备」页，点一下
   「一键安装 Python 环境」即可（约 11 MB，国内镜像，装到用户数据目录，不污染系统）；
   之后首次会显示「三步上手」引导

## Linux

| 形态 | 文件 | 使用方式 |
|---|---|---|
| AppImage | `Kevrai-Omni-2.6.0-x86_64.AppImage` | `chmod +x` 后双击或命令行运行 |
| deb | `kevrai-omni_2.6.0_amd64.deb` | `sudo apt install ./kevrai-omni_2.6.0_amd64.deb` |

deb 安装会写入桌面项与开始菜单项；AppImage 无需安装。

## macOS

下载 `Kevrai-Omni-2.6.0.dmg`（x64 / arm64 两版），拖入 Applications。
内部构建未签名，首次打开请在「系统设置 → 隐私与安全性」中允许。

## 三步上手

1. **安装引擎**：「AI 引擎」页选择引擎（如 llama.cpp、MNN）点安装。引擎下载到
   `AppData/KevraiOmni/engines/`（Windows），已安装会提示并跳过；「检查引擎更新」
   可查询新版本并一键更新。
2. **下载模型**：「模型市场」搜索并安装模型（支持模糊搜索与中文搜索）；GGUF 模型可在
   详情页按量化档位单独下载；本地已有模型文件可直接拖入窗口导入。
3. **开始生成**：对话（llama.cpp / MNN）、图片、视频（LTX-2.5）、音频、3D —— 选模型、
   输提示词、点生成。

## gated（受控访问）模型

LTX-2.5 等部分仓库为 HuggingFace gated 仓库：

1. 先在 HuggingFace 对应仓库页面登录并接受许可协议（申请获批后生效）
2. 在 Kevrai Omni「设置 → HuggingFace Token」填入你的 Token（仅保存在本机）
3. 之后即可正常下载；未配置 Token 时软件会给出明确提示

## 从源码构建（开发者）

```bash
git clone https://github.com/Bullobis/kevrai-omni.git
cd kevrai-omni
npm ci
npm run test:js          # JS 语法冒烟
cd python && pip install -e ".[dev]" && python -m pytest -q tests/ && cd ..
npm run build:win        # 产出 NSIS 安装包（需 Windows；Linux 下可用 wine，见 scripts/build_linux.sh）
```

## 卸载

Windows 通过「设置 → 应用」卸载；默认保留 `AppData/KevraiOmni/` 中已下载的引擎与模型
（重装后自动识别、无需重新下载）。如需彻底清理，手动删除该目录即可。

## License

Kevrai Omni Community License v1.0 (English, see `LICENSE`) — source code is public and free for non-commercial use, modification, and distribution. **Commercial use is permitted but requires prior written Commercial Authorization from the Licensor** (apply: 2671369836@qq.com). Unauthorized commercial use may result in cease-and-desist / lawyer's letters and legal action. Derivative works must be open-sourced under the same license. Third-party models/engines/weights are governed by their own upstream licenses.
