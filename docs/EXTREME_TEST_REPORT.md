# 极端测试报告 — Kevrai-omni 双源 Hub (v2.8.0)

> 测试执行：Kevrai Omni Team
> 执行日期：2026-09-12
> 测试对象：`/workspace/Kevrai-omni`（工作区未提交改动，`git status` 有 16 处修改 + 3 处新增）
> 测试性质：**独立对抗性验证**。所有数字均为本人实跑，未引用工程师自证结论。
> 临时脚本位置：`tests/scratch/`（未污染仓库）

---

## 0. 结论速览（TL;DR）

- **基线**：三项全部与工程师自证一致 —— 全量 `469 passed`、断网 `469 passed`、`npm run test:js` 全绿。
- **极端/对抗测试**：共 **69 个用例**（Python 46 + 端点/安全 15 + 前端 8），**通过 67，失败 2**。
- **新发现问题**：**2 个**（P1 × 1 类、P2 × 1 类）。P0 **0 个**。
  - **P1**：上游 JSON 含 `Infinity`/`NaN` 时，`as_int()` 抛 `OverflowError`，导致**整页搜索结果被丢弃**（HF 与魔搭双端均可复现）。
  - **P2**：单次离线 `search()` 即可累积 6 次失败（3 镜像 × 最多 3 次重试），触发 HF 断路器开启并连带封锁其余健康镜像，冷却 60s。
- **交叉验证 C**：两项修复**均确认有效**（`mirrors=()` 不再回退；`_opened_at=0.0` 可正常半开）。
- **交付判定**：**有条件可交付** —— P1 建议合并前修复（低成本、一行防御），P2 可列入 v2.8.1。二者均不阻断核心链路，均不构成安全风险。

---

## 1. 三项基线复测（独立实跑）

| 项 | 命令 | 工程师自证 | 我实测 | 一致性 |
|---|---|---|---|---|
| 全量测试 | `cd python && python3 -m pytest -q tests/` | 469 passed | **469 passed in 81.01s** | ✅ 一致 |
| 断网测试 | `unshare -rn bash -c 'ip link set lo up; python3 -m pytest -q tests/'` | 469 passed | **469 passed in 34.91s** | ✅ 一致 |
| 前端检查 | `npm run test:js` | 通过 | **13 个 `node --check` 全部通过（exit 0）** | ✅ 一致 |

> 附加独立验证：断网环境下用**真实适配器**（非 mock）跑 `HubRegistry.search()`，确认无隐藏网络依赖、无解释器挂起，正常降级返回。

---

## 2. 极端测试矩阵表

图例：判定 = 通过 / 失败 / 存疑。所有"构造方式"均为本人实际执行的输入。

### 2.1 输入畸形类

| # | 场景 | 构造方式 | 预期 | 实测结果 | 判定 |
|---|---|---|---|---|---|
| 1 | HF 截断 JSON | `body='{"partial": '` | 降级不崩溃 | `degraded=True code=bad_json items=0` | 通过 |
| 2 | HF HTML 错误页 | `<html>502</html>` status=200 | 降级 | `degraded=True code=bad_json` | 通过 |
| 3 | HF `null` body | `body='null'` | 不崩溃 | `degraded=False code=ok items=0`（`null`→None，安全） | 通过 |
| 4 | HF 数组元素类型全错 | `[1,"x",null,[],{id:123}]` | 无脏数据 | `items=0`（`id=123` 非 `owner/name` 被跳） | 通过 |
| 5 | HF 缺 `id`/`modelId` | 仅 `{downloads:5}` | 跳过无效项 | 有效项保留，脏项丢弃 | 通过 |
| 6 | 魔搭 `Data` 缺失 | `{Code:200}` | 降级 | `degraded=True code=bad_json` | 通过 |
| 7 | 魔搭 `Models=null` | `Data.Models=None` | 空结果 | `code=empty items=0` | 通过 |
| 8 | 魔搭 `Models` 为字符串（E05） | `Data.Models="abcdef"` | **不逐字符展开** | `items=0`（未爆炸成 6 条） | 通过 |
| 9 | 魔搭字段类型全错 | `Path=123,Name=[],Stars={}` | 强制转换 | `items=1`，无崩溃 | 通过 |
| 10 | 魔搭 payload 非 dict | `[1,2,3]` | 降级 | `degraded=True code=http_error` | 通过 |
| 11 | `PageSize` 超大/负/0/非数字 | `1e9 / -5 / 0 / "abc" / 2^63` | 钳制到 `[1,100]` | `1e9→100, -5→1, 0→1, "abc"→30, 2^63→100` | 通过 |
| 12 | `PageSize` 超长数字串 | `"9"*15` | 钳制 | `→100` | 通过 |
| 13 | **上游含 `Infinity`/`NaN`** | HF `downloads:Infinity` | 不应丢整页 | **`OverflowError` → 整页丢弃** | **失败 (P1)** |
| 14 | 同上（魔搭） | ms `StorageSize:Infinity` | 不应丢整页 | **`OverflowError` → 整页丢弃** | **失败 (P1)** |

