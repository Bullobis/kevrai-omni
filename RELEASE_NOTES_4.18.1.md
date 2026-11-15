# Kevrai Omni v4.18.1

> v4.18.0 的紧急修复版：修复已发布安装包中图片生成请求字段错误、TripoSG 官方 CLI 调用签名错误，以及 CI 的 lint / mypy 回归。三平台安装包由 GitHub Actions 原生构建。

## 修复

- **图片生成恢复**：`/api/multimodal/generate-image` 不再把视频的 `audio_path` / `video_path` 误传给图片管理器；图片文生图/图生图请求不会因为缺字段直接 500。
- **TripoSG 恢复**：官方 CLI 调用改为 `python -m scripts.inference_triposg --image-input ... --output-path ...`，不再出现 “multiple values for module”。
- **TRELLIS 错误类型**：没有返回 mesh 时抛出已定义的 `ThreeDEngineMissing`，不再引用未定义的 `ThreeDModelError`。
- **视频空提示词测试**：允许图生视频/音频驱动模型的空提示词；纯文本请求仍由运行时返回 400，测试与真实行为一致。
- **CI 回归**：修复导入排序、未使用变量、未使用 `shutil`、`contextlib.suppress` 和 mypy 类型错误。

## 与 v4.18.0 相同的范围

- 图片页 20 个选项、视频页 22 个选项、音乐页 12 个选项、3D 页 26 个选项。
- 259 个市场模型均带 capability；8 个尚未接官方推理的 3D 条目明确显示“本地暂无适配”并禁用运行，不谎称可运行。
- ComfyUI 仍只作为后台引擎，普通界面不出现节点或工作流 JSON。
- Linux AppImage/deb、Windows NSIS/zip、macOS dmg 由 CI 构建。

## 验证

- `python3 -m pytest -q python/tests/test_v24_api.py python/tests/test_ltx_runtime.py python/tests/test_three_d_runtime.py`：153 passed。
- 图片/API 定向：37 passed。
- `ruff check python/`：通过。
- `mypy python/app/` 相关文件：0 errors。
- `scripts/check-versions.sh`：通过。

## 已知边界

- 视频音频输入、MiniMax Music 3、3D 官方权重仍需要在目标机器安装权重后做端到端生成验证。
