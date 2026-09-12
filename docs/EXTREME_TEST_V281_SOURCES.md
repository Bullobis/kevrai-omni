# 极端测试报告 · v2.8.1「多源扩展 + 智能源调度」

- **版本**：Kevrai Omni v2.8.1（Electron + Python FastAPI sidecar）
- **测试执行**：Kevrai Omni Team
- **实施**：Kevrai Omni Team
- **测试日期**：2026-09-12
- **测试性质**：独立对抗性验证（全部脚本独立重跑）
- **红线遵守**：未修改任何源码或现有断言；所有临时脚本位于 `tests/scratch/`

---

## 0. 执行摘要

| 维度 | 结果 |
|---|---|
| 基线全量测试 | **493 passed**（独立重跑） |
| 本轮极端测试用例 | **44 例**（A 13 + B 16 + D 9 + E 6 网络/GitCode） |
| 通过 | 39 |
| 新增问题 | **P0: 0 · P1: 3 · P2: 3** |
| 分档权重是否起作用 | **是**（已验证同组源在不同档位选出不同赢家） |
| GitCode 结论 | **无法用于程序化下载**（独立复现并**细化**） |
| 能否交付 | **可交付**（无 P0；3 个 P1 建议下个迭代修，均有降级路径，不阻断功能） |

**最严重问题**：P1-1 —— 「声称成功但 0 字节」的源在**小文件档**下会击败真实可用的高速源，被自动选为下载源，可能导致下载空文件/错误文件。评分完全不校验探测体字节数。

---

## 1. 基线复测数字（独立跑）

| 项目 | 命令 | 结果 |
|---|---|---|
| Python 全量 | `python -m pytest -q tests/` | **493 passed in 71.52s** |
| 离线子集 | `pytest -q tests/ -k "not network"` | **492 passed, 1 deselected** |
| 调度器专项 | `pytest -q tests/test_source_scheduler.py tests/test_sources.py tests/test_mirror_expand.py` | **33 passed** |
| JS 语法 | `npm run test:js` | **通过（13 文件 node --check 全绿）** |

> 说明：本轮测试全程在**有网**环境运行；调度器专项用例均使用 mock，不依赖网络。

---

## 2. 极端测试矩阵

### A. 调度器选优正确性（重点）

| 编号 | 场景 | 构造方式 | 预期 | 实测 | 判定 |
|---|---|---|---|---|---|
| A1a | **分档翻转（核心）** | A=(lat10,spd1) / B=(lat500,spd100)，同组源 | small→A，large→B，**赢家不同** | small→A，large→B，flipped=True | ✅ PASS |
| A1b | 快/慢/失败组合 | fast=(30,40) slow=(400,3) dead=fail | best=fast，failed 排最后 | best=fast，dead 在 rank 末尾 | ✅ PASS |
| A1c | 阈值边界 | 511.99MB / 512MB / 512.01MB | `<`→SMALL(0.6,0.4)；`≥`→LARGE(0.2,0.8) | (0.6,0.4) / (0.2,0.8) / (0.2,0.8) | ✅ PASS |
| A2b | α 参数生效 | sample100/hist50，α=0.9 vs 0.1 | 95 vs 55 | 95.0 / 55.0 | ✅ PASS |
| A2c | 脏样本后恢复 | 1×9999ms 后 6×50ms | 历史回落 | 4029.6 → 235.7 | ✅ PASS |
| A3a | 断路器 3 次开门 | record_fail×2 / ×3 | 2→closed，3→open | closed / open | ✅ PASS |
| A3b | 冷却期跳过探测 | 开门后 select | measure 零调用，URL 入 skipped | 0 调用，URL 在 skipped | ✅ PASS |
| A3c | half-open 恢复 | 开门→+300s→成功 | open→half-open→放行1→closed | 全部符合 | ✅ PASS |
| A4a | 渐进降权 | P(sr=0.5) vs T(sr=0) | score_P > score_T 且非 -inf | 0.5 > 0.0 | ✅ PASS |
| A5a | 缓存 TTL | t=0/t=0/t=301 | 命中1次；过期重探 | calls=2，命中/过期正确 | ✅ PASS |
| A5b | force 绕缓存 | 先探再 force | 2 次网络 | calls=2 | ✅ PASS |
| A2a | **EWMA 抗脏样本（排序级）** | X 有 5×40ms 历史后 1×9999ms；Y=120ms | 有历史应保护 X | **Y 仍赢**，X 有效延迟 4023.6ms | ⚠️ **P1-2** |
| A4b | 全失败源新探测 | 3 失败→record_ok→新 ok 探测 | 得分≥0 | **-inf**（health 仍 open 时） | ℹ️ 语义澄清 |

