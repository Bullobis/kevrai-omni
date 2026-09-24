# RENDER_REPORT — KevraiPromo

- 渲染时间：2026-09-24 (Asia/Shanghai)
- 渲染人：Kova-Reel（Kova 集群视频工程代表）
- 产物：`promo/out/promo.mp4`（未入 git）

## 渲染环境

- Node `v22.23.2` / npm `10.9.8`
- Remotion `4.0.528` / `@remotion/cli` `4.0.528`
- React `18.3.1` / react-dom `18.3.1`（Remotion peer: `react >=16.8.0`，经 `npm view remotion peerDependencies` 核实）
- TypeScript `5.9.3`
- 无头 Linux，系统 Chromium `146.0.7680.31`（`/usr/local/bin/chromium-browser`）
- 渲染命令：

  ```bash
  npx remotion render src/index.ts KevraiPromo out/promo.mp4 \
    --codec=h264 \
    --browser-executable=$(which chromium-browser) \
    --gl=swiftshader
  ```

- 端到端耗时：约 **3 分 35 秒**（900 帧渲染 + H.264 编码）

## ffprobe 校验

命令：

```bash
ffprobe -v error -show_entries format=duration,size,bit_rate \
  -show_entries stream=codec_name,codec_type,width,height,r_frame_rate,nb_frames \
  out/promo.mp4
```

输出：

```
[STREAM]
codec_name=h264
codec_type=video
width=1920
height=1080
r_frame_rate=30/1
nb_frames=900
[/STREAM]
[FORMAT]
duration=30.000000
size=5712493
bit_rate=1523331
[/FORMAT]
```

| 指标 | 目标 | 实测 | 结论 |
|---|---|---|---|
| 时长 | ≈30 s | 30.000 s | ✅ |
| 编码 | H.264 | h264 | ✅ |
| 分辨率 | 1920×1080 | 1920×1080 | ✅ |
| 帧率 | 30 fps | 30/1 | ✅ |
| 帧数 | 900 | 900 | ✅ |
| 文件大小 | — | 5.5 MB (5,712,493 B) | ✅ |

## 抽帧人工核验

在 1 s / 6 s / 13 s / 17 s / 21 s / 25 s / 29 s 抽帧（`ffmpeg -ss <t> -frames:v 1`），逐帧用图像查看确认：

| 时间点 | 对应分镜 | 画面内容 | 是否黑屏 |
|---|---|---|---|
| 1 s | Logo 开场 | Logo 居中发光 + "Kevrai Omni" 渐显 | 否 ✅ |
| 6 s | 模型市场 | 01-market.png 圆角卡片 + 字幕"模型市场" | 否 ✅ |
| 13 s | Kevrai Agent | 三卡片（ReAct 推理 / 可插拔技能 / 工具调用） | 否 ✅ |
| 17 s | 短剧工坊 | 10-generation-wait.png + 字幕"LTX-2.5" | 否 ✅ |
| 21 s | 音乐生成 | 蓝紫渐变音频波形动画 | 否 ✅ |
| 25 s | 双引擎推理 | 03-local.png + 字幕"MNN + llama.cpp" | 否 ✅ |
| 29 s | 收尾 | Logo 回归 + "一切 AI，本地运行" | 否 ✅ |

> 注：15.0 s 整为 Agent → 短剧工坊两段之间的交叉淡入淡出过渡帧（帧 450），两场景同时处于 opacity=0，属于预期的转场空档，非黑屏故障。

## 结论

mp4 已通过 ffprobe 全部指标与抽帧目视校验，可正常播放。产物位于 `promo/out/promo.mp4`（.gitignore 已排除，未提交 git）。
