"""User-facing settings (theme, telemetry, defaults, hardware accel).

Persistence is atomic: write to ``settings.json.tmp`` → fsync → rename.
Corrupt or missing files fall back to defaults without raising.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

ThemeMode = Literal["light", "dark", "system"]
HardwareAccel = Literal["auto", "nvidia", "amd", "apple", "ascend", "cpu"]


def default_data_root() -> Path:
    """Return the per-platform user data directory for Kevrai Omni."""
    if sys.platform.startswith("win"):
        base = Path(os.environ.get("APPDATA") or os.environ.get("LOCALAPPDATA") or Path.home())
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(
            os.environ.get("XDG_DATA_HOME")
            or str(Path.home() / ".local" / "share")
        )
    return base / "KevraiOmni"


def default_settings_path() -> Path:
    return default_data_root() / "settings.json"


def default_cache_root() -> Path:
    """For non-critical caches (e.g. parsed catalog)."""
    if sys.platform.startswith("win"):
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home())
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Caches"
    else:
        base = Path(
            os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
        )
    return base / "KevraiOmni"


# ---------------------------------------------------------------------------
# Model
# ---------------------------------------------------------------------------


class Settings(BaseModel):
    """User settings — schema-versioned for forward-compat."""

    # Fields legitimately start with "model_" (model_dir); opt out of the
    # pydantic v2 protected namespace warning.
    model_config = {"protected_namespaces": ()}

    schema_version: int = 1

    # Filesystem layout
    model_dir: str = ""
    engine_dir: str = ""
    download_dir: str = ""

    # UI / preferences
    theme: ThemeMode = "system"
    default_engine_id: str = "llama.cpp"
    hardware_acceleration: HardwareAccel = "auto"
    telemetry_enabled: bool = False
    max_concurrent_downloads: int = 3
    max_model_size_gb: int = 200

    # Advanced
    debug_http_logs: bool = False

    # Multi-source / mirror configuration
    # list of extra pip mirrors to use when installing packages
    pip_mirrors: list[str] = Field(default_factory=lambda: [
        "https://mirrors.aliyun.com/pypi/simple/",
        "https://pypi.tuna.tsinghua.edu.cn/simple/",
        "https://mirrors.huaweicloud.com/repository/pypi/simple/",
        "https://pypi.org/simple/",
    ])
    # list of extra model download mirrors (appended to each model's sources[])
    # ALL enabled by default — the auto-picker speed-tests every one and uses
    # the fastest reachable mirror at download time.
    extra_model_mirrors: list[str] = Field(default_factory=lambda: [
        "https://hf-mirror.com",
        "https://hf-mirror.us",
        "https://hf-cdn.sufy.com",
        "https://huggingface.dl.in.tel",
        "https://hf-cn-mirror.com",
    ])
    # auto_pick enabled by default; user can disable to always use primary_url
    auto_pick_best_source: bool = True

    # HuggingFace token — required for gated repos (e.g. Lightricks/LTX-2.5).
    # The user must also accept the model's license agreement on the HF repo
    # page; the token alone is not sufficient for gated access.
    hf_token: str = ""

    # Anything else, key-by-key
    extra: dict[str, Any] = Field(default_factory=dict)

    @field_validator("max_concurrent_downloads")
    @classmethod
    def _cap_concurrency(cls, v: int) -> int:
        if v < 1:
            return 1
        if v > 16:
            return 16
        return v

    @field_validator("max_model_size_gb")
    @classmethod
    def _cap_size(cls, v: int) -> int:
        if v < 1:
            return 1
        if v > 4096:
            return 4096
        return v

    # --- helpers ---

    def resolved_model_dir(self) -> Path:
        if self.model_dir:
            return Path(self.model_dir).expanduser()
        return default_data_root() / "models"

    def resolved_engine_dir(self) -> Path:
        if self.engine_dir:
            return Path(self.engine_dir).expanduser()
        return default_data_root() / "engines"

    def resolved_download_dir(self) -> Path:
        if self.download_dir:
            return Path(self.download_dir).expanduser()
        return default_data_root() / "downloads"


# ---------------------------------------------------------------------------
# I/O
# ---------------------------------------------------------------------------


def _defaults() -> Settings:
    s = Settings()
    # Pre-populate the resolved-on-disk paths so first-run sees sensible values.
    s.model_dir = str(s.resolved_model_dir())
    s.engine_dir = str(s.resolved_engine_dir())
    s.download_dir = str(s.resolved_download_dir())
    return s


def load_settings(path: str | os.PathLike[str] | None = None) -> Settings:
    """Load settings from disk; fall back to defaults on any failure."""
    fp = Path(path) if path else default_settings_path()
    if not fp.exists():
        return _defaults()
    try:
        data = json.loads(fp.read_text(encoding="utf-8") or "{}")
    except (OSError, json.JSONDecodeError):
        return _defaults()
    if not isinstance(data, dict):
        return _defaults()
    try:
        # Pop non-schema keys into "extra"
        known = set(Settings.model_fields.keys())
        extras = {k: v for k, v in data.items() if k not in known}
        data_clean = {k: v for k, v in data.items() if k in known}
        if extras:
            data_clean["extra"] = extras
        return Settings.model_validate(data_clean)
    except Exception:
        # Schema mismatch — fall back to defaults but write back on next save
        return _defaults()


def save_settings(
    settings: Settings,
    path: str | os.PathLike[str] | None = None,
) -> Path:
    """Atomically write settings. Returns the final path."""
    fp = Path(path) if path else default_settings_path()
    fp.parent.mkdir(parents=True, exist_ok=True)
    payload = settings.model_dump()
    # Merge extras onto top level
    if "extra" in payload and isinstance(payload["extra"], dict):
        payload.update(payload.pop("extra"))

    blob = json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True)

    # Atomic write: temp + fsync + rename
    fd, tmp_path = tempfile.mkstemp(
        prefix=".settings-", suffix=".tmp", dir=str(fp.parent)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(blob)
            fh.flush()
            try:
                os.fsync(fh.fileno())
            except OSError:
                pass
        os.replace(tmp_path, fp)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise
    return fp


def ensure_dirs(settings: Settings) -> list[Path]:
    """Create user data directories. Idempotent."""
    paths = [
        settings.resolved_model_dir(),
        settings.resolved_engine_dir(),
        settings.resolved_download_dir(),
        default_data_root(),
    ]
    for p in paths:
        try:
            p.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
    return paths
