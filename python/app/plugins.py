"""Kevrai Omni Plugin Manager — DSH-compatible MVP.

Two manifest formats accepted:

1. ``cordis.yml`` (DSH compatible) — the DeepSeek Harness plugin manifest
   simplified to the fields Kevrai actually needs::

       name: my-plugin
       version: 1.0.0
       description: Does something cool
       author: bullobis
       main: plugin.py          # Python entry point (relative to plugin dir)
       # optional hooks (Kevrai extension, not DSH standard)
       hooks:
         - type: tool           # registers a @tool function
           name: my_tool
         - type: engine         # registers a custom engine runtime
         - type: command        # registers a slash command

2. ``plugin.json`` (Kevrai native, minimal)::

       {
         "id": "my-plugin",
         "name": "My Plugin",
         "version": "1.0.0",
         "main": "plugin.py"
       }

Plugin lifecycle: install → activate → deactivate → uninstall.

Directory layout per plugin::

    <DATA_ROOT>/plugins/<id>/
    ├── cordis.yml              or    ├── plugin.json
    ├── <main>.py              (entry point; exported hooks register themselves)
    └── requirements.txt       (optional, pip-installed on activation)

The entry point must expose a ``register(kevrai)`` function or decorate
functions with ``@kevrai.plugin.tool`` / ``@kevrai.plugin.engine`` etc.
The ``kevrai`` handle is a thin namespace exposing hook registration and
access to the sidecar state (``engines``, ``models``, ``catalog``).
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Callable
from functools import wraps

try:
    import yaml  # type: ignore[import-not-found]
except Exception:
    yaml = None  # optional; we fall back to plugin.json


# ---------------------------------------------------------------------------
# Public decorator API — plugins import this as ``from app.plugins import plugin``
# ---------------------------------------------------------------------------

class PluginAPI:
    """Handle passed to plugin entry points. Plugins call methods on it
    to register hooks and query sidecar state."""

    def __init__(self, plugin_id: str) -> None:
        self._id = plugin_id
        self._registered_tools: dict[str, Callable] = {}
        self._registered_engines: dict[str, Any] = {}
        self._registered_commands: dict[str, Callable] = {}
        self._registered_routes: list[tuple[str, str, Callable]] = []

    # --- Hook registration (fluent decorator factories) ---
    def tool(self, name: str | None = None, description: str = ""):
        """``@kevrai.plugin.tool(name="my_tool", description="...")``"""
        def deco(fn):
            tname = name or fn.__name__
            self._registered_tools[tname] = fn
            fn.__kevrai_tool__ = {"name": tname, "description": description}
            return fn
        return deco

    def engine(self, name: str):
        """``@kevrai.plugin.engine(name="my-engine")`` — registers a custom
        engine runtime (subclass of EngineRuntime)."""
        def deco(cls):
            self._registered_engines[name] = cls
            return cls
        return deco

    def command(self, name: str, description: str = ""):
        """``@kevrai.plugin.command(name="/hello")`` — slash command for Agent."""
        def deco(fn):
            self._registered_commands[name] = fn
            fn.__kevrai_command__ = {"name": name, "description": description}
            return fn
        return deco

    def route(self, path: str, methods: str | list[str] = "GET"):
        """``@kevrai.plugin.route("/api/my-plugin/...")`` — FastAPI route.

        Plugins that need full router access can append here; the manager
        will mount them on the main app when the plugin is activated."""
        if isinstance(methods, str):
            methods = [methods]
        def deco(fn):
            for m in methods:
                self._registered_routes.append((path, m.upper(), fn))
            return fn
        return deco


# Module-level singleton — plugins import ``plugin.tool`` etc.
plugin = PluginAPI(plugin_id="__unbound__")  # reassigned per-plugin at load time


# ---------------------------------------------------------------------------
# Plugin metadata & manager
# ---------------------------------------------------------------------------

@dataclass
class PluginMeta:
    id: str
    name: str
    version: str
    description: str = ""
    author: str = ""
    main: str = "plugin.py"
    hooks: list[str] = field(default_factory=list)
    path: str = ""           # absolute path, filled at load time
    installed: bool = False
    active: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class PluginManager:
    """Loads, installs, activates, and deactivates plugins."""

    def __init__(self, data_root: Path) -> None:
        self.root = Path(data_root) / "plugins"
        self.root.mkdir(parents=True, exist_ok=True)
        self._plugins: dict[str, PluginMeta] = {}
        self._active_handles: dict[str, PluginAPI] = {}
        self._tools: dict[str, Callable] = {}
        self._engines: dict[str, Any] = {}
        self._commands: dict[str, Callable] = {}
        self._routes: list[tuple[str, str, Callable]] = []
        self._discover()

    # --- Discovery ---

    def _discover(self) -> None:
        """Scan plugins dir for installed plugins, load their manifests."""
        self._plugins.clear()
        for d in sorted(self.root.iterdir()):
            if not d.is_dir() or d.name.startswith("."):
                continue
            meta = self._load_manifest(d)
            if meta:
                meta.installed = True
                self._plugins[meta.id] = meta

    def _load_manifest(self, plugin_dir: Path) -> PluginMeta | None:
        """Load cordis.yml (preferred) or plugin.json."""
        cordis = plugin_dir / "cordis.yml"
        pjson = plugin_dir / "plugin.json"

        if cordis.exists():
            if yaml is None:
                # cordis.yml needs PyYAML; fall back to plugin.json or bail
                return None
            try:
                data = yaml.safe_load(cordis.read_text()) or {}
            except Exception:
                return None
            pid = str(data.get("name", plugin_dir.name))
            # Kevrai extension: allow ``id`` override; default == dir name
            pid = data.get("id", plugin_dir.name)
            hooks_raw = data.get("hooks") or []
            hooks = [h.get("type", h) if isinstance(h, dict) else str(h)
                     for h in hooks_raw]
            return PluginMeta(
                id=str(pid),
                name=str(data.get("displayName") or data.get("name", pid)),
                version=str(data.get("version", "0.0.0")),
                description=str(data.get("description", "")),
                author=str(data.get("author", "")),
                main=str(data.get("main", data.get("entry", "plugin.py"))),
                hooks=hooks,
                path=str(plugin_dir),
            )

        if pjson.exists():
            try:
                data = json.loads(pjson.read_text())
            except Exception:
                return None
            pid = str(data.get("id", plugin_dir.name))
            return PluginMeta(
                id=pid,
                name=str(data.get("name", pid)),
                version=str(data.get("version", "0.0.0")),
                description=str(data.get("description", "")),
                author=str(data.get("author", "")),
                main=str(data.get("main", "plugin.py")),
                hooks=list(data.get("hooks", [])),
                path=str(plugin_dir),
            )
        return None

    # --- Listing ---

    def list(self) -> list[PluginMeta]:
        return sorted(self._plugins.values(), key=lambda p: p.id)

    def get(self, plugin_id: str) -> PluginMeta | None:
        return self._plugins.get(plugin_id)

    def tools(self) -> dict[str, Callable]:
        return dict(self._tools)

    # --- Install / uninstall ---

    def install(self, git_url: str, plugin_id: str | None = None) -> PluginMeta:
        """Clone a plugin from GitHub (or any git repo)."""
        # DSH convention: github shorthand like "owner/repo"
        if not git_url.startswith(("http", "git@")) and "/" in git_url:
            git_url = f"https://github.com/{git_url}"

        pid = plugin_id or git_url.rstrip("/").rsplit("/", 1)[-1]
        if pid.endswith(".git"):
            pid = pid[:-4]
        target = self.root / pid

        if target.exists():
            # Update existing
            subprocess.run(["git", "-C", str(target), "pull", "--ff-only"],
                           check=False, timeout=30)
        else:
            subprocess.run(
                ["git", "clone", "--depth", "1", git_url, str(target)],
                check=True, timeout=60,
            )

        meta = self._load_manifest(target)
        if meta is None:
            raise RuntimeError(f"plugin at {target} has no cordis.yml or plugin.json")
        meta.installed = True
        self._plugins[meta.id] = meta
        return meta

    def uninstall(self, plugin_id: str) -> None:
        meta = self._plugins.get(plugin_id)
        if not meta:
            raise KeyError(plugin_id)
        if meta.active:
            self.deactivate(plugin_id)
        import shutil
        shutil.rmtree(self.root / plugin_id, ignore_errors=True)
        del self._plugins[plugin_id]

    # --- Activate / deactivate ---

    def activate(self, plugin_id: str) -> PluginMeta:
        meta = self._plugins.get(plugin_id)
        if not meta:
            raise KeyError(plugin_id)
        if meta.active:
            return meta

        plugin_dir = Path(meta.path)

        # 1. Install Python deps if requirements.txt present
        reqs = plugin_dir / "requirements.txt"
        if reqs.exists():
            subprocess.run(
                [sys.executable, "-m", "pip", "install",
                 "--target", str(plugin_dir / "_deps"), "-r", str(reqs)],
                check=False, timeout=120,
            )

        # 2. Import entry point
        main_file = plugin_dir / meta.main
        if not main_file.exists():
            raise RuntimeError(f"plugin {plugin_id}: entry {meta.main} not found")

        # Temporarily prepend plugin dir + _deps to sys.path
        extra_paths = [str(plugin_dir)]
        deps_dir = plugin_dir / "_deps"
        if deps_dir.exists():
            extra_paths.insert(0, str(deps_dir))
        saved_path = sys.path[:]
        for p in extra_paths:
            if p not in sys.path:
                sys.path.insert(0, p)

        try:
            # Fresh PluginAPI handle scoped to this plugin
            handle = PluginAPI(plugin_id=plugin_id)
            spec = importlib.util.spec_from_file_location(
                f"kevrai_plugin_{plugin_id}", str(main_file))
            if spec is None or spec.loader is None:
                raise RuntimeError(f"cannot import {main_file}")
            mod = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = mod
            spec.loader.exec_module(mod)

            # Plugins can either:
            # (a) define a register(kevrai) function — call it
            # (b) decorate top-level functions with @tool/@command etc.
            register = getattr(mod, "register", None)
            if callable(register):
                register(handle)

        finally:
            sys.path[:] = saved_path

        # 3. Merge registered hooks into manager-level registry
        self._active_handles[plugin_id] = handle
        for tname, tfn in handle._registered_tools.items():
            self._tools[f"{plugin_id}:{tname}"] = tfn
        for ename, cls in handle._registered_engines.items():
            self._engines[f"{plugin_id}:{ename}"] = cls
        for cname, cfn in handle._registered_commands.items():
            self._commands[f"{plugin_id}:{cname}"] = cfn
        for path, method, fn in handle._registered_routes:
            self._routes.append((path, method, fn))

        meta.active = True
        return meta

    def deactivate(self, plugin_id: str) -> PluginMeta:
        meta = self._plugins.get(plugin_id)
        if not meta:
            raise KeyError(plugin_id)
        handle = self._active_handles.pop(plugin_id, None)
        if handle:
            for key in list(self._tools):
                if key.startswith(f"{plugin_id}:"):
                    del self._tools[key]
            for key in list(self._engines):
                if key.startswith(f"{plugin_id}:"):
                    del self._engines[key]
            for key in list(self._commands):
                if key.startswith(f"{plugin_id}:"):
                    del self._commands[key]
            self._routes = [r for r in self._routes
                            if not any(r[2] is fn for _, _, fn in handle._registered_routes)]
        meta.active = False
        return meta

    # --- Apply routes to FastAPI app ---

    def mount_routes(self, app: Any) -> None:
        """Call once on FastAPI startup to mount any plugin-declared routes."""
        from fastapi import APIRouter
        router = APIRouter()
        for path, method, fn in self._routes:
            try:
                router.add_api_route(path, fn, methods=[method])
            except Exception:
                pass
        if router.routes:
            app.include_router(router)
