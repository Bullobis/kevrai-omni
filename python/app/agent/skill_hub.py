"""Skill hub — import external Agent Skills (Anthropic ``SKILL.md`` format).

Background (researched, not assumed)
------------------------------------
There is **no central skills store** in the Agent Skills ecosystem. Skills are
distributed as *plugins* through git repositories that carry a
``.claude-plugin/marketplace.json`` manifest, and installed with a tool such as
``npx skills add <owner>/<repo>``. The only format the whole ecosystem agrees on
is a directory containing a ``SKILL.md`` whose YAML frontmatter carries at
minimum ``name`` and ``description``; the markdown body is guidance text handed
to the model. Optional ``scripts/``, ``references/`` and ``assets/`` subdirs may
accompany it.

So "hooking up a skill hub" cannot mean calling a fantasy HTTP API. It means:
a **local library** that can be filled from three real sources — a local
directory, a ``.zip`` archive, or a git repository / marketplace — and then
exposed to the agent as ordinary :class:`~app.agent.skill.Skill` objects.

Design decisions
----------------
* The SKILL.md body is imported **verbatim** as ``guidance``. It is instructions
  for the model, not prose for a human; translating or rewriting it would
  silently corrupt the author's intent.
* Imported skills never join ``BUILTIN_SKILLS`` — they are passed to
  :func:`build_skill_manager` via ``extra_skills``. The built-in set is a frozen
  contract and user imports must not be able to shrink it.
* Every filesystem path is validated through :mod:`app.hub.paths`
  (``safe_join`` / ``is_within``) — the same helpers the model hub uses. No
  ``startswith`` containment checks.
* Archives are size- and count-capped, and extracts go through
  ``safe_extract`` (Zip-Slip hardened) rather than ``extractall``.

NOTE: ``app.hub`` is the **model** hub (HuggingFace / ModelScope). This module
is the **skill** hub; the name collision is intentional and the two share only
the path-safety helpers.
"""
from __future__ import annotations

import logging
import re
import shutil
import subprocess
import tempfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..hub.paths import UnsafePathError, is_within, safe_extract, safe_join
from .skill import _SKILL_ID_RE, Skill
from .tools import BUILTIN_SKILLS

log = logging.getLogger("kevrai.agent.skill_hub")

# ---------------------------------------------------------------------------
# Limits — an imported archive is untrusted input.
# ---------------------------------------------------------------------------
MAX_ARCHIVE_BYTES = 32 * 1024 * 1024        # 32 MiB compressed
MAX_UNCOMPRESSED_BYTES = 128 * 1024 * 1024  # 128 MiB expanded (zip-bomb guard)
MAX_MEMBERS = 4096                          # entry-count guard
MAX_SKILL_MD_BYTES = 512 * 1024             # a SKILL.md larger than this is junk
MAX_NAME_LEN = 80
MAX_DESCRIPTION_LEN = 1024
MAX_GUIDANCE_CHARS = 200_000

DEFAULT_GIT_TIMEOUT = 120


class SkillHubError(RuntimeError):
    """Any user-facing failure while importing or managing skills."""

    def __init__(self, message: str, *, code: str = "skill_hub_error") -> None:
        super().__init__(message)
        self.code = code


# ---------------------------------------------------------------------------
# SKILL.md parsing
# ---------------------------------------------------------------------------
@dataclass
class ParsedSkill:
    """Result of :func:`parse_skill_md`."""

    name: str
    description: str
    body: str
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def guidance(self) -> str:
        return self.body.strip()


def _strip_quotes(value: str) -> str:
    v = value.strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in ("'", '"'):
        return v[1:-1]
    return v


def _parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """Split a ``---`` delimited YAML frontmatter from the markdown body.

    Only the flat ``key: value`` subset actually used by SKILL.md manifests is
    supported; that is what real skills ship. Returns ``({}, text)`` when no
    frontmatter is present so the caller can produce a precise error.
    """
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    if not normalized.lstrip("\ufeff").startswith("---"):
        return {}, normalized
    lines = normalized.lstrip("\ufeff").split("\n")
    if lines[0].strip() != "---":
        return {}, normalized
    meta: dict[str, str] = {}
    for idx in range(1, len(lines)):
        line = lines[idx]
        if line.strip() == "---":
            return meta, "\n".join(lines[idx + 1:])
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip()
        # Nested blocks (rare) are ignored rather than mis-parsed.
        if key and not key.startswith("-"):
            meta[key] = _strip_quotes(value)
    # Unterminated frontmatter: treat the whole file as body.
    return {}, normalized


