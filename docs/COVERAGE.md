# 测试覆盖率现状与追赶计划

> 基线测量时间：2026-09-22
> 测量方式：`cd python && pytest --cov=app`，Python 3.11（干净 venv，按 CI 的依赖集安装）
> 用例数：**832 全部通过**

## 一、当前数值

| 指标 | 值 |
|------|-----|
| 总覆盖率 | **69.6%** |
| 已覆盖语句 | 6,531 |
| 语句总数 | 9,388 |
| 未覆盖语句 | 2,857 |
| CI 门槛（floor） | 65% —— 防止倒退 |
| 目标值（goal） | 80% |
| 距 80% 缺口 | **约 976 行** |

> 覆盖率门槛由 ci.yml 中 `FLOOR` 常量定义。它被刻意设在实测基线**下方约 4.5 个百分点**：
> 职责是捕获*倒退*，而不是长期标红。**随着覆盖率真实提升再上调门槛，
> 绝不允许把门槛抬到实际值之上。**

## 二、为什么门槛不是 80%

CI 最初写的门槛是 80%，但**实测只有 66.5%，意味着 python job 从建立起就必然是红的** ——
一个永远失败的检查等于没有检查，只会让人学会忽略它。

现在的做法是把门槛设在**实测基线略下方**，并用 `::warning::` 提示距 80% 目标还有多远。

## 三、缺口分布

前 12 个模块占了全部缺口的 75% —— 补覆盖率应该从这里下手，而不是撒胡椒面。

| 模块 | 覆盖率 | 未覆盖 | 语句数 |
|------|--------|--------|--------|
| `app/main.py` | 49% | 827 | 1628 |
| `app/converter.py` | 21% | 309 | 392 |
| `app/mnn_runtime.py` | 15% | 217 | 256 |
| `app/drama.py` | 51% | 164 | 334 |
| `app/ltx_runtime.py` | 61% | 122 | 310 |
| `app/engines.py` | 78% | 114 | 526 |
| `app/gpu.py` | 48% | 99 | 191 |
| `app/hub/hf.py` | 68% | 91 | 286 |
| `app/downloader.py` | 74% | 67 | 261 |
| `app/hub/modelscope.py` | 75% | 65 | 256 |
| `app/importer.py` | 77% | 60 | 262 |
| `app/hub/taxonomy.py` | 71% | 58 | 197 |

## 四、优先级建议

### P0：缺口大户（单模块 > 150 行）

| 模块 | 缺口 | 说明 |
|------|------|------|
| `app/main.py` | 827 | FastAPI 路由定义。大量是路由注册样板，可先用 pytest 客户端做冒烟式覆盖，收益快 |
| `app/converter.py` | 309 | 格式转换。纯函数逻辑，输入输出明确，最容易写单测 |
| `app/mnn_runtime.py` | 217 | MNN 引擎适配。需 mock 外部进程调用 |
| `app/drama.py` | 164 | 短剧生成流程。副作用多，需先理清依赖边界 |

**只补 `converter.py` + `mnn_runtime.py` + `drama.py` 三个（690 行）
就能把总覆盖率推到约 76.9%**，是性价比最高的一步。

### P1：接近及格线的模块（50%–75%）

补起来边际成本低，往往再写几个用例就能到 80%：

| 模块 | 覆盖率 | 未覆盖 | 语句数 |
|------|--------|--------|--------|
| `app/ltx_runtime.py` | 61% | 122 | 310 |
| `app/agent/model_router.py` | 62% | 14 | 37 |
| `app/hub/hf.py` | 68% | 91 | 286 |
| `app/hub/taxonomy.py` | 71% | 58 | 197 |
| `app/hub/paths.py` | 76% | 23 | 97 |
| `app/agent/tools/system_tools.py` | 72% | 29 | 105 |
| `app/hardware.py` | 77% | 33 | 141 |
| `app/env.py` | 75% | 44 | 176 |
| `app/downloader.py` | 74% | 67 | 261 |

### P2：近乎零覆盖的模块

覆盖率极低但语句数不多，属于「一直没被碰过」的角落：

| 模块 | 覆盖率 | 未覆盖 | 语句数 |
|------|--------|--------|--------|
| `app/mnn_catalog.py` | 29% | 50 | 70 |
| `app/agent/tools/drama_tools.py` | 45% | 33 | 60 |
| `app/gpu.py` | 48% | 99 | 191 |

## 五、v2.9.0 已完成的补测

| 模块 | 改动前 | 改动后 | 说明 |
|------|--------|--------|------|
| `app/recommend.py` | 7.4% | **100%** | 新增 `tests/test_recommend.py`（33 个用例），覆盖打分/排序/并列打破 |
| `app/runner.py` | 0% | — | 已确认为死代码并删除（`find_engine_binary` 零调用者，`spawn_llama_server` 仅抛 `NotImplementedError`） |

## 六、推进节奏

不建议一次性投入数天冲 80%。建议按发布周期分批：

1. **每个版本补 150–250 行覆盖** —— 约 5–8 个用例文件，工作量可控
2. 优先补 **P0 的纯逻辑模块**（`converter.py` 最优先）
3. 每补一批就把 CI 门槛上调到新的实测值下方 1–2 个百分点
4. 覆盖率不再倒退的前提下，约 4–5 个版本可到 80%

## 七、如何本地复现

```bash
cd python
pytest -q tests/ --cov=app --cov-report=term-missing
```

查看具体哪一行没覆盖，把 `term-missing` 换成分模块报告：

```bash
pytest -q tests/ --cov=app --cov-report=term-missing \
  --cov-report=html:htmlcov
# 打开 python/htmlcov/index.html
```

> **注意**：`test_json_integrity.py` 会遍历仓库内全部 `*.json` 做校验。
> 它会跳过 `__pycache__` / `node_modules` / `.mypy_cache` / `.pytest_cache` /
> `.ruff_cache` / `.git`。若在本地先跑过 `mypy` 再跑 pytest，**没有这个排除项**
> 会让用例数从 832 膨胀到约 2 万（mypy 缓存在 `.mypy_cache` 下写了上万个 JSON），
> 造出「本地比 CI 检查得多」的假象。CI 因为 lint 与 pytest 跑在不同 job、
> 且都是干净检出，所以从未暴露这个问题。
