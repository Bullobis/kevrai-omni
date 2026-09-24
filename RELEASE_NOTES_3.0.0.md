# Kevrai Omni v3.0.0 — 安全加固 + 体验打磨 + 文档与发布工程统一

发布日期：2026-09-24
上一版本：v2.9.0

> 这是一份发布说明**草稿**，随 `kevrai-forge/v3.0.0` 分支一并整理。
> `scripts/release.sh` 发布时会自动把本文件复制为 GitHub Release notes。

v3.0.0 是一次**安全与工程基线收敛**版本：把分散在 README / INSTALL /
RELEASE / SECURITY 里的版本号、产物名、联系方式统一到 3.0.0；补齐本地开发、
API、故障排查三份文档；同时打磨界面体验并接入 Remotion 动画视频流水线。
**无破坏性 API 变更**，旧的 `/api/*`、`/v1/*`、WebSocket 路径与响应形状保持不变。

---

## 1. 安全加固

| 项 | 说明 |
|---|---|
| 🔒 **sidecar 网络面收敛** | sidecar 仅绑定 `127.0.0.1:17890`，CORS 限定在 Electron 本地源与 dev server（`app://.`、`file://`、`localhost:5173/3000`），不再对任意源开放；渲染层 `connect-src` 明确指向本机回环。 |
| 🔑 **HF Token 保护** | `hf_token` / `ms_token` 等敏感设置在 `GET/PUT /api/settings` 响应中**一律脱敏**，只回传 `*_set` 存在性标记，永不把明文 Token 吐给渲染层；Token 仅由 sidecar 服务端在下载 gated 模型时作为 `Authorization: Bearer` 附加给 HuggingFace。 |
| 🛡️ **下载主机白名单 + SHA-256** | 模型/引擎下载继续走默认白名单主机（huggingface.co、hf-mirror.com、github.com、pypi 官方/国内镜像），钓鱼 CDN `hf-cdn.sufy.com` 在三层（schema / Pydantic / 运行时）硬封禁；引擎清单带 SHA-256 时强制校验。 |
| 📮 **安全报告通道** | SECURITY.md 中失效的占位邮箱 `security@kevrai-studio.example` 已替换为 GitHub Security Advisory 私有报告通道。 |

> 说明：sidecar 不引入额外的调用方 Bearer 鉴权——它的安全模型是
> 「仅本机回环可达 + CORS 白名单 + 下载主机白名单」，而非网络层面的 token 校验。

## 2. UI / UX 改进

| 项 | 说明 |
|---|---|
| 🌗 **浅色主题修复** | 修复浅色主题下部分面板、消息区、卡片对比度不足的问题，两套主题可读性均达标。 |
| ⏳ **骨架屏** | 模型列表 / 目录加载期间以骨架屏占位，替代空白闪烁，交互更平稳。 |
| 🧭 **侧边栏折叠** | 侧边栏支持折叠为图标轨，释放内容区宽度，状态可持久化。 |
| ✨ **过渡动画** | 面板切换、列表进入、加载态补充轻量过渡动画，避免生硬跳变。 |

## 3. DevOps / 发布工程

| 项 | 说明 |
|---|---|
| 🏷️ **版本号统一** | `package.json` 与 `python/pyproject.toml` 统一为 `3.0.0`；README 徽章、INSTALL 产物名、RELEASE 手册全部对齐，清除残留的 `2.8.1` / `2.6.0` / `2.4.1` / `kevrai-studio` 旧引用。 |
| 📦 **产物命名统一** | 统一走 `electron-builder.yml` 的 `artifactName` 模板：`Kevrai-Omni-3.0.0-{arch}.exe / .AppImage / .deb / .dmg`；随包发布 `latest.yml` / `latest-linux.yml` 供应用内自动更新。 |
| 🔁 **CI build 与脚本对齐** | `scripts/release.sh` 从 `package.json` 读版本、预检 token 形状、拒绝历史泄露前缀、自动复制 `RELEASE_NOTES_<VERSION>.md`、上传 `.blockmap` 差分更新块，并打印产物 SHA-256 便于核对。 |

## 4. Remotion 动画视频集成

新增独立的 `remotion/` 工程，用 React 代码化生成宣传物料（Logo 片头
`LogoIntro`、版本海报 `VersionPoster`，H.264 MP4）：

```bash
cd remotion && npm install
npm run dev         # Remotion Studio 实时预览
npm run render:all  # 渲染到 remotion/out/*.mp4
```

这与运行时的 LTX-2.5 视频生成相互独立：Remotion 负责**代码化宣传动画**，
LTX-2.5 负责**用户在应用内文生/图生视频**。

## 5. 文档完善

- 新增 **[docs/DEVELOPMENT.md](docs/DEVELOPMENT.md)**：Windows / Linux / macOS
  本地开发环境搭建、从源码运行、测试、打包、常见开发问题。
- 新增 **[docs/API.md](docs/API.md)**：sidecar 全部 REST 端点与 WebSocket
  （含 `/v1/*` OpenAI 兼容面）的方法、路径、请求/响应示例。
- 新增 **[docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md)**：sidecar 启动失败、
  模型下载、引擎安装、LTX 显存、端口冲突、浅色主题、日志位置。
- README 增加上述文档链接与 Remotion 说明。

---

## 升级注意事项

- **普通用户**：直接覆盖安装新版即可；已下载到 `AppData/KevraiOmni/`
  （macOS/Linux 对应用户数据目录）的引擎与模型会被自动识别，无需重新下载。
- **开发者**：
  - 版本号以 `package.json` 为准，发版前确认 `python/pyproject.toml` 与
    `python/app/__init__.py` 的 `__version__` 同步。
  - Python 依赖无新增破坏性变更；建议在干净 venv 里重新
    `pip install -r python/requirements.txt`。
  - 跑一遍 `npm run test:python && npm run test:js && npm run smoke` 再发版。
- **配置兼容**：`/api/settings` 的字段保持向后兼容，仅敏感字段改为脱敏返回；
  外部脚本若之前误读 `hf_token` 明文，需改为依据 `hf_token_set` 判断。
