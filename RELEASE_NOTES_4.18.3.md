# Kevrai Omni v4.18.3

> 本版本由 GitHub Actions 原生构建并发布 Linux AppImage/deb、Windows NSIS 安装器 + 便携 zip、macOS dmg（x64 + arm64）。安装包内置冻结 sidecar 与 pip-runner。

## 重点变化

- **Agent「深度思考」卡**：Agent 每一步原有的 `thought` 不再被丢弃；执行时实时展开灰色推理文本和工具调用，回答开始流式输出后自动收起。收起态保留两行、190 字以内的最新推理预览，点「点击展开」看完整推理；手动开合不会被后续步骤强行覆盖。浅色、深色主题均已适配，收起/展开两端都在打包后的应用里做了界面验证。
- **引擎与模型彻底解耦**：模型市场卡片同时提供「安装引擎 / 下载模型」两个独立按钮。引擎未安装时，下载按钮明确提示「引擎 X 未安装，请先点『安装引擎』」，不再自动替用户安装；引擎已安装后按钮变「引擎已安装」，下载入口始终保留。多引擎模型先选引擎，所有模型下载前先选仓库版本，GGUF 再选量化版本。
- **ModelScope 仓库版本修正**：按模型来源选择 `modelscope` 或 `hf`，ModelScope 默认 revision 为 `master`（HF 仍为 `main`）；修复 ModelScope 模型 404 与 revision 查询红 toast。
- **MNN 模型权重漏选修复**：自动挑文件补齐 `.weight`、`llm_config.json`、`llm.mnn.json`、`configuration.json`、`tokenizer.mtok`，不再只下载 1.7 MB 的 `llm.mnn` 而漏掉 365 MB 的 `llm.mnn.weight`。
- **MNN 下载完成态轮询递归修复**：完成/取消/失败先隐藏进度卡，停止 `pollDownload → refreshAll → pollDownload` 递归；完成提示只弹一次；只有无 `.part` 的文件才判定完整，取消后再次下载可续传。

## 验证

- renderer 全量：**462 pass，0 fail，0 skipped**。
- `python/tests/test_repo_picker.py`：18 passed；`plan-install` 专项：13 passed。
- 打包后的 `.deb` 与 AppImage 均确认 `app.asar` 内包含深度思考卡；在无 FUSE 环境用 AppImage 解包运行，Agent 页折叠/展开与预览样式实际渲染正常。
- MNN 引擎真实安装、Hunyuan-0.5B-Instruct-MNN 真实下载 11/11 文件（0.37 GB），加载后真实对话返回 10.83 秒 / 37.2 字/秒。
- 实机复跑还验证了系统包安装路径、模型市场首屏 21 张卡、未装引擎拦截提示、ModelScope 文件 200 响应。

## 已知限制

- 测试机无 GPU、3.9 GiB 内存，只验证 0.5B 级模型。
- 直接 `dpkg -i` 在缺少 `libnotify4`、`libsecret-1-0`、`libsecret-common` 的干净系统上需要先补依赖；应用本体不受影响。
- 本轮未覆盖 TTS、ASR、向量、分离、多模态、LTX、图片、3D 等页面的逐页真实生成。