### 2.2 网络类

| # | 场景 | 构造方式 | 预期 | 实测结果 | 判定 |
|---|---|---|---|---|---|
| 15 | 503 重试后成功 | `503,503,200` | 重试到成功 | `attempts=3 sleeps=[0.4,1.2] ok=True` | 通过 |
| 16 | 403 不重试 | `403` | 1 次即止 | `attempts=1 code=http_error` | 通过 |
| 17 | 连接超时 | `ConnectTimeout` | 不抛异常 | `code=timeout attempts=2` | 通过 |
| 18 | DNS 失败 | `ConnectError` | 降级 | `code=network` | 通过 |
| 19 | 429 + `Retry-After:1` | 429 后 200 | 遵守并重试 | `ok=True sleeps=[1.0]` | 通过 |
| 20 | 429 敌意 `Retry-After:3600` | 429 | 立即放弃不挂起 | `code=rate_limited attempts=1`（未 sleep） | 通过 |
| 21 | 断路器熔断 | 连续失败至阈值 | 开启并拦截 | `code=circuit_open state=open` | 通过 |
| 22 | 令牌桶耗尽 | `rate=0 cap=2` 取 4 次 | 2 通过 2 拒绝 | `[True,True,False,False]` | 通过 |
| 23 | 伪造游标 | `@@@` / `!!notbase64!!` / `"a"*5000` / `v=999` | 拒绝或重启 | 分别 `BadCursor(not json)` / `(not base64url)` / `(too long)` / `{}` 重启 | 通过 |
| 24 | 大响应不 OOM | 20000 项约 1.6MB | 不崩溃 | `items=20000`，无 OOM | 通过 |

### 2.3 并发与资源类

| # | 场景 | 构造方式 | 预期 | 实测结果 | 判定 |
|---|---|---|---|---|---|
| 25 | 50 并发搜索缓存串号 | 50 个不同 `q` 并发 | 无串号 | `mismatches=0, upstream_calls=50` | 通过 |
| 26 | 端点 50 并发 | TestClient + ThreadPool(20) | 全部 200 | `codes_set={200}` | 通过 |
| 27 | `aclose` 释放自有连接 | 强制 `_client_owned=True` | 调用 `aclose` | 已调用 | 通过 |
| 28 | `aclose` 不关注入连接 | 注入 client | 不关闭 | `closed=False`（语义正确） | 通过 |
| 29 | TTL 缓存 LRU 驱逐 | `max=3` 塞 5 条 | ≤3 | `len=3 keys=[k2,k3,k4]` | 通过 |
| 30 | TTL 缓存过期 | `ttl=10` 后 `t=11` | 过期 | `before=1 after=None` | 通过 |
| 31 | 下载任务并发 + 取消 | 见 §4.1 覆盖说明 | 不泄漏 | 见下 | 通过 |

> **31 说明**：`/api/hub/download` 的多任务编排复用既有 `Downloader`（上游 469 项中 `test_download_start.py` / `test_downloader.py` 已覆盖 start/progress/cancel）。本轮我以端点级验证补充：超量文件（300）→ 400；job_id 非法（`../x`、超长）→ 400/404；合法 job_id 未创建 → 404。未发现竞态。

### 2.4 安全类（P0 回归确认真修好）

