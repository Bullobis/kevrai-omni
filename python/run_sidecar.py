#!/usr/bin/env python3
"""Thin launcher for the Kevrai Omni sidecar.

Used both when running from source and as the PyInstaller entry point so the
frozen binary needs no external ``uvicorn`` CLI. Host / port / log level are
read from environment variables set by the Electron main process.
"""
import os

import uvicorn


def main() -> None:
    host = os.environ.get("KEVRAI_SIDECAR_HOST", "127.0.0.1")
    port = int(os.environ.get("KEVRAI_SIDECAR_PORT", "8800"))
    log_level = os.environ.get("KEVRAI_SIDECAR_LOGLEVEL", "warning")
    access_log = os.environ.get("KEVRAI_SIDECAR_ACCESS_LOG", "0") == "1"
    uvicorn.run(
        "app.main:app",
        host=host,
        port=port,
        log_level=log_level,
        access_log=access_log,
    )


if __name__ == "__main__":
    main()
