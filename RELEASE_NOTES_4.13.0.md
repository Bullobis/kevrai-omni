# Kevrai Omni v4.13.0

引擎补齐 + 架构地基 + 环境自检向导。本版按"旗舰能力三选全做 + 地基走
ComfyUI + 体验先做自检"推进，主题是**把"能下载"变成"能真跑、且跑得明白"**。

## 一、音乐：接入 ACE-Step 1.5（最大落差）

原实现只有 MusicGen（2023、纯器乐、CC BY-NC 非商用）。现在登记 ACE-Step 1.5：

| 项 | MusicGen | ACE-Step 1.5 |
|---|---|---|
| 许可 | CC BY-NC 4.0（非商用） | **MIT（可商用）** |
| 人声 | 无，纯器乐 | 有，支持演唱 |
| 显存 | — | 低于 4GB |
| 时长上限 | 60 秒 | **600 秒（10 分钟）** |
| 速度 | — | A100 <2 秒／3090 <10 秒一首 |

ACE-Step 与 MusicGen 是两套完全不同的架构（前者是 DiT+LM 两阶段、歌词独立
入参），因此走了独立的加载与合成路径，**不共用** MusicGen 的 `AutoProcessor`
+ `MusicgenForConditionalGeneration`。加载器依次尝试已知入口，全部失败时报清楚
"试过哪些"，而不是丢一个 ImportError。

时长上限按模型区分：MusicGen 仍是 60 秒（长音频会明显退化），ACE-Step 600 秒。

**许可如实标注**：新增 `LICENSE_WARNINGS`，每个模型都带许可与门槛，且
`capabilities()` 返回体里每个模型都**自带许可字段**——UI 展示模型时不可能只展示
模型不展示许可。MiniMax Music3 明确标注：非商用、约 57GB、官方参考部署要双 GPU，
且 HF 卡与 GitHub README 对最低配置的说法**不一致**（这是事实，不抹平）。
未登记模型一律返回 `unknown`，绝不默认成"可商用"。

## 二、TTS：接入 Kokoro

Kokoro 82M、Apache-2.0 可商用、CPU 即可实时、54 音色、9 语言（含普通话），
采样率 24kHz。相比 Piper（偏机械、22kHz）是明显改善。

音色判定做了严格的边界处理：Piper 音色一律形如 `en_US-libritts_r-medium`
（含连字符），Kokoro 形如 `af_heart`，两者不会互相误判。**误判的后果是用错引擎
去加载对方的权重，报错信息完全指不到真正原因**，所以这条边界有专门测试守着。

支持 `kokoro:af_heart` 与裸写 `af_heart` 两种写法。

注：Kokoro 无声音克隆（只有预设音色）。需要克隆应另选 Chatterbox（MIT），
本次未接入。

## 三、ASR：接入 SenseVoice / Qwen3-ASR

SenseVoice Small 226MB、普通话/粤语/英/日/韩、RTF≈0.10，中文场景明显快于
Whisper，且**粤语是它相对 Whisper 的关键优势**。

引擎分流按仓库名判定，不靠"名字里有没有 whisper"这种模糊匹配。未登记的仓库
按 Whisper 处理，保持既有部署行为不变。

`capabilities()` 现在分别报告两个引擎的可用性，依赖缺失时**列出缺哪个包**，
而不是只给一个 False。

funasr 返回带 `<|zh|><|NEUTRAL|><|Speech|>` 标签，已做剥离。

## 四、3D：许可事实结构化

3D 运行时此前已接线（6 个路由、88 项测试通过）。本次补上许可事实表：

- **TripoSR** — 纯 MIT，无用户上限、无地区排除，最无限制
- **TRELLIS** — MIT（少数渲染子模块带其他许可，商用前需做依赖核查）
- **Hunyuan3D** — 腾讯社区许可，**有月活上限且排除欧盟/英国/韩国商用**

Hunyuan3D 的地区排除是"下载了就能商用"这个普遍误解里后果最严重的一条，必须
结构化暴露而非只写在注释里。

## 五、架构地基：ComfyUI 通用后端

新增 `app/comfyui_runtime.py`。一次接线即可覆盖图像/视频/音乐，避免为每个模型
单独写 runtime。它只做编排，不实现推理。

调研中确认的四个生产坑，逐个处理：

1. **seed 被随机化** — 工作流导出默认带 `control_after_generate: "randomize"`，
   作为 API 会让同样输入产出不同结果，且**不报错**。新增 `fix_randomize_seeds()`，
   并保留"改了几处"的计数。
2. **模型冷加载 8–20 秒** — 提供 `warmup()` 把加载挪到用户等待之前。
3. **VRAM 碎片** — 封装 ComfyUI 的 `POST /free`。
4. **队列饥饿** — 短任务被长任务堵死；提交时带 `job_class`，超时按类别分档
   （图像 300s / 视频 3600s）。

ComfyUI 拒绝工作流时会返回 400 + 详细原因，客户端**原样透传**——只报"提交失败"
用户根本没法修工作流。

## 六、体验：环境自检向导增强

`scripts/health_check.py` 新增三项，并把"只报告"升级为"给已验证的解"：

- **安装路径检查** — 中文/空格/Windows 保留名。这是排障 FAQ 最高频的一条，
  而报错往往表现为"找不到文件"或莫名的编码错误，完全指不到真正原因。
- **杀软提示** — Windows 下扫描常见杀软进程，只提示加白，不做任何改动。
- **端口自动换** — 原逻辑直接建议 `port + 1`，但那个端口很可能同样被占用。
  现在调用 `find_free_port()` 返回**已实际绑定验证过**的空闲端口。

## 测试

- 新增 **78 项**（音乐 26、TTS/ASR 32、ComfyUI 20）
- 相关回归 **732 项通过**

## 改动文件

| 文件 | 说明 |
|---|---|
| `app/music_runtime.py` | ACE-Step 接入、许可事实表、时长上限分档 |
| `app/piper_runtime.py` | Kokoro 引擎接入与音色判定 |
| `app/asr_runtime.py` | SenseVoice/Qwen3-ASR 分流、文本标签剥离 |
| `app/three_d_runtime.py` | 3D 许可事实结构化 |
| `app/comfyui_runtime.py` | **新建**：ComfyUI 通用后端 |
| `scripts/health_check.py` | 路径/杀软/端口三项 + 已验证可用端口 |
| `tests/test_music_ace_step.py` | **新建** 27 项 |
| `tests/test_tts_asr_engines.py` | **新建** 32 项 |
| `tests/test_comfyui_runtime.py` | **新建** 20 项 |
| `tests/test_v428_music_generation.py` | 去掉模型数量的硬编码断言 |

## 已知未改动

- `test_v411_piper_runtime.py::test_route_engine_missing` 仍失败：piper 路由返回
  422 而非 503，pydantic 2.14 的 schema 差异让请求体校验就没过，端点逻辑根本
  没执行。与本次改动无关（本次改动都在 `synthesize` 内部，未被执行到）。
- 声音克隆（Chatterbox）未接入，本次只补了预设音色的 Kokoro。

## 安装包

安装包由仓库自带的发布流水线（`release.yml`）在打 tag 后自动构建并上传，产物见
本 Release 的附件清单。
