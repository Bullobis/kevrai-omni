# Neuron-Runtime2 · P1 审计：LTX/MNN 外部引擎子进程取消时的进程组回收

- **日期**：2026-09-24
- **切片**：Project K-Cortex 第二切片 / Neuron-Runtime2
- **基线 commit**：`d350bdb`（分支 `feature/k-cortex-n2-runtime`）
- **结论**：**已核实，本轮不改生产代码（no-op fix）**。两个 runtime 在取消/超时/异常路径上均不存在需要 `killpg` 整组回收的子进程。仅新增 3 个护栏回归测试锁定该不变量。
- **测试基线**：940 passed → 943 passed（+3 护栏），零新增失败。

---

## 1. 问题（被审计的 P1 假设）

> LTX 在 daemon 线程跑 diffusers、MNN 是进程内 C++ 单例，上一切片已确认二者均未使用 `subprocess.Popen`。
> 但若 main(d350bdb) 中二者引入了外部推理 CLI / ffmpeg 后处理等子进程，取消时只 kill 父进程会留下孤儿子进程（ffmpeg、推理引擎子进程），造成资源泄漏。需要跨平台进程组隔离回收（POSIX `start_new_session`+`killpg`；Win `CREATE_NEW_PROCESS_GROUP`+`taskkill /T`）。

## 2. 证据：当前架构逐行核实

核实方法：通读 `python/app/ltx_runtime.py`（581 行）、`python/app/mnn_runtime.py`（421 行）全文，并对整个 `python/app/` 做正则交叉扫描。

### 2.1 直接子进程调用：零

全 `python/app/` 扫描 `subprocess|Popen|os.exec|os.system|check_output|os.spawn|create_subprocess|os.killpg|start_new_session|CREATE_NEW_PROCESS_GROUP|taskkill` 的结果：

| 文件 | 子进程用途 | 是否在本切片范围 |
|---|---|---|
| `converter.py` | 模型转换 `git`/转换 CLI，`Popen` + 协作取消 | 否（范围外，已有 terminate 路径） |
| `gpu.py` | nvidia/rocm/npu-smi/system_profiler 探测，`asyncio` 子进程 + 超时 `proc.kill()`（commit `36eaf99`） | 否 |
| `env.py` / `engines.py` / `hardware.py` / `agent/skill_hub.py` | 环境/引擎/依赖探测 | 否 |
| **`ltx_runtime.py`** | **无任何匹配** | ✅ 本切片 |
| **`mnn_runtime.py`** | **无任何匹配** | ✅ 本切片 |

`ltx_runtime.py` 顶部仅 `import contextlib / threading / time / uuid / dataclasses / enum / pathlib / typing`；`mnn_runtime.py` 顶部 `import contextlib / logging / os / threading / time / ...`，其中 `os` 仅用于 `os.path.exists`（多模态图片/音频路径校验，行 230/249），**不调用任何 `os.exec/spawn/system`**。

### 2.2 唯一的间接子进程：imageio-ffmpeg（LTX 写 MP4）

`ltx_runtime._write_video()`（行 497–541）在 `fmt=="mp4"` 时调用 `imageio.get_writer(..., codec="libx264")`。imageio-ffmpeg 官方 README 明确：

> "This library calls ffmpeg in a subprocess, and video frames are communicated over pipes." —— 即写 MP4 时 **确实会 spawn 一个 ffmpeg 子进程**，帧经 stdin 管道喂入。

但关键在于**生命周期时序**（`LtxManager._run`，行 342–375）：

```
LOADING
  pipe = _load_pipeline(...)          # 纯进程内 torch/diffusers
  if cancel: return CANCELLED         # 取消点 A
RUNNING
  frames = _generate(...)             # 进程内；step 回调里 raise _Cancelled
  if cancel: return CANCELLED         # 取消点 B
SAVING
  out = self._write_output(...)      # ← ffmpeg 子进程仅在此时才被 imageio 拉起
  ...
DONE
```

逐取消路径推演：