def parse_skill_md(text: str) -> ParsedSkill:
    """Parse ``SKILL.md`` content into a :class:`ParsedSkill`.

    ``name`` and ``description`` are the only required frontmatter keys — the
    same minimum the Anthropic format mandates.
    """
    if not isinstance(text, str) or not text.strip():
        raise SkillHubError("SKILL.md 为空", code="empty_skill_md")
    meta, body = _parse_frontmatter(text)
    if not meta:
        raise SkillHubError(
            "SKILL.md 缺少 YAML frontmatter（需以 --- 开头，并包含 name 与 description）",
            code="missing_frontmatter",
        )
    name = (meta.get("name") or "").strip()
    description = (meta.get("description") or "").strip()
    if not name:
        raise SkillHubError("SKILL.md frontmatter 缺少 name", code="missing_name")
    if not description:
        raise SkillHubError("SKILL.md frontmatter 缺少 description", code="missing_description")
    if len(name) > MAX_NAME_LEN:
        raise SkillHubError(f"技能 name 过长（>{MAX_NAME_LEN}）", code="name_too_long")
    if len(description) > MAX_DESCRIPTION_LEN:
        raise SkillHubError(f"技能 description 过长（>{MAX_DESCRIPTION_LEN}）", code="description_too_long")
    if not body.strip():
        raise SkillHubError(
            "SKILL.md 正文为空：该技能既无工具也无指导内容",
            code="empty_body",
        )
    if len(body) > MAX_GUIDANCE_CHARS:
        raise SkillHubError(f"SKILL.md 正文过长（>{MAX_GUIDANCE_CHARS} 字符）", code="body_too_long")
    return ParsedSkill(name=name, description=description, body=body, extra=dict(meta))


def derive_skill_id(raw: str) -> str:
    """Normalize an arbitrary directory/plugin name into a valid skill id.

    ``_SKILL_ID_RE`` demands ``[a-z][a-z0-9_]{1,63}`` (2–64 chars, snake_case).
    """
    candidate = re.sub(r"[^a-z0-9_]+", "_", str(raw or "").strip().lower())
    candidate = re.sub(r"_+", "_", candidate).strip("_")
    if not candidate:
        candidate = "imported_skill"
    if not candidate[0].isalpha():
        candidate = f"sk_{candidate}"
    return candidate[:64]


def validate_skill_id(raw: str) -> str:
    """Validate an already-derived id, raising :class:`SkillHubError`."""
    sid = str(raw or "").strip()
    if not _SKILL_ID_RE.fullmatch(sid):
        raise SkillHubError(
            f"非法技能 id {raw!r}：需匹配 [a-z][a-z0-9_]{{1,63}}",
            code="invalid_skill_id",
        )
    return sid


# ---------------------------------------------------------------------------
# Library root
# ---------------------------------------------------------------------------
def default_library_root(app_root: str | Path) -> Path:
    """``{app_root}/agent/skill_hub`` — sibling of ``skills.json``."""
    return Path(app_root).expanduser().resolve() / "agent" / "skill_hub"


def builtin_skill_ids() -> set[str]:
    return {sk.id for sk in BUILTIN_SKILLS}


def builtin_tool_names() -> set[str]:
    names: set[str] = set()
    for sk in BUILTIN_SKILLS:
        names.update(sk.tool_names)
    return names


