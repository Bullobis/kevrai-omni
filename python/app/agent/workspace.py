"""Workspace and command execution boundary for the coding agent skill.

This module is deliberately *not* an operating-system sandbox. It provides:

* a fixed workspace root and path resolution that rejects absolute paths,
  parent traversal, and symlink escapes;
* an explicit, per-workspace execution mode (``none`` / ``test_runner`` /
  ``approval_shell``);
* bounded command execution with a timeout, output cap, process-group kill,
  a scrubbed environment, and a small audit log.

The shell mode is a trusted-user mode: the command is shown to the user and
must pass the existing approval gate. It does not isolate network, processes,
or the rest of the filesystem. The UI must say so.
"""
from __future__ import annotations

import contextlib
import difflib
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

MODE_NONE = "none"
MODE_TEST_RUNNER = "test_runner"
MODE_APPROVAL_SHELL = "approval_shell"
VALID_MODES = (MODE_NONE, MODE_TEST_RUNNER, MODE_APPROVAL_SHELL)

MODE_LABELS = {
    MODE_NONE: "不执行命令",
    MODE_TEST_RUNNER: "测试运行器",
    MODE_APPROVAL_SHELL: "完整 Shell（无隔离）",
}

MAX_READ_BYTES = 1 * 1024 * 1024
MAX_WRITE_BYTES = 1 * 1024 * 1024
MAX_COMMAND_OUTPUT_BYTES = 64 * 1024
MAX_COMMAND_LENGTH = 2000
DEFAULT_COMMAND_TIMEOUT = 120
MAX_COMMAND_TIMEOUT = 300

_WINDOWS_ABS_RE = re.compile(r"^[A-Za-z]:")
_SECRET_ENV_RE = re.compile(r"(KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL|AUTH)", re.I)


class WorkspaceError(Exception):
    """A user-facing workspace boundary error."""


@dataclass(frozen=True)
class CommandResult:
    command: str
    cwd: str
    mode: str
    exit_code: int | None
    output: str
    timed_out: bool
    truncated: bool
    duration_ms: int

    def public(self) -> dict[str, Any]:
        return {
            "command": self.command,
            "cwd": self.cwd,
            "mode": self.mode,
            "exit_code": self.exit_code,
            "output": self.output,
            "timed_out": self.timed_out,
            "truncated": self.truncated,
            "duration_ms": self.duration_ms,
        }


