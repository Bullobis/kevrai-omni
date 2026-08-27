# Release Runbook — Kevrai Studio v1.0.0

## ⚠️ 第一件事：轮换 token

对话里贴过的 token `ghp_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx` **必须立刻吊销/轮换**。`scripts/release.sh` 已内置保护：检测到这个前缀直接退出。

去 GitHub → Settings → Developer settings → Personal access tokens → 删旧、生成新的（`repo` + `read:org` scope 足够）。

## 步骤

```bash
# 0. 在能上网到 github.com 的机器上（不是这个 Cloud Studio 沙箱）
export GITHUB_TOKEN=<新 token>
export REPO=<你的用户名>/kevrai-studio     # 或 Bullobis/kevrai-studio（如果你有权限）

# 1. 打 Windows 安装包（需 Windows / 或 Wine）
bash scripts/build_windows.sh

# 2. 打 Linux 包（AppImage + deb，方便开发者）
npx electron-builder --linux

# 3. 发布
bash scripts/release.sh
```

`scripts/release.sh` 会：
- 校验新 token（拒绝泄露前缀）
- 校验 `gh auth status`
- 收集所有 artifact（`build/output/*.{exe,AppImage,deb}`）
- 生成 release notes
- 调用 `gh release create v1.0.0 --repo <repo> ...`

## 验证发布成功

```bash
gh release view v1.0.0 --repo $REPO
# 应该看到：
#   Kevrai Studio-Setup-1.0.0.exe
#   Kevrai Studio-1.0.0-x86_64.AppImage
#   kevrai-studio_1.0.0_amd64.deb
```

## 升级路径

- 改版本：`package.json` 和 `pyproject.toml` 同步改 `version`
- 重新打：上面 3 步
- 发新版：`TAG=v1.0.1 bash scripts/release.sh`