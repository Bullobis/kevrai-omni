"""JSON Schema definitions for catalog/models.json and catalog/engines.json.

Multi-source catalog schema:
    * Each model entry may carry a `sources[]` list of mirror URLs. The
      downloader speed-tests every mirror and picks the fastest.
    * No negative blocklist — the user can add any mirror via Settings.
    * Validation enforces *structure* (required fields, type, semver, id
      shape, http URL shape) and *category* (only known categories), but
      leaves host selection entirely to the user.
"""
from __future__ import annotations

from typing import Any

try:
    from jsonschema import Draft202012Validator  # type: ignore
    _HAS_JSONSCHEMA = True
except Exception:  # pragma: no cover
    Draft202012Validator = None  # type: ignore
    _HAS_JSONSCHEMA = False


# (Kept for backward compatibility with callers/tests that imported these.)
BLOCKED_MIRRORS: tuple[str, ...] = ()
DISCOURAGED_MIRRORS: tuple[str, ...] = ()
# v2.2.0 — no positive host restriction; any http(s) URL is acceptable.
ALLOWED_MODEL_HOSTS: tuple[str, ...] = ()
ALLOWED_ENGINE_HOSTS: tuple[str, ...] = ()

# Allowed model categories — must match python/app/main.py.
ALLOWED_CATEGORIES: tuple[str, ...] = (
    "llm",
    "tts",
    "video",
    "image",
    "superres",
    "audio",
    "3d",
    "vision",
    "pending",
)

ALLOWED_ENGINE_CATEGORIES: tuple[str, ...] = (
    "llm",
    "lightweight",
    "diffusion",
    "tts",
    "3d",
    "vision",
    "general",
)


def _semver_like() -> dict[str, Any]:
    return {
        "type": "string",
        "pattern": r"^\d+\.\d+\.\d+([\-+][0-9A-Za-z.\-]+)?$",
    }


def _http_url() -> dict[str, Any]:
    """Any http(s) URL is acceptable (no host restrictions)."""
    return {
        "type": "string",
        "pattern": r"^https?://",
        "minLength": 8,
        "maxLength": 2048,
    }


MODEL_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "Kevrai Studio — model catalog",
    "type": "object",
    "required": ["version", "models"],
    "additionalProperties": True,
    "properties": {
        "version": {"type": "string", "minLength": 1},
        "updated": {"type": "string"},
        "notice": {"type": "string"},
        "custom_sources_allowed": {"type": "boolean"},
        "categories": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["id", "label"],
                "properties": {
                    "id": {"enum": list(ALLOWED_CATEGORIES)},
                    "label": {"type": "string", "minLength": 1},
                },
            },
        },
        "models": {
            "type": "array",
            "minItems": 1,
            "items": {"$ref": "#/$defs/ModelEntry"},
        },
        "gguf_repos": {
            "type": "array",
            "items": {"$ref": "#/$defs/GGUFRepoEntry"},
        },
    },
    "$defs": {
        "ModelEntry": {
            "type": "object",
            "required": ["id", "category", "name"],
            "additionalProperties": True,
            "properties": {
                "id": {
                    "type": "string",
                    "pattern": r"^[a-zA-Z0-9_.\-]+$",
                    "minLength": 1,
                    "maxLength": 100,
                },
                "category": {"enum": list(ALLOWED_CATEGORIES)},
                "name": {"type": "string", "minLength": 1, "maxLength": 200},
                "repo": {"type": "string", "maxLength": 1024},
                "gguf_repo": {"type": "string", "maxLength": 1024},
                "size_gb": {"type": "number", "minimum": 0, "maximum": 10000},
                "size_bytes": {"type": "integer", "minimum": 0},
                "engine": {
                    "type": "array",
                    "items": {"type": "string", "minLength": 1},
                },
                "license": {"type": "string"},
                "trending": {"type": "boolean"},
                "description": {"type": "string"},
                "source": {"type": "string", "maxLength": 1024},
                "primary_url": _http_url(),
                "sources": {
                    "type": "array",
                    "items": _http_url(),
                },
                "files": {
                    "type": "array",
                    "items": {"type": "string"},
                },
            },
        },
        "GGUFRepoEntry": {
            "type": "object",
            "required": ["id", "name", "owner_repo"],
            "additionalProperties": True,
            "properties": {
                "id": {"type": "string", "minLength": 1, "maxLength": 100},
                "name": {"type": "string", "minLength": 1},
                "owner_repo": {
                    "type": "string",
                    "pattern": r"^[A-Za-z0-9_.\-]+/[A-Za-z0-9_.\-]+$",
                },
                "filter": {"type": "string", "minLength": 1},
                "note": {"type": "string"},
            },
        },
    },
}


ENGINE_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "Kevrai Studio — engine catalog",
    "type": "object",
    "required": ["version", "engines"],
    "additionalProperties": True,
    "properties": {
        "version": {"type": "string", "minLength": 1},
        "updated": {"type": "string"},
        "engines": {
            "type": "array",
            "minItems": 1,
            "items": {"$ref": "#/$defs/EngineEntry"},
        },
    },
    "$defs": {
        "EngineEntry": {
            "type": "object",
            "required": ["id", "name", "category"],
            "additionalProperties": True,
            "properties": {
                "id": {
                    "type": "string",
                    "pattern": r"^[a-zA-Z0-9_.\-]+$",
                    "minLength": 1,
                    "maxLength": 100,
                },
                "name": {"type": "string", "minLength": 1},
                "category": {"enum": list(ALLOWED_ENGINE_CATEGORIES)},
                "description": {"type": "string"},
                "github": {
                    "type": "string",
                    "pattern": r"^[A-Za-z0-9_.\-]+/[A-Za-z0-9_.\-]+$",
                },
                "license": {"type": "string"},
                "version": _semver_like(),
                "size_mb": {"type": "number", "minimum": 0, "maximum": 100000},
                "trending": {"type": "boolean"},
                "install": {"enum": ["pip", "zip", "binary", "source"]},
                "pypi": {"type": "string", "minLength": 1},
                "platforms": {
                    "type": "object",
                    "properties": {
                        "windows-x64": _http_url(),
                        "linux-x64": _http_url(),
                        "darwin-arm64": _http_url(),
                        "darwin-x64": _http_url(),
                    },
                    "additionalProperties": False,
                    "minProperties": 1,
                },
                "sources": {
                    "type": "array",
                    "items": _http_url(),
                },
            },
        },
    },
}


def validate_models(data: dict[str, Any]) -> list[str]:
    if not _HAS_JSONSCHEMA:
        return []
    v = Draft202012Validator(MODEL_SCHEMA)
    return [
        f"models.json: {err.message} (at {list(err.absolute_path) or '<root>'})"
        for err in v.iter_errors(data)
    ]


def validate_engines(data: dict[str, Any]) -> list[str]:
    if not _HAS_JSONSCHEMA:
        return []
    v = Draft202012Validator(ENGINE_SCHEMA)
    return [
        f"engines.json: {err.message} (at {list(err.absolute_path) or '<root>'})"
        for err in v.iter_errors(data)
    ]


def assert_models_ok(data: dict[str, Any]) -> None:
    errs = validate_models(data)
    if errs:
        raise ValueError("\n".join(errs))


def assert_engines_ok(data: dict[str, Any]) -> None:
    errs = validate_engines(data)
    if errs:
        raise ValueError("\n".join(errs))