class WorkspaceRuntime:
    """A fixed workspace plus the user's explicit execution-mode choice."""

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.settings_path = self.root.parent / "workspace.json"
        self.audit_path = self.root.parent / "workspace-executions.jsonl"
        self._mode = MODE_NONE
        self._explicit = False
        self._load_settings()

    # ------------------------------------------------------------------
    # Settings
    # ------------------------------------------------------------------
    def _load_settings(self) -> None:
        try:
            raw = json.loads(self.settings_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, TypeError):
            return
        if not isinstance(raw, dict):
            return
        mode = raw.get("mode")
        if mode in VALID_MODES:
            self._mode = mode
            self._explicit = bool(raw.get("explicit", True))
        # A file with an unknown mode is not a valid user choice; fail closed.

    def _save_settings(self) -> None:
        self.settings_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"mode": self._mode, "explicit": self._explicit}
        tmp = self.settings_path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        tmp.replace(self.settings_path)

    @property
    def mode(self) -> str:
        return self._mode

    @property
    def explicit(self) -> bool:
        return self._explicit

    def set_mode(self, mode: str, *, acknowledge_risk: bool = False) -> str:
        if mode not in VALID_MODES:
            raise WorkspaceError(f"invalid execution mode: {mode!r}")
        if mode == MODE_APPROVAL_SHELL and not acknowledge_risk:
            raise WorkspaceError(
                "完整 Shell 没有操作系统隔离。继续前必须确认你理解这一风险。"
            )
        self._mode = mode
        self._explicit = True
        self._save_settings()
        return self._mode

    def describe(self) -> dict[str, Any]:
        return {
            "mode": self._mode,
            "explicit": self._explicit,
            "root": str(self.root),
            "modes": [
                {
                    "id": MODE_NONE,
                    "label": MODE_LABELS[MODE_NONE],
                    "description": "只能读写工作区文件，不能运行测试或命令。",
                },
                {
                    "id": MODE_TEST_RUNNER,
                    "label": MODE_LABELS[MODE_TEST_RUNNER],
                    "description": (
                        "只运行 pytest / npm test / cargo test 等白名单命令。"
                        "它会执行项目代码，不是沙箱；只用于你信任的工作区。"
                    ),
                },
                {
                    "id": MODE_APPROVAL_SHELL,
                    "label": MODE_LABELS[MODE_APPROVAL_SHELL],
                    "description": (
                        "可以运行任意命令；每次执行前显示命令并等待确认。"
                        "没有网络/进程/文件系统隔离，只建议开发者临时使用。"
                    ),
                },
            ],
        }

    # ------------------------------------------------------------------
    # Path boundary
    # ------------------------------------------------------------------
    def resolve(self, path: str, *, allow_missing: bool = False) -> Path:
        """Resolve a workspace-relative path and reject escapes.

        This is a path guard, not an OS sandbox. It rejects absolute paths,
        ``..`` components, and symlinks that resolve outside the workspace.
        """
        if not isinstance(path, str):
            raise WorkspaceError("path must be a string")
        path = path.strip()
        if not path:
            raise WorkspaceError("path must not be empty")
        if len(path) > 512:
            raise WorkspaceError("path too long")
        if _WINDOWS_ABS_RE.match(path) or path.startswith(("/", "\\", "~")):
            raise WorkspaceError("path must be relative to the workspace")
        rel = Path(path)
        if any(part in ("..", "") for part in rel.parts):
            raise WorkspaceError("path must not contain '..'")

        candidate = self.root / rel
        if not allow_missing:
            try:
                resolved = candidate.resolve(strict=True)
            except (OSError, RuntimeError) as exc:
                raise WorkspaceError(f"path not found: {path}") from exc
            self._assert_inside(resolved)
            return resolved

        # For writes, the target may not exist yet. Resolve the nearest
        # existing ancestor, so a symlinked parent cannot smuggle the target
        # outside the workspace.
        existing = candidate
        while not existing.exists():
            if existing == self.root:
                break
            parent = existing.parent
            if parent == existing:
                break
            existing = parent
        try:
            resolved_existing = existing.resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise WorkspaceError(f"cannot resolve parent: {path}") from exc
        self._assert_inside(resolved_existing)
        return candidate

    def _assert_inside(self, path: Path) -> None:
        if path != self.root and self.root not in path.parents:
            raise WorkspaceError(
                f"path escapes workspace: {path} is outside {self.root}"
            )

    def relative(self, path: Path) -> str:
        try:
            return str(path.relative_to(self.root))
        except ValueError:
            return str(path)

    # ------------------------------------------------------------------
    # File operations
    # ------------------------------------------------------------------
    def read_file(
        self, path: str, *, max_chars: int = 120_000, with_line_numbers: bool = False,
    ) -> dict[str, Any]:
        target = self.resolve(path)
        if not target.is_file():
            raise WorkspaceError(f"not a file: {path}")
        if target.stat().st_size > MAX_READ_BYTES:
            raise WorkspaceError(
                f"file too large ({target.stat().st_size} bytes; max {MAX_READ_BYTES})"
            )
        with target.open("rb") as fh:
            head = fh.read(8192)
        if b"\x00" in head:
            raise WorkspaceError("binary file is not supported")
        text = target.read_text(encoding="utf-8", errors="replace")
        line_count = len(text.splitlines())
        truncated = len(text) > max_chars
        if truncated:
            text = text[:max_chars] + "\n… [truncated]"
        if with_line_numbers:
            text = "\n".join(
                f"{line_no:>5} | {line}"
                for line_no, line in enumerate(text.splitlines(), 1)
            )
        return {
            "path": self.relative(target),
            "content": text,
            "bytes": target.stat().st_size,
            "line_count": line_count,
            "truncated": truncated,
        }

    def list_dir(self, path: str = ".", *, limit: int = 200) -> dict[str, Any]:
        target = self.resolve(path)
        if not target.is_dir():
            raise WorkspaceError(f"not a directory: {path}")
        entries: list[dict[str, Any]] = []
        for child in sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
            if len(entries) >= limit:
                break
            entries.append({
                "name": child.name,
                "type": "dir" if child.is_dir() else "file",
            })
        return {
            "path": self.relative(target) or ".",
            "entries": entries,
            "truncated": len(entries) >= limit,
        }

    def search_text(
        self,
        query: str,
        path: str = ".",
        *,
        max_matches: int = 80,
        max_file_bytes: int = 512 * 1024,
    ) -> dict[str, Any]:
        if not query:
            raise WorkspaceError("query must not be empty")
        base = self.resolve(path)
        if not base.exists():
            raise WorkspaceError(f"path not found: {path}")
        ignored = {".git", "node_modules", ".venv", "venv", "target", "dist", "build", "__pycache__"}
        matches: list[dict[str, Any]] = []
        q = query.lower()
        for current, dirs, files in os.walk(base, followlinks=False):
            dirs[:] = [d for d in dirs if d not in ignored and not d.startswith(".")]
            for name in files:
                candidate = Path(current) / name
                try:
                    self.resolve(self.relative(candidate))
                except WorkspaceError:
                    continue
                try:
                    if candidate.stat().st_size > max_file_bytes:
                        continue
                    raw = candidate.read_bytes()
                    if b"\x00" in raw[:8192]:
                        continue
                    text = raw.decode("utf-8", errors="replace")
                except OSError:
                    continue
                for line_no, line in enumerate(text.splitlines(), 1):
                    if q in line.lower():
                        matches.append({
                            "path": self.relative(candidate),
                            "line": line_no,
                            "preview": line.strip()[:200],
                        })
                        if len(matches) >= max_matches:
                            return {
                                "query": query,
                                "matches": matches,
                                "truncated": True,
                            }
        return {"query": query, "matches": matches, "truncated": False}

    def write_file(self, path: str, content: str) -> dict[str, Any]:
        if not isinstance(content, str):
            raise WorkspaceError("content must be a string")
        if len(content) > MAX_WRITE_BYTES:
            raise WorkspaceError(f"content too large (max {MAX_WRITE_BYTES} bytes)")
        target = self.resolve(path, allow_missing=True)
        if target == self.root or target.is_dir():
            raise WorkspaceError("target must be a file path")
        target.parent.mkdir(parents=True, exist_ok=True)
        old_text = ""
        if target.is_file() and target.stat().st_size <= MAX_READ_BYTES:
            old_text = target.read_text(encoding="utf-8", errors="replace")
        tmp = target.with_name(f".{target.name}.kevrai-tmp")
        tmp.write_text(content, encoding="utf-8")
        tmp.replace(target)
        additions, deletions = _line_change_counts(old_text, content)
        return {
            "path": self.relative(target),
            "bytes": len(content.encode("utf-8")),
            "additions": additions,
            "deletions": deletions,
            "diff": _unified_diff(old_text, content),
        }

    def edit_file(
        self,
        path: str,
        old_string: str,
        new_string: str,
        *,
        replace_all: bool = False,
    ) -> dict[str, Any]:
        if not old_string:
            raise WorkspaceError("old_string must not be empty")
        target = self.resolve(path)
        if not target.is_file():
            raise WorkspaceError(f"not a file: {path}")
        text = target.read_text(encoding="utf-8", errors="replace")
        count = text.count(old_string)
        if count == 0:
            raise WorkspaceError("old_string not found")
        replacements = count if replace_all else 1
        updated = text.replace(old_string, new_string, replacements)
        if len(updated) > MAX_WRITE_BYTES:
            raise WorkspaceError(f"result too large (max {MAX_WRITE_BYTES} bytes)")
        tmp = target.with_name(f".{target.name}.kevrai-tmp")
        tmp.write_text(updated, encoding="utf-8")
        tmp.replace(target)
        additions, deletions = _line_change_counts(text, updated)
        return {
            "path": self.relative(target),
            "replacements": replacements,
            "additions": additions,
            "deletions": deletions,
            "diff": _unified_diff(text, updated),
        }

    def edit_lines(
        self,
        path: str,
        start_line: int,
        end_line: int | None,
        new_text: str,
        *,
        operation: str = "replace",
        expected_text: str | None = None,
    ) -> dict[str, Any]:
        """Edit a 1-based, inclusive line range instead of a full-file rewrite.

        ``operation`` is ``replace`` (default), ``delete``, ``insert_before``,
        or ``insert_after``. For ``replace``/``delete`` both line numbers are
        required; for ``insert_before`` only the anchor line is used; for
        ``insert_after`` the anchor may be 0 to insert at the beginning.
        ``expected_text`` optionally guards the selected range against drift.
        """
        if operation not in ("replace", "delete", "insert_before", "insert_after"):
            raise WorkspaceError(f"invalid line operation: {operation!r}")
        if isinstance(start_line, bool) or not isinstance(start_line, int):
            raise WorkspaceError("start_line must be an integer")
        if end_line is not None and (isinstance(end_line, bool) or not isinstance(end_line, int)):
            raise WorkspaceError("end_line must be an integer")
        if not isinstance(new_text, str):
            raise WorkspaceError("new_text must be a string")
        target = self.resolve(path)
        if not target.is_file():
            raise WorkspaceError(f"not a file: {path}")
        text = target.read_text(encoding="utf-8", errors="replace")
        newline = "\r\n" if "\r\n" in text else "\n"
        lines = text.splitlines()
        total = len(lines)
        repl = [] if operation == "delete" else new_text.splitlines()

        if operation in ("replace", "delete"):
            end = start_line if end_line is None else end_line
            if total == 0:
                raise WorkspaceError("empty file has no lines to replace")
            if not (1 <= start_line <= total and start_line <= end <= total):
                raise WorkspaceError(
                    f"line range out of bounds: {start_line}-{end} (file has {total} lines)"
                )
            selected = lines[start_line - 1:end]
            if expected_text is not None and expected_text.rstrip("\r\n") != "\n".join(selected):
                raise WorkspaceError("selected lines do not match expected_text")
            updated_lines = lines[:start_line - 1] + repl + lines[end:]
            anchor_start, anchor_end = start_line, end
        elif operation == "insert_before":
            if not (1 <= start_line <= total + 1):
                raise WorkspaceError(
                    f"insert line out of bounds: {start_line} (file has {total} lines)"
                )
            if not repl:
                raise WorkspaceError("insert requires non-empty new_text")
            updated_lines = lines[:start_line - 1] + repl + lines[start_line - 1:]
            anchor_start = anchor_end = start_line
        else:  # insert_after
            if not (0 <= start_line <= total):
                raise WorkspaceError(
                    f"insert line out of bounds: {start_line} (file has {total} lines)"
                )
            if not repl:
                raise WorkspaceError("insert requires non-empty new_text")
            updated_lines = lines[:start_line] + repl + lines[start_line:]
            anchor_start = anchor_end = start_line

        updated = newline.join(updated_lines)
        if text.endswith("\n") and updated_lines:
            updated += newline
        if len(updated) > MAX_WRITE_BYTES:
            raise WorkspaceError(f"result too large (max {MAX_WRITE_BYTES} bytes)")
        tmp = target.with_name(f".{target.name}.kevrai-tmp")
        tmp.write_text(updated, encoding="utf-8")
        tmp.replace(target)
        additions, deletions = _line_change_counts(text, updated)
        return {
            "path": self.relative(target),
            "operation": operation,
            "start_line": anchor_start,
            "end_line": anchor_end,
            "additions": additions,
            "deletions": deletions,
            "diff": _unified_diff(text, updated),
        }

    # ------------------------------------------------------------------
    # Commands
    # ------------------------------------------------------------------
    def run_test(self, command: str, *, timeout_s: int | None = None) -> dict[str, Any]:
        if self.mode != MODE_TEST_RUNNER:
            raise WorkspaceError(
                "当前不是测试运行器模式。请先在 Agent 执行方式中选择“测试运行器”。"
            )
        argv = _parse_test_command(command)
        return self._run(
            argv=argv,
            shell=False,
            display=command,
            timeout_s=timeout_s,
            mode=MODE_TEST_RUNNER,
        ).public()

    def run_command(self, command: str, *, timeout_s: int | None = None) -> dict[str, Any]:
        if self.mode != MODE_APPROVAL_SHELL:
            raise WorkspaceError(
                "当前不是完整 Shell 模式。请先在 Agent 执行方式中明确选择“完整 Shell”。"
            )
        if not isinstance(command, str) or not command.strip():
            raise WorkspaceError("command must not be empty")
        if len(command) > MAX_COMMAND_LENGTH:
            raise WorkspaceError(f"command too long (max {MAX_COMMAND_LENGTH})")
        return self._run(  # noqa: S604 - this is the explicitly approved shell mode
            argv=None,
            shell=True,
            display=command.strip(),
            timeout_s=timeout_s,
            mode=MODE_APPROVAL_SHELL,
        ).public()

    def _run(
        self,
        *,
        argv: list[str] | None,
        shell: bool,
        display: str,
        timeout_s: int | None,
        mode: str,
    ) -> CommandResult:
        timeout = _clamp_timeout(timeout_s)
        started = time.monotonic()
        output_file = tempfile.TemporaryFile(mode="w+b")  # noqa: SIM115 - closed in finally
        proc: subprocess.Popen[bytes] | None = None
        timed_out = False
        try:
            env = _safe_env()
            kwargs: dict[str, Any] = {
                "cwd": str(self.root),
                "stdin": subprocess.DEVNULL,
                "stdout": output_file,
                "stderr": subprocess.STDOUT,
                "env": env,
                "shell": shell,
            }
            if os.name == "nt":
                kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            else:
                kwargs["start_new_session"] = True
            proc = subprocess.Popen(  # noqa: S603 - command is user-approved
                argv if argv is not None else display, **kwargs
            )
            try:
                proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                timed_out = True
                _kill_process_tree(proc)
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
            output_file.seek(0)
            raw = output_file.read(MAX_COMMAND_OUTPUT_BYTES + 1)
            truncated = len(raw) > MAX_COMMAND_OUTPUT_BYTES
            if truncated:
                raw = raw[:MAX_COMMAND_OUTPUT_BYTES]
            text = raw.decode("utf-8", errors="replace")
            duration_ms = int((time.monotonic() - started) * 1000)
            result = CommandResult(
                command=display,
                cwd=str(self.root),
                mode=mode,
                exit_code=proc.returncode,
                output=text,
                timed_out=timed_out,
                truncated=truncated,
                duration_ms=duration_ms,
            )
            self._audit(result)
            return result
        finally:
            output_file.close()

    def _audit(self, result: CommandResult) -> None:
        try:
            record = {
                "ts": round(time.time(), 3),
                "mode": result.mode,
                "command": _redact_command(result.command),
                "cwd": result.cwd,
                "exit_code": result.exit_code,
                "timed_out": result.timed_out,
                "truncated": result.truncated,
                "duration_ms": result.duration_ms,
            }
            with self.audit_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        except OSError:
            # Audit failure must not break execution; the user still sees the
            # result in the Agent transcript.
            pass