- **取消点 A（LOADING 中取消）**：在 `_write_output` 之前 `return`，ffmpeg 尚未拉起，无子进程可泄漏。
- **取消点 B（RUNNING 中取消）**：`_generate` 的 step 回调抛 `_Cancelled`，异常穿出 `_generate`，**根本到不了 SAVING**，ffmpeg 未拉起。
- **SAVING 中点击取消**：`LtxManager.cancel()`（行 311）只 `task._cancel.set()`；而 `_write_video` **内部没有任何 cancel 检查**，它会喂完所有帧并 `writer.close()`（向 ffmpeg stdin 发 EOF），ffmpeg 自然编码退出 0。取消事件被置位但写盘照常跑完 —— ffmpeg **被排空到自然结束，不会成孤儿**。

此外 LTX **没有任何整体生成超时**，取消纯靠协作式 Event，因此不存在"超时路径"需要 killpg。

### 2.3 MNN：进程内 C++ 单例 + 后台线程（非子进程）

`mnn_runtime.load_model()`（行 79）经 `from MNN.llm import create` 构造的是**进程内 C++ 扩展对象**（模块 docstring 行 1–17 明确）。流式路径（`chat_stream`，行 312）的"后台 generate"用的是：

```python
t = threading.Thread(target=_run, daemon=True)   # 行 363
t.start()
```

这是**同进程内的 daemon 线程**，不是 OS 子进程。超时（`_STREAM_GENERATION_TIMEOUT_S=600s`，行 35）时抛 `TimeoutError` 并释放全局 `_LOCK`（上一切片已修，回归测试 `test_stream_timeout_releases_lock` 覆盖）。若 C++ 引擎硬死锁，残留的只是一个**同进程内 parked 的线程**，对它 `killpg` 没有意义——进程组 kill 会把整个 app 一起杀掉。

## 3. 为什么进程内模型不需要 killpg

`killpg` 解决的是"父进程被终止后，已 spawn 的孙进程被 reparent 到 init 而成孤儿继续占用资源"。它的前提是**存在一个本进程 fork/exec 出来的子进程树**。

- LTX 的重活（torch/diffusers 去噪、VAE 编解码）全部在本进程地址空间内、在 daemon 线程里跑；取消是协作式抛异常，线程随异常退出而结束，GPU 显存随对象释放回收——**没有 OS 子进程树需要整组回收**。
- MNN 同理，C++ 引擎就在本进程内；超时只影响一个 Python 线程，不涉及进程边界。
- 二者唯一可能拉起的 ffmpeg（§2.2）在取消路径上**根本不会被拉起**；一旦被拉起（成功写盘路径），它会被 `writer.close()` 排空到自然结束。

因此当前架构下加 `start_new_session`/`killpg` 没有作用对象——属于"为了改而改"，按硬约束不予实施。

## 4. 残余风险（如实记录，均在范围外）

1. **整进程被 SIGKILL 时的 ffmpeg**：仅当 app 进程在 SAVING 中途被外部按 PID 强杀（非任务取消、非优雅退出），ffmpeg 才会被 reparent 残留写一个残缺 mp4。注意 imageio-ffmpeg 未 `setsid`，ffmpeg 与 app **同处一个进程组**，因此"按进程组杀 app"会连 ffmpeg 一起带走；只有"按单个 PID 精确强杀 app"才会漏。这是**全 app 共性问题**（converter.py 等子进程同样如此），不是 LTX/MNN 任务取消语义内的问题，超出本切片范围。
2. **MNN C++ 引擎硬死锁**：超时后轮询线程抛错、锁释放，但 C++ 侧若不响应取消，那个 daemon 线程会一直 parked（模块注释行 369–370 已声明）。这是同进程线程泄漏，`killpg` 无能为力；唯一彻底解法是把 MNN 推理搬到独立子进程（见 §5 未来设计）。
3. **LTX 无整体超时**：若 diffusers 某次 step 回调之间卡住（CUDA hang），任务会一直 RUNNING。这与子进程回收无关，属于另一可立项项。

## 5. 未来若引入外部推理引擎 / 更多子进程：参考实现（本轮不改）

