# -*- coding: utf-8 -*-
"""
downloader.py — 统一模型下载器（真实下载 / 断点续传 / 进度回调）
================================================================
直接走各源已验证的 resolve URL，用 requests 流式下载：
- Range 断点续传（.part 文件保留，重启/中断后从已下载字节继续）
- 目录型文件（如 processor/、transformer/ 分片目录）先列举远端文件再逐个下载
- 实时进度 / 速度 / ETA 回调；支持取消
- 失败自动重试（指数退避）

文件列举：
- HF 系:  GET {base}/api/models/{repo}/tree/{branch}/{path}   （实测可用）
- 魔搭:   GET https://modelscope.cn/api/v1/models/{repo}/repo/files?Revision={branch}&Root={path}
"""

import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List, Optional

import requests

from . import __version__
from .facts import DOWNLOAD_SOURCES, get_bundle
from .sources import resolve_url

CHUNK = 512 * 1024
UA = {"User-Agent": f"H3Studio/{__version__}"}


# ─────────────────────────────────────────────────────────────
# 远端目录列举
# ─────────────────────────────────────────────────────────────
def list_remote_dir(source_key: str, repo: str, dir_path: str) -> List[dict]:
    """返回 [{path, size}]，path 为相对 dir_path 的文件路径。"""
    dir_path = dir_path.rstrip("/")
    src = next(s for s in DOWNLOAD_SOURCES if s["key"] == source_key)

    if source_key == "modelscope":
        url = (f"https://modelscope.cn/api/v1/models/{repo}/repo/files"
               f"?Revision={src['branch']}&Root={requests.utils.quote(dir_path, safe='/')}")
        r = requests.get(url, timeout=30, headers=UA)
        r.raise_for_status()
        data = r.json()
        files = (data.get("Data") or {}).get("Files") or []
        out = []
        for f in files:
            if f.get("Type") != "blob":
                continue
            name = f.get("Name", "")
            # 魔搭 API 的 Name 可能带子目录前缀（如 processor/tokenizer.json）
            out.append({"path": name, "size": int(f.get("Size", 0))})
        if not out:
            raise RuntimeError(f"魔搭目录列举为空: {repo}/{dir_path}")
        return out

    # HF / hf-mirror：tree API
    url = f"{src['base_url']}/api/models/{repo}/tree/{src['branch']}/{requests.utils.quote(dir_path, safe='/')}"
    r = requests.get(url, timeout=30, headers=UA)
    r.raise_for_status()
    entries = r.json()
    out = []
    for e in entries:
        if e.get("type") == "file":
            rel = e["path"]
            if rel.startswith(dir_path + "/"):
                rel = rel[len(dir_path) + 1:]
            out.append({"path": rel, "size": int(e.get("size", 0))})
    if not out:
        raise RuntimeError(f"HF 目录列举为空: {repo}/{dir_path}")
    return out


# ─────────────────────────────────────────────────────────────
# 单文件下载（断点续传）
# ─────────────────────────────────────────────────────────────
def download_file(url: str, dest: Path,
                  progress_cb: Optional[Callable[[int, int], None]] = None,
                  stop_flag: Optional[threading.Event] = None,
                  retries: int = 5,
                  session: Optional[requests.Session] = None) -> bool:
    """
    下载单个文件，支持断点续传与取消。
    progress_cb(downloaded_bytes_total, total_bytes) — total 可能为 -1。
    返回 True=完成 / False=被取消；异常向上抛。
    """
    sess = session or requests
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_suffix(dest.suffix + ".part")

    attempt = 0
    while True:
        if stop_flag is not None and stop_flag.is_set():
            return False
        existing = part.stat().st_size if part.exists() else 0
        headers = dict(UA)
        if existing > 0:
            headers["Range"] = f"bytes={existing}-"
        try:
            with sess.get(url, headers=headers, stream=True,
                          timeout=(10, 60)) as r:
                if r.status_code == 416:  # 已完整
                    part.rename(dest)
                    if progress_cb:
                        sz = dest.stat().st_size
                        progress_cb(sz, sz)
                    return True
                if r.status_code not in (200, 206):
                    raise RuntimeError(f"HTTP {r.status_code}: {url}")
                if r.status_code == 200 and existing > 0:
                    existing = 0  # 服务器不支持 Range，重头下载
                total = -1
                cr = r.headers.get("Content-Range")
                if cr and "/" in cr:
                    try:
                        total = int(cr.split("/")[-1])
                    except ValueError:
                        pass
                elif r.headers.get("Content-Length"):
                    total = existing + int(r.headers["Content-Length"])

                mode = "ab" if (r.status_code == 206 and existing > 0) else "wb"
                got = existing if mode == "ab" else 0
                with open(part, mode) as f:
                    for chunk in r.iter_content(chunk_size=CHUNK):
                        if stop_flag is not None and stop_flag.is_set():
                            return False
                        if chunk:
                            f.write(chunk)
                            got += len(chunk)
                            if progress_cb:
                                progress_cb(got, total)
            # 完成
            if total > 0 and part.stat().st_size < total:
                raise RuntimeError("连接中断（文件不完整）")
            if dest.exists():
                dest.unlink()
            part.rename(dest)
            return True
        except (requests.exceptions.RequestException, RuntimeError, OSError) as e:
            attempt += 1
            if attempt >= retries:
                raise RuntimeError(f"下载失败（重试 {attempt} 次）: {url} → {e}")
            time.sleep(min(2 ** attempt, 15))


