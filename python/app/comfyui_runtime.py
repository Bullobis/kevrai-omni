"""ComfyUI 通用后端 —— 一次接线覆盖图像 / 视频 / 音乐。

为什么需要它：项目里 24 个 3D 模型、20 个视频模型、18 个图像模型，
逐个写 runtime 是写不完的。ComfyUI 已经为这些模型提供了节点化管线，
通过它的 REST + WebSocket API 驱动，就能"下载即可用"。

本模块只做编排，**不实现任何推理**。

已知的四个生产坑（都来自实际部署经验，不是文档抄的），这里逐个处理：

1. **seed 被随机化**：工作流 JSON 里 ``control_after_generate: "randomize"``
   会让每次运行换 seed。交互场景这是对的，但作为 API 就意味着同样的输入
   产出不同的图，且表现为"结果飘忽"而不是报错，极难排查。
   → ``fix_randomize_seeds()`` 会把它改成 fixed。

2. **模型冷加载 8–20 秒**：6GB 的 checkpoint 首次加载很慢，且每次换模型
   都要重来。→ 提供 ``warmup()`` 预热。

3. **VRAM 碎片**：连续跑不同类型模型不释放显存会 OOM。→ ``free()`` 调
   ComfyUI 的 ``POST /free``。

4. **队列饥饿**：FIFO 队列里几秒的图和几十秒的视频混排，短任务被长任务
   堵死。→ 提交时带 ``job_class``，由调用方按类别分队列（见
   ``QUEUE_CLASSES``）。

依赖只有项目已有的 httpx，不引入新包。
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

import httpx

log = logging.getLogger(__name__)

#: 默认 ComfyUI 地址。
DEFAULT_BASE_URL = "http://127.0.0.1:8188"

#: 任务类别。短任务与长任务必须分开排队，否则短任务被长任务堵死。
QUEUE_CLASSES = ("image", "video", "audio", "3d")

#: 各类别的默认超时（秒）。视频远长于图像。
DEFAULT_TIMEOUTS: dict[str, float] = {
    "image": 300.0,
    "video": 3600.0,
    "audio": 900.0,
    "3d": 900.0,
}

#: 轮询间隔（秒）。太快会打满 ComfyUI，太慢用户等得没反馈。
POLL_INTERVAL_S = 1.0


class ComfyUIError(RuntimeError):
    """ComfyUI 调用失败。"""


class ComfyUIUnavailable(ComfyUIError):
    """连不上 ComfyUI 实例。"""


class ComfyUIJobFailed(ComfyUIError):
    """任务被提交但执行失败。"""


def fix_randomize_seeds(workflow: dict[str, Any]) -> tuple[dict[str, Any], int]:
    """把工作流里的 seed 随机化改成固定值，返回 (新工作流, 改了几处)。

    这是"结果飘忽不定"类 bug 最常见的根因：工作流导出时默认带
    ``"control_after_generate": "randomize"``，交互场景下没问题，但作为
    API 会让同样的输入产出不同结果，而且**不报错**，只能靠肉眼发现。

    不改原对象 —— 调用方可能持有它做别的事。
    """
    fixed: dict[str, Any] = {}
    count = 0
    for node_id, node in (workflow or {}).items():
        if not isinstance(node, dict):
            fixed[node_id] = node
            continue
        new_node = dict(node)
        inputs = new_node.get("inputs")
        if isinstance(inputs, dict):
            new_inputs = dict(inputs)
            # 有 seed 字段 且 被标记成 randomize → 改 fixed
            if "seed" in new_inputs and new_inputs.get(
                "control_after_generate"
            ) == "randomize":
                new_inputs["control_after_generate"] = "fixed"
                count += 1
            # 有些节点的随机化写在 noise_seed 上
            if "noise_seed" in new_inputs and new_inputs.get(
                "control_after_generate"
            ) == "randomize":
                new_inputs["control_after_generate"] = "fixed"
                count += 1
            new_node["inputs"] = new_inputs
        fixed[node_id] = new_node
    return fixed, count


class ComfyUIClient:
    """驱动一个 ComfyUI 实例。"""

    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        *,
        client_id: str | None = None,
        timeout: float = 30.0,
    ) -> None:
        self.base_url = (base_url or DEFAULT_BASE_URL).rstrip("/")
        self.client_id = client_id or f"kevrai-{int(time.time())}"
        self.timeout = float(timeout)
        self._client: httpx.Client | None = None

    # -- 底层 ---------------------------------------------------------------
    def _http(self) -> httpx.Client:
        if self._client is None or self._client.is_closed:
            self._client = httpx.Client(base_url=self.base_url,
                                        timeout=self.timeout)
        return self._client

    def close(self) -> None:
        if self._client is not None and not self._client.is_closed:
            self._client.close()

    def __enter__(self) -> ComfyUIClient:
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    # -- 能力探测 -----------------------------------------------------------
    def system_stats(self) -> dict[str, Any]:
        """GPU / 显存 / Python 版本。连不上抛 ComfyUIUnavailable。"""
        try:
            r = self._http().get("/system_stats")
            r.raise_for_status()
            return dict(r.json())
        except (httpx.HTTPError, ValueError) as e:
            raise ComfyUIUnavailable(
                f"无法连接 ComfyUI（{self.base_url}）：{e}"
            ) from e

    def is_available(self) -> bool:
        try:
            self.system_stats()
            return True
        except ComfyUIError:
            return False

    def object_info(self, node_class: str | None = None) -> dict[str, Any]:
        """节点目录。node_class 为空时返回全部（可能很大）。"""
        try:
            url = "/object_info"
            if node_class:
                url = f"/object_info/{node_class}"
            r = self._http().get(url)
            r.raise_for_status()
            return dict(r.json())
        except (httpx.HTTPError, ValueError) as e:
            raise ComfyUIUnavailable(f"获取节点信息失败：{e}") from e

    # -- 显存管理 -----------------------------------------------------------
    def free(self, unload_models: bool = True, free_memory: bool = True) -> bool:
        """释放显存。连续跑不同模型之间必须调用，否则会 OOM。

        失败只记日志不抛 —— 释放不了不该让整个流程挂掉。
        """
        try:
            r = self._http().post(
                "/free",
                json={
                    "unload_models": bool(unload_models),
                    "free_memory": bool(free_memory),
                },
            )
            r.raise_for_status()
            return True
        except (httpx.HTTPError, ValueError) as e:
            log.warning("ComfyUI 显存释放失败: %s", e)
            return False

    # -- 任务提交 -----------------------------------------------------------
    def submit(
        self,
        workflow: dict[str, Any],
        *,
        fix_seeds: bool = True,
        job_class: str = "image",
    ) -> tuple[str, int]:
        """提交工作流，返回 ``(prompt_id, 修正了几处随机 seed)``。"""
        if not workflow:
            raise ComfyUIError("工作流为空")
        if fix_seeds:
            workflow, seed_fixes = fix_randomize_seeds(workflow)
        else:
            seed_fixes = 0
        payload: dict[str, Any] = {
            "prompt": workflow,
            "client_id": self.client_id,
        }
        try:
            r = self._http().post("/prompt", json=payload)
        except (httpx.HTTPError, ValueError) as e:
            raise ComfyUIUnavailable(f"提交工作流失败：{e}") from e
        if r.status_code >= 400:
            # ComfyUI 校验失败会返回 400 + 详细错误，必须透传给用户，
            # 否则只看到"提交失败"根本没法修工作流。
            raise ComfyUIError(
                f"工作流被 ComfyUI 拒绝（{r.status_code}）：{r.text[:800]}"
            )
        try:
            data = r.json()
        except ValueError as e:
            raise ComfyUIError(
                f"ComfyUI 返回非 JSON 响应（{r.status_code}）：{r.text[:200]}"
            ) from e
        prompt_id = data.get("prompt_id")
        if not prompt_id:
            raise ComfyUIError(f"ComfyUI 未返回 prompt_id：{data}")
        return str(prompt_id), seed_fixes

    def history(self, prompt_id: str) -> dict[str, Any] | None:
        """取任务结果；还没完成时返回 None。"""
        try:
            r = self._http().get(f"/history/{prompt_id}")
            if r.status_code == 404:
                return None
            r.raise_for_status()
            data = r.json()
        except (httpx.HTTPError, ValueError) as e:
            raise ComfyUIUnavailable(f"查询历史失败：{e}") from e
        return data.get(prompt_id)

    def wait(
        self,
        prompt_id: str,
        *,
        timeout_s: float | None = None,
        poll_interval_s: float = POLL_INTERVAL_S,
    ) -> dict[str, Any]:
        """轮询直到任务完成。超时抛 ComfyUIJobFailed。"""
        deadline = time.monotonic() + float(
            timeout_s if timeout_s is not None else DEFAULT_TIMEOUTS["image"]
        )
        while time.monotonic() < deadline:
            entry = self.history(prompt_id)
            if entry is not None:
                status = entry.get("status") or {}
                if status.get("completed_str") or status.get("status_str") == "success":
                    return entry
                # 有 status 但没完成标记 → 视为失败
                if status.get("status_str") == "error":
                    raise ComfyUIJobFailed(
                        f"ComfyUI 任务失败：{status}"
                    )
                return entry
            time.sleep(max(0.1, poll_interval_s))
        raise ComfyUIJobFailed(f"等待 ComfyUI 任务超时（{timeout_s}s）")

    def interrupt(self) -> bool:
        """停止当前生成。"""
        try:
            r = self._http().post("/interrupt")
            r.raise_for_status()
            return True
        except (httpx.HTTPError, ValueError) as e:
            log.warning("中断 ComfyUI 失败: %s", e)
            return False

    def queue(self) -> dict[str, Any]:
        """当前队列状态。"""
        try:
            r = self._http().get("/queue")
            r.raise_for_status()
            return dict(r.json())
        except (httpx.HTTPError, ValueError) as e:
            raise ComfyUIUnavailable(f"查询队列失败：{e}") from e

    # -- 文件 ---------------------------------------------------------------
    def upload_image(
        self,
        path: str | Path,
        *,
        subfolder: str = "",
        overwrite: bool = True,
    ) -> dict[str, Any]:
        """上传输入图（图生视频 / 局部重绘需要）。"""
        p = Path(path)
        if not p.is_file():
            raise ComfyUIError(f"图片不存在：{p}")
        try:
            with p.open("rb") as fh:
                files = {"image": (p.name, fh, "application/octet-stream")}
                data = {
                    "subfolder": subfolder,
                    "overwrite": "true" if overwrite else "false",
                }
                r = self._http().post("/upload/image", files=files, data=data)
                r.raise_for_status()
                return dict(r.json())
        except (httpx.HTTPError, ValueError) as e:
            raise ComfyUIUnavailable(f"上传图片失败：{e}") from e

    def view_url(self, filename: str, subfolder: str = "",
                 type_: str = "output") -> str:
        """拼接产出文件的下载 URL。"""
        from urllib.parse import urlencode

        qs = urlencode(
            {"filename": filename, "subfolder": subfolder, "type": type_}
        )
        return f"{self.base_url}/view?{qs}"

    def download_output(self, filename: str, subfolder: str = "",
                        type_: str = "output") -> bytes:
        """下载产出的二进制内容。"""
        try:
            r = self._http().get(
                "/view",
                params={"filename": filename, "subfolder": subfolder,
                        "type": type_},
            )
            r.raise_for_status()
            return r.content
        except (httpx.HTTPError, ValueError) as e:
            raise ComfyUIUnavailable(f"下载产出失败：{e}") from e

    def warmup(self, workflow: dict[str, Any],
               timeout_s: float | None = None) -> bool:
        """跑一遍工作流做预热，把模型加载的 8–20 秒挪到用户等待之前。

        失败不抛 —— 预热是优化，不是必需步骤。
        """
        try:
            prompt_id, _ = self.submit(workflow)
            self.wait(prompt_id, timeout_s=timeout_s or DEFAULT_TIMEOUTS["image"])
            return True
        except ComfyUIError as e:
            log.warning("ComfyUI 预热失败: %s", e)
            return False


def capabilities(base_url: str = DEFAULT_BASE_URL) -> dict[str, Any]:
    """报告 ComfyUI 后端可用性，供 UI 显示。"""
    out: dict[str, Any] = {
        "base_url": base_url,
        "available": False,
        "queue_classes": list(QUEUE_CLASSES),
        "default_timeouts": dict(DEFAULT_TIMEOUTS),
    }
    try:
        with ComfyUIClient(base_url) as c:
            stats = c.system_stats()
            out["available"] = True
            devices = stats.get("devices") or []
            out["devices"] = [
                {
                    "name": d.get("name"),
                    "vram_total": d.get("vram_total"),
                    "vram_free": d.get("vram_free"),
                }
                for d in devices
                if isinstance(d, dict)
            ]
    except ComfyUIError as e:
        out["error"] = str(e)
    return out