# ---------------------------------------------------------------------------
# Library scanning / loading
# ---------------------------------------------------------------------------
def scan_library(root: str | Path) -> list[dict[str, Any]]:
    """List imported skills currently on disk (metadata only, never raises)."""
    base = Path(root).expanduser()
    if not base.is_dir():
        return []
    out: list[dict[str, Any]] = []
    for entry in sorted(base.iterdir(), key=lambda p: p.name):
        if not entry.is_dir():
            continue
        md = entry / "SKILL.md"
        if not md.is_file():
            continue
        try:
            raw = md.read_text(encoding="utf-8", errors="replace")[: MAX_SKILL_MD_BYTES]
            parsed = parse_skill_md(raw)
        except (SkillHubError, OSError) as exc:
            out.append({
                "id": entry.name,
                "name": entry.name,
                "description": "",
                "ok": False,
                "error": str(exc),
                "path": str(entry),
            })
            continue
        out.append({
            "id": entry.name,
            "name": parsed.name,
            "description": parsed.description,
            "ok": True,
            "error": "",
            "path": str(entry),
            "has_scripts": (entry / "scripts").is_dir(),
            "has_references": (entry / "references").is_dir(),
            "has_assets": (entry / "assets").is_dir(),
        })
    return out


def load_imported(root: str | Path) -> list[Skill]:
    """Materialise on-disk imports as guidance-only :class:`Skill` objects.

    Failures are logged and skipped — one malformed import must never stop the
    agent from starting.
    """
    base = Path(root).expanduser()
    builtin_ids = builtin_skill_ids()
    skills: list[Skill] = []
    for info in scan_library(base):
        if not info.get("ok"):
            log.warning("skill hub: 跳过损坏技能 %s: %s", info.get("id"), info.get("error"))
            continue
        sid = str(info["id"])
        if sid in builtin_ids:
            log.warning("skill hub: 跳过与内置技能同名的导入 %s", sid)
            continue
        try:
            raw = (Path(info["path"]) / "SKILL.md").read_text(
                encoding="utf-8", errors="replace"
            )[: MAX_SKILL_MD_BYTES]
            parsed = parse_skill_md(raw)
        except (SkillHubError, OSError) as exc:
            log.warning("skill hub: 解析失败 %s: %s", sid, exc)
            continue
        try:
            skills.append(_make_skill(sid, parsed, Path(info["path"])))
        except ValueError as exc:  # pragma: no cover - defensive
            log.warning("skill hub: 构建 Skill 失败 %s: %s", sid, exc)
    return skills


def _make_skill(sid: str, parsed: ParsedSkill, directory: Path) -> Skill:
    """Build a guidance-only Skill from a parsed SKILL.md.

    External skills deliberately carry **no Python tools**: executing code from
    a downloaded archive inside the sidecar process is not something we can
    sandbox, and the format's own contract is that the markdown body *is* the
    skill. Scripts stay on disk for the user to inspect.
    """
    try:
        resolved = directory.expanduser().resolve()
    except OSError:  # pragma: no cover - defensive
        resolved = directory
    return Skill(
        id=sid,
        name=parsed.name,
        description=parsed.description,
        tools=[],
        category="imported",
        icon="📥",
        guidance=parsed.guidance,
        default_enabled=False,
        required=False,
        source="imported",
        path=str(resolved),
    )


# ---------------------------------------------------------------------------
# Tool-name collision guard
# ---------------------------------------------------------------------------
def find_tool_conflicts(skills: list[Skill]) -> dict[str, list[str]]:
    """Map tool name → owning skill ids when the same tool appears twice.

    :class:`~app.agent.skill.SkillManager` hard-fails on duplicate tool names
    *across* skills, which would make the whole agent unusable. Import paths call
    this first so the user gets a precise error instead of a dead panel.
    """
    owners: dict[str, list[str]] = {}
    for sk in skills:
        for name in sk.tool_names:
            owners.setdefault(name, []).append(sk.id)
    return {name: ids for name, ids in owners.items() if len(ids) > 1}


def assert_no_conflicts(extra: list[Skill]) -> None:
    conflicts = find_tool_conflicts(list(BUILTIN_SKILLS) + list(extra))
    if conflicts:
        detail = "; ".join(f"{n} → {ids}" for n, ids in sorted(conflicts.items()))
        raise SkillHubError(f"技能工具名冲突：{detail}", code="tool_conflict")


# ---------------------------------------------------------------------------
# Import — local directory
# ---------------------------------------------------------------------------
def _dest_for(root: Path, sid: str) -> Path:
    try:
        dest = safe_join(root, sid)
    except UnsafePathError as exc:
        raise SkillHubError(f"非法技能目录名 {sid!r}: {exc}", code="unsafe_path") from exc
    if not is_within(root, dest):
        raise SkillHubError(f"目标路径逃逸技能库：{dest}", code="path_escape")
    return dest


