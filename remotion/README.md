# Kevrai Remotion

独立的 [Remotion](https://www.remotion.dev/) 工程，用 React 代码化生成 Kevrai Omni
的宣传动画视频（H.264 MP4）。本目录与主项目 Electron 工程完全隔离，拥有独立的
`package.json`，**不要**把这里的依赖加回主项目。

## Compositions

| ID | 时长 | 分辨率 | 用途 |
|---|---|---|---|
| `LogoIntro` | 10s @ 30fps (300 帧) | 1920×1080 | Logo 片头：粒子汇聚 → 品牌光晕 → 打字机副标题 → 功能标签 → 版本号淡入 |
| `VersionPoster` | 15s @ 30fps (450 帧) | 1080×1080 | 社交媒体方形版本海报：版本号落入 → 更新亮点左滑 → Download 按钮脉冲 |

### Props

两个 Composition 都在 `src/index.ts` 中声明了 `defaultProps`，可在渲染时用
`--props` 覆盖：

```bash
npx remotion render LogoIntro out/logo-intro.mp4 \
  --props='{"version":"v3.0.0","date":"2026-09-24"}'

npx remotion render VersionPoster out/version-poster.mp4 \
  --props='{"version":"v3.0.0","highlights":["新 LLM 引擎 2x 速度","Redesigned UI","3D preview"]}'
```

## 目录结构

```
remotion/
├── package.json          # 独立依赖（remotion / react / typescript）
├── remotion.config.ts    # CLI 配置
├── tsconfig.json
├── src/
│   ├── index.tsx         # registerRoot + 注册 Composition
│   ├── compositions/
│   │   ├── LogoIntro.tsx
│   │   └── VersionPoster.tsx
│   └── components/
│       ├── GradientBg.tsx
│       ├── ParticleField.tsx
│       └── TypeWriter.tsx
└── out/                  # 渲染产物（.gitignore，不提交）
```

## 本地开发

```bash
cd remotion
npm install

# 实时预览 / 调参（浏览器打开 Remotion Studio）
npm run dev

# 渲染全部合成
npm run render:all

# 只渲染单个
npx remotion render LogoIntro out/logo-intro.mp4 --codec=h264
```

> Linux 本地渲染需要 headless Chrome 系统依赖（`libgtk-3-0`、`libnss3`、
> `libasound2`、`libgbm1` 等）。Remotion 会自动下载 headless shell；缺库时
> 参考 `.github/workflows/remotion-render.yml` 里的 apt 安装列表。

## CI 定时渲染

`.github/workflows/remotion-render.yml`：

- **触发**：每月 1 号 UTC 00:00 cron；或 Actions 页面手动 `workflow_dispatch`
  （可输入自定义 `version`）。
- **步骤**：checkout → setup-node 20 → apt 装 headless Chrome 依赖 → `npm ci` →
  渲染两个 mp4 → `gh release upload` 到最新 Release → 上传 artifact。
- **超时**：15 分钟。

渲染产物**不提交到 git**（`remotion/out/` 已在根 `.gitignore`），通过 GitHub
Release 附件分发。
