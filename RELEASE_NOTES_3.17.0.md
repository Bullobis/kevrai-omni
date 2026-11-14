# Kevrai Omni v3.17.0 — 向量嵌入引擎（Sentence Transformers）

## 主要变化

### 新增向量嵌入能力
- 新增 **Sentence Transformers** 引擎，在 CPU 与 CUDA 下将文本编码为向量，用于语义检索、相似度计算、聚类与 RAG 流程。
- 侧边栏新增「向量嵌入」工作台：选择模型后可一次提交多条文本（每行一条），支持向量归一化，结果展示维度与向量预览，并可一键复制完整 JSON。
- 提供与 OpenAI 兼容的嵌入接口：`POST /v1/embeddings`，支持 `float` 与 `base64` 输出、词元用量统计；另提供 `GET /api/embeddings/capabilities` 查询引擎状态。
- 模型市场新增「向量嵌入」分类与筛选。

### 模型市场
- 模型数量由 v3.16.0 的 240 个扩充至 **246 个**；引擎数量由 33 个增加至 **34 个**。
- 新增嵌入模型：
  - **BGE-M3**：多语言、多功能嵌入（稠密/稀疏/ColBERT）。
  - **BGE Large** 英文 / 中文 v1.5：高质量句向量。
  - **GTE Large**：通用文本嵌入，体积小巧，适合 CPU 场景。
  - **Multilingual E5 Large**：多语言句向量。
  - **GTE Qwen2 7B Instruct**：大规模指令式嵌入（需较大显存）。

### 安装与依赖
- 引擎随模型市场一键安装；安装前可查看所需显存、内存与磁盘空间。
- 固定 Sentence Transformers 版本，依赖随引擎独立部署，不影响系统环境。

## 验证
- Python 后端全量测试通过（1560 项，较上一版本新增 27 项嵌入相关测试）。
- 代码静态检查通过；JavaScript 语法检查通过（48 个文件）。
- 冒烟测试通过；在真实环境中确认引擎就绪，使用真实权重完成文本嵌入，并核对向量维度、归一化与语义相似度结果。

## 安装
- Linux：`.AppImage` 与 `.deb`（amd64）。
- macOS：arm64 与 x64 的 `.dmg`。
- Windows：`.exe` 安装包与 `.zip`。