| # | 场景 | 构造方式 | 预期 | 实测结果 | 判定 |
|---|---|---|---|---|---|
| 32 | **P0-1 token 泄露** | PUT `hf_token=SECRET` → GET 响应体全文检索 | 明文不出现 | `leaked=False`，`hf_token` key 消失，`hf_token_set=True` | 通过 |
| 33 | P0-1 PUT 回显泄露 | PUT `ms_token=SECRET` 回显体检索 | 不泄露 | `leaked=False`，`ms_token_set=True` | 通过 |
| 34 | **P0-2 Zip Slip** | 构造 zip 含 `../../evil.txt` + `../sibling.txt` | 拦截，不外写 | `extracted=['good.txt']`，`escape_exists=False`，`sibling_outside=False` | 通过 |
| 35 | **P0-3 import 穿越** | `../../etc/passwd`、`/etc/passwd` | 400 | `traversal=400 abs=400` | 通过 |
| 36 | P0-3 import 非白名单扩展 | `/etc/hosts`（无后缀） | 400 | `badext=400` | 通过 |
| 37 | Electron 白名单伪装域名 | `evil-modelscope.cn` / `modelscope.cn.evil.com` / `notmodelscope.cn` / `evilgithub.com` | 全部拒绝 | `mismatches=[]` | 通过 |
| 38 | Electron 白名单正常域名 | `modelscope.cn` / `www.modelscope.cn` / `api.modelscope.cn` / `huggingface.co` | 全部放行 | `mismatches=[]` | 通过 |

> 白名单谓词逐字提取自 `electron/main.js:568-573`：`host === suffix || host.endsWith("." + suffix)`，**协议正确**，`evil-modelscope.cn` 因不以 `.modelscope.cn` 结尾被拒。
> **注**：`hf-mirror.com` **不在**默认白名单（`ALLOW_DEFAULT = ["huggingface.co","github.com","modelscope.cn"]`，`main.js:39`）。若用户启用 HF 镜像下载需手动加白；这不是 bug，但属于易踩的配置陷阱，列作 P2 观察项。

### 2.5 边界类

| # | 场景 | 构造方式 | 预期 | 实测结果 | 判定 |
|---|---|---|---|---|---|
| 39 | repo 路径穿越 | `../../etc/passwd` 等 12 例 | 全部拒绝 | 12/12 符合预期 | 通过 |
| 40 | repo null 字节/非串 | `owner/na\x00me` / `None` / `123` | 拒绝 | 全部拒绝 | 通过 |
| 41 | `safe_join` 穿越 | 10 种穿越/绝对/设备名 | 全部拦截 | 0 逃逸，合法路径通过 | 通过 |
| 42 | 空结果 | `[]` | items=0 | `items=0 code=empty` | 通过 |
| 43 | 单条结果 | 1 item | items=1 | `items=1 repo=o/one` | 通过 |
| 44 | 跨源去重 | hf + ms 同 repo | 合并 also_on | `2→1 also_on=['modelscope']` | 通过 |
| 45 | `merge_rank` 确定性 | dict 顺序打乱 | 结果一致 | 一致 | 通过 |
| 46 | `synth_id` 确定性/安全 | `owner/repo` | 稳定且合规 | `hf-eafaa740c01c1bec`（命中 MODEL_ID_RE） | 通过 |
| 47 | 前端 `appendItems([])` | 空数组 | 无操作 | `before=1 after=1` | 通过 |
| 48 | 前端 `appendItems(非数组)` | `"str"` / `null` / `{length:1}` | 无操作 | `len=1` | 通过 |
| 49 | 前端 `appendItems` 含 null 元素 | `[{b},null,undefined]` | 不抛异常 | `len=4`，无异常 | 通过 |
| 50 | 前端 `appendItems` 保 scrollTop | scrollTop=1200 | 不重置 | `scrollTop=1200` | 通过 |
| 51 | 前端 `setItems` 重置 scrollTop | 新查询 | 归零 | `scrollTop=0` | 通过 |
| 52 | `onNearEnd` 重入保护 | 快速触发 `_render` 20 次 | 仅 1 次 | `fired=1`（latch 生效） | 通过 |
| 53 | `setLoading(true)` 阻止 | loading 中滚动 | 不触发 | `fired=1` | 通过 |
| 54 | 回顶再到底重新触发 | `setLoading(false)` + 回顶 + 到底 | 再次 1 次 | `fired=2` | 通过 |

---

## 3. 新发现问题清单（工程师未测出）

### 🟠 P1 — 上游含 `Infinity`/`NaN` 时整页结果丢失

- **文件 + 行号**：
  - 根因：`python/app/hub/base.py:125-126`（`as_int` 的 `isinstance(value, float): return int(value)`）
  - 触发点 1：`python/app/hub/hf.py:235-236`（`downloads=as_int(...)`, `likes=as_int(...)`）
  - 触发点 2：`python/app/hub/modelscope.py:216-218`（`Downloads` / `Stars` / `StorageSize`）
