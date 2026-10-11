# Kevrai v4.5.0 — 知识库 PDF 文档支持与模型市场布局修复

## 4.5.0（2026-10-11）

### Added — 知识库支持 PDF 文档

- 知识库添加文档现在支持 `.pdf`：上传 PDF 后会逐页提取其中的文字，
  再按现有方式分块并建立向量索引，可与文本、Markdown、JSON、CSV 文档一起检索和提问。
- 选择文件窗口已包含 PDF；PDF 内容以安全的编码方式传输，不依赖额外的上传服务。
- 当 PDF 是扫描件 / 图片版、没有可供提取的文字时，会明确提示暂不支持扫描件 OCR，
  不会静默建立一个空索引；损坏或无法读取的 PDF 会给出清晰的错误说明。

### Fixed — 模型市场首屏卡片重叠

- 修复模型市场在首次打开、切换侧边栏或启动布局尚未稳定时，模型卡片偶尔相互重叠、
  文字堆叠的问题。现在网格会在容器宽度变化后自动重新排版，首屏即显示整齐的卡片布局。

### 验证（本机实数）

| 检查项 | 结果 |
|---|---|
| Python 全量测试 | 1824 passed |
| JS 语法检查 | 56 files OK |
| Renderer 单测 | 275 passed |
| Smoke | All smoke tests passed |
| PDF 实测 | 含文字 PDF 可准确提取并进入索引；扫描件 / 损坏 PDF 提示正确 |

### Version bump

- package.json、python/app/__init__.py、catalog/models.json、catalog/engines.json、
  python/pyproject.toml 统一升级至 4.5.0。
