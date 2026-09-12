# Kevrai Omni v2.8.0 交付报告 — 双源对接 + 懒加载 + 极端测试加固

> 交付日期：2026-09-12
> 基线提交：`5156e9c`（v2.7.0）
> Kevrai Omni Team

---

## TL;DR

Kevrai Omni 已完成 **HuggingFace + 魔搭 ModelScope 双源对接**，模型市场支持**游标分页懒加载**，并通过 **69 个对抗性极端测试用例**验证。全量测试 **471 passed**，断网同样全绿，真实上游端到端 **12/12 通过**。

---

## 一、交付概览

| 维度 | 结果 |
|---|---|
| 交付状态 | ✅ 可交付（1 个 P1 已修复，1 个 P2 列为后续项） |
| 后端测试 | **471 passed / 0 failed**（基线 421 → +50） |
| 断网测试 | **471 passed**（`unshare -rn` 实测） |
| 真实上游验证 | 魔搭 5/5 + HF 2/2 + HTTP 端点 7/7 = **14/14** |
| 极端测试 | 69 用例，**67 通过 / 2 暴露真实缺陷** |
| P0 安全问题 | **3/3 已修复** |
| 关键陷阱 | **3/3 已修复** |
| 前端懒加载 | ✅ 游标分页 + 增量 append，加载/错误/重试/空态齐全 |

---

## 二、核心能力：双源对接

### 2.1 已实测核准的上游 API（真实 curl 结果，非文档推断）

| 接口 | 方法 | 实测 |
|---|---|---|
| 魔搭搜索 | `PUT https://modelscope.cn/api/v1/models` | 200，`Data.Models[]` + `TotalCount` |
| 魔搭详情 | `GET /api/v1/models/{ns}/{name}` | 200 |
| 魔搭文件列表 | `GET /api/v1/models/{ns}/{name}/repo/files` | 200，`Data.Files[]` |
| 魔搭单文件 | `GET /models/{ns}/{name}/resolve/master/{file}` | 200 |
| HF 搜索/详情/文件 | `huggingface.co/api/*` | 200（主站 403 时自动回退镜像） |

魔搭全站模型总量实测 **253,409** 条，全部可达可检索。

### 2.2 真实上游端到端验证

```
魔搭侧（5/5）：
  ✓ 搜索 "Qwen2.5" → Qwen/Qwen2.5-7B-Instruct ↓7,211,878 ★518 Apache-2.0
  ✓ 详情 / ✓ 文件列表(15 个) / ✓ resolve_url / ✓ health

HF 侧（2/2）：
  ✓ 搜索 "qwen2.5" → Qwen/Qwen2.5-7B-Instruct ↓10,059,299 ★1588
  ✓ health: online=True circuit=closed

HTTP 端点（7/7）：
  ✓ /api/hub/sources  ✓ 双源 search 返回真实数据
  ✓ 非法 hub / 超长查询 / 路径穿越 均不 500
  ✓ /api/settings 不泄露 token
```

### 2.3 架构

```
SourceAdapter (ABC)
  ├── HuggingFaceAdapter   —— snake_case 映射（_HF_FIELD_MAP 单点收口）
  ├── ModelScopeAdapter    —— PascalCase 映射，复用 mnn_catalog 既有客户端
  └── CuratedAdapter       —— 本地策展 catalog/models.json
         ↓ 统一归一化为
  RemoteModel / RemoteFile / PageResult / SourceCursor
         ↓ 编排
  HubRegistry  →  断路器 · TTL 缓存 · 令牌桶限流 · 重试 · 镜像回退
```

**新增 9 个模块共 3346 行**：`base.py` `hf.py` `modelscope.py` `curated.py` `registry.py` `net.py` `paths.py` `taxonomy.py` `__init__.py`

### 2.4 新增端点（6 个，现有 60+ 路由零改动）

| 端点 | 说明 |
|---|---|
| `GET /api/hub/sources` | 列出可用源及健康状态 |
| `GET /api/hub/search` | 双源检索，游标分页 |
| `GET /api/hub/model` | 模型详情 |
| `GET /api/hub/model/files` | 文件列表 |
| `POST /api/hub/download` | 触发下载（复用既有 downloader + 镜像回退） |
| `GET /api/hub/jobs/{job_id}` | 下载任务状态 |