> **A2a 深挖结论**：EWMA 阻尼**与历史深度无关**。实测 hist=1/5/20/100/500 后注入单次 9999ms，EWMA 一律变为 **4023.6ms**（=0.4×9999+0.6×40）。这是固定 α=0.4 的数学必然（EWMA 是"近因加权"，非"有界影响滤波器"），但 `source_scheduler.py` 文档声称它能 "damping single-shot jitter"（§2.4.2）。在仅 2~3 个候选的 min-max 归一化下，**单次抖动仍能翻转排序**。详见 P1-2。

### B. 极端 / 对抗场景

| 编号 | 场景 | 构造方式 | 预期 | 实测 | 判定 |
|---|---|---|---|---|---|
| B1 | 全部源失败 | 3 源全 ok=False | best=''，不崩溃，全 None | best=''，3 条，score 全 None | ✅ PASS |
| B2 | 速度剧烈波动 | 同源 8 轮 spd∈{0.01..100} | EWMA 落在样本区间内 | ewma_spd=20.83∈[0.01,80] | ✅ PASS |
| B3 | 全部探测超时（mock） | 3 源 status=0,error=timeout | best=''，不崩 | best=''，3 条 | ✅ PASS |
| B3' | 全部超时（**真实网络**） | 黑洞 IP 10.255.255.x，timeout=1.5 | pick_best→None | 0.10s 返回，pick_best=None | ✅ PASS |
| B4 | 高并发 select | 20 并发同列表 | 无异常，best 一致 | 0 异常，best 唯一 | ✅ PASS |
| B4' | 并发跨源串号 | 10 并发探测不同 URL | 每 URL 独立 trials/cache | trials=10, cache=10 | ✅ PASS |
| B4b | 注册表隔离 | r1 探测后查 r2 | r2 缓存空/trials=0 | 0 / 0 | ✅ PASS |
| B5a | 空列表/去重 | []、None、同 URL×3+空白 | 空→无调用；去重下发 1 | best=''，下发 1 条 | ✅ PASS |
| B5b | 畸形/非URL/超长 | ftp/js/空格/3000字符/unicode/无scheme | 不抛异常，best='' | 无异常，best='' | ✅ PASS |
| B5c | 大小写变体去重 | `HF-Mirror.com` vs `hf-mirror.com` | 理想去重 1 | **下发 2 条**（大小写敏感） | ⚠️ **P2-1** |
| B6a | **0字节/0速度源** | 源A ok=True size=0 spd=0 vs 源B 10MB/s | 不应选空源 | **小文件档 A 胜出** | ⚠️ **P1-1** |
| B7a | 非法 profile 名 | profile='totally-bogus' | 退化为按大小自动 | 与 auto 一致 (0.6,0.4) | ✅ PASS |
| B7b | 非法 purpose | purpose='bogus' | 不抛异常 | best 正常 | ✅ PASS |
| B8a | 重复 source_id | add_source×2 | last-write-wins | name='second' | ✅ PASS |
| B8b | 空 host_patterns | patterns=[] | matches=False，不误匹配 | False / None | ✅ PASS |
| B8c | 非法 origin | origin='not-a-url' | 不抛异常 | 快照保留原值 | ✅ PASS |
| B8d | 用户覆盖预置 | add_source(id='hf-mirror-com') | 预置被替换 | name='USER OVERRIDE' | ℹ️ NOTE |
| B8e | 镜像规范化边界 | 空白/非URL/ftp/裸scheme/重复 | 仅保留合法、去重 | ['ok-example','same-example'] | ✅ PASS |

### C. GitCode 处理验证

| 编号 | 场景 | 预期 | 实测 | 判定 |
|---|---|---|---|---|
| C1 | 默认配置未启用 | 注册表 gitcode.enabled=False | `_build_source_registry(Settings())` → False | ✅ PASS |
| C2 | 不在 enabled_sources | 全量/model 均不含 gitcode | enabled=['hf-mirror-com','modelscope','github'] | ✅ PASS |
| C3 | 未配置模板返回空 | build_gitcode_candidates(tpl='')→[] | `[]`（空白/缺占位符/非http/未知占位符均 `[]`） | ✅ PASS |
| C4 | 无硬编下载 URL | 搜索代码 | PRESET/extra_model_mirrors **无** gitcode 下载 URL | ✅ PASS |
| C5 | 实际探测下载链路 | 独立复现 | 见 §4 | ✅ 复现并细化 |

### D. 安全与回归

