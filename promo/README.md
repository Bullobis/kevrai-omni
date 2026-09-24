# Kevrai Omni — Promotional Video (Remotion)

独立的 Remotion 工程，用于渲染 Kevrai Omni 的约 30 秒宣传视频。
与主项目 `renderer/`、`electron/`、`python/` **零耦合**，仅消费主仓库 `assets/media/logo.png` 与 `screenshots/*.png`（构建时已复制到 `public/`）。

## 技术栈

- Remotion `4.0.528` + `@remotion/cli` `4.0.528`
- React `18.3.1` / react-dom `18.3.1`（Remotion peer: `react >=16.8.0`）
- TypeScript `5.9.3`
- 输出：1920×1080 @ 30fps，H.264 mp4，时长 900 帧 = 30.0s

## 目录结构

```
promo/
├── package.json
├── tsconfig.json
├── remotion.config.ts
├── public/
│   ├── logo.png                 # 来自 assets/media/logo.png
│   └── screens/01-market.png … # 来自 screenshots/
├── src/
│   ├── index.ts                 # registerRoot
│   ├── Root.tsx                 # Composition id=KevraiPromo
│   └── compositions/
│       └── KevraiPromo.tsx      # 8 段主合成
└── out/promo.mp4                # 渲染产物（不入 git）
```

## 视频分镜（30 fps，共 900 帧）

| 帧区间 | 时间 | 内容 |
|---|---|---|
| 0–90 | 0–3s | Logo 开场 + "Kevrai Omni" / "本地 AI 工作站" |
| 90–210 | 3–7s | 模型市场（01-market.png） |
| 210–330 | 7–11s | 硬件体检（02-engines.png） |
| 330–450 | 11–15s | Kevrai Agent（视觉卡片） |
| 450–570 | 15–19s | 短剧工坊（10-generation-wait.png） |
| 570–690 | 19–23s | MiniMax-Music3（波形动画） |
| 690–810 | 23–27s | 双引擎推理（03-local.png） |
| 810–900 | 27–30s | 收尾 Logo + "一切 AI，本地运行" |

## 渲染

```bash
cd promo
npm install
npx remotion render src/index.ts KevraiPromo out/promo.mp4 --codec=h264
```

无头 Linux 备注：
- 首次 `npm install` 会随 Remotion 下载 Chromium（~150MB）。
- 若沙箱限制可指定系统 Chromium：
  `--browser-executable=$(which chromium-browser)`
- 若 GPU/GL 不可用，可回退软件渲染：`--gl=swiftshader`。

## 校验

```bash
ffprobe -v error -show_entries format=duration,size \
  -show_entries stream=codec_name,width,height,r_frame_rate \
  out/promo.mp4
```

详见 `RENDER_REPORT.md`。
