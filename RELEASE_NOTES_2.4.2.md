# Kevrai Omni v2.4.2 发布说明

发布日期：2026-09-02

## 关键修复（建议 v2.4.1 用户升级）
1. **设置保存崩溃**：`PUT /api/settings` 引用了 Downloader 上不存在的
   `max_concurrent` 属性，导致每次保存设置都 500 —— 已修复。
2. **HF Token 无法下发**：`SettingsUpdate` 缺失 `hf_token` 字段，设置页填的
   Token 同步不到后端，gated 模型（如 LTX-2.5）实际下载不到。已修复并补
   HTTP 级回归测试（先复现、后修复）。
3. **converter 隐性崩溃**：`sys_executable()` 使用 `sys.executable` 但模块未
   导入 `sys`，调用即 NameError。已修复。

## 优化与清理
- 按用户偏好移除内置负面提示词：LTX 生成的 `negative_prompt` 默认值清空，
  界面文本框不再预填（字段保留，高级用户可自行填写）。
- 死代码清理：未使用导入 20+ 处（catalog/converter/downloader/engines/env/
  hardware/importer/ltx_runtime/main/mnn_runtime/runner）、xxhash 无效可选导入、
  只读场景多余 `global`、`needs_update` 占位死代码。
- 弃用 API：`datetime.utcnow()` → timezone-aware；pydantic protected
  namespace 警告全消（Settings/SettingsUpdate/LtxGenerateReq/MnnLoadReq/
  DramaRenderPlanReq）。
- INSTALL.md 重写至 v2.4.2：清除停留在 v2.2.0 的"便携包"旧说法，改为
  安装包 + 桌面快捷方式 + 引擎按需下载 + gated Token 指引。

## 测试
- 323 passed / 0 failed（新增 6 项 HTTP 级 v2.4.1 功能回归）
- 全部 JS `node --check` 通过；smoke.sh 通过
- 许可证不变：CC BY-NC-SA 4.0（禁止商用）
