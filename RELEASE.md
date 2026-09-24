# Release Runbook — Kevrai Omni v3.0.0

本文件是发布到 GitHub Releases 的操作手册。实际发布逻辑由
[`scripts/release.sh`](scripts/release.sh) 实现，本文件描述其前置条件与步骤。

## ⚠️ 发布前：轮换 / 使用新 token

历史上曾在对话中泄露过一个 PAT，`scripts/release.sh` 已内置保护：
当 `GITHUB_TOKEN` 以泄露前缀 `ghp_xxxxxxxx...` 开头时**直接退出**。

- 到 GitHub → Settings → Developer settings → Personal access tokens 生成新 PAT
  （scope 只需 `repo`；组织仓库 release 如需 `read:org`）。
- 新 token 必须以 `ghp_` 或 `github_pat_` 开头且长度 ≥ 40，否则脚本拒绝。
- **不要**把 token 提交进仓库或贴进聊天。

## 版本号单一来源

版本号只在一处维护：[`package.json`](package.json) 的 `version` 字段。
`scripts/release.sh` 自动读取它作为 `VERSION` 与 tag（默认 `v<VERSION>`）。

Python sidecar 版本见 [`python/pyproject.toml`](python/pyproject.toml) 与
`python/app/__init__.py` 的 `__version__`；发版前确认二者与 `package.json`
一致（CI 有回归测试锁定版本统一）。

## 前置条件

```bash
export GITHUB_TOKEN=<新 PAT>
export REPO=Bullobis/kevrai-omni     # 或你有权限的 fork
```

- `gh` CLI 已安装并可通过 `gh auth status`。
- 工作树干净（或已接受脚本的未提交变更警告）。
- 目标 tag（默认 `v3.0.0`）在远端**尚不存在**。
- 本次发布的说明写在 `RELEASE_NOTES_<VERSION>.md`（脚本会自动复制为
  release notes；不存在时才回退到自动生成的模板）。

## 发布步骤

```bash
# 0. 先跑测试（务必全绿）
npm run test:python
npm run test:js
npm run smoke

# 1. 打 Windows 安装包（需 Windows，或在 Linux 上用 wine，见 scripts/build_linux.sh）
bash scripts/build_windows.sh

# 2. 打 Linux 包（AppImage + deb）
npx electron-builder --linux --publish never

# 3. 一键发布（预检 → 收集 build/output/* → 复制 RELEASE_NOTES_<VERSION>.md → gh release create）
bash scripts/release.sh
```

常用 flags：

```bash
bash scripts/release.sh --dry-run     # 只打印将要执行的动作，不真正发布
bash scripts/release.sh --skip-build  # 跳过打包，直接发布 build/output/ 已有产物
TAG=v3.0.1 bash scripts/release.sh    # 覆盖默认 tag
```

## 产物命名（electron-builder.yml `artifactName`）

```
Kevrai-Omni-<VERSION>-x64.exe        # Windows NSIS 安装包
Kevrai-Omni-<VERSION>-x64.zip        # Windows 便携版
Kevrai-Omni-<VERSION>-x86_64.AppImage
kevrai-omni_<VERSION>_amd64.deb
Kevrai-Omni-<VERSION>.dmg            # macOS（x64 / arm64）
latest.yml / latest-linux.yml        # electron-updater 自动更新元数据（必须随包发布）
```

脚本会同时上传 `.blockmap` 差分更新块，确保应用内自动更新可用。

## 验证发布成功

```bash
gh release view v3.0.0 --repo "$REPO"
# 应能看到上述 exe / AppImage / deb / dmg / latest*.yml 等资产
# 脚本结尾会打印第一个产物的 SHA-256，可与下载页展示值核对
```

## 升级 / 发补丁版本

1. 改 `package.json`（以及 `python/pyproject.toml`、`python/app/__init__.py`）的
   `version` 到新版本。
2. 新建对应的 `RELEASE_NOTES_<新版本>.md`。
3. 重新执行上面「发布步骤」三步，脚本会自动用新 tag。
