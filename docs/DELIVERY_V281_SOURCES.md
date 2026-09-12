# Kevrai Omni v2.8.1 交付报告 — 多源扩展 + 智能源调度

> 交付日期：2026-09-12
> 基线：v2.8.0（双源对接）
> Kevrai Omni Team

---

## TL;DR

新增**源元数据注册表**（8 个源，健康度分级）与**智能源调度器**（分档归一化评分 + EWMA 历史平滑 + 断路器 + 探测缓存），并在真实上游验证选源正确。全量测试 **497 passed**，断网同样全绿。

---

## 一、交付概览

| 维度 | 结果 |
|---|---|
| 交付状态 | ✅ 可交付（QA 发现 3 个 P1，2 个已修复验证） |
| 后端测试 | **497 passed / 0 failed**（v2.8.0 基线 471 → +26） |
| 断网测试 | **497 passed** |
| 真实上游验证 | 源测速选优、DNS 失败/503 正确排后 |
| 极端测试 | 44 例（QA 独立执行） |
| P0 问题 | 0 |
| P1 问题 | 3（其中 2 个已修复并加防回归） |

---

## 二、源清单治理

### 2.1 实测源健康度（真实探测，非文档推断）

| 源 | 类型 | 状态 | 实测 |
|---|---|---|---|
| hf-mirror.com | hf_mirror | ✅ 启用 | HTTP 200 / 0.21s |
| 魔搭 ModelScope | modelscope | ✅ 启用 | HTTP 200 |
| GitHub | github_engine | ✅ 启用 | 引擎二进制源（`purpose=engine`），非模型源 |
| hf-mirror.us | hf_mirror | ❌ 禁用 | 连接失败（DNS） |
| hf-cn-mirror.com | hf_mirror | ❌ 禁用 | 连接失败 |
| huggingface.dl.in.tel | hf_mirror | ❌ 禁用 | 连接失败 |
| hf-cdn.sufy.com | hf_mirror | ❌ 禁用 | HTTP 403 反爬 |
| GitCode | gitcode | ❌ 禁用 | 未验证（见 §2.2） |

**关键设计**：4 个死源**不删除**，改为 `enabled=False` + `note="实测不可用"`，运行时靠断路器跳过，避免每次下载白等 4 次超时。

### 2.2 GitCode 处理（用户点名要的源，如实交付）

**本环境实测结论（我独立探测 + QA 独立复现，结论一致）**：

| 探测项 | 结果 |
|---|---|
| `gitcode.com` 首页 | 200 |
| 仓库页面 | 302 重定向 |
| `api.gitcode.com/api/v5/*` | 404 |
| `raw.gitcode.com/...` | **418 反爬拦截** |
| **决定性证据** | 真路径与假路径返回 **MD5 完全相同**的 5785 字节 SPA 壳 → 无法程序化下载 |

**结论：无法证实其模型下载链路**。因此实现为：
- **可插拔、默认关闭**，`build_gitcode_candidates(..., template='')` 返回 `[]`
- **绝不硬编任何未验证 URL** 进默认配置
- 附用户自测验证步骤，用户在自己网络确认可用后填配置启用

QA 补充发现：`api.gitcode.com/api/v5/.../contents/` 对**小型文本文件**返回 200（base64），可作未来小文件备用源，但**模型权重仍不可用**。

---

## 三、智能源调度器

### 3.1 核心算法（用户要的"看延迟和下载速度"）

```
分档归一化评分  ×  EWMA 历史平滑  ×  渐进降权
       ↓                  ↓                ↓
  按文件大小选权重      抗抖动          按成功率衰减
```

**分档权重**（实测验证）：

| 文件大小 | 延迟权重 | 吞吐权重 | 策略 |
|---|---|---|---|
| < 512 MB | 0.6 | 0.4 | 重延迟 |
| ≥ 512 MB | 0.2 | 0.8 | 重吞吐 |
| engine 用途 | 0.1 | 0.9 | 极致吞吐 |

**EWMA 平滑**（α=0.4）：`历史 50 / 新样本 100 → 70.0`，脏样本不会直接翻转决策。

### 3.2 复用而非重写
直接复用 `hub/net.py` 的 **CircuitBreaker**（3 次失败 → 300s 冷却 → half-open）与 **TTLCache**（探测缓存 300s），未另起炉灶。

### 3.3 真实上游验证