| 编号 | 场景 | 预期 | 实测 | 判定 |
|---|---|---|---|---|
| D1a | measure 输入校验 | 空/非list/缺key/全非字符串→400；file_size 非法不 500 | 400/400/400/400，file_size=200 | ✅ PASS |
| D1b | URL 数量上限 | 100 个截断到 32 | 200，正常 ranking | ✅ PASS |
| D2 | 注册表/健康端点 | 200，含预置 id | 200/200，含 hf-mirror-com | ✅ PASS |
| D3 | 锁定源：不存在/禁用/有效/解锁 | 404/404/200/200 | 404/404/200/200 | ✅ PASS |
| D3b | 锁定源注入 | 路径穿越/SQL/换行/斜杠 | 均 404（换行 id 被 strip 后 200） | ✅ PASS |
| D4 | measure 非法 scheme/超长 | 不 500 | 200，4 条 | ✅ PASS |
| D5 | **P0-1 脱敏回归** | 响应无明文 token，含 `hf_token_set` | leaked=False, hf_token_set=True | ✅ PASS |
| D5 | **P0-2 Zip Slip 回归** | 穿越/绝对/保留名/NUL 全拒 | 全 rejected | ✅ PASS |
| D5 | **P0-3 import 校验回归** | 均 4xx 非 500 | 400/400/400，429 | ✅ PASS |

### E. 端到端 / 真实网络

| 编号 | 场景 | 实测 | 判定 |
|---|---|---|---|
| E1 | 真实源测速（DNS 失败/503 排后） | 本轮未重复验证 | — |
| E2 | `/api/sources/measure` 真实探测 hf-mirror.com | 200，探测成功 | ✅ PASS |
| E3 | 全超时真实降级 | pick_best→None | ✅ PASS |
| E4 | JS 语法检查 | 13/13 通过 | ✅ PASS |
| E5 | 渲染层 XSS | 所有源字段经 `escapeHtml()` | ✅ PASS |
| E6 | 离线全量回归 | 492 passed | ✅ PASS |

---

## 3. 新发现问题清单

### P1-1（严重）· 0 字节「成功」源被选为下载源；评分不校验探测体大小

- **文件**：`python/app/source_scheduler.py:160-175`（`score_source`）、`:193/209`（`ok` 判定）；`python/app/sources.py:112`（`ok = 200 <= status < 400`）
- **复现步骤**：
  1. 构造源 A：HTTP 200、`size_bytes=0`、`speed_mbps=0`、`latency_ms=5`（模拟 SPA 兜底页 / 空响应 / 错误页返回 200）。
  2. 构造源 B：HTTP 200、`size_bytes=65535`、`speed_mbps=10`、`latency_ms=120`（真实可用）。
  3. `scheduler.select([A,B], file_size=1MB)`。
- **实际表现**：`best_url = A`（0 字节源），score A=0.6 > B=0.4。**小文件档下空源胜出**。大文件档（8GB）下 B 胜出（0.8>0.2），问题仅在小文件档暴露。
- **影响**：若某镜像返回 200 的空/兜底页（GitCode SPA 兜底正是此形态），调度器会把它选为最优源，导致下载 0 字节或错误内容；且该源会被写入 health 缓存，污染后续 N 分钟（TTL 300s）决策。
- **建议修法**：`score_source` / `_score_ranking` 中，将 `ok` 判定收紧为「status 2xx **且** `size_bytes > 0`」；或在 `_probe_one` 中把 `size_bytes == 0 && status==200` 标记为可疑（ok=False, error="empty body"）。至少对 `size_bytes==0` 的源做强制降权（如 `score *= 0.0`）。

### P1-2（严重）· EWMA 抗抖动能力与文档宣称不符；单次脏样本可翻转排序

- **文件**：`python/app/source_scheduler.py:92-100`（`ewma_blend`）、`:196-215`（先 EWMA 混合后 min-max 归一化）；文档 `docs/DESIGN_V281_SOURCES.md` §2.4.2 声称 "damping single-shot jitter"
- **复现步骤**：
  1. 为源 X 灌入 N 次 `latency_ms=40, speed_mbps=60` 的历史（N=1/5/20/100/500）。
  2. 注入单次 `latency_ms=9999`。
  3. 与稳定源 Y（120ms）同组 `select`。
