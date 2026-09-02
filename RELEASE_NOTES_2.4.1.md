# Kevrai Omni v2.4.1 发布说明（修复优化版）

发布日期：2026-09-02

## 事实性修正（模型目录）
1. `minimax-h3` 条目原指向不存在的 `MiniMaxAI/Hailuo-H3`，现修正为 ComfyUI 官方重打包仓库
   `Comfy-Org/MiniMax-H3`；官方权重条目 `minimax-h3-omni` 保留 `MiniMaxAI/MiniMax-H3`，并补充 GitHub 源码源。
2. `minimax-h3-omni` 描述修正：本地开源的 H3-Base 最高输出 768p；2K 由未开源的
   H3-Regenerate-2K 模块完成（官方 2026-08-07 Reddit AMA 承诺将开源，暂无日期），目前 2K 仅可走官方 API。
3. `minimax-2k-pending` 更新为最新进展时间线（7/31 发布 → 8/3 H3-Base 开源 → 8/7 承诺 2K 模块开源）。
4. `ltx-2.5` 条目修正：官方最低显存 16GB（原写 12GB）；许可修正为
   LTX-2.x Community License（年收入 < 1000 万美元免费）；新增 `gated: true` 标记（HF 受控访问仓库）。
5. `qwen3.8-27b` 条目修正：明确为社区开发者 JonathanColetti 的无审查微调 + GGUF 量化版（非 Qwen 官方仓库），
   `repo` 指向真实下载源 `JonathanColetti/Qwen3.8-27B-Uncensored-GGUF`。

## 新功能
- **gated 下载支持**：设置页新增 HuggingFace Token；gated 模型下载自动附加
  `Authorization: Bearer` 请求头（Token 不会回显到进度快照/日志）；未配置 Token 时
  `/api/download/start` 返回 422 + 中文指引。
- **引擎更新检测**：`POST /api/engines/check-updates`（GitHub releases 查询，6 小时缓存）、
  `POST /api/engines/update`（一键更新）；`GET /api/engines` 附带版本号与最新 tag。
  新装引擎自动以当前最新 tag 作为基线，避免误报「有更新」。
- **首次启动新手引导**：三步上手浮层（localStorage 记忆，不再重复打扰）。

## 修复
- LTX 生成面板：低于官方最低 16GB 显存的预设标注「实验」，选择时显示明确提示。
- 产品名统一为 Kevrai Omni（原 Kevrai Studio），覆盖安装包、快捷方式、窗口标题、文档。

## 兼容性
- 升级覆盖安装即可；桌面快捷方式随安装包自动创建/更新。
- 许可证不变：CC BY-NC-SA 4.0（禁止商用）。
