# Kevrai Omni v3.15.0 — 经典图像 / 视频生成模型回填

## 主要变化

### 模型市场
- 模型数量由 v3.14.0 的 234 个扩充至 **238 个**；引擎数量保持 32 个。
- 补全经典生成模型，覆盖从早期到现代的常用基座：
  - **Stable Diffusion 1.5**：512px 文生图，插件与 LoRA 生态最庞大。
  - **Stable Diffusion XL Base 1.0**：1024px 文生图经典基座。
  - **Stable Video Diffusion XT**：单张图片生成短视频。
  - **HiDream-I1-Fast**：开源图像生成 MoE 的快速版本。

### 数据质量
- 对新增模型核对了上游仓库、许可与权重体积；各模型在安装前可查看所需显存与磁盘空间。

## 验证
- Python 后端全量测试通过（1503 项）。
- JavaScript 语法检查通过（46 个文件）。
- 目录结构与多源字段校验通过，无已知失效镜像引用。

## 安装
- Linux：`.AppImage` 与 `.deb`（amd64）。
- macOS：arm64 与 x64 的 `.dmg`。
- Windows：`.exe` 安装包与 `.zip`。