# ---------------------------------------------------------------------------
# Test-runner command grammar
# ---------------------------------------------------------------------------
_SIMPLE_TEST_COMMANDS: dict[tuple[str, ...], list[str]] = {
    ("pytest",): [sys.executable, "-m", "pytest"],
    ("python", "-m", "pytest"): [sys.executable, "-m", "pytest"],
    ("python3", "-m", "pytest"): [sys.executable, "-m", "pytest"],
    ("python", "-m", "unittest"): [sys.executable, "-m", "unittest"],
    ("python3", "-m", "unittest"): [sys.executable, "-m", "unittest"],
    ("python", "-m", "compileall"): [sys.executable, "-m", "compileall"],
    ("python3", "-m", "compileall"): [sys.executable, "-m", "compileall"],
    ("npm", "test"): ["npm", "test"],
    ("npm", "run", "test"): ["npm", "run", "test"],
    ("npm", "run", "lint"): ["npm", "run", "lint"],
    ("cargo", "test"): ["cargo", "test"],
    ("cargo", "check"): ["cargo", "check"],
    ("go", "test"): ["go", "test"],
    ("ruff", "check"): ["ruff", "check"],
    ("mypy",): ["mypy"],
    ("eslint",): ["eslint"],
}

_SHELL_META = re.compile(r"[;&|><`$]|\n")


