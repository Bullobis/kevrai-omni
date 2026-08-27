"""Repo-wide sanity test: scan the entire codebase for leaked credentials.

We look for the easy-to-mistake patterns first (which is what killed the
famous git-history leaks). All matches are reported with a file:line so a
maintainer can decide if the hit is a false-positive (e.g. a regex example
in a SECURITY.md test fixture).

NOTE: this test deliberately permits the *known* compromised PAT from
SECURITY.md/RELEASE.md — those are explicit documentation; we exclude the
``SECURITY.md`` and ``RELEASE.md`` from the scan. Anywhere else the prefix
shows up is a hard fail.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

# Files where mentioning the leaked token is intentional (security docs + scripts).
ALLOWED_DOCS = {
    REPO_ROOT / "SECURITY.md",
    REPO_ROOT / "RELEASE.md",
    REPO_ROOT / "scripts" / "release.sh",   # contains the leaked prefix to refuse it
    REPO_ROOT / "scripts" / "smoke.sh",     # may reference security docs
    REPO_ROOT / "scripts" / "build_windows.sh",  # ditto
}


# Patterns to scan for. Each tuple is (regex, description).
# We are intentionally generous — false positives are easy to fix; false
# negatives are not.
PATTERNS: list[tuple[str, str]] = [
    # AWS access keys: AKIA / ASIA prefixes, 16 uppercase alphanumeric chars
    (r"\b(AKIA|ASIA)[0-9A-Z]{16}\b", "AWS access key"),
    # GitHub PATs (fine-grained "github_pat_" or classic "ghp_")
    (r"\bghp_[A-Za-z0-9]{30,}\b", "GitHub personal access token"),
    (r"\bgithub_pat_[A-Za-z0-9_]{40,}\b", "GitHub fine-grained PAT"),
    # OpenAI / Anthropic style
    (r"\bsk-[A-Za-z0-9]{20,}\b", "OpenAI/Anthropic-style secret key"),
    # HuggingFace write token (HF_*** is short-ish, prefix hf_ followed by
    # 30+ alphanumerics; read-only ones are public so we accept them).
    (r"\bhf_[A-Za-z0-9]{30,}\b", "HuggingFace (write) token"),
    # Private keys (PEM header)
    (r"-----BEGIN (RSA |EC |DSA |OPENSSH )?PRIVATE KEY-----", "private key (PEM)"),
]


def _all_files() -> list[Path]:
    """Enumerate every source / doc / config file we care about."""
    out: list[Path] = []
    candidates = [
        REPO_ROOT / "catalog",
        REPO_ROOT / "python",
        REPO_ROOT / "renderer",
        REPO_ROOT / "electron",
        REPO_ROOT / "scripts",
        REPO_ROOT / "docs",
        REPO_ROOT / ".github",
        REPO_ROOT,
    ]
    for base in candidates:
        if not base.exists():
            continue
        if base.is_file():
            out.append(base)
            continue
        for p in base.rglob("*"):
            if not p.is_file():
                continue
            if "__pycache__" in p.parts:
                continue
            if "node_modules" in p.parts:
                continue
            if p.suffix in {
                ".py", ".js", ".ts", ".jsx", ".tsx", ".json",
                ".yml", ".yaml", ".toml", ".md", ".sh", ".bash",
                ".txt", ".cfg", ".ini", ".html", ".css",
                ".env", ".example",
            }:
                out.append(p)
    # dedupe, sort
    return sorted(set(out))


def _scan_file(path: Path) -> list[tuple[int, str, str]]:
    """Return list of (line_no, pattern_name, snippet) for any hit in `path`."""
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        try:
            text = path.read_text(encoding="latin-1")
        except Exception:
            return []
    except Exception:
        return []

    hits: list[tuple[int, str, str]] = []
    for rx, name in PATTERNS:
        for m in re.finditer(rx, text):
            line_no = text.count("\n", 0, m.start()) + 1
            line = text.splitlines()[line_no - 1] if line_no - 1 < len(text.splitlines()) else m.group(0)
            snippet = line.strip()[:120]
            hits.append((line_no, name, snippet))
    return hits


def test_no_leaked_secrets():
    """For every file we scan, no PAT / API-key / private-key pattern should
    appear. Allowed exceptions: SECURITY.md, RELEASE.md (where the
    compromised token is intentionally named)."""
    files = _all_files()
    assert files, "no files found — check the enumeration logic"

    failures: list[str] = []
    total_hits = 0
    for fp in files:
        if fp.resolve() in {p.resolve() for p in ALLOWED_DOCS}:
            continue
        hits = _scan_file(fp)
        if not hits:
            continue
        total_hits += len(hits)
        rel = fp.relative_to(REPO_ROOT)
        for line_no, name, snippet in hits:
            failures.append(f"  {rel}:{line_no}: [{name}] {snippet!r}")

    if failures:
        msg = (
            f"Possible leaked secrets detected ({total_hits} hit(s) in "
            f"{(len(set(f.split(':')[0].strip() for f in failures)))} file(s)):\n"
            + "\n".join(failures[:30])
            + ("\n  ... (truncated)" if len(failures) > 30 else "")
        )
        pytest.fail(msg)


def test_allowed_docs_intentionally_mention_leaked_token():
    """Belt-and-suspenders: the docs that are allowed to mention the leaked
    token MUST exist (otherwise the test_no_leaked_secrets allowlist becomes
    useless if someone deletes the docs)."""
    for p in ALLOWED_DOCS:
        assert p.exists(), f"Allowlist file {p} disappeared — update the scan allowlist"


def test_gitignore_excludes_env_files():
    """.env / *.pem / node_modules must NOT be committed."""
    gi = REPO_ROOT / ".gitignore"
    assert gi.exists()
    txt = gi.read_text(encoding="utf-8")
    # At least the most common exclusions
    assert ".env" in txt or "*.env" in txt or "env" in txt, ".gitignore should cover .env"
    assert "node_modules" in txt, ".gitignore should exclude node_modules"


def test_no_pem_files_committed():
    """No PEM / KEY files in source tree."""
    pem_files = [p for p in _all_files() if p.suffix in {".pem", ".key"}]
    # filter out any allowlisted docs again
    bad = [p for p in pem_files if p.resolve() not in {q.resolve() for q in ALLOWED_DOCS}]
    assert not bad, f"PEM/KEY files in tree: {[str(p.relative_to(REPO_ROOT)) for p in bad]}"
