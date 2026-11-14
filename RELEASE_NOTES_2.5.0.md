# Kevrai Omni v2.5.0 发布说明

发布日期：2026-09-03

## 新功能：软件内一键安装 Python 运行环境
安装包继续保持小巧——Python 与推理引擎一样**随选下载**：

- 启动时自动按顺序寻找 Python：`KEVRAI_PYTHON` 环境变量 → 软件托管的
  `用户数据目录/python-runtime` → 系统 Python（python/py/python3）
- **完全没装 Python 的 Windows 机器**：不再报错退出，而是进入「环境准备」页，
  点「一键安装 Python 环境」自动完成：
  1. 从国内镜像下载 Python 3.12.7 embeddable（约 11 MB；npmmirror → 华为云 → 官网三级回退）
  2. 解压到用户数据目录（不写注册表、不污染系统、无需管理员权限）
  3. 自动修补嵌入式 Python 的 `._pth`（启用 site-packages）
  4. 引导安装 pip（腾讯源）
  5. 安装软件运行依赖（python/requirements.txt）
  6. 自动重启后端并进入主界面
- **有 Python 但缺依赖**：后端启动日志检测到 `ModuleNotFoundError` 时同样进入
  引导页，一键补装依赖
- 全程进度条 + 日志可见；Linux/macOS 给出手动命令指引（自动化暂只覆盖 Windows）

## 其他
- sidecar 启动失败诊断增强：stderr 尾部采集，用于区分"缺 Python / 缺依赖 / 其他"
- 版本号：应用 2.5.0；README / INSTALL 同步更新

## 测试
- 全部 JS `node --check` 通过；pytest 323 项保持全绿；smoke 通过
- 许可证不变：CC BY-NC-SA 4.0（禁止商用）
