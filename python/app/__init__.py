"""Kevrai Omni — inference orchestration sidecar (FastAPI)."""

__version__ = "3.4.1"

#: Canonical HTTP User-Agent for every outbound request the sidecar makes.
#: Single source of truth — modules MUST import this instead of hardcoding a
#: version string, otherwise the UA silently drifts from ``__version__``.
USER_AGENT = f"kevrai-omni/{__version__}"
