# Kevrai Omni v4.11.0

本次更新围绕六件事：**下载更快更稳**、**更多国产显卡能被认出来**、**装不上能一键查原因**、
**3D 从"只能下载"变成"能在应用内生成"**、**模型按用途分槽**、**参数说人话**。
另外修正了模型市场里几个会导致下载失败的地址，补录了魔搭（ModelScope）免令牌下载源，
新增一个地址批量核验工具，并修掉了一个"点了取消却报生成失败"的状态机错误。

> 说明：v4.10.0 中已实现的「知识库混合检索」与「Agent 联网搜索」两项，
> 本次已完整合并进主线并保留其全部测试（见文末"承接"一节）。

---

## 新增

### 1. 下载提速：多线并发 + 分片续传

几十 GB 的模型权重原先只能单线下载，断线后虽然能续传，但只有一个连接，
丢一段就要重排在它后面的所有字节。现在：

- **自动分片**：大文件会先探测服务器是否支持断点续传（`Accept-Ranges: bytes`），
  支持且文件足够大时，自动切成最多 8 段同时下载，各自按偏移写入同一个文件。
- **按段续传**：每段进度单独记录，中断后重启，**已完成的分片直接跳过**，
  未完成的分片从断点继续 —— 不再从头再来。
- **完整性校验不变**：分片只负责把字节按偏移写对，下载完仍由原有流程统一做
  SHA-256 校验，行为与以前完全一致。
- **服务器不支持时自动退回单线**：探测不到 `Accept-Ranges`、文件太小、
  或服务端忽略 `Range` 请求（返回 200 而不是 206），一律退回原来的单线方式，
  **不会变慢，也不会改变原有行为**。

回退时会把 `.partial` 截断到「从 0 开始连续已完成的字节数」，
保证单线续传拿到的前缀是完整正确的 —— 宁可少留几个字节，也不能留空洞。

### 2. 硬件识别补齐：国产加速卡 + Intel

原先只认 NVIDIA、AMD、Apple、昇腾，国产卡一律识别不出（评分也因此偏低）。现在新增：

| 厂商 | 识别方式 |
|---|---|
| 摩尔线程 Moore Threads | `mthreads-smi`（MUSA） |
| 天数智芯 Iluvatar CoreX | `ix-smi` |
| 寒武纪 Cambricon | `cnmon`（MLU） |
| 海光 Hygon | `hy-smi`，或按 `rocm-smi` 输出中的 DCU 字样判出 |
| Intel 显卡 / 加速卡 | `xpu-smi` |

- **没装厂商工具也能认**：全部失败时会用系统信息兜底
  （Linux 走 `lspci`，Windows 走 `Win32_VideoController`），
  只是拿不到显存（显存留 0，不猜）。
- **评分体系补上对应权重**：原先最后一档 10 分只奖励 NVIDIA / Apple，
  国产卡一律 0 分。现在按各家本地推理生态的成熟度加权（NVIDIA 1.0 → 海光/摩尔线程 0.45 → …），
  未知厂商给保守中间值，不会出现"明明有卡却得 0 分"。
- 界面可取用 `gpu_vendor_label` 直接显示中文厂商名。

### 3. 一键环境体检脚本

新增 `scripts/health_check.py`，**只依赖标准库** —— 即使项目依赖一个都没装，
它也能跑起来并把缺什么说清楚（否则体检工具自己打不开就太讽刺了）。

依次检查：Python 版本 → 操作系统 → 后端依赖 → 可选依赖 → Node.js 工具链 →
前端依赖 → 显卡 → 端口占用 → 磁盘空间 → 写入权限 → 模型源连通性。

每一项都给出「哪里不行 + 怎么修」，修复命令写到能直接复制执行。

```bash
python scripts/health_check.py                  # 打印报告
python scripts/health_check.py --md report.md   # 导出 Markdown
python scripts/health_check.py --json out.json  # 导出 JSON
python scripts/health_check.py --no-network     # 离线环境跳过联网检查
```

退出码：有必须修复的问题返回 1，否则返回 0（方便写进 CI 或批处理）。

---

### 4. 3D 生成运行时（补上最大的一块空白）

在此之前，catalog 里的 24 个 3D 模型**只能下载、无法调用**——没有 `/api/3d` 路由，
也没有任何 runtime。这是全项目最大的能力断层，本次补上：

- 新增 `python/app/three_d_runtime.py`，沿用已验证过的 LTX 运行时骨架：
  懒加载重依赖（没装 torch 也能 import，体检和目录浏览不受影响）、
  参数前置校验、单飞任务队列、进度回调、取消、状态快照。
- 新增 6 个路由：`/api/3d/capabilities`、`/api/3d/generate`、
  `/api/3d/tasks`、`/api/3d/tasks/{id}`、`/api/3d/tasks/{id}/cancel`、`/api/3d/outputs`。