def _read_skill_md(directory: Path, *, tmp_root: Path | None = None) -> ParsedSkill:
    """Read ``SKILL.md`` from ``directory``.

    ``tmp_root`` handles flat archives: ``directory`` is then a *virtual* name
    that does not exist on disk, while the file really lives at
    ``tmp_root/SKILL.md``.
    """
    md = directory / "SKILL.md"
    if not md.is_file() and tmp_root is not None:
        md = tmp_root / "SKILL.md"
    if not md.is_file():
        raise SkillHubError(f"目录中缺少 SKILL.md：{directory}", code="missing_skill_md")
    try:
        size = md.stat().st_size
    except OSError as exc:
        raise SkillHubError(f"无法读取 SKILL.md：{exc}", code="read_failed") from exc
    if size > MAX_SKILL_MD_BYTES:
        raise SkillHubError(
            f"SKILL.md 过大（{size} 字节 > {MAX_SKILL_MD_BYTES}）", code="skill_md_too_large"
        )
    try:
        text = md.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise SkillHubError(f"无法读取 SKILL.md：{exc}", code="read_failed") from exc
    return parse_skill_md(text)


def import_dir(src: str | Path, root: str | Path) -> dict[str, Any]:
    """Import a single local skill directory into the library."""
    source = Path(src).expanduser()
    if not source.exists():
        raise SkillHubError(f"路径不存在：{source}", code="not_found")
    if not source.is_dir():
        raise SkillHubError(f"不是目录：{source}", code="not_a_directory")
    source = source.resolve()
    parsed = _read_skill_md(source)
    base = Path(root).expanduser().resolve()
    builtin_ids = builtin_skill_ids()

    sid = derive_skill_id(parsed.extra.get("id") or source.name)
    if sid in builtin_ids:
        raise SkillHubError(
            f"技能 id {sid!r} 与内置技能冲突，内置技能不可被覆盖", code="builtin_conflict"
        )
    dest = _dest_for(base, sid)
    if is_within(base, source):
        raise SkillHubError("源目录已位于技能库内", code="already_in_library")
    base.mkdir(parents=True, exist_ok=True)
    try:
        if dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(source, dest)
    except OSError as exc:
        raise SkillHubError(f"复制失败：{exc}", code="copy_failed") from exc
    return {
        "id": sid,
        "name": parsed.name,
        "description": parsed.description,
        "path": str(dest),
        "source": "dir",
    }


# ---------------------------------------------------------------------------
# Import — zip archive
# ---------------------------------------------------------------------------
def _check_archive(zf: zipfile.ZipFile) -> None:
    infos = zf.infolist()
    if len(infos) > MAX_MEMBERS:
        raise SkillHubError(
            f"压缩包条目过多（{len(infos)} > {MAX_MEMBERS}）", code="too_many_members"
        )
    total = 0
    for info in infos:
        total += max(0, int(info.file_size or 0))
        if total > MAX_UNCOMPRESSED_BYTES:
            raise SkillHubError(
                f"压缩包解压后体积过大（>{MAX_UNCOMPRESSED_BYTES} 字节）", code="archive_too_large"
            )


def _find_skill_dirs(extracted_root: Path, *, named_root: Path | None = None) -> list[Path]:
    """Locate every directory holding a ``SKILL.md`` (top-level or nested).

    ``named_root`` names the *logical* directory for an archive whose
    ``SKILL.md`` sits at the root: without it the id would be derived from the
    caller's random temporary directory (``kevrai-skillhub-xxxx``), which is
    both meaningless and would defeat the built-in-name collision check.
    """
    direct = extracted_root / "SKILL.md"
    if direct.is_file():
        return [named_root if named_root is not None else extracted_root]
    found: list[Path] = []
    for md in sorted(extracted_root.rglob("SKILL.md")):
        parent = md.parent
        if is_within(extracted_root, parent):
            found.append(parent)
    return found


def _archive_stem(archive_path: Path) -> str:
    """``skills-v1.zip`` → ``"skills_v1"`` — the logical name of a flat archive."""
    return derive_skill_id(archive_path.stem)


