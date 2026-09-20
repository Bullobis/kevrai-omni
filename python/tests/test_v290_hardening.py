"""Regression tests for the 2.9.0 hardening pass.

Each test here pins a **specific defect found by audit**, so the failure mode
is documented in the test itself rather than only in a commit message.

Covered:

* ``degraded_message`` — rate limiting must not masquerade as an upstream fault
* ``HubRegistry.search`` in-flight coalescing (single-flight)
* ``Downloader.aclose`` releasing the self-built client pool
* downloader final fsync happening inside the ``with`` block (fd hygiene)
* path containment via ``is_relative_to`` (sibling-directory bypass)

**100% offline** — no real hostname is ever contacted.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from hub_fake_server import FakeHubServer  # noqa: E402

from app.hub import HubRegistry, SearchSpec  # noqa: E402
from app.hub.base import SourceCursor, degraded_message  # noqa: E402
from app.hub.hf import HuggingFaceAdapter  # noqa: E402
from app.hub.net import TokenBucket  # noqa: E402

# ---------------------------------------------------------------------------
# degraded_message — 限流 ≠ 上游故障
# ---------------------------------------------------------------------------


class TestDegradedMessage:
    @pytest.mark.parametrize(
        "code,fragment",
        [
            ("local_limited", "限流"),
            ("circuit_open", "熔断"),
            ("timeout", "超时"),
            ("network", "网络"),
        ],
    )
    def test_cause_is_named(self, code: str, fragment: str) -> None:
        msg = degraded_message("HuggingFace", code)
        assert fragment in msg, f"{code} 未在提示中体现原因: {msg}"
        assert code in msg, "原始 code 应保留以便排查"

    def test_local_limited_is_distinguishable_from_upstream(self) -> None:
        """本机限流必须与上游故障有不同文案。

        否则用户看到空市场只会以为是网络问题，反复重试反而加剧限流。
        """
        limited = degraded_message("HuggingFace", "local_limited")
        upstream = degraded_message("HuggingFace", "network")
        assert limited != upstream

    def test_unknown_code_does_not_crash(self) -> None:
        msg = degraded_message("魔搭 ModelScope", "something_new")
        assert "未知原因" in msg
        assert "魔搭 ModelScope" in msg

    @pytest.mark.parametrize("code", ["", None])
    def test_empty_code_is_tolerated(self, code) -> None:
        msg = degraded_message("HuggingFace", code)  # type: ignore[arg-type]
        assert "HuggingFace" in msg


# ---------------------------------------------------------------------------
# single-flight —— 并发相同查询应只打一次上游
# ---------------------------------------------------------------------------


class _CountingAdapter:
    """Counts how many times ``search`` actually fanned out."""

    def __init__(self) -> None:
        self.calls = 0
        self.gate = asyncio.Event()

    async def search(self, spec, cursor):  # noqa: ANN001, ANN201
        self.calls += 1
        await self.gate.wait()
        return _empty_page()

    def health(self) -> dict:
        return {"hub": "counted", "enabled": True, "online": True}


def _empty_page():
    from app.hub.base import PageResult

    return PageResult(items=[], next=None, total=0)


class TestSingleFlight:
    @pytest.mark.asyncio
    async def test_concurrent_identical_queries_are_coalesced(self) -> None:
        """N 个并发相同查询 → 1 次上游扇出。

        修复前实测 30 并发会打出 30 次上游请求，延迟 1.6s → 11.4s，
        且各请求拿到的分片不同导致返回不一致。
        """
        ad = _CountingAdapter()
        reg = HubRegistry(curated=ad)
        spec = SearchSpec(q="qwen", sources=["curated"], page_size=5)

        tasks = [asyncio.ensure_future(reg.search(spec)) for _ in range(12)]
        await asyncio.sleep(0)  # let them all pile onto the same in-flight task
        await asyncio.sleep(0)
        ad.gate.set()
        results = await asyncio.gather(*tasks)

        assert ad.calls == 1, f"应只扇出 1 次，实际 {ad.calls} 次"
        assert len(results) == 12
        assert all(r.items == [] for r in results)

    @pytest.mark.asyncio
    async def test_different_queries_are_not_coalesced(self) -> None:
        """不同查询不能被错误合并。"""
        ad = _CountingAdapter()
        ad.gate.set()  # no waiting needed
        reg = HubRegistry(curated=ad)

        await reg.search(SearchSpec(q="qwen", sources=["curated"], page_size=5))
        await reg.search(SearchSpec(q="llama", sources=["curated"], page_size=5))

        assert ad.calls == 2

    @pytest.mark.asyncio
    async def test_slot_is_released_after_completion(self) -> None:
        """完成后必须释放槽位，否则第二次同名查询会永远挂住。"""
        ad = _CountingAdapter()
        ad.gate.set()
        reg = HubRegistry(curated=ad)
        spec = SearchSpec(q="qwen", sources=["curated"], page_size=5)

        await reg.search(spec)
        assert reg._inflight == {}, "in-flight 槽位未释放"

        await reg.search(spec)  # 必须能再次真正执行
        assert ad.calls == 2

    @pytest.mark.asyncio
    async def test_caller_cancellation_does_not_break_others(self) -> None:
        """单个调用方取消不得影响其他等待者（shield 的用途）。"""
        ad = _CountingAdapter()
        reg = HubRegistry(curated=ad)
        spec = SearchSpec(q="qwen", sources=["curated"], page_size=5)

        first = asyncio.ensure_future(reg.search(spec))
        second = asyncio.ensure_future(reg.search(spec))
        await asyncio.sleep(0)
        await asyncio.sleep(0)

        first.cancel()
        ad.gate.set()
        with pytest.raises(asyncio.CancelledError):
            await first
        # 第二个仍应拿到结果
        page = await second
        assert page.items == []
        assert ad.calls == 1


# ---------------------------------------------------------------------------
# downloader —— 连接池与 fsync / fd
# ---------------------------------------------------------------------------


class TestDownloaderClientLifecycle:
    @pytest.mark.asyncio
    async def test_aclose_releases_self_built_client(self) -> None:
        """``aclose`` 必须关掉自建的 ``_own_client``。

        修复前只关 ``self._client``，每次 put_settings 重建 Downloader
        都会泄漏一个 httpx 连接池。
        """
        from app.downloader import Downloader

        dl = Downloader()
        client = await dl._get_client()
        assert client is not None

        await dl.aclose()
        assert dl._own_client is None, "自建连接池未被释放"
        assert client.is_closed, "httpx 客户端仍处于打开状态"

    @pytest.mark.asyncio
    async def test_aclose_is_idempotent(self) -> None:
        from app.downloader import Downloader

        dl = Downloader()
        await dl._get_client()
        await dl.aclose()
        await dl.aclose()  # 不应抛异常


class TestDownloaderFsync:
    def test_final_fsync_is_inside_with_block(self) -> None:
        """最终 fsync 必须在 ``with partial.open(...)`` 块内。

        修复前它在块外：``fh`` 已关闭 → ``flush()`` 抛 ValueError 被吞 →
        最终 fsync 从未生效；且 ``partial.open("rb")`` 的 fd 无人关闭。
        """
        import ast

        from app import downloader as dl_mod

        src = Path(dl_mod.__file__).read_text(encoding="utf-8")
        tree = ast.parse(src)

        target = None
        for node in ast.walk(tree):
            # 该函数即 chunk 写入 + fsync 的所在处
            if (
                isinstance(node, ast.AsyncFunctionDef)
                and node.name == "_stream"
                and any(
                    isinstance(c, ast.Call)
                    and isinstance(c.func, ast.Attribute)
                    and c.func.attr == "fsync"
                    for c in ast.walk(node)
                )
            ):
                target = node
                break
        assert target is not None, "未找到含 fsync 的 _stream"

        # 找到 with partial.open(...) 的块，收集其内部所有 fsync 行号
        with_spans: list[tuple[int, int]] = []
        for node in ast.walk(target):
            if isinstance(node, ast.With):
                with_spans.append((node.lineno, max(
                    getattr(n, "lineno", node.lineno) for n in ast.walk(node))))

        # os.fsync(...) → Call(func=Attribute(attr='fsync'))
        fsync_lines = [
            n.lineno
            for n in ast.walk(target)
            if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Attribute)
            and n.func.attr == "fsync"
        ]
        assert fsync_lines, "未找到 fsync 调用"

        for line in fsync_lines:
            inside = any(lo <= line <= hi for lo, hi in with_spans)
            assert inside, f"fsync 位于 with 块之外（行 {line}）→ 永不生效"

    def test_no_reopen_of_partial_for_fsync(self) -> None:
        """不得再为 fsync 重新 open 文件（会泄漏 fd）。"""
        from app import downloader as dl_mod

        src = Path(dl_mod.__file__).read_text(encoding="utf-8")
        assert 'partial.open("rb").fileno()' not in src
        assert "noqa: F821" not in src, "遗留的 noqa 掩盖了已关闭文件的问题"


# ---------------------------------------------------------------------------
# 路径包含 —— 兄弟目录绕过
# ---------------------------------------------------------------------------


class TestPathContainment:
    def _contains(self, root: Path, candidate: Path) -> bool:
        """复刻 main.py 中修复后的判定（resolve + is_relative_to）。"""
        return candidate.resolve().is_relative_to(root.resolve())

    def test_sibling_directory_is_rejected(self, tmp_path: Path) -> None:
        """``models-evil`` 不能被当成 ``models`` 的子路径。

        修复前用 ``str.startswith``：``/x/models-evil`` 对 ``/x/models``
        返回 True，兄弟目录即可绕过校验。
        """
        root = tmp_path / "models"
        root.mkdir()
        evil = tmp_path / "models-evil"
        evil.mkdir()

        # 先证明旧写法确实会误判
        assert str(evil).startswith(str(root)), "前置条件：startswith 会误判"
        # 新写法必须拒绝
        assert not self._contains(root, evil)

    def test_real_child_is_accepted(self, tmp_path: Path) -> None:
        root = tmp_path / "models"
        (root / "sub").mkdir(parents=True)
        assert self._contains(root, root / "sub")

    def test_root_itself_is_accepted(self, tmp_path: Path) -> None:
        root = tmp_path / "models"
        root.mkdir()
        assert self._contains(root, root)

    def test_parent_traversal_is_rejected(self, tmp_path: Path) -> None:
        root = tmp_path / "models"
        root.mkdir()
        assert not self._contains(root, root / ".." / "secret")

    def test_symlink_escape_is_rejected(self, tmp_path: Path) -> None:
        """resolve() 后再比较，symlink 指向外部也应被拒。"""
        root = tmp_path / "models"
        root.mkdir()
        outside = tmp_path / "outside"
        outside.mkdir()
        link = root / "link"
        try:
            link.symlink_to(outside, target_is_directory=True)
        except (OSError, NotImplementedError):
            pytest.skip("本机不支持符号链接")
        assert not self._contains(root, link)


# ---------------------------------------------------------------------------
# 适配器降级路径 —— 限流时也必须带可见告警
# ---------------------------------------------------------------------------


class TestAdapterDegradationVisible:
    @pytest.mark.asyncio
    async def test_rate_limited_search_reports_warning(self) -> None:
        """令牌桶为空时，PageResult 必须带 degraded + 可读 warning。"""
        srv = FakeHubServer().start()
        try:
            # capacity=0 → take() 永远失败，模拟限流
            bucket = TokenBucket(rate=0.0, capacity=0.0)
            ad = HuggingFaceAdapter(base_url=f"{srv.base_url}/api", mirrors=(), bucket=bucket)
            res = await ad.search(SearchSpec(q="qwen"), SourceCursor())

            assert res.degraded is True
            assert res.code == "local_limited"
            assert res.warning, "限流必须给出可见提示（修复前是空 warnings）"
            assert "限流" in res.warning
        finally:
            srv.stop()


# ---------------------------------------------------------------------------
# 假服务器保真度 —— null 字段耐受
# ---------------------------------------------------------------------------


class TestUpstreamNullTolerance:
    """真实上游常给 null；假服务器必须能复现，否则测试比生产更宽容。

    修复前 ``_hf_search`` **永远返回完整字段**，从不给 null，导致适配器的
    null 降级路径从未被真实执行 —— 生产环境才会炸。
    """

    @pytest.mark.asyncio
    async def test_hf_search_tolerates_null_fields(self) -> None:
        srv = FakeHubServer().start()
        try:
            srv.force["bad"] = "5"      # 让可选字段全部返回 null
            ad = HuggingFaceAdapter(base_url=f"{srv.base_url}/api", mirrors=())
            res = await ad.search(SearchSpec(q="qwen"), SourceCursor())

            assert isinstance(res.items, list), "null 字段导致返回类型异常"
            assert res.items, "null 字段导致条目全部被丢弃（不应如此）"
            for m in res.items:
                assert m.repo, "repo 必须有值"
                assert m.downloads == 0, f"null downloads 应降级为 0，实际 {m.downloads}"
                assert m.frameworks == [], f"null frameworks 应降级为 []，实际 {m.frameworks}"
        finally:
            srv.stop()

    @pytest.mark.asyncio
    async def test_hf_detail_returns_requested_repo(self) -> None:
        """详情必须对应请求的 repo，而不是永远返回第一个。"""
        srv = FakeHubServer().start()
        try:
            ad = HuggingFaceAdapter(base_url=f"{srv.base_url}/api", mirrors=())
            target = srv.hf_items[1]["id"]
            assert target != srv.hf_items[0]["id"], "前置条件：需有两个不同条目"
            detail = await ad.detail(target)
            assert detail is not None, f"未能取到 {target} 的详情"
            assert detail.repo == target, f"详情串了：请求 {target} 却返回 {detail.repo}"
        finally:
            srv.stop()

    @pytest.mark.asyncio
    async def test_hf_detail_unknown_repo_is_not_found(self) -> None:
        """不存在的 repo 必须给 404 语义，而不是默默返回别的模型。"""
        srv = FakeHubServer().start()
        try:
            ad = HuggingFaceAdapter(base_url=f"{srv.base_url}/api", mirrors=())
            # 适配器对「查不到」的既定契约是抛 LookupError（见 hf.py:418），
            # 因此这里断言异常，而不是断言返回 None —— 重点在于
            # **绝不能把别的模型的数据当成这个 repo 的**。
            with pytest.raises(LookupError):
                await ad.detail("nobody/does-not-exist")
        finally:
            srv.stop()