- **实际表现**：无论 N 多大，X 的 EWMA 延迟一律变为 **4023.6ms**（α=0.4）。随后 min-max 归一化把 X 的 `lat_norm` 压到 0，Y 胜出。**"历史深度"完全无法抵抗单次脏样本**；α=0.4 偏高，一次异常即回收 40% 权重。
- **影响**：一次网络抖动即可让本来最优的源在接下来一轮被降级；与"智能源调度"承诺的鲁棒性有落差。
- **建议修法**：
  - 在 EWMA 前做**截断/中位数滤波**（如对 sample 取 `min(sample, 3×ewma)` 或滑窗中位数），使单次极值无法主导；
  - 或降低 α（如 0.2），使单次样本权重大幅下降；
  - 或归类为"文档不实"，把 §2.4.2 措辞改为"EWMA 提供近因平滑，但不提供单样本有界保护"（诚实的风险披露）。

### P1-3（严重）· `measure_sources(profile=...)` 参数被静默丢弃

- **文件**：`python/app/sources.py:165-172`（registry 分支）
- **复现步骤**：`await measure_sources(urls, registry=reg, profile='speed')`，其中 A=(lat10,spd1)、B=(lat500,spd100)。
- **实际表现**：`profile='latency'`、`profile='speed'`、`profile=''` **三者返回完全相同的赢家**（A）。源码中该分支写死 `file_size=0, purpose=purpose, force=False`，**`profile` 根本没传给 `scheduler.select`**，且 `file_size` 恒为 0（永远走 throughput 档）。
- **影响**：文档明确写 `profile` 会影响排序（"``"latency"`` or ``"speed"``"），实际是死参数。任何依赖 `measure_sources(registry=..., profile=...)` 的调用（含未来端点）都会得到错误的档位。
- **建议修法**：改为
  ```python
  result = await scheduler.select(
      uniq, file_size=file_size, purpose=purpose,
      profile=profile, force=force,
  )
  ```
  并给 `measure_sources` 增加 `file_size` / `force` 形参（默认 0/False，向后兼容）。

### P2-1（一般）· URL 去重大小写敏感，同一主机重复探测

- **文件**：`python/app/sources.py:157-163`、`python/app/source_scheduler.py:255-261`
- **复现步骤**：`select(["https://HF-Mirror.com/a", "https://hf-mirror.com/a"])`
- **实际表现**：下发 **2 个 URL** 给 `measure_sources`（去重是精确字符串比较）。
- **影响**：浪费一次探测；同一源的健康被记两次。cache_key 用了 `.lower()`（`_cache_key`），所以缓存层不重复，但探测层重复。
- **建议修法**：去重时对 URL 做规范化（`url.strip().lower()` 或按 `(host.lower(), path)` 判重）。

### P2-2（一般）· `/api/download/start` 与 `/api/hub/download` 未接入分档/调度器

- **文件**：`python/app/main.py:1282`（`file_size=0` 写死）、`:1160`（`measure_sources(candidates)` **未传 registry**）
- **实际表现**：真实的模型下载入口 `/api/download/start` 永远按 `file_size=0`（吞吐档）；`/api/hub/download` 完全绕过调度器（无缓存、无断路器、无加权评分）。
- **影响**：分档设计的核心价值（小文件重延迟 / 大文件重吞吐）**在真实下载主路径上大部分未被利用**——仅 `/api/sources/measure` 且显式传 `file_size` 时才生效。用户点下载时拿不到分档收益。
- **建议修法**：`download_start` 从请求体/目录元数据取 `file_size` 传入 `sched.select`；`hub/download` 改用 `_get_scheduler(request)`。

### P2-3（一般）· 源代码 `_score` 线性回退与调度器评分口径不一致

- **文件**：`python/app/sources.py:70-83`
- **实际表现**：无 registry 时 `_score = speed*10 - latency*0.05`（线性）；有 registry 时用 `w_lat·lat_norm + w_spd·spd_norm`（归一化）。两套口径在极值下排序可能不同。
- **影响**：同一组 URL 在「有源注册表」与「无源注册表」两种路径下可能选出不同最优源，行为不直观（虽不崩溃）。
- **建议修法**：明确文档化两条路径的差异，或在无 registry 时也用同一归一化评分。

---

## 4. GitCode 结论（独立验证）

### 4.1 代码层（静态验证）

| 检查项 | 方法 | 结果 |
|---|---|---|
| 默认是否启用 | `main._build_source_registry(Settings())` | `gitcode.enabled = False` ✅ |
| settings 默认 | `Settings()` | `gitcode_enabled=False`，`gitcode_repo_api=''` ✅ |
| 是否进入候选 | `enabled_sources('')` / `('model')` | 均**不含** gitcode ✅ |
| 模板为空行为 | `build_gitcode_candidates('o/r','f','main','')` | `[]` ✅ |
| 其他边界 | 空白/缺 `{path}`/非 http/未知占位符 | 全部 `[]` ✅ |
| 硬编下载 URL | 全库 grep | `PRESET_SOURCES` / `extra_model_mirrors` **无** gitcode 下载 URL ✅ |