- 支持 `i23d`（单图重建）与 `t23d`（文生 3D）两种模式，
  输出 GLB（默认）/ OBJ / PLY。
- 三档预设按显存给出：`fast`(6GB) / `balanced`(8–12GB) / `quality`(16GB+)。

**如实标注的两点**：

- 3D pipeline 的调用形态各家差异极大（TripoSR 走 marching cubes 出网格、
  Hunyuan3D-2 出带纹理网格、TRELLIS 需要先解码结构化潜变量）。
  加载器会依次尝试已知类名，**都不匹配时报清楚"试过哪些"**，不静默猜。
- TripoSR 这类**纯重建模型不支持文生 3D**，会在校验阶段就明确拒绝，
  而不是让用户等到 pipeline 加载失败才发现。

### 5. 模型能力标签 + 用途槽位（借鉴 Cherry Studio）

Cherry Studio 最值钱的不是某个功能，而是它的**默认基线**：模型按用途分组
（默认助手 / 快速 / 翻译 / 绘画），新用户不需要先学会参数就能用。

- 新增 `python/app/model_roles.py`。把"默认模型"这一个全局单选，
  拆成 9 个用途槽位：`chat / fast / reasoning / vision / image /
  embedding / rerank / planning / tool`，每个槽位配一句人话说明。
- **能力标签来自结构化字段**（`category` / `modality` / `tags`），
  **不用模型 ID 正则猜**——Cherry Studio 明确反对这种做法。
  名字里带 "vision" 的模型不会凭空获得看图能力（有测试守着这条线）。
  确定不了就显示 `unknown`，不装作知道。
- 槽位自动选模：`fast` 挑最小的（后台小任务就该快），
  其余在显存预算内挑能力最强的，并说明"为什么选它"。
- **用户手动指定是独立覆盖层**，不修改出厂目录；
  钉住的模型若已从目录移除，会明确提示而不是偷偷换一个。
- 新增路由：`/api/roles`、`/api/roles/pin`(POST/DELETE)、
  `/api/roles/capabilities`、`/api/roles/fit`。
- 附带落地了知识库的**默认值基线**（沿用 Cherry Studio 实测基线）：
  BM25 起步、Top K=6、相似度阈值 0.0、Rerank 默认关闭——
  关键词能命中就不开向量，召回不稳再加 Rerank。

### 6. 生成参数"人话化"

Cherry Studio 的做法是**不配术语词典**：temperature 说明直接绑定低/中/高区间和
"代码 / 日常对话 / 创意"场景，并给出默认 0.7。本次照此改造：

- 新增 `python/app/gen_params.py`。每个参数都有人话名字、一句话解释、
  **调小会怎样 / 调大会怎样**、以及**调坏了怎么办**：

  | 参数 | 界面显示 |
  |---|---|
  | temperature | 有多大创造力 |
  | top_p | 选词范围有多宽 |
  | context_length | 能记住多少内容 |
  | max_tokens | 最多一次写多长 |
  | tool_rounds | Agent 最多连续操作多少步 |

- 提供 5 个**场景档位**一键套用：写代码 / 日常对话 / 创意写作 / 翻译 / 精确抽取。
  写代码用低温度保证可复现，精确抽取直接用 0.0 零发挥。
- 任何取值都会实时返回"这个值的后果"，用户能判断"对我意味着什么"。
- 新增路由：`/api/params`、`/api/params/scenarios/{key}`、
  `/api/params/explain`、`/api/params/explain-all`、`/api/params/scenario-values/{key}`。

### 7. 模型地址批量核验工具

新增 `scripts/verify_catalog_urls.py`（只依赖标准库）。

**为什么需要它**：CI / 沙箱环境常常**整体无法访问** huggingface.co 与
modelscope.cn（连确定存在的仓库也返回 403），在那里"核验"出来的结果全是假的。
这个脚本设计成**在你能正常上网的机器上跑**。

**最关键的设计**：`403 不等于地址错`。它有好几种完全不同的含义，混为一谈会把大量
正常地址误判成死链：

| 结果 | 含义 | 是否要修 |
|---|---|---|
| `ok` (200) | 正常 | — |
| `gated` (401/403) | gated 仓库（要登录/同意协议）或站点反爬 | **否** |
| `missing` (404) | 地址真的不存在 | **是** |
| `unreachable` | 超时/DNS，网络问题 | 否 |

```bash
python scripts/verify_catalog_urls.py                     # 全量
python scripts/verify_catalog_urls.py --source modelscope  # 只验魔搭
python scripts/verify_catalog_urls.py --only-missing       # 只查还没魔搭源的
python scripts/verify_catalog_urls.py --md 报告.md         # 导出报告
```

