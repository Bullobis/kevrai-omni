# python/run_sidecar.py — PyInstaller thin entry point.
# Turns `python -m uvicorn app.main:app` into an explicit call so PyInstaller's
# static analysis can pin the entry point. Host/port/log come from env vars,
# matching electron/main.js's sidecarEnv().
import os

import uvicorn

if __name__ == "__main__":
    uvicorn.run(
        "app.main:app",
        host=os.environ.get("KEVRAI_HOST", "127.0.0.1"),
        port=int(os.environ.get("KEVRAI_PORT", "8765")),
        log_level=os.environ.get("KEVRAI_LOG", "info"),
    )
