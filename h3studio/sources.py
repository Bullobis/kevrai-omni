# -*- coding: utf-8 -*-
"""
sources.py — 下载源测速与智能选择
==================================
测速方式：对每个源的探针文件做真实 HTTP Range 下载（默认采样 4MB），
同时测量：连接延迟(TTFB)、真实吞吐量(MB/s)。
综合评分 = 吞吐量为主（权重 0.75）+ 延迟为辅（权重 0.25），
并惩罚不稳定（采样失败）的源。绝不使用伪造的速度数字。
"""

import threading
import time
from dataclasses import dataclass

import requests

from . import __version__
from .facts import DOWNLOAD_SOURCES, PROBE_FILES

SAMPLE_BYTES = 4 * 1024 * 1024   # 采样 4MB，足以区分快慢源
CONNECT_TIMEOUT = 6
READ_TIMEOUT = 20


@dataclass
class ProbeResult:
    key: str
    name: str
    ok: bool = False
    latency_ms: float = -1.0      # TTFB
    speed_mbs: float = 0.0        # 真实下载速度
    sampled_mb: float = 0.0
    score: float = 0.0
    error: str = ""


def probe_source(src: dict, sample_bytes: int = SAMPLE_BYTES) -> ProbeResult:
    pf = PROBE_FILES.get(src["key"])
    res = ProbeResult(key=src["key"], name=src["name"])
    if not pf:
        res.error = "该源无探针文件"
        return res
    url = src["resolve_tpl"].format(repo=pf["repo"], path=pf["path"])
    headers = {"Range": f"bytes=0-{sample_bytes - 1}", "User-Agent": f"H3Studio/{__version__}"}
    t_start = time.perf_counter()
    ttfb = None
    got = 0
    try:
        with requests.get(url, headers=headers, stream=True,
                          timeout=(CONNECT_TIMEOUT, READ_TIMEOUT)) as r:
            if r.status_code not in (200, 206):
                res.error = f"HTTP {r.status_code}"
                return res
            for chunk in r.iter_content(chunk_size=256 * 1024):
                if ttfb is None:
                    ttfb = time.perf_counter() - t_start
                if chunk:
                    got += len(chunk)
                if got >= sample_bytes:
                    break
        elapsed = time.perf_counter() - (t_start + (ttfb or 0))
        res.ok = got > 512 * 1024   # 至少下到 512KB 才算有效采样
        res.latency_ms = round((ttfb or (time.perf_counter() - t_start)) * 1000, 1)
        res.sampled_mb = round(got / (1024 * 1024), 2)
        if elapsed > 0.01:
            res.speed_mbs = round(got / (1024 * 1024) / elapsed, 2)
    except requests.exceptions.Timeout:
        res.error = "超时"
    except requests.exceptions.ConnectionError:
        res.error = "连接失败"
    except Exception as e:
        res.error = str(e)[:80]
    return res


def score_results(results: list) -> list:
    """综合评分：速度 75% + 延迟 25%（延迟按 2000ms 归一化封顶）。"""
    ok = [r for r in results if r.ok]
    if not ok:
        return results
    max_speed = max(r.speed_mbs for r in ok) or 1.0
    for r in results:
        if not r.ok:
            r.score = 0.0
            continue
        speed_part = r.speed_mbs / max_speed
        lat_part = max(0.0, 1.0 - r.latency_ms / 2000.0)
        r.score = round((0.75 * speed_part + 0.25 * lat_part) * 100, 1)
    results.sort(key=lambda r: (-r.score, -r.speed_mbs))
    return results


def run_speed_test(on_result=None, stop_flag: threading.Event = None,
                   sample_bytes: int = None) -> list:
    """
    串行探测所有源（避免并发抢占带宽影响真实性）。
    on_result(ProbeResult) 每完成一个源回调一次。
    """
    results = []
    sb = sample_bytes or SAMPLE_BYTES
    for src in DOWNLOAD_SOURCES:
        if stop_flag is not None and stop_flag.is_set():
            break
        r = probe_source(src, sample_bytes=sb)
        results.append(r)
        if on_result:
            on_result(r)
    return score_results(results)


def pick_best_source(results: list, preferred: str = "auto") -> str:
    """返回选中的源 key。preferred != auto 且该源可用时优先尊重用户选择。"""
    if preferred and preferred != "auto":
        for r in results:
            if r.key == preferred and r.ok:
                return r.key
    for r in results:
        if r.ok and r.score > 0:
            return r.key
    return results[0].key if results else "modelscope"


def resolve_url(source_key: str, repo: str, path: str) -> str:
    """按源构造真实下载 URL（模板均实测 200）。"""
    for src in DOWNLOAD_SOURCES:
        if src["key"] == source_key:
            return src["resolve_tpl"].format(repo=repo, path=path)
    raise KeyError(f"未知下载源: {source_key}")