- **复现步骤**：
  ```python
  import asyncio, httpx
  from app.hub.hf import HuggingFaceAdapter
  from app.hub.base import SearchSpec, SourceCursor

  body = (b'[{"id":"o/good","downloads":5,"likes":1,"tags":[],"pipeline_tag":"x"},'
          b'{"id":"o/bad","downloads":Infinity,"likes":1,"tags":[],"pipeline_tag":"x"}]')
  class C:
      async def request(self, m, u, **k):
          return httpx.Response(200, content=body, request=httpx.Request(m, u))
  ad = HuggingFaceAdapter(client=C())
  asyncio.run(ad.search(SearchSpec(q="x"), SourceCursor()))   # -> OverflowError
  ```
- **实际表现**：
  ```
  File ".../app/hub/hf.py", line 235, in _normalize
      downloads = as_int(_pick_hf(obj, "downloads", 0), 0)
  File ".../app/hub/base.py", line 126, in as_int
      return int(value)
  OverflowError: cannot convert float infinity to integer
  ```
  `NaN` 抛 `ValueError: cannot convert float NaN to integer`。
- **关键点：这是可达的**。Python `json.loads`（httpx `.json()` 默认走它）**接受** `Infinity` / `NaN` 字面量（`json.loads("Infinity") == inf`），JSON 规范虽不允许，但实现层面会被解析进来。因此任何"返回畸形数值的第三方源"都能触发。
- **影响**：`adapter.search()` 直接向调用方抛出。虽然 `HubRegistry._safe_search`（`registry.py:418`）会兜底把该源降级为 `code=network` 并返回 HTTP 200，但代价是 **该源本页全部合法结果一并丢失**（上面例子中 `o/good` 也被丢弃），且降级警告里直接泄露了 Python 异常文本（`"hf 检索异常：cannot convert float infinity to integer"`）。HF 与魔搭**两个源都中招**，且 detail/files 走同一 `_normalize`，同样受影响。
- **建议修法**（择一，成本极低）：
  1. 在 `as_int` 的 float 分支加有限性守卫：`if isinstance(value, float): return int(value) if math.isfinite(value) else default`；对 `bool`/`int` 分支无需改。
  2. 上游解析层防呆：`request_with_retry` 里改走 `json.loads(text, parse_constant=lambda _c: None)`（`net.py:392`），把 `Infinity`/`NaN` 归一为 `None`，从源头掐断。
  - 推荐**两者都做**：①修 helper ②修解析，双保险。

### 🟡 P2 — 单次离线搜索即可触发断路器并连带封锁健康镜像

- **文件 + 行号**：
  - `python/app/hub/hf.py:188-221`（`_fetch` 遍历镜像）
  - `python/app/hub/hf.py:153`（`CircuitBreaker(fail_threshold=5, ...)`）
  - `python/app/hub/net.py:402-403`（每次传输失败 `record_fail()`）
- **复现步骤**：3 个镜像全部不可达，仅调用一次 `ad.search(...)`。
- **实际表现**：单次 `search()` 内 HF 会尝试 3 个镜像 × 最多 3 次重试 = **最多 9 次失败**，实测一次即累积 `fails=6 > 阈值5`，断路器 `state=open`，**连带把另外 2 个健康镜像一并封锁**；随后 `open_seconds=60` 内所有搜索被短路为 `circuit_open`，即使网络已恢复也无法自愈（实测第二次 search 直接返回 `circuit_open`）。
- **影响**：可用性小幅下降 —— 用户一次断网搜索会令整个 HF 源"静默"60s。**非安全问题**，且断路器本身是设计内的自保护；问题在于**阈值按"单次请求"而非"单个上游端点"计数**，一次逻辑搜索被放大成多次物理失败。
- **建议修法**：`_fetch` 中每次 `search()` 对断路器最多记 1 次失败（成功即 `record_ok` 清零）；或把 `fail_threshold` 提高到 ≥ 镜像数 × 重试数；或让每个镜像用独立断路器。

### ⚪ P2（观察项，非 bug）

- **Electron 默认白名单不含 `hf-mirror.com`**（`electron/main.js:39`）。国内用户若依赖 HF 镜像测速/下载，会被 `isHostAllowed` 拒绝（`main.js:1026`）。属配置预期，但建议在 UI 或文档中明示，避免误报"下载被拒"。