---

## 三、模型市场懒加载

- **游标分页**：`next_cursor` / `has_more`，而非 offset 翻页，避免深翻页性能塌陷
- **虚拟网格增量追加**：`virtual-grid.js` 新增 `appendItems()`（增量插入，不重建 DOM）+ `onNearEnd` 回调
- **重入保护**：`loadingMore` 标志位，防止滚动快速触底时并发请求
- **状态完备**：加载态 / 错误态 / 重试 / 空态，含"滚动加载更多"提示
- **零框架引入**，未大规模重写渲染层，复用既有 `virtual-grid` 窗口化机制

---

## 四、安全加固

| 编号 | 问题 | 状态 |
|---|---|---|
| P0-1 | `GET /api/settings` 明文回吐 token | ✅ 已改为 `_redact_settings()`，返回 `*_set` 布尔 |
| P0-2 | `engines.py` 两处 `zf.extractall()` Zip Slip | ✅ 统一走 `hub.paths.safe_extract`（逐 member 校验） |
| P0-3 | `/api/models/import` 无路径/扩展名校验 | ✅ 已加路径包含校验 + 扩展名白名单 |

**三个致命陷阱（会导致"测试全绿但功能不可用"）**：

| 陷阱 | 问题 | 状态 |
|---|---|---|
| 1 | `electron/main.js` `ALLOW_DEFAULT` 无魔搭域名 → 下载被静默拦下 | ✅ 已加 `modelscope.cn` |
| 2 | `hf_token` 在 `saveSettings` 持久化白名单缺失 → token 存不下来 | ✅ 已修 + 新增 `ms_token` |
| 3 | `test:js` 未覆盖 `virtual-grid.js` | ✅ 已补白名单 |

---

## 五、极端测试结果（69 用例）

### 5.1 覆盖维度

| 类别 | 用例数 | 结果 |
|---|---|---|
| 输入畸形（截断 JSON / 类型全错 / 超大 PageSize / 路径穿越 / 控制字符） | 46 | 通过 |
| 网络（超时 / 429 / 500 / 403 / 游标伪造 / 大响应） | 含上 | 通过 |
| 并发与资源（50 并发 / 下载取消 / 连接泄漏） | 含上 | 通过 |
| 安全回归（P0×3 / 白名单伪装域名） | 15 | 通过 |
| 前端 `virtual-grid`（空数组 / 非数组 / null 元素 / 重入） | 8 | 通过 |

### 5.2 独立发现的两个真实缺陷

**P1 — 已修复** ✅

> 上游 JSON 含 `Infinity` / `NaN` 字面量时（`json.loads` 默认接受），`base.py` 的 `as_int` 抛 `OverflowError`，导致**整页合法结果被一并丢弃**。HF 与魔搭双端、search/detail/files 均受影响。
>
> 根因：`int(float("inf"))` 抛 `OverflowError`（已独立复现）
> 修复：`as_int` 加 `math.isfinite()` 守卫 + 补 `import math`，非有限值退化为默认值
> 验证：15 组脏数据全部安全退化，正常路径不受影响
> 防回归：新增 2 项测试锁定该行为

**P2 — 列为 v2.8.1** ⚠️

> 单次离线 `search()` 累积 6 次失败（3 镜像 × 3 重试 > 阈值 5），触发 HF 断路器并**连带封锁 2 个健康镜像**，冷却 60s 内无法自愈。
> 影响：离线场景下恢复延迟，不阻断核心链路。

### 5.3 过程中发现并修复的其他缺陷

工程师在实跑中（而非静态检查）发现并修复：