```
=== 真实源并发探测（4 源）===
  1. ✅ hf-mirror.com      HTTP:200  延迟:  578.0ms
  2. ❌ modelscope.cn      HTTP:  0  延迟:  250.8ms  err=peer closed connection
  3. ❌ hf-mirror.us       HTTP:  0  延迟:    2.6ms  err=DNS 解析失败
  4. ❌ hf-cdn.sufy.com    HTTP:503  延迟:  839.8ms
  🏆 自动选中: hf-mirror.com   ✓ 排序正确

=== 真实大文件吞吐（2 源）===
  ✅ modelscope.cn    延迟:  335.0ms  速度: 0.190MB/s
  ✅ hf-mirror.com    延迟: 1962.3ms  速度: 0.030MB/s
  🏆 自动选中: modelscope.cn   ← 综合更快，决策正确
```

---

## 四、QA 独立发现的问题与修复

| 编号 | 问题 | 状态 |
|---|---|---|
| **P1-1** | 评分**完全不校验探测体字节数**——返回 200 但 0 字节的源（正是 SPA 兜底页形态）在小文件档下会以 0.6 分**击败真实 10MB/s 的源**，并污染 health 缓存 300s | ✅ **已修复** |
| **P1-2** | EWMA 抗抖动名不符实——灌入任意深度好历史后注入一次 9999ms，一律变为 4023.6ms，可翻转排序 | ⚠️ 列 v2.8.2 |
| **P1-3** | `measure_sources(registry=..., profile=...)` **静默丢弃 profile 参数**（源码写死 `file_size=0`） | ✅ **已修复** |

### 修复详情

**P1-1**：新增 `_is_usable_probe()` 作为**单点判定**（同时供归一化池与最终评分使用，防止两处逻辑漂移），拒绝 `2xx + 空 body`。修复过程中发现 `_ProbeView` 未暴露 `size_bytes`、且 `_score_ranking` 传的是原始 dict，两处一并修正（dict 与对象形态均正确判定）。

验证：0 字节源 score=`None` 被排除，真源胜出。

**P1-3**：`measure_sources` 改为转发 `profile=profile`。修复中发现参数取值语义：`profile` 需传 `"latency"`/`"speed"` 字符串，而 `PROFILE_SMALL`/`PROFILE_LARGE` 是权重元组常量。

验证：`profile="latency"` → a.com(0.6)；`profile="speed"` → b.com(0.8)，**分档真正改变赢家**。

**防回归**：新增 4 项测试锁定上述行为。

### QA 确认的其余结论
- 分档权重**确实起作用**（同组源在不同档位选出不同赢家）✓
- 安全回归：P0-1 脱敏 / P0-2 Zip Slip / P0-3 import 校验**全部仍有效** ✓
- 畸形输入 / 全超时 / 高并发均优雅降级，3 个新端点校验完备 ✓

### 已知未利用价值（P2-2）
真实下载主路径 `/api/download/start` 写死 `file_size=0`、`/api/hub/download` 未接调度器 → **分档价值在实际下载时大部分未被利用**。建议 v2.8.2 打通。

---

## 五、文件清单

### 新增
```
python/app/sources_registry.py          源元数据注册表（8 源 + 健康度）
python/app/source_scheduler.py          调度器内核  ← 含 P1-1/P1-3 修复
python/tests/test_source_scheduler.py    +4 项防回归
docs/DESIGN_V281_SOURCES.md              架构设计
docs/class-diagram.mermaid               类图
docs/sequence-diagram.mermaid            时序图
docs/EXTREME_TEST_V281_SOURCES.md        QA 极端测试报告
```

### 修改
```
python/app/sources.py          +50   profile 转发（P1-3）
python/app/settings.py         +29   源注册表配置
python/app/main.py            +610   3 个新端点 + 调度器接线
python/app/hub/registry.py            candidate_meta
electron/main.js / preload.js         IPC 通道
renderer/modules/downloads.js +106    源测速可视化
renderer/styles.css            +40
```

---

## 六、验证命令

```bash
cd python && python3 -m pytest -q tests/                   # 497 passed
unshare -rn bash -c 'ip link set lo up; python3 -m pytest -q tests/'   # 497 passed
npm run test:js
cd python && python3 -c "from app import main"
npm start
```

---

## 七、用户下一步建议

1. **验证源测速 UI**：下载页应能看到各源的延迟/速度条形对比，点「重新测速」可强制刷新
2. **验证自动选源**：下载一个模型，观察日志中是否自动选中了更快的源
3. **GitCode 自测**：若你的网络环境能访问 gitcode.com，按 `docs/DESIGN_V281_SOURCES.md` §2.6 的 curl 步骤验证后，填配置启用。**我无法在本环境证实其可用性，故默认关闭**
4. **建议 v2.8.2**：① 把 `file_size` 打通到真实下载路径（让分档真正生效）；② 修 P1-2 EWMA 抗抖动深度
5. **发布准备**：`package.json` 版本号建议 bump 至 2.8.1，README 测试数字统一为 497