def import_zip(src: str | Path, root: str | Path) -> list[dict[str, Any]]:
    """Import every skill directory found in a zip archive.

    Extracts into a temporary directory using the Zip-Slip hardened
    :func:`~app.hub.paths.safe_extract`, validates each candidate, then copies.
    """
    archive_path = Path(src).expanduser()
    if not archive_path.is_file():
        raise SkillHubError(f"文件不存在：{archive_path}", code="not_found")
    size = archive_path.stat().st_size
    if size > MAX_ARCHIVE_BYTES:
        raise SkillHubError(
            f"压缩包过大（{size} 字节 > {MAX_ARCHIVE_BYTES}）", code="archive_too_large"
        )
    if not zipfile.is_zipfile(archive_path):
        raise SkillHubError("不是合法的 zip 文件", code="not_a_zip")

    base = Path(root).expanduser().resolve()
    builtin_ids = builtin_skill_ids()
    results: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="kevrai-skillhub-") as tmp:
        try:
            with zipfile.ZipFile(archive_path, "r") as zf:
                _check_archive(zf)
                safe_extract(zf, tmp)
        except zipfile.BadZipFile as exc:
            raise SkillHubError(f"压缩包损坏：{exc}", code="bad_zip") from exc
        candidates = _find_skill_dirs(
            Path(tmp), named_root=Path(_archive_stem(archive_path))
        )
        if not candidates:
            raise SkillHubError("压缩包中未找到 SKILL.md", code="missing_skill_md")
        for cand in candidates:
            parsed = _read_skill_md(cand, tmp_root=Path(tmp))
            sid = derive_skill_id(parsed.extra.get("id") or cand.name)
            if sid in builtin_ids:
                raise SkillHubError(
                    f"技能 id {sid!r} 与内置技能冲突，内置技能不可被覆盖",
                    code="builtin_conflict",
                )
            dest = _dest_for(base, sid)
            base.mkdir(parents=True, exist_ok=True)
            # ``cand`` is a *virtual* path for flat archives (it does not exist
            # on disk — only the temp root does), so copy from the temp root.
            source_dir = cand if cand.is_dir() else Path(tmp)
            try:
                if dest.exists():
                    shutil.rmtree(dest)
                shutil.copytree(source_dir, dest)
            except OSError as exc:
                raise SkillHubError(f"复制失败：{exc}", code="copy_failed") from exc
            results.append({
                "id": sid,
                "name": parsed.name,
                "description": parsed.description,
                "path": str(dest),
                "source": "zip",
            })
    return results


# ---------------------------------------------------------------------------
# Import — git repository / plugin marketplace
# ---------------------------------------------------------------------------
_MARKETPLACE_REL = Path(".claude-plugin") / "marketplace.json"


def read_marketplace(repo_root: str | Path) -> list[dict[str, Any]]:
    """Parse ``.claude-plugin/marketplace.json`` → list of plugin entries.

    Returns ``[]`` when the manifest is absent or unusable; a plain git repo of
    skills (no manifest) is still importable, so this is not an error.
    """
    import json

    manifest = Path(repo_root) / _MARKETPLACE_REL
    if not manifest.is_file():
        # Some repos put SKILL.md at the root: synthesize a single entry.
        if (Path(repo_root) / "SKILL.md").is_file():
            return [{"name": Path(repo_root).name, "source": "."}]
        return []
    try:
        data = json.loads(manifest.read_text(encoding="utf-8", errors="replace"))
    except (OSError, ValueError) as exc:
        log.warning("skill hub: marketplace.json 解析失败: %s", exc)
        return []
    plugins = data.get("plugins")
    if not isinstance(plugins, list):
        return []
    out: list[dict[str, Any]] = []
    for item in plugins:
        if isinstance(item, dict) and item.get("name"):
            out.append(item)
        elif isinstance(item, str):
            out.append({"name": item, "source": item})
    return out


_GIT_URL_RE = re.compile(r"^(https?://|git://|ssh://|git@|file://)")