一旦哪个 runtime 真的要 `subprocess.Popen` 拉起外部 CLI，**必须**用下面的"进程组感知启动 + 整组回收"封装，并在取消/超时/异常三条路径都调用。代码片段（零新增依赖，仅标准库）：

```python
import os, signal, subprocess, sys

class ProcessGroupChild:
    """在独立会话/进程组里跑一个外部引擎，取消时整组回收。"""
    def __init__(self, argv):
        popen_kw = dict(stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        if sys.platform == "win32":
            popen_kw["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            popen_kw["start_new_session"] = True   # == setsid(), 独立进程组
        self.proc = subprocess.Popen(argv, **popen_kw)

    def kill_group(self, hard=False):
        proc, pid = self.proc, self.proc.pid
        if proc.poll() is not None:
            return
        if sys.platform == "win32":
            # /T = 连子进程树一起, /F = 强杀
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(pid)],
                           check=False, capture_output=True)
        else:
            sig = signal.SIGKILL if hard else signal.SIGTERM
            try:
                os.killpg(os.getpgid(pid), sig)     # 整组
            except ProcessLookupError:
                return
            if not hard:
                try:
                    proc.wait(timeout=3)            # 给 TERM 一点退出时间
                except subprocess.TimeoutExpired:
                    os.killpg(os.getpgid(pid), signal.SIGKILL)  # 升级 KILL
```

要点：
- 启动时就隔离进程组（POSIX `start_new_session=True`；Win `CREATE_NEW_PROCESS_GROUP`），**不要事后补**——事后子进程可能已经 fork 了自己的孙进程。
- 取消/超时先 `SIGTERM`，宽限 ~3s 不退再 `SIGKILL`；Windows 直接 `taskkill /T /F`。
- 绝不能只 `proc.terminate()`：对"拉起了自己孙进程的引擎"，那只杀组长，孙进程变孤儿。

## 6. 测试策略（护栏已落地，未来改码时补）

本轮新增 `python/tests/test_neuron2_runtime_audit.py`（3 例）：

- **G1**（参数化 ×2）：静态扫描 `ltx_runtime.py` / `mnn_runtime.py` 源码，断言不含 `import subprocess` / `subprocess.` / `Popen(` / `os.exec` / `os.spawn` / `os.system(` / `create_subprocess`。任何人未来往这两个文件加子进程，CI 立即红，倒逼其按 §5 加进程组回收。
- **G2**：mock `_generate` 在 RUNNING 中置取消并抛 `_Cancelled`，断言任务落到 `CANCELLED` 且 `_write_output`（即 ffmpeg 子进程）**一次都没被调用**。

未来真改码时的补充用例（当前不适用，记录备查）：
- mock `Popen`，断言取消时调用了 `os.killpg`/`taskkill` 而非仅 `terminate()`。
- 用临时 `python3 -c "import time; time.sleep(60)"` 做真实轻量子进程，验证取消后 pid 已不存在（Linux 分支）。
- 正常完成路径不触发任何 kill；超时路径触发整组回收。

## 7. 风险评估：为什么本轮不改

| 维度 | 判断 |
|---|---|
| 是否存在需回收的子进程 | 否（§2.1 直接调用为零；§2.2 间接 ffmpeg 不在取消路径上） |
| 强行加 killpg 的作用对象 | 无——会引入一个从不被 kill 的空抽象，徒增复杂度 |
| 改动对既有取消流程的影响 | 上一切片刚修好 LTX 取消误判 FAILED、MNN 轮询超时；本切片再动取消路径，回归风险高于收益 |
| 测试可覆盖性 | 无头环境无 GPU、无真实外部引擎，真实子进程整组回收无法端到端验证，只能 mock——mock 出来的"验证"价值有限 |
| 跨平台一致性 | 无法在本机验证 Windows 分支，贸然交付跨平台逻辑违反"风险不可控则降级"硬约束 |

**结论**：当前代码确实没有子进程需要进程组回收，属"已核实无需修复"。本轮不改任何生产代码，仅留护栏测试 + 本提案，把未来引入外部引擎时的正确做法固化下来。
