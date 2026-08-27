"""Markdown link checker — every relative link to a file must resolve.

We don't follow absolute (http/https) links — those change constantly and
require network. We DO follow every Markdown link that points at a local
relative path (``./foo.md``, ``../bar/`` etc.) and fail if the target file
is missing.

Also flags ``#fragment`` anchors as a sanity check (they may resolve to a
heading in the same file; we don't try to verify those, just ensure the
file itself exists).
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

# `[text](target)` — capture the target group only. We keep this regex simple
# on purpose; markdown link parsing is famously ambiguous.
_MD_LINK_RE = re.compile(r"(?<!\!)\[[^\]]+\]\(([^)]+)\)")

# Files we never want to walk into (deps, caches).
_EXCLUDE_PARTS = {"node_modules", "__pycache__", ".git", "dist", "build"}


def _iter_markdown_files() -> list[Path]:
    out: list[Path] = []
    for p in REPO_ROOT.rglob("*.md"):
        if any(part in _EXCLUDE_PARTS for part in p.parts):
            continue
        out.append(p)
    return sorted(out)


@pytest.fixture(scope="module")
def md_files() -> list[Path]:
    return _iter_markdown_files()


def test_there_are_markdown_files(md_files):
    assert md_files, "no .md files found anywhere in the repo"


def _is_local_target(target: str) -> bool:
    """Skip absolute URLs and pure anchors."""
    if target.startswith(("http://", "https://", "mailto:", "tel:", "data:")):
        return False
    if target.startswith("#"):
        return False
    return True


def test_every_md_link_resolves(md_files):
    """Walk every Markdown link in every .md file under the repo.

    For local relative targets, the destination file MUST exist (or, if it
    ends with ``/``, the directory must exist).
    """
    failures: list[str] = []
    seen = 0

    for md in md_files:
        rel = md.relative_to(REPO_ROOT)
        text = md.read_text(encoding="utf-8", errors="surrogateescape")
        for m in _MD_LINK_RE.finditer(text):
            seen += 1
            target = m.group(1).split("#", 1)[0].split("?", 1)[0]
            if not target:
                # pure #anchor — skip
                continue
            if not _is_local_target(target):
                continue
            # Resolve the relative target against the markdown file's dir
            base = md.parent
            resolved = (base / target).resolve()
            # Allow trailing slash → directory
            if str(target).endswith("/"):
                if not resolved.is_dir():
                    failures.append(
                        f"{rel}: link target is not a directory: {target!r} → {resolved}"
                    )
            else:
                if not resolved.exists():
                    failures.append(
                        f"{rel}: link target does not exist: {target!r} → {resolved}"
                    )

    if failures:
        msg = f"{len(failures)} broken Markdown link(s):\n  " + "\n  ".join(failures[:20])
        if len(failures) > 20:
            msg += f"\n  ... and {len(failures) - 20} more"
        pytest.fail(msg)


def test_security_md_links_resolve():
    """SECURITY.md is the most-referenced doc; check its links specifically."""
    p = REPO_ROOT / "SECURITY.md"
    assert p.exists()
    text = p.read_text(encoding="utf-8")
    failures: list[str] = []
    for m in _MD_LINK_RE.finditer(text):
        target = m.group(1).split("#", 1)[0]
        if not target:
            continue
        if not _is_local_target(target):
            continue
        resolved = (p.parent / target).resolve()
        if not resolved.exists():
            failures.append(f"SECURITY.md: missing link target {target!r}")
    assert not failures, "broken links: " + "\n".join(failures)


def test_readme_links_resolve():
    p = REPO_ROOT / "README.md"
    assert p.exists()
    text = p.read_text(encoding="utf-8")
    failures: list[str] = []
    for m in _MD_LINK_RE.finditer(text):
        target = m.group(1).split("#", 1)[0]
        if not target or not _is_local_target(target):
            continue
        resolved = (p.parent / target).resolve()
        if not resolved.exists():
            failures.append(f"README.md: missing link target {target!r}")
    assert not failures, "broken links: " + "\n".join(failures)


def test_top_level_md_files_exist():
    """The most-referenced top-level docs must always be present."""
    for name in ("README.md", "SECURITY.md", "INSTALL.md", "RELEASE.md"):
        p = REPO_ROOT / name
        assert p.exists(), f"required top-level doc missing: {name}"