> 注：`gitcode.com` 仅出现在 `converter.py`（**git clone 镜像**，注释标注"已验证可用"）与 `catalog.py`（允许主机白名单），**不属于**模型下载候选集。二者与"下载链路"无关。

### 4.2 网络层（live 复现）

| 探测目标 | 源结论 | 本轮独立实测 | 一致性 |
|---|---|---|---|
| `gitcode.com/alibaba/MNN`（仓库页） | 302/200 | **200，193663 B** | ✅ |
| `api.gitcode.com/api/v5/repos/alibaba/MNN` | 404 | **400**（无 token） | ⚠️ 同为失败 |
| `raw.gitcode.com/.../README.md` | 418 反爬 | **418，3223 B** | ✅ |
| `gitcode.com/.../-/raw/main/README.md` | SPA 兜底 5785 B | **200，5785 B** | ✅ |
| 真路径 vs 假路径 | 均 5785 B | **MD5 完全相同**（`9d3fed31bdbe5695d13f0a90834eb11e`） | ✅ 决定性 |

**细化发现（供架构师参考）**：`api.gitcode.com/api/v5/repos/{repo}/contents/{path}` **返回 200**，body 为 base64 JSON（README.md，13307 B）。

**独立结论**：
1. **GitCode raw/archive 直链不可用于程序化下载**——真/假路径返回**字节完全相同**的 SPA HTML 壳，程序无法区分真实文件与兜底页。源结论**成立**。
2. **细化**：GitCode v5 **`contents` API 对小型文本文件部分可用**（base64 JSON）。但它：(a) 返回 API 包装而非裸流；(b) base64 膨胀 + 大小限制；(c) 未验证大文件（模型权重）能力；(d) 需精确模板（当前未硬编）。**对"模型权重下载"这一实际场景仍不可用**。
3. 当前实现（默认关闭 + 未配置模板返回空 + 无硬编 URL）**完全正确**，符合红线。

> **建议**：若未来要支持 GitCode，应基于 `api.gitcode.com/api/v5/repos/{repo}/contents/{path}`（base64 解码）作为**小型文件**备用源，并明确标注大文件不支持；切勿启用 `raw.gitcode.com`（反爬 418）或 `/{repo}/-/raw/`（SPA 兜底）。

---

## 5. 总结

### 5.1 能否交付

**可以交付（Go）**。理由：

1. **无 P0**：无崩溃、无数据泄漏、无安全回归。
2. 三项 P0 回归（脱敏 / Zip Slip / import 校验）**全部有效**。
3. 新增 3 个端点输入校验完备，非法输入不 500。
4. 极端场景（全失败 / 全超时 / 高并发 / 畸形输入）**均优雅降级**，无异常。
5. **分档权重机制真实有效**（A1a 验证同组源在不同档位选出不同赢家）。
6. 全量 493 + 离线回归 492 均通过。

### 5.2 风险敞口

| 风险 | 等级 | 触发条件 | 缓解 |
|---|---|---|---|
| 0 字节源被选 | P1-1 | 镜像返回 200 空体/兜底页 + 小文件 | 下迭代收紧 ok 判定；当前可用「锁定源」规避 |
| EWMA 抗抖动弱 | P1-2 | 单次网络抖动 | 下迭代加中位数/截断滤波 |
| profile 死参数 | P1-3 | 经 `measure_sources(registry=)` 调用 | 下迭代透传；当前 `select` 直调不受影响 |
| 分档在下拉主路径未利用 | P2-2 | 真实下载 | 下迭代接入 file_size |

### 5.3 交付建议

- **可交付**，但建议把 **P1-1 / P1-3** 作为 v2.8.2 的首批修复项（均为 1-3 行改动，风险低）。
- GitCode 维持现状（默认关闭）即为**正确的保守选择**。
- 建议补 3 条回归断言：①0 字节源不得胜出；②`measure_sources(profile='speed')` 必须换赢家；③URL 大小写去重。

---

## 附录：测试产物

| 文件 | 说明 |
|---|---|
| `tests/scratch/extreme_a_scheduler.py` | A 段调度器选优（13 例） |
| `tests/scratch/extreme_a_refined.py` | A2/A4 精化复测 |
| `tests/scratch/extreme_b_adversarial.py` | B 段极端/对抗（16 例） |
| `tests/scratch/extreme_d_security.py` | D 段安全/回归（9 例） |
| `tests/scratch/extreme_*_results.json` | 各段结构化结果 |
