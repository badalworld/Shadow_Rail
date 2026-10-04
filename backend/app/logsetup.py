"""
Process logging.

The dashboard's Workflow Log lives in SQLite and is streamed over the websocket;
that is the *operator* view.  A production deployment also needs the machine view:
one line per event on stdout, so Docker/journald/supervisors capture history that
survives the database, and so `docker logs` is enough to debug a bad boot.

Nothing here changes what the engine records — it mirrors the same records out.
"""
from __future__ import annotations

import logging
import os
import sys

LOGGER_NAME = "shadow_rail"

_LEVELS = {"debug": logging.DEBUG, "info": logging.INFO, "success": logging.INFO,
           "warn": logging.WARNING, "error": logging.ERROR, "sos": logging.CRITICAL}


def level_for(record_level: str) -> int:
    """Map the journal's level vocabulary onto stdlib logging levels."""
    return _LEVELS.get((record_level or "info").lower(), logging.INFO)


def get_logger() -> logging.Logger:
    return logging.getLogger(LOGGER_NAME)


def configure(level: str | None = None) -> None:
    """
    Idempotent one-line-per-event logging to stdout.

    `SHADOW_RAIL_LOG_LEVEL` (default INFO) and `SHADOW_RAIL_LOG_FILE` (optional,
    in addition to stdout) are read here; uvicorn keeps its own access log.
    """
    lvl = (level or os.environ.get("SHADOW_RAIL_LOG_LEVEL", "info")).upper()
    fmt = "%(asctime)s %(levelname)-7s %(name)s %(message)s"
    root = logging.getLogger()
    if not root.handlers:
        logging.basicConfig(level=getattr(logging, lvl, logging.INFO),
                            format=fmt, stream=sys.stdout)
    root.setLevel(getattr(logging, lvl, logging.INFO))

    log = get_logger()
    path = os.environ.get("SHADOW_RAIL_LOG_FILE", "").strip()
    if path and not any(getattr(h, "baseFilename", "") == os.path.abspath(path)
                        for h in log.handlers):
        try:
            handler = logging.FileHandler(path, encoding="utf-8")
            handler.setFormatter(logging.Formatter(fmt))
            log.addHandler(handler)
        except OSError:                    # an unwritable path must not kill boot
            log.warning("log file %s is not writable — stdout only", path)


def journal(level: str, bot_id: str, message: str, topic: str | None = None) -> None:
    """Emit one journal record to the process log."""
    get_logger().log(level_for(level), "%s %s", bot_id or "-", f"[{topic}] {message}"
                     if topic else message)