def import_git(
    url: str,
    root: str | Path,
    *,
    timeout: int = DEFAULT_GIT_TIMEOUT,
) -> list[dict[str, Any]]:
    """Clone ``url`` shallowly and import every SKILL.md it contains."""
    target = str(url or "").strip()
    if not target:
        raise SkillHubError("git 地址为空", code="empty_url")
    if not _GIT_URL_RE.match(target):
        raise SkillHubError(
            "仅支持 http(s):// / git:// / ssh:// / git@ / file:// 形式的仓库地址",
            code="invalid_url",
        )
    base = Path(root).expanduser().resolve()
    builtin_ids = builtin_skill_ids()
    results: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="kevrai-skillhub-git-") as tmp:
        clone_dir = Path(tmp) / "repo"
        try:
            proc = subprocess.run(  # noqa: S603, S607 - fixed argv, no shell
                ["git", "clone", "--depth", "1", "--quiet", target, str(clone_dir)],
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        except FileNotFoundError as exc:
            raise SkillHubError("系统未安装 git，无法从仓库导入", code="git_missing") from exc
        except subprocess.TimeoutExpired as exc:
            raise SkillHubError(f"git clone 超时（{timeout}s）", code="git_timeout") from exc
        if proc.returncode != 0:
            err = (proc.stderr or proc.stdout or "").strip()[:500]
            raise SkillHubError(f"git clone 失败：{err}", code="git_failed")

        candidates = _find_skill_dirs(clone_dir)
        if not candidates:
            plugins = read_marketplace(clone_dir)
            for plug in plugins:
                rel = str(plug.get("source") or plug.get("name") or ".").strip()
                sub = clone_dir / rel
                if is_within(clone_dir, sub):
                    candidates.extend(_find_skill_dirs(sub))
        if not candidates:
            raise SkillHubError(
                "仓库中未找到 SKILL.md（也未见 .claude-plugin/marketplace.json）",
                code="missing_skill_md",
            )
        seen: set[str] = set()
        for cand in candidates:
            try:
                rel_key = str(cand.resolve())
            except OSError:  # pragma: no cover - defensive
                rel_key = str(cand)
            if rel_key in seen:
                continue
            seen.add(rel_key)
            parsed = _read_skill_md(cand)
            sid = derive_skill_id(parsed.extra.get("id") or cand.name)
            if sid in builtin_ids:
                raise SkillHubError(
                    f"技能 id {sid!r} 与内置技能冲突，内置技能不可被覆盖",
                    code="builtin_conflict",
                )
            dest = _dest_for(base, sid)
            base.mkdir(parents=True, exist_ok=True)
            try:
                if dest.exists():
                    shutil.rmtree(dest)
                # Skip .git when copying a repo root.
                shutil.copytree(cand, dest, ignore=shutil.ignore_patterns(".git"))
            except OSError as exc:
                raise SkillHubError(f"复制失败：{exc}", code="copy_failed") from exc
            results.append({
                "id": sid,
                "name": parsed.name,
                "description": parsed.description,
                "path": str(dest),
                "source": "git",
            })
    return results


# ---------------------------------------------------------------------------
# Removal
# ---------------------------------------------------------------------------
def remove_imported(skill_id: str, root: str | Path) -> dict[str, Any]:
    """Delete an *imported* skill. Built-ins can never be removed."""
    sid = validate_skill_id(skill_id)
    if sid in builtin_skill_ids():
        raise SkillHubError(
            f"{sid!r} 是内置技能，不允许删除", code="builtin_protected"
        )
    base = Path(root).expanduser().resolve()
    dest = _dest_for(base, sid)
    if not is_within(base, dest):
        raise SkillHubError(f"路径逃逸技能库：{dest}", code="path_escape")
    if not dest.is_dir():
        raise SkillHubError(f"技能不存在：{sid}", code="not_found")
    try:
        shutil.rmtree(dest)
    except OSError as exc:
        raise SkillHubError(f"删除失败：{exc}", code="delete_failed") from exc
    return {"id": sid, "removed": True}


__all__ = [
    "SkillHubError",
    "ParsedSkill",
    "parse_skill_md",
    "derive_skill_id",
    "validate_skill_id",
    "default_library_root",
    "builtin_skill_ids",
    "builtin_tool_names",
    "scan_library",
    "load_imported",
    "find_tool_conflicts",
    "assert_no_conflicts",
    "import_dir",
    "import_zip",
    "import_git",
    "read_marketplace",
    "remove_imported",
]