有 404 才返回退出码 1，可挂进 CI。

**已核实过的地址**（本次抽查，均有出处）：LTX-2.5、Kokoro-82M、
HunyuanVideo-1.5、Qwen-Image-2.1、Wan2.2-TI2V-5B 的魔搭地址均与官方页面一致。
其中 **LTX-2.5 的魔搭地址本来就正确且免令牌**——这一条无需修改。

---

## 错误修复：取消被误报成失败

**现象**：用户点了「取消」，结果任务状态显示 `failed`（生成失败）。

**根因**：`_run_safe` 捕获异常时没区分"是不是用户取消导致的"。
用户在加载/推理过程中取消，底层很可能因为中断而抛异常，
这个异常本质是**取消的后果**，却被一律记成了 `failed`。

**修复**：异常分支先检查取消标志——已取消就记 `cancelled`（error 为空），
否则才是 `failed`。LTX 与 3D 两个运行时都修了，并各自补了回归测试
（含反向保护：没有取消时，普通失败仍必须是 `failed`）。

---

## 修正：模型市场地址

地址写错会导致点了下载却 404，这是最容易让用户觉得"软件坏了"的问题。
本次**只修正已核实的条目**，没核实的一律不动。

| 条目 | 问题 | 已改为 |
|---|---|---|
| `hunyuanimage-3.0` | 官方仓库名带 `-Instruct` 后缀，原名无法解析 | `tencent/HunyuanImage-3.0-Instruct` |
| `pixal3d` | 仓库不存在 | `TencentARC/Pixal3D-T` |
| `apple-sharp` | `apple/Sharp` 是第三方镜像 | `apple/ml-sharp`（官方） |

补录魔搭（ModelScope）源 —— **国内下载无需令牌**，这是国内用户的关键优势：

- `minimax-music3` → 魔搭名为 `MiniMax/MiniMax-Music3`
  （注意**不等于** HF 上的 `MiniMaxAI/MiniMax-Music3`，两边名不同）
- `minimax-h3` → `MiniMax/MiniMax-H3`

另有 4 条「官方尚未发布权重」的占位条目（`wan3.0`、`glm-5-code`、
`happyshrimp-1.0`、`minimax-2k-pending`），原先链接指向的是搜索页而非下载地址，
现已补上明确说明，**不再让前端把搜索页当成下载地址**。

修正记录写进了 catalog 的 `url_audit` 字段，可追溯。
可用 `python scripts/fix_catalog_urls.py` 复现（默认只预览，加 `--apply` 才写入）。

---

## 承接：v4.10.0 已实现能力的合并

以下两项在上一轮已实现并通过测试，本次完整合并进主线，**未作删改**：

1. **知识库混合检索**（BM25 + 向量 RRF）—— `app/retrieval.py`，
   CJK 友好分词，默认 `dense` 排序逐位不变，`mode=dense|bm25|hybrid` 可选。
2. **Agent 联网搜索** —— `app/agent/tools/web_tools.py`，
   SearXNG 优先、DuckDuckGo 兜底，注册为**默认关闭**的 opt-in 技能。

---

## 测试

- 新增 **285 项**测试：
  - 分片下载 25 项、硬件识别 36 项
  - 3D 运行时 **88 项**（含 10 项路由端到端）
  - 模型能力标签与用途槽位 **59 项**（含 13 项路由端到端）
  - 参数人话化 **33 项**
  - 地址核验脚本 **34 项**（含"403 不得误判为坏地址"专项回归）
  - LTX 取消语义回归 2 项、契约测试同步 8 项
- 全量后端：**2299 项通过**
- 与干净基线（未改动的 v4.10.0）逐项对比确认，剩余 7 项失败均为环境所致、
  与本版改动无关：
  - `mcp` 模块未安装 → `test_mcp_stdio`、`test_v540_mcp` 共 4 项
  - Piper 路由 422（pydantic 2.14 schema 差异）→ 1 项
  - MNN spawn fd 计数用例（顺序敏感 flaky，时有时无）→ 1 项
  - sidecar 真实子进程冒烟 → 1 项

---

## 仓库存储

- 历史 `RELEASE_NOTES_*.md` 已累积 46 份（约 137KB）。
  本项目按"一个版本一份说明"的节奏发布，**不会为每个小改动单独发版**。
- 地址修正脚本产生的 catalog 备份（`catalog/models.bak-*.json`）已加入
  `.gitignore`，不会进入仓库。

---

## 升级说明

- 无需数据迁移，原有模型与知识库目录不变。
- 分片下载默认开启；如需退回旧行为，构造 `Downloader` 时传
  `shard_downloads=False` 即可。
- 硬件识别新增的厂商只影响展示与评分，不改变任何既有推理路径。