def _parse_test_command(command: str) -> list[str]:
    if not isinstance(command, str) or not command.strip():
        raise WorkspaceError("command must not be empty")
    if len(command) > MAX_COMMAND_LENGTH:
        raise WorkspaceError(f"command too long (max {MAX_COMMAND_LENGTH})")
    if _SHELL_META.search(command):
        raise WorkspaceError("测试命令不能包含 shell 元字符")
    try:
        args = shlex.split(command, posix=os.name != "nt")
    except ValueError as exc:
        raise WorkspaceError(f"cannot parse command: {exc}") from exc
    if not args:
        raise WorkspaceError("command must not be empty")

    base: list[str] | None = None
    prefix_len = 0
    for prefix, resolved in _SIMPLE_TEST_COMMANDS.items():
        if tuple(args[:len(prefix)]) == prefix:
            base = list(resolved)
            prefix_len = len(prefix)
            break
    if base is None:
        raise WorkspaceError(
            "只允许测试运行器白名单中的命令：pytest、npm test、cargo test、"
            "go test、ruff check、mypy 等"
        )

    extra = args[prefix_len:]
    for arg in extra:
        if arg.startswith(("/", "\\", "~")) or _WINDOWS_ABS_RE.match(arg):
            raise WorkspaceError("测试命令参数不能是绝对路径")
        if ".." in Path(arg).parts:
            raise WorkspaceError("测试命令参数不能包含 '..'")
        if arg in ("-c", "--command"):  # common code-execution flags
            raise WorkspaceError("测试命令不允许执行内联代码")
    if base[0] in ("npm", "cargo", "go", "ruff", "mypy", "eslint"):
        executable = shutil.which(base[0])
        if not executable:
            raise WorkspaceError(f"未找到可执行命令：{base[0]}")
        base[0] = executable
    return base + extra


