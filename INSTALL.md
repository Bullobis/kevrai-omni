# Install — Kevrai Omni v2.2.0

## 普通用户：选你的平台

| 平台 | 下载 | 安装 |
|------|------|------|
| **Linux x64** | `Kevrai-Omni-2.2.0-linux-x64-portable.tar.gz` | `tar -xzf Kevrai-Omni-2.2.0-linux-x64-portable.tar.gz && cd Kevrai-Omni-2.2.0-linux-x64 && ./run.sh` |
| **Windows x64** | `Kevrai-Omni-2.2.0-win32-x64-portable.zip` | 解压到任意目录 → 双击 `run.bat`；想加桌面快捷方式 → 双击 `install-shortcut.bat` |

> **首次启动**：软件会自动用阿里云 PyPI 镜像装 Python 依赖。引擎和模型**按需下载**——在"环境管理"页或"模型市场"点下载即用。

## 首次启动小贴士

1. 打开 **设置 → 下载源**，勾选你所在网络快可达的镜像（默认含阿里云/清华/HF-Mirror 等）。
2. 点 **"测速全部镜像"** 让软件测一下，UI 会按延迟+吞吐排好序。
3. 在 **环境管理** 页检查 Python / GPU / 磁盘 / 引擎状态。
4. 在 **模型市场** 浏览 62 个模型，每个都自带多镜像；点下载就自动选最快源。

## Developer: 从源码构建

```bash
# 要求：Python 3.11+ · Node 22+ · npm 10+
tar -xzf kevrai-studio-2.2.0-source.tar.gz
cd kevrai-studio-2.2.0
npm install
pip install -i https://mirrors.aliyun.com/pypi/simple/ -r python/requirements.txt
bash scripts/smoke.sh     # 9 步验证
cd python && pytest -ra   # 195 tests
```

要打单文件 Windows 安装包（需要 Windows + Wine 或在 Windows 上跑）：

```bash
bash scripts/build_windows.sh   # 产物在 dist/
bash scripts/release.sh         # 推到 GitHub Releases
```

## 故障排查

- **白屏/启动失败**：日志在 `~/.local/share/KevraiOmni/logs/main.log`（Linux/macOS）或 `%APPDATA%\KevraiOmni\logs\main.log`（Windows）。
- **下载慢**：到"环境管理 → 下载源"重新测速，或在设置里换镜像。
- **GPU 检测不到**：先在系统装好 NVIDIA / AMD 驱动，再重启软件。
- **Python 依赖装不上**：手动跑 `pip install -i https://mirrors.aliyun.com/pypi/simple/ -r python/requirements.txt`。