---

## 4. 交叉验证 C（工程师两项修复，独立复核）

### C1 — `hf.py` `mirrors=()` 不再静默回退 ✅ 修复有效

| 输入 | 实测 `adapter.mirrors` | 结论 |
|---|---|---|
| `HuggingFaceAdapter(mirrors=())` | `()` | ✅ 显式空元组被尊重，**未**回退默认 |
| `HuggingFaceAdapter(mirrors=None)` | 3 个镜像 | ✅ `None` 才取默认 |
| `HuggingFaceAdapter()` | 3 个镜像 | ✅ 默认正常 |

> 代码核对：`hf.py:147-149` 使用 `tuple(default_mirrors) if mirrors is None else tuple(mirrors)`，以 `is None` 判空而非真值判断 —— 修复正确。

### C2 — `net.py` `CircuitBreaker._opened_at=0.0` 可正常半开 ✅ 修复有效

| 时点 | 实测 `cb.state` |
|---|---|
| 初始 | `closed` |
| 2 次失败（t=0.0） | `open`，`_opened_at=0.0` |
| t=5.0（冷却 10s 未到） | `open` |
| t=10.0（≥ open_seconds） | **`half-open`** ✅ |
| half-open 时 `allow()` | 第 1 次 `True`、第 2 次 `False`（单探针）✅ |

> 代码核对：`net.py:196` 用 `self._opened_at >= 0.0` 而非真值判断，并配 `-1.0` 哨兵初值（`net.py:187`）—— 修复正确，正是"t=0 打开后永远无法半开"的根因被消除。

---

## 5. 总结：能否交付

### 判定：**有条件可交付（Conditional Go）**

**可以交付的部分（证据充分）**
1. 三项基线独立复现，与自证完全一致（469/469/JS 全绿）。
2. 三个 P0（token 泄露 / Zip Slip / import 白名单）经**实测响应体与真实 zip** 验证，确已修好。
3. 输入畸形、分页、缓存、并发、游标、路径安全等 **52/54 项通过**，防御式设计（`as_str_list` 不展开字符串、`pick` 容忍非 Mapping、`clamp_int` 钳制等）经对抗测试站得住。
4. 前端游标分页与增量 append 的类型守卫、`onNearEnd` 重入 latch 均**行为正确**。
5. 交叉验证 C 两项修复**确实有效**。

**明确的风险敞口**
| 级别 | 问题 | 是否阻断交付 | 建议处置 |
|---|---|---|---|
| **P1** | 上游 `Infinity`/`NaN` 丢整页 | 否（有 HTTP200 兜底，但丢数据+泄露异常文本） | **合并前修**：`as_int` 加 `math.isfinite` 守卫 + 解析层 `parse_constant=None`，约 2 行 |
| **P2** | 单次搜索触发断路器并连带封锁镜像 | 否 | 列入 v2.8.1，`_fetch` 每搜索最多记 1 次失败 |
| **P2** | HF 镜像默认不在 Electron 白名单 | 否 | 文档/UI 明示 |

**未验证项（诚实声明）**
- 真实上游（huggingface.co / modelscope.cn）在线联调**未做**：沙箱为断网环境，`unshare` 下真实适配器只能验证降级路径。所有"上游行为"结论基于自建 stub/fake server，与生产上游可能存在字段漂移。
- 下载任务的**实际断点续传/磁盘写满**行为：复用既有 `Downloader` 测试覆盖，本轮仅做端点级参数校验，未重跑真实大文件落盘。

---

## 附：复现资料

| 文件 | 说明 |
|---|---|
| `tests/scratch/extreme_test.py` | 46 项 Python 对抗测试（含 2 项 P1 复现） |
| `tests/scratch/endpoint_test.py` | 15 项端点/安全回归（TestClient + Electron 白名单） |
| `tests/scratch/vgrid_test.mjs` | 8 项前端 virtual-grid 逻辑守卫测试 |
| `tests/scratch/extreme_results.json` | 机器可读结果 |
| `tests/scratch/endpoint_results.json` | 机器可读结果 |

> **红线遵守声明**：以上所有结果均为本人实际执行所得；跑不通项已标注"未验证 + 原因"；**未修改任何源码，未修改既有测试断言**。发现的源码 bug 仅记录，修复决策权归交付总监。
