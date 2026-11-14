# Kevrai Omni v3.12.0 — 完整 R1 蒸馏家族与 Ovis2 视觉模型

## 主要变化

### 模型市场
- 模型数量由 v3.11.0 的 222 个扩充至 **228 个**；引擎数量保持 32 个。
- 补齐 **DeepSeek-R1 官方蒸馏家族**，覆盖从端侧到旗舰的多个规格，均为 MIT 许可，并标注了各量化档的文件体积：
  - R1-Distill-Qwen-1.5B（端侧，Q4 约 1.1 GB）
  - R1-Distill-Qwen-7B（Q4 约 4.7 GB）
  - R1-Distill-Qwen-14B（Q4 约 9 GB）
  - R1-Distill-Llama-8B（Q4 约 4.9 GB）
  - R1-Distill-Llama-70B（旗舰开源推理，Q4 约 42.5 GB）
- 新增 **Ovis2 16B**：强图像 / 文档理解的视觉语言模型，Apache-2.0，transformers 原生支持。

### 数据质量
- 对新增模型逐一核对了上游仓库、许可与文件体积；确认官方蒸馏集的具体型号范围，未收录不存在的条目。

## 验证
- Python 后端全量测试通过（1482 项）。
- JavaScript 语法检查通过（46 个文件）。
- 目录结构与多源字段校验通过，无已知失效镜像引用。

## 安装
- Linux：`.AppImage` 与 `.deb`（amd64）。
- macOS：arm64 与 x64 的 `.dmg`。
- Windows：`.exe` 安装包与 `.zip`。
