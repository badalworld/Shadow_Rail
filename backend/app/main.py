"""
Shadow Rail — entry point.

    python -m app.main              # API + dashboard on 0.0.0.0:8080
    SHADOW_RAIL_PORT=9000 python -m app.main
"""
from __future__ import annotations

import os
import sys

import uvicorn

from .logsetup import configure


def run() -> None:
    configure()
    port = int(os.environ.get("SHADOW_RAIL_PORT", "8080"))
    host = os.environ.get("SHADOW_RAIL_HOST", "0.0.0.0")
    reload_flag = os.environ.get("SHADOW_RAIL_RELOAD", "0") == "1"
    uvicorn.run("app.api:app" if not reload_flag else "app.api:app",
                host=host, port=port, reload=reload_flag, log_level="info",
                ws_ping_interval=20, ws_ping_timeout=20, timeout_keep_alive=30)


if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    run()