# ─────────────────────────────────────────────────────────────
# Bundle 下载任务
# ─────────────────────────────────────────────────────────────
@dataclass
class TaskProgress:
    status: str = "pending"          # pending/downloading/done/error/cancelled
    percent: float = 0.0
    done_bytes: int = 0
    total_bytes: int = 0
    speed_mbs: float = 0.0
    eta_s: float = -1.0
    current_file: str = ""
    error: str = ""


@dataclass
class BundleDownloadTask:
    bundle_id: str
    source_key: str
    dest_dir: Path
    bundle: dict = field(default=None, repr=False)
    retries: int = 5
    progress: TaskProgress = field(default_factory=TaskProgress)
    stop_flag: threading.Event = field(default_factory=threading.Event)
    _thread: Optional[threading.Thread] = field(default=None, repr=False)

    def __post_init__(self):
        if self.bundle is None:
            self.bundle = get_bundle(self.bundle_id)

    # ── 组装下载清单：[(url, dest_path, size)] ──
    def build_file_list(self) -> List[tuple]:
        items = []
        repo_map = self.bundle.get("source_repos", {})
        proc_repo_map = self.bundle.get("processor_repos", {})

        for f in self.bundle["files"]:
            # 该文件属于哪个仓库：默认主仓库；processor 目录用 processor_repos
            repo = repo_map.get(self.source_key) or f["repo"]
            if f.get("is_dir") and f["dest"].startswith("processor_") and proc_repo_map:
                repo = proc_repo_map.get(self.source_key, repo)
            # HF 系源使用文件自带的 HF 仓库名（如官方仓库在 HF 上名为 MiniMaxAI/...）
            if self.source_key in ("hf", "hf_mirror") and f.get("repo_hf"):
                repo = f["repo_hf"]

            if f.get("is_dir"):
                # 目录 → 列举后逐文件
                entries = list_remote_dir(self.source_key, repo, f["path"].rstrip("/"))
                for e in entries:
                    url = resolve_url(self.source_key, repo,
                                      f["path"].rstrip("/") + "/" + e["path"])
                    items.append((url, self.dest_dir / f["dest"] / e["path"], e["size"]))
            else:
                url = resolve_url(self.source_key, repo, f["path"])
                items.append((url, self.dest_dir / f["dest"], int(f["size_gb"] * 1e9)))
        return items

    def run(self):
        self._thread = threading.Thread(target=self._worker, daemon=True)
        self._thread.start()

    def _worker(self):
        p = self.progress
        p.status = "downloading"
        sess = requests.Session()
        try:
            items = self.build_file_list()
            p.total_bytes = sum(sz for _, _, sz in items) or 1
            done_total = 0
            t_last = time.time()
            b_last = 0  # 于跳过统计后初始化为 done_total，避免首个速度样本虚高

            # 跳过已完成的文件（断点续传：整文件级）
            for url, dest, size in items:
                if dest.exists() and size > 0 and dest.stat().st_size == size:
                    done_total += size
            p.done_bytes = done_total
            b_last = done_total

            for url, dest, size in items:
                if self.stop_flag.is_set():
                    p.status = "cancelled"
                    return
                if dest.exists() and size > 0 and dest.stat().st_size == size:
                    continue
                p.current_file = dest.name

                def cb(got, total, _base=done_total):
                    now = _base + got
                    p.done_bytes = now
                    p.percent = round(now / p.total_bytes * 100, 2) if p.total_bytes else 0
                    # 速度（滑动 1s）
                    nonlocal t_last, b_last
                    t = time.time()
                    if t - t_last >= 1.0:
                        p.speed_mbs = round((now - b_last) / (t - t_last) / (1024 * 1024), 1)
                        t_last, b_last = t, now
                    if p.speed_mbs > 0.1:
                        p.eta_s = int((p.total_bytes - now) / (p.speed_mbs * 1024 * 1024))

                ok = download_file(url, dest, progress_cb=cb,
                                   stop_flag=self.stop_flag, session=sess,
                                   retries=self.retries)
                if not ok:
                    p.status = "cancelled"
                    return
                done_total += (dest.stat().st_size if size <= 0 else size)

            p.percent = 100.0
            p.status = "done"
        except Exception as e:
            p.status = "error"
            p.error = str(e)[:300]

    def cancel(self):
        self.stop_flag.set()

    def is_alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()