1. `hf.py`：`tuple(mirrors or default_mirrors)` 会静默忽略显式 `mirrors=()`，导致**离线测试实际在打真实镜像** —— 已修复为区分 `None` 与空序列
2. `net.py` `CircuitBreaker`：`_opened_at = 0.0` 是 falsy，导致 t=0 打开的断路器**永远无法半开** —— 已改为 `-1.0` 哨兵
3. `hub/paths.py`：正则 `r"^(?i)(con|prn|...)"` 在 Python 3.11 直接抛 `re.error: global flags not at the start of the expression`，**模块导入即崩**（该模块上一轮从未被真正导入运行过）—— 已改为 `re.compile(..., re.IGNORECASE)`

---

## 六、文件清单

### 新增 · 后端核心（3346 行）
```
python/app/hub/__init__.py        160
python/app/hub/base.py            491   ← 含 P1 修复
python/app/hub/curated.py         201
python/app/hub/hf.py              447
python/app/hub/modelscope.py      403
python/app/hub/net.py             466
python/app/hub/paths.py           188   ← 含导入崩溃修复
python/app/hub/registry.py        539
python/app/hub/taxonomy.py        451
```

### 新增 · 测试（1016 行）
```
python/tests/conftest.py                  79
python/tests/hub_fake_server.py          377   ← 离线 mock 服务
python/tests/test_hub_dual_source.py     560   ← 50 项（含 2 项防回归）
```

### 修改（16 文件，+1006 / -30）
```
python/app/main.py                  +449   ← 6 个 hub 端点 + P0-1/P0-3
python/app/engines.py               +9     ← P0-2 Zip Slip
python/app/settings.py              +10    ← ms_token + 持久化
python/app/sources.py               +4
python/tests/test_v241_api.py       +19
electron/main.js                    +84    ← 白名单 + IPC 校验
electron/preload.js                 +57
renderer/modules/search.js          +227   ← 游标分页
renderer/modules/models.js          +78
renderer/modules/virtual-grid.js    +52    ← appendItems
renderer/modules/{api,settings}.js  +13
renderer/index.html / styles.css    +27
package.json / python/pyproject.toml +7
```

### 文档产出
```
docs/QUALITY_AUDIT.md                质量审计报告
docs/OPTIMIZATION_PLAN.md            优化方案
docs/DESIGN_V280_DUAL_SOURCE.md      双源架构设计（1723 行）
docs/EXTREME_TEST_REPORT.md          极端测试报告
docs/DELIVERY_V280_DUAL_SOURCE.md    本文件
```

---

## 七、验证命令

```bash
# 全量测试（应 471 passed）
cd python && python3 -m pytest -q tests/

# 断网测试（应 471 passed）
unshare -rn bash -c 'ip link set lo up; python3 -m pytest -q tests/'

# 前端语法检查
npm run test:js

# 侧车可导入
cd python && python3 -c "from app import main"

# 启动应用
npm start
```

---

## 八、已知限制与后续建议

| 项 | 说明 | 建议 |
|---|---|---|
| P2 断路器耦合 | 离线时失败计数跨镜像累积，误封健康镜像 | v2.8.1：按镜像独立计数 |
| 魔搭私有库鉴权 | 私有模型鉴权头沿用 `mnn_catalog` 常量，未经私有库实测 | 需真实私有库验证 |
| HF 字段映射 | `_HF_FIELD_MAP` 已单点收口，但部分字段未经上游实测确认 | 关注上游变更 |
| 覆盖率 | 仍低于理想值（converter / drama / recommend 偏低） | 后续补测试 |
| 版本号 | `package.json` 仍为 2.7.0 | 发布前需 bump 至 2.8.0 |
| 文档数字 | README 中 303/323/372/420 四处历史测试数字互相矛盾 | 统一更新为 471 |

---

## 九、用户下一步建议

1. **验证双源**：启动应用 → 模型市场 → 切换「魔搭 / HuggingFace」源，确认能检索到两个社区的模型
2. **验证懒加载**：搜索"qwen"这类高频词，滚动到底部确认自动加载下一页
3. **验证下载**：从魔搭挑一个模型下载，确认不再被 Electron 白名单拦下（这是之前会静默失败的路径）
4. **发布准备**：`package.json` 版本号 bump 至 2.8.0，README 测试数字统一为 471
5. **后续项**：P2 断路器隔离建议纳入 v2.8.1
