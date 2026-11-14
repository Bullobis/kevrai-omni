# Kevrai v4.0.3 — version bump & copyright cleanup

## 4.0.3（2026-10-08）

### Fixed
- **Version sync**：`package.json` / `python/app/__init__.py` /
  `catalog/models.json` / `catalog/engines.json` 全部对齐到 **v4.0.3**
  （v4.0.2 tag 上产物文件名误为 `Kevrai-Omni-4.0.1-*`，因为 tag bump
  时忘了同步代码内版本号。此补丁修正该问题，产物文件名将正确显示为
  `Kevrai-Omni-4.0.3-*`）。
- **Changelog**：本文件 + `RELEASE_NOTES_4.0.1.md` 一并纳入版本归档，
  方便用户按 tag 查 release notes（历史 4.0.0 / 4.0.1 / 4.0.2 无独立
  notes，从 tag 页可看 commit 列表）。

### Housekeeping
- 未包含运行时行为变更（v4.0.3 是纯版本号 + 元数据修正）。
