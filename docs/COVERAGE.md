# 测试覆盖率现状与追赶计划

> 基线测量时间：2026-11-16
> 测量方式：`cd python && pytest -q --cov=app --cov-report=term-missing --cov-report=json`
> 用例数：**2787 passed, 7 warnings**
> 耗时：949.48s
> Python：3.12.11

## 一、当前数值

| 指标 | 值 |
|------|-----|
| 总覆盖率 | **72.2%** |
| 已覆盖语句 | 15,449 |
| 语句总数 | 21,397 |
| 未覆盖语句 | 5,948 |
| CI 门槛（floor） | 68% |
| 目标值（goal） | 80% |
| 距 80% 缺口 | **约 1,669 行** |

> 门槛由 `.github/workflows/ci.yml` 的 `FLOOR = 68.0` 定义。它低于实测基线
> 4.2 个百分点，职责是捕获倒退；覆盖率真实提升后再上调，不能把门槛抬到实测值之上。

## 二、缺口分布

前 12 个模块占全部缺口的约 79%：

| 模块 | 覆盖率 | 未覆盖 | 语句数 |
|------|--------|--------|--------|
| `app/main.py` | 64% | 1,304 | 3,626 |
| `app/three_d_runtime.py` | 40% | 556 | 929 |
| `app/engines.py` | 59% | 396 | 974 |
| `app/converter.py` | 32% | 271 | 398 |
| `app/mnn_runtime.py` | 57% | 242 | 560 |
| `app/piper_runtime.py` | 53% | 216 | 460 |
| `app/special_runtime.py` | 25% | 187 | 249 |
| `app/drama.py` | 59% | 148 | 363 |
| `app/ltx_runtime.py` | 65% | 136 | 392 |
| `app/preview_engine.py` | 33% | 125 | 186 |
| `app/music_runtime.py` | 59% | 124 | 305 |
| `app/downloader.py` | 84% | 123 | 777 |

其余缺口较大的模块：`app/media_runtime.py`（38%，115 行）、
`app/demucs_runtime.py`（55%，102 行）、`app/gpu.py`（55%，96 行）、
`app/hub/modelscope.py`（45%，171 行，OpenAPI 新路径需要补单测）、
`app/hub/taxonomy.py`（51%，98 行）。

## 三、优先级

### P0：单模块缺口 > 200 行

| 模块 | 缺口 | 策略 |
|------|------|------|
| `app/main.py` | 1,304 | 路由注册样板先用 FastAPI TestClient 做参数/鉴权冒烟；重点补 `/api/health`、`/api/hub/*`、`/api/engines/*` 的异常分支 |
| `app/three_d_runtime.py` | 556 | mock 外部进程与文件布局，补命令构造、失败回退、超时取消 |
| `app/engines.py` | 396 | 纯逻辑的安装计划/版本探测优先；真实下载只做 mock |
| `app/converter.py` | 271 | 输入输出明确的格式转换，最容易用临时文件做表驱动测试 |
| `app/mnn_runtime.py` | 242 | mock 进程与 `safe_join` 错误分支，重点覆盖 Windows 路径拒绝 |

### P1：50%–70%

`app/piper_runtime.py`、`app/special_runtime.py`、`app/drama.py`、
`app/ltx_runtime.py`、`app/preview_engine.py`、`app/music_runtime.py`、
`app/gpu.py`、`app/hub/taxonomy.py`、`app/hub/modelscope.py`。

> `hub/modelscope.py` 现在有两条列表路径：OpenAPI 搜索（快路径）和 legacy
> detail/files。新搜索路径要补：OpenAPI 成功映射、`success=false`、`models`
> 非列表、total_count 分页、sort 参数、OpenAPI 失败降级；legacy 路径继续覆盖
> 组织信息、ReadMe、文件列表。

### P2：低覆盖但语句少

`app/mnn_catalog.py`、`app/agent/tools/drama_tools.py`、`app/gpu.py`。

## 四、推进节奏

1. 每个版本补 150–250 行覆盖，约 5–8 个用例文件；
2. 优先 P0 的纯逻辑模块（`converter.py`、`engines.py`）；
3. 每补一批把 CI 门槛上调到实测值下方 1–2 个百分点；
4. 覆盖率达到 80% 前不把 goal 当成 gate。

## 五、如何本地复现

```bash
cd python
pytest -q --cov=app --cov-report=term-missing --cov-report=json:/tmp/coverage.json
```

分模块 HTML：

```bash
pytest -q --cov=app --cov-report=term-missing --cov-report=html:htmlcov
# 打开 python/htmlcov/index.html
```

> `test_json_integrity.py` 会遍历仓库内全部 `*.json` 做校验，并跳过
> `__pycache__` / `node_modules` / `.mypy_cache` / `.pytest_cache` /
> `.ruff_cache` / `.git`。若先跑 `mypy` 再跑 pytest，缺少该排除项会让
> 用例数异常膨胀；CI 的 lint 与 pytest 在不同 job、都是干净检出，不会触发。