def _clamp_timeout(value: int | None) -> int:
    if value is None:
        return DEFAULT_COMMAND_TIMEOUT
    try:
        timeout = int(value)
    except (TypeError, ValueError) as exc:
        raise WorkspaceError("timeout_s must be an integer") from exc
    if timeout < 5:
        raise WorkspaceError("timeout_s must be at least 5 seconds")
    return min(timeout, MAX_COMMAND_TIMEOUT)


def _safe_env() -> dict[str, str]:
    env: dict[str, str] = {}
    for key, value in os.environ.items():
        if _SECRET_ENV_RE.search(key):
            continue
        env[key] = value
    env.setdefault("PYTHONIOENCODING", "utf-8")
    return env


def _kill_process_tree(proc: subprocess.Popen[bytes]) -> None:
    if proc.poll() is not None:
        return
    if os.name == "nt":
        try:
            taskkill = shutil.which("taskkill") or "taskkill"
            subprocess.run(  # noqa: S603,S607 - fixed Windows system utility
                [taskkill, "/F", "/T", "/PID", str(proc.pid)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=10,
                check=False,
            )
            return
        except (OSError, subprocess.SubprocessError):
            pass
    else:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            return
        except (OSError, ProcessLookupError):
            pass
    with contextlib.suppress(OSError):
        proc.kill()


def _redact_command(command: str) -> str:
    return re.sub(
        r"(?i)(api[_-]?key|token|secret|password|credential)=([^\s]+)",
        r"\1=[redacted]",
        command,
    )


def _line_change_counts(before: str, after: str) -> tuple[int, int]:
    """Count changed lines with the same line model used by the diff view."""
    matcher = difflib.SequenceMatcher(
        None, before.splitlines(), after.splitlines(), autojunk=False,
    )
    additions = 0
    deletions = 0
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag in ("replace", "delete"):
            deletions += i2 - i1
        if tag in ("replace", "insert"):
            additions += j2 - j1
    return additions, deletions


def _unified_diff(before: str, after: str) -> str:
    diff = "\n".join(
        difflib.unified_diff(
            before.splitlines(),
            after.splitlines(),
            fromfile="before",
            tofile="after",
            lineterm="",
        )
    )
    if len(diff) > 4000:
        return diff[:4000] + "\n… [diff truncated]"
    return diff
